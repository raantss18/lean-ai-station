"""Update manager (weekly check, notification, one-click install) and the « Mises à jour » card of the System tab."""
from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import sys

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QVBoxLayout, QWidget

from .. import config, updates
from ..errors import Friendly
from ..i18n import _
from ..services import guarded
from .widgets import BusyBar, Card, button, label

STEPS = {
    "toolchain": "Installation de la nouvelle version de Lean…",
    "deps": "Téléchargement de Mathlib…",
    "cache": "Téléchargement des fichiers compilés de Mathlib (plusieurs Go)…",
    "build": "Finalisation de Mathlib…",
    "verify": "Vérification avec un théorème test…",
    "download": "Téléchargement du modèle…",
    "smoke": "Test du nouveau modèle (chargement et réponse)…",
    "switch": "Bascule vers la nouvelle version…",
    "cleanup": "Suppression de l'ancienne version…",
}
ROLE = {"prover": "prouveur", "formalizer": "traducteur"}


def describe(c: updates.Candidate) -> str:
    gb = f"{c.size / 1e9:.1f}"
    if c.kind == "mathlib":
        return _("Lean + Mathlib {new} (installé : {cur}). Environ 8 Go, 10 à 60 minutes.").format(new=c.latest,
                                                                                                    cur=c.current)
    if c.note == "gguf_pending":
        return _("{name} est publié, mais pas encore au format utilisable ici (GGUF). L'outil revérifiera la semaine "
                 "prochaine.").format(name=c.title)
    if c.note == "revision":
        return _("{name} : fichier republié par son auteur (correctifs), {gb} Go.").format(name=c.title, gb=gb)
    return _("{name} : nouvelle version du {role} ({cur} → {new}), {gb} Go.").format(
        name=c.title, role=_(ROLE.get(c.key, c.key)), cur=c.current, new=c.latest, gb=gb)


class UpdateManager(QObject):
    changed = Signal()

    def __init__(self, ctx):
        super().__init__(ctx)
        self.ctx = ctx
        self.state = updates.load_state()
        self.checking = False
        self.proc: QProcess | None = None
        self.current: updates.Candidate | None = None
        self.step = ""
        self.progress = ""
        self.output: list[str] = []

    @property
    def candidates(self) -> list[updates.Candidate]:
        return updates.candidates_from(self.state)

    @property
    def busy(self) -> bool:
        return self.checking or self.proc is not None

    # ------------------------------------------------------------ check
    def maybe_check(self):
        """Startup hook: weekly check if the user keeps it enabled."""
        if self.ctx.settings.check_updates and updates.due(self.state) and os.environ.get("LAS_NO_UPDATE_CHECK") != "1":
            self.check(manual=False)

    def check(self, manual: bool = True):
        if self.busy:
            return
        self.checking = True
        self.changed.emit()
        self.ctx.run_bg(updates.check_all, lambda st: self._checked(st, manual),
                        lambda _t, msg: self._checked({"t": 0, "candidates": [], "errors": [msg]}, manual))

    def _checked(self, state: dict, manual: bool):
        self.checking = False
        notified = set(self.state.get("notified", []))
        if state.get("t"):
            self.state = state
        else:
            self.state = {**self.state, "errors": state.get("errors", [])}
        fresh = [c for c in self.candidates if c.installable and f"{c.key}:{c.latest}" not in notified]
        self.state["notified"] = sorted(notified | {f"{c.key}:{c.latest}" for c in self.candidates})
        self._save()
        self.changed.emit()
        if fresh:
            self.notify(fresh)
        elif manual:
            errs = self.state.get("errors") or []
            self.ctx.toast.emit(_("Vérification impossible (pas d'Internet ?).") if errs and not state.get("t")
                                else _("Tout est à jour.") if not self.candidates
                                else _("Pas de nouvelle mise à jour installable."), None, None)

    def notify(self, cands: list[updates.Candidate]):
        names = ", ".join(c.title for c in cands)
        self.ctx.banner.emit(Friendly(_("Mise à jour disponible"),
                                      _("{names}. L'ancienne version sera supprimée automatiquement une fois la "
                                        "nouvelle vérifiée.").format(names=names),
                                      [(_("Voir les mises à jour"), "goto_system")], "info"), "")
        if shutil.which("notify-send"):
            QProcess.startDetached("notify-send", ["-a", "Lean AI Station", "-i", "system-software-update",
                                                   _("Lean AI Station : mise à jour disponible"), names])

    def _save(self):
        try:
            updates.save_state(self.state)
        except OSError:
            pass

    # ------------------------------------------------------------ install
    def install(self, c: updates.Candidate):
        if self.busy or not c.installable:
            return
        if self.ctx.pipeline.busy or self.ctx.verifier.busy:
            self.ctx.toast.emit(_("Une opération est en cours : arrêtez-la d'abord (Échap)."), None, None)
            return
        self.current, self.step, self.progress, self.output = c, "", "", []
        p = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        p.setProcessEnvironment(env)
        p.setProcessChannelMode(QProcess.MergedChannels)
        p.readyReadStandardOutput.connect(self._read)
        p.finished.connect(self._finished)
        self.proc = p
        prog, args = guarded(sys.executable, [str(config.STATION_DIR / "scripts" / "updater.py"), *c.args()])
        p.start(prog, args)
        self.changed.emit()

    def cancel(self):
        if self.proc:
            self.proc.kill()        # the updater only switches after verification: the old version stays

    def _read(self):
        p = self.sender()
        if p is not self.proc:
            return
        for line in bytes(p.readAllStandardOutput()).decode("utf-8", "replace").splitlines():
            self.output.append(line)
            if line.startswith("STEP "):
                self.step = line.split()[1]
                self.progress = ""
            elif "segments" in line and "GB" in line:
                self.progress = line.split("segments", 1)[1].strip()
            self.changed.emit()

    def _finished(self, code: int, _status):
        p, c = self.proc, self.current
        self.proc, self.current = None, None
        if p is not None:
            p.deleteLater()
        done = next((ln[5:] for ln in self.output if ln.startswith("DONE ")), None)
        fail = next((ln for ln in self.output if ln.startswith("FAIL ")), None)
        if done:
            res = json.loads(done)
            self._apply(res)
            self.state["candidates"] = [d for d in self.state.get("candidates", []) if d.get("key") != c.key]
            self._save()
            removed = ", ".join(res.get("removed") or []) or "—"
            self.ctx.banner.emit(Friendly(_("Mise à jour installée"),
                                          _("{name} est installé et vérifié. Supprimé : {removed}.").format(
                                              name=c.title, removed=removed), [], "ok"), "")
        else:
            key = fail.split()[1] if fail else ("cancelled" if code != 0 else "error")
            if key == "disk":
                msg = _("Pas assez d'espace disque. L'ancienne version est conservée.")
            elif fail is None and code != 0:
                msg = _("Mise à jour annulée. L'ancienne version est conservée.")
            else:
                msg = _("La mise à jour a échoué à l'étape « {step} ». L'ancienne version est conservée.").format(
                    step=_(STEPS.get(self.step, self.step or "?")).rstrip("…"))
            self.ctx.banner.emit(Friendly(_("Mise à jour non installée"), msg, [], "warn"),
                                 "\n".join(self.output[-40:]))
        self.step = self.progress = ""
        self.changed.emit()

    def _apply(self, res: dict):
        srv = self.ctx.server
        if res.get("kind") == "model":
            if res.get("role") == "prover":
                self.ctx.settings.model_path = res["path"]
                self.ctx.save_now()
            loaded = getattr(srv, "model_path", None)
            if loaded and loaded.name in (res.get("removed") or []):
                srv.stop()                      # the next request loads the new model
            self.ctx.refresh_models()
        else:
            self.ctx.refresh_workspaces()


class UpdatesCard(Card):
    """System tab card: installed versions, weekly check toggle, available updates with « Installer »."""

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.m: UpdateManager = ctx.updates
        head = QHBoxLayout()
        head.addWidget(label(_("Mises à jour de Lean et des modèles Goedel"), "H2"))
        head.addStretch(1)
        self.check_btn = button(_("Vérifier maintenant"), tip=_("Contacte GitHub (Mathlib) et Hugging Face (modèles)"),
                                slot=lambda _c=False: self.m.check(manual=True))
        head.addWidget(self.check_btn)
        self.lay.addLayout(head)
        self.auto = QCheckBox(_("Vérifier chaque semaine au démarrage (contacte uniquement GitHub et Hugging Face, "
                                "même en mode hors-ligne)"))
        self.auto.setChecked(ctx.settings.check_updates)
        self.auto.toggled.connect(self._auto)
        self.lay.addWidget(self.auto)
        self.installed = label("", "Muted", wrap=True)
        self.lay.addWidget(self.installed)
        self.status = label("", "Muted", wrap=True)
        self.lay.addWidget(self.status)
        self.list = QVBoxLayout()
        self.lay.addLayout(self.list)
        self.busy = BusyBar()
        self.busy.cancelled.connect(self.m.cancel)
        self.lay.addWidget(self.busy)
        self.lay.addWidget(label(_("Chaque mise à jour est installée à côté de l'ancienne, vérifiée, puis l'ancienne "
                                   "version est supprimée automatiquement (une version de Lean encore utilisée par un "
                                   "de vos projets est gardée). Le prouveur Lean 4.9 reste fixe : c'est la version "
                                   "d'entraînement du modèle."), "Muted", wrap=True))
        self.m.changed.connect(self.refresh)
        ctx.modelsChanged.connect(self.refresh)
        ctx.workspacesChanged.connect(self.refresh)
        self.refresh()

    def _auto(self, on: bool):
        self.ctx.settings.check_updates = on
        self.ctx.save_later()

    def refresh(self, *_a):
        inst = updates.installed_models()
        parts = [_("Lean actuel : Mathlib {v}").format(v=updates.installed_mathlib() or "—")]
        for role in ("prover", "formalizer"):
            if role in inst:
                parts.append(_("{role} : V{v}").format(role=_(ROLE[role]).capitalize(), v=inst[role]["version"]))
        self.installed.setText(" · ".join(parts))
        t = self.m.state.get("t")
        when = _dt.datetime.fromtimestamp(t).strftime("%d/%m/%Y %H:%M") if t else _("jamais")
        if self.m.checking:
            st = _("Vérification en cours…")
        elif self.m.state.get("errors"):
            st = _("Dernière vérification : {when}. Problème : {err}").format(when=when, err=self.m.state["errors"][0][:160])
        elif t and not self.m.candidates:
            st = _("Dernière vérification : {when}. Tout est à jour.").format(when=when)
        else:
            st = _("Dernière vérification : {when}.").format(when=when)
        self.status.setText(st)
        while self.list.count():
            it = self.list.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        for c in self.m.candidates:
            row = QWidget()
            row.setStyleSheet("background: transparent;")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 2, 0, 2)
            rl.addWidget(label(("🔔 " if c.installable else "⏳ ") + describe(c), wrap=True), 1)
            if c.installable:
                b = button(_("Installer"), "Primary", _("Télécharger, vérifier, puis remplacer l'ancienne version"),
                           slot=lambda _c=False, cc=c: self.m.install(cc))
                b.setEnabled(not self.m.busy)
                rl.addWidget(b)
            self.list.addWidget(row)
        self.check_btn.setEnabled(not self.m.busy)
        if self.m.proc is not None:
            txt = _(STEPS.get(self.m.step, "Préparation…"))
            self.busy.start(f"{self.m.current.title} — {txt} {self.m.progress}".strip(), True)
        else:
            self.busy.stop()
