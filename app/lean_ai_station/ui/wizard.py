"""First-run wizard: detect → choose (pre-filled) → self-test → ready. Zero config files to edit."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QComboBox, QFormLayout, QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from .. import leancheck
from ..i18n import _
from ..services import ChatStream, CompileResult, LeanCompiler, llama_bin
from . import theme
from .widgets import Card, button, label


class _Step(QWidget):
    def __init__(self, text: str):
        super().__init__()
        self.setObjectName("Clear")
        r = QHBoxLayout(self)
        r.setContentsMargins(0, 3, 0, 3)
        self.icon = label("⏳")
        self.icon.setFixedWidth(28)
        self.text = label(text, wrap=True)
        self.detail = label("", "Muted", wrap=True)
        col = QVBoxLayout()
        col.addWidget(self.text)
        col.addWidget(self.detail)
        r.addWidget(self.icon)
        r.addLayout(col, 1)

    def set(self, state: str, detail: str = ""):
        self.icon.setText({"wait": "⏳", "run": "🔄", "ok": "✅", "warn": "⚠️", "fail": "❌"}[state])
        self.detail.setText(detail)


class Wizard(QWidget):
    finished = Signal()

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        outer = QVBoxLayout(self)
        outer.setContentsMargins(60, 40, 60, 40)
        self.card = Card(margins=28)
        self.card.setFixedWidth(780)
        outer.addWidget(self.card, 0, Qt.AlignHCenter)
        outer.addStretch(1)
        self.title = label("", "H1", wrap=True)
        self.card.lay.addWidget(self.title)
        self.sub = label("", "Muted", wrap=True)
        self.card.lay.addWidget(self.sub)
        self.pages = QStackedWidget()
        self.pages.setObjectName("Clear")
        self.card.lay.addWidget(self.pages)
        nav = QHBoxLayout()
        self.skip = button(_("Passer l'assistant"), "Link", _("Configurer plus tard (tout reste modifiable)"), self._finish)
        nav.addWidget(self.skip)
        nav.addStretch(1)
        self.back = button(_("← Retour"), slot=self._back)
        self.next = button(_("Commencer →"), "Primary", slot=self._next)
        nav.addWidget(self.back)
        nav.addWidget(self.next)
        self.card.lay.addLayout(nav)

        # 0 welcome
        w = QWidget()
        w.setObjectName("Clear")
        wl = QVBoxLayout(w)
        wl.addWidget(label(_("Cet assistant vérifie votre ordinateur et prépare tout automatiquement. "
                           "Comptez environ une minute. Rien à installer ni à configurer à la main."), wrap=True))
        wl.addWidget(label(_("• Trois IA spécialisées traduisent votre problème en Lean, écrivent la preuve et l'expliquent.\n"
                           "• Lean vérifie chaque preuve : seules les preuves correctes sont acceptées.\n"
                           "• Rien n'est envoyé sur Internet : l'IA est un fichier installé chez vous, exécuté par la carte graphique (à défaut, le processeur)."), wrap=True))
        self.pages.addWidget(w)
        # 1 detection
        d = QWidget()
        d.setObjectName("Clear")
        dl = QVBoxLayout(d)
        self.det = {k: _Step(_(t)) for k, t in [("gpu", "Carte graphique"), ("engine", "Moteur d'IA"),
                                               ("model", "Modèle d'IA"), ("lean", "Lean et Mathlib")]}
        for s in self.det.values():
            dl.addWidget(s)
        dl.addStretch(1)
        self.pages.addWidget(d)
        # 2 choices
        c = QWidget()
        c.setObjectName("Clear")
        form = QFormLayout(c)
        self.model_combo = QComboBox()
        self.model_combo.setToolTip(_("Le modèle conseillé est présélectionné."))
        self.ws_combo = QComboBox()
        self.ws_combo.setToolTip(_("La version de Lean conseillée pour Goedel-Prover est présélectionnée."))
        form.addRow(_("Modèle d'IA :"), self.model_combo)
        form.addRow(_("Espace Lean :"), self.ws_combo)
        form.addRow(label(_("Ces choix sont déjà les bons dans la plupart des cas : cliquez simplement sur « Suivant »."),
                          "Muted", wrap=True))
        self.pages.addWidget(c)
        # 3 self-test
        t = QWidget()
        t.setObjectName("Clear")
        tl = QVBoxLayout(t)
        self.test = {k: _Step(_(tx)) for k, tx in [("load", "Chargement du modèle sur la carte graphique"),
                                                 ("gen", "Test de génération"),
                                                 ("lean", "Test de vérification Lean")]}
        for s in self.test.values():
            tl.addWidget(s)
        tl.addStretch(1)
        self.pages.addWidget(t)
        # 4 ready
        r = QWidget()
        r.setObjectName("Clear")
        rl = QVBoxLayout(r)
        self.ready_text = label("", wrap=True)
        rl.addWidget(self.ready_text)
        rl.addStretch(1)
        self.pages.addWidget(r)

        self.verifier = LeanCompiler(self)
        self.verifier.finished.connect(self._lean_done)
        self._stream = None
        self._show(0)

    # ------------------------------------------------------------ navigation
    def restart(self):
        self._show(0)

    def _show(self, i: int):
        self.pages.setCurrentIndex(i)
        titles = [("Bienvenue !", "Préparons Lean AI Station."),
                  ("Détection de votre ordinateur", "Vérification automatique en cours…"),
                  ("Choix automatiques", "Voici ce que nous vous conseillons."),
                  ("Auto-test", "Nous vérifions que tout fonctionne vraiment."),
                  ("Tout est prêt 🎉", "")]
        self.title.setText(_(titles[i][0]))
        self.sub.setText(_(titles[i][1]) if titles[i][1] else "")
        self.back.setVisible(0 < i < 4)
        self.next.setText([_("Commencer →"), _("Suivant →"), _("Suivant →"), _("Suivant →"), _("Ouvrir l'accueil")][i])
        self.skip.setVisible(i < 4)
        if i == 1:
            self._detect()
        elif i == 2:
            self._fill_choices()
        elif i == 3:
            self._selftest()

    def _next(self):
        i = self.pages.currentIndex()
        if i == 2:
            self._apply_choices()
        if i == 4:
            self._finish()
            return
        self._show(i + 1)

    def _back(self):
        self._cancel_tests()
        self._show(max(0, self.pages.currentIndex() - 1))

    def _finish(self):
        self._cancel_tests()
        self.ctx.settings.wizard_done = True
        self.ctx.save_now()
        self.finished.emit()

    # ------------------------------------------------------------ detection
    def _detect(self):
        for s in self.det.values():
            s.set("run")
        self.next.setEnabled(False)
        self.ctx.gpu.poll()
        self.ctx.refresh_models(then=lambda: self.ctx.refresh_workspaces(then=self._detected))

    def _detected(self):
        g = self.ctx.gpu.last
        if g:
            self.det["gpu"].set("ok", _("{name} — {n:.1f} Go de mémoire graphique").format(name=g['name'], n=g['total'] / 1024))
        else:
            self.det["gpu"].set("warn", _("Non détectée : le modèle fonctionnera sur le processeur (beaucoup plus lent)."))
        b = llama_bin("llama-server")
        self.det["engine"].set("ok" if b.exists() else "fail", _("llama.cpp (CUDA)") if b.exists() else
                               _("Introuvable : ouvrez « Système » → « Recompiler »."))
        good = [m for m in self.ctx.models if not isinstance(m, tuple)]
        dm = self.ctx.default_model()
        self.det["model"].set("ok" if dm else "fail", _("{n} modèle(s) ; conseillé : {name}").format(n=len(good), name=dm.name) if dm else
                              _("Aucun modèle : importez-en un dans « Modèles »."))
        ready = [w for w in self.ctx.workspaces if not w.problems]
        self.det["lean"].set("ok" if ready else "fail", _("{n} espace(s) prêt(s)").format(n=len(ready)) if ready else
                             _("Aucun espace Lean prêt : ouvrez « Système » pour l'installer."))
        self.next.setEnabled(True)

    def _fill_choices(self):
        self.model_combo.clear()
        dm = self.ctx.default_model()
        for m in self.ctx.models:
            if isinstance(m, tuple):
                continue
            self.model_combo.addItem(f"{m.path.name}  ({m.quant}, {m.size / 1e9:.1f} Go)" +
                                     (_("  — conseillé") if m.path == dm else ""), str(m.path))
            if m.path == dm:
                self.model_combo.setCurrentIndex(self.model_combo.count() - 1)
        self.ws_combo.clear()
        for w in self.ctx.workspaces:
            if w.problems:
                continue
            self.ws_combo.addItem(_(w.label) + (_("  — conseillé") if w.key == "lean-prover49" else ""), w.key)
            if w.key == self.ctx.settings.workspace:
                self.ws_combo.setCurrentIndex(self.ws_combo.count() - 1)

    def _apply_choices(self):
        if self.model_combo.currentData():
            self.ctx.settings.model_path = self.model_combo.currentData()
        if self.ws_combo.currentData():
            self.ctx.settings.workspace = self.ws_combo.currentData()
        self.ctx.save_later()

    # ------------------------------------------------------------ self test
    def _selftest(self):
        for s in self.test.values():
            s.set("wait")
        self.next.setEnabled(False)
        self._t0 = time.monotonic()
        self.test["load"].set("run", _("Environ 10 à 30 secondes…"))
        if not self.ctx.default_model():
            self.test["load"].set("fail", _("Aucun modèle installé."))
            self._lean_test()
            return
        self.ctx.server.failed.connect(self._load_failed)
        self._connected = True
        self.ctx.load_model(self.ctx.default_model(), then=self._loaded)

    def _load_failed(self, kind, details):
        self._disc()
        self.test["load"].set("fail", _("Le modèle n'a pas pu être chargé (voir le message en haut)."))
        self._lean_test()

    def _disc(self):
        if getattr(self, "_connected", False):
            self._connected = False
            self.ctx.server.failed.disconnect(self._load_failed)

    def _loaded(self):
        self._disc()
        if self.pages.currentIndex() != 3:
            return
        srv = self.ctx.server
        self.test["load"].set("ok", _("Chargé en {s:.1f} s — couches sur la carte graphique : {n}").format(
                                    s=srv.load_seconds or 0, n=srv.offload or "?"))
        self.test["gen"].set("run")
        self._stream = ChatStream(srv.url, self)
        self._stream.done.connect(self._gen_done)
        self._stream.error.connect(self._gen_failed)
        self._stream.start([{"role": "user", "content": "Reply with the single word: OK"}], 0.0, 1.0, 48)

    def _gen_failed(self, *_a):
        self._stream = None
        self.test["gen"].set("fail", _("Pas de réponse du modèle."))
        self._lean_test()

    def _gen_done(self, d: dict):
        self._stream = None
        self.test["gen"].set("ok", _("Réponse en {s:.1f} s ({t:.0f} tokens/s)").format(s=d['seconds'], t=d['tps']))
        self._lean_test()

    def _lean_test(self):
        if self.pages.currentIndex() != 3:
            return
        ws = self.ctx.workspace()
        if ws is None or ws.check():
            self.test["lean"].set("fail", _("Aucun espace Lean prêt (voir « Système »)."))
            self._tests_done()
            return
        self.test["lean"].set("run", _("Première vérification : jusqu'à 1 minute (chargement de Mathlib en mémoire)…"))
        code = leancheck.with_axiom_probe("import Mathlib\n\ntheorem test_station : (2 : ℕ) + 2 = 4 := by norm_num\n",
                                          "test_station")
        self.verifier.compile(code, ws, 300, "test_station")

    def _lean_done(self, res: CompileResult):
        if res.verdict.ok:
            self.test["lean"].set("ok", _("Lean a vérifié une preuve en {s:.1f} s").format(s=res.seconds))
        else:
            self.test["lean"].set("fail", res.verdict.summary + (" " + res.infra_error[:200] if res.infra_error else ""))
        self._tests_done()

    def _tests_done(self):
        oks = sum(1 for s in self.test.values() if s.icon.text() == "✅")
        self.next.setEnabled(True)
        if oks == 3:
            self.ready_text.setText(_("Tout fonctionne. Sur l'écran d'accueil, cliquez sur « ▶ Prouver » sous un exercice "
                                    "pour voir l'IA trouver une preuve vérifiée par Lean.\n\nRaccourcis : Ctrl+1 à Ctrl+6 "
                                    "pour changer d'onglet, Ctrl+Entrée pour envoyer une demande, F5 pour vérifier, "
                                    "Échap pour arrêter."))
            QTimer.singleShot(600, lambda: self._show(4) if self.pages.currentIndex() == 3 else None)
        else:
            self.ready_text.setText(_("L'installation est utilisable mais certains tests ont échoué. L'onglet « Système » "
                                    "indique quoi réparer, avec un bouton pour chaque problème."))

    def _cancel_tests(self):
        if self._stream:
            self._stream.cancel()
            self._stream = None
        if self.verifier.busy:
            self.verifier.cancel()
        self._disc()
