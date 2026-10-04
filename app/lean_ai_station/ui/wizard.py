"""First-run wizard: detect → choose (pre-filled) → self-test → ready. Zero config files to edit."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QComboBox, QFormLayout, QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from .. import leancheck
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
        self.skip = button("Passer l'assistant", "Link", "Configurer plus tard (tout reste modifiable)", self._finish)
        nav.addWidget(self.skip)
        nav.addStretch(1)
        self.back = button("← Retour", slot=self._back)
        self.next = button("Commencer →", "Primary", slot=self._next)
        nav.addWidget(self.back)
        nav.addWidget(self.next)
        self.card.lay.addLayout(nav)

        # 0 welcome
        w = QWidget()
        w.setObjectName("Clear")
        wl = QVBoxLayout(w)
        wl.addWidget(label("Cet assistant vérifie votre ordinateur et prépare tout automatiquement. "
                           "Comptez environ une minute. Rien à installer ni à configurer à la main.", wrap=True))
        wl.addWidget(label("• Un modèle d'IA spécialisé (Goedel-Prover) écrit des preuves Lean.\n"
                           "• Lean vérifie chaque preuve : seules les preuves correctes sont acceptées.\n"
                           "• Rien n'est envoyé sur Internet : l'IA est un fichier installé chez vous, exécuté par la carte graphique (à défaut, le processeur).", wrap=True))
        self.pages.addWidget(w)
        # 1 detection
        d = QWidget()
        d.setObjectName("Clear")
        dl = QVBoxLayout(d)
        self.det = {k: _Step(t) for k, t in [("gpu", "Carte graphique"), ("engine", "Moteur d'IA"),
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
        self.model_combo.setToolTip("Le modèle conseillé est présélectionné.")
        self.ws_combo = QComboBox()
        self.ws_combo.setToolTip("La version de Lean conseillée pour Goedel-Prover est présélectionnée.")
        form.addRow("Modèle d'IA :", self.model_combo)
        form.addRow("Espace Lean :", self.ws_combo)
        form.addRow(label("Ces choix sont déjà les bons dans la plupart des cas : cliquez simplement sur « Suivant ».",
                          "Muted", wrap=True))
        self.pages.addWidget(c)
        # 3 self-test
        t = QWidget()
        t.setObjectName("Clear")
        tl = QVBoxLayout(t)
        self.test = {k: _Step(tx) for k, tx in [("load", "Chargement du modèle sur la carte graphique"),
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
        self.title.setText(titles[i][0])
        self.sub.setText(titles[i][1])
        self.back.setVisible(0 < i < 4)
        self.next.setText(["Commencer →", "Suivant →", "Suivant →", "Suivant →", "Ouvrir l'accueil"][i])
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
            self.det["gpu"].set("ok", f"{g['name']} — {g['total'] / 1024:.1f} Go de mémoire graphique")
        else:
            self.det["gpu"].set("warn", "Non détectée : le modèle fonctionnera sur le processeur (beaucoup plus lent).")
        b = llama_bin("llama-server")
        self.det["engine"].set("ok" if b.exists() else "fail", "llama.cpp (CUDA)" if b.exists() else
                               "Introuvable : ouvrez « Système » → « Recompiler ».")
        good = [m for m in self.ctx.models if not isinstance(m, tuple)]
        dm = self.ctx.default_model()
        self.det["model"].set("ok" if dm else "fail", f"{len(good)} modèle(s) ; conseillé : {dm.name}" if dm else
                              "Aucun modèle : importez-en un dans « Modèles ».")
        ready = [w for w in self.ctx.workspaces if not w.problems]
        self.det["lean"].set("ok" if ready else "fail", f"{len(ready)} espace(s) prêt(s)" if ready else
                             "Aucun espace Lean prêt : ouvrez « Système » pour l'installer.")
        self.next.setEnabled(True)

    def _fill_choices(self):
        self.model_combo.clear()
        dm = self.ctx.default_model()
        for m in self.ctx.models:
            if isinstance(m, tuple):
                continue
            self.model_combo.addItem(f"{m.path.name}  ({m.quant}, {m.size / 1e9:.1f} Go)" +
                                     ("  — conseillé" if m.path == dm else ""), str(m.path))
            if m.path == dm:
                self.model_combo.setCurrentIndex(self.model_combo.count() - 1)
        self.ws_combo.clear()
        for w in self.ctx.workspaces:
            if w.problems:
                continue
            self.ws_combo.addItem(w.label + ("  — conseillé" if w.key == "lean-prover49" else ""), w.key)
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
        self.test["load"].set("run", "Environ 10 à 30 secondes…")
        if not self.ctx.default_model():
            self.test["load"].set("fail", "Aucun modèle installé.")
            self._lean_test()
            return
        self.ctx.server.failed.connect(self._load_failed)
        self._connected = True
        self.ctx.load_model(self.ctx.default_model(), then=self._loaded)

    def _load_failed(self, kind, details):
        self._disc()
        self.test["load"].set("fail", "Le modèle n'a pas pu être chargé (voir le message en haut).")
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
        self.test["load"].set("ok", f"Chargé en {srv.load_seconds or 0:.1f} s — couches sur la carte graphique : "
                                    f"{srv.offload or '?'}")
        self.test["gen"].set("run")
        self._stream = ChatStream(srv.url, self)
        self._stream.done.connect(self._gen_done)
        self._stream.error.connect(self._gen_failed)
        self._stream.start([{"role": "user", "content": "Reply with the single word: OK"}], 0.0, 1.0, 48)

    def _gen_failed(self, *_):
        self._stream = None
        self.test["gen"].set("fail", "Pas de réponse du modèle.")
        self._lean_test()

    def _gen_done(self, d: dict):
        self._stream = None
        self.test["gen"].set("ok", f"Réponse en {d['seconds']:.1f} s ({d['tps']:.0f} tokens/s)")
        self._lean_test()

    def _lean_test(self):
        if self.pages.currentIndex() != 3:
            return
        ws = self.ctx.workspace()
        if ws is None or ws.check():
            self.test["lean"].set("fail", "Aucun espace Lean prêt (voir « Système »).")
            self._tests_done()
            return
        self.test["lean"].set("run", "Première vérification : jusqu'à 1 minute (chargement de Mathlib en mémoire)…")
        code = leancheck.with_axiom_probe("import Mathlib\n\ntheorem test_station : (2 : ℕ) + 2 = 4 := by norm_num\n",
                                          "test_station")
        self.verifier.compile(code, ws, 300, "test_station")

    def _lean_done(self, res: CompileResult):
        if res.verdict.ok:
            self.test["lean"].set("ok", f"Lean a vérifié une preuve en {res.seconds:.1f} s")
        else:
            self.test["lean"].set("fail", res.verdict.summary + (" " + res.infra_error[:200] if res.infra_error else ""))
        self._tests_done()

    def _tests_done(self):
        oks = sum(1 for s in self.test.values() if s.icon.text() == "✅")
        self.next.setEnabled(True)
        if oks == 3:
            self.ready_text.setText("Tout fonctionne. Sur l'écran d'accueil, cliquez sur « ▶ Prouver » sous un exercice "
                                    "pour voir l'IA trouver une preuve vérifiée par Lean.\n\nRaccourcis : Ctrl+1 à Ctrl+5 "
                                    "pour changer d'onglet, Ctrl+Entrée pour vérifier, Ctrl+Maj+Entrée pour prouver, "
                                    "Échap pour arrêter.")
            QTimer.singleShot(600, lambda: self._show(4) if self.pages.currentIndex() == 3 else None)
        else:
            self.ready_text.setText("L'installation est utilisable mais certains tests ont échoué. L'onglet « Système » "
                                    "indique quoi réparer, avec un bouton pour chaque problème.")

    def _cancel_tests(self):
        if self._stream:
            self._stream.cancel()
            self._stream = None
        if self.verifier.busy:
            self.verifier.cancel()
        self._disc()
