"""System tab: GPU/VRAM/temperature, disk, offline toggle, health checks with one-click repair."""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt
from PySide6.QtWidgets import (QComboBox, QLineEdit, QCheckBox, QGridLayout, QHBoxLayout, QPlainTextEdit, QProgressBar, QScrollArea,
                               QSpinBox, QVBoxLayout, QWidget)

from .. import config
from ..i18n import LANGS, _
from ..errors import Friendly
from ..services import guarded, kill_orphan_server, llama_bin
from . import theme
from .widgets import BusyBar, Card, button, hline, label


class SystemPage(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(20, 16, 20, 12)

        grid = QGridLayout()
        # GPU card
        g = Card()
        g.lay.addWidget(label(_("Carte graphique"), "H2"))
        self.gpu_name = label(_("Détection…"), "Muted", wrap=True)
        g.lay.addWidget(self.gpu_name)
        self.vram = QProgressBar()
        self.vram.setTextVisible(True)
        self.vram.setFormat("%p %")
        self.vram.setToolTip(_("Mémoire graphique (VRAM) utilisée"))
        g.lay.addWidget(label(_("Mémoire graphique (VRAM)")))
        g.lay.addWidget(self.vram)
        self.gpu_misc = label("", "Muted")
        g.lay.addWidget(self.gpu_misc)
        grid.addWidget(g, 0, 0)
        # Disk / RAM
        d = Card()
        d.lay.addWidget(label(_("Disque et mémoire"), "H2"))
        self.disk = QProgressBar()
        self.disk.setTextVisible(True)
        d.lay.addWidget(label(_("Disque (dossier personnel)")))
        d.lay.addWidget(self.disk)
        self.disk_lbl = label("", "Muted")
        d.lay.addWidget(self.disk_lbl)
        self.ram = QProgressBar()
        self.ram.setTextVisible(True)
        d.lay.addWidget(label(_("Mémoire vive (RAM)")))
        d.lay.addWidget(self.ram)
        grid.addWidget(d, 0, 1)
        # Network / options
        n = Card()
        n.lay.addWidget(label(_("Confidentialité et options"), "H2"))
        self.offline = QCheckBox(_("Mode hors-ligne (aucune connexion Internet depuis l'application)"))
        self.offline.setChecked(ctx.settings.offline)
        self.offline.setToolTip(_("Activé : l'application ne contacte que cet ordinateur. Seul le téléchargement de "
                                "modèles a besoin d'Internet."))
        self.offline.toggled.connect(ctx.set_offline)
        n.lay.addWidget(self.offline)
        n.lay.addWidget(label(_("Aucune télémétrie : rien n'est jamais envoyé."), "Muted"))
        n.lay.addWidget(hline())
        tr = QHBoxLayout()
        tr.addWidget(label(_("Délai max. de vérification Lean (s) :")))
        self.timeout = QSpinBox()
        self.timeout.setRange(20, 1800)
        self.timeout.setValue(ctx.settings.compile_timeout_s)
        self.timeout.setToolTip(_("Au-delà, Lean est arrêté et l'essai est compté comme un échec."))
        self.timeout.valueChanged.connect(self._timeout)
        tr.addWidget(self.timeout)
        tr.addStretch(1)
        n.lay.addLayout(tr)
        orow = QHBoxLayout()
        orow.addWidget(label(_("Adresse de votre Overleaf (optionnel) :")))
        self.overleaf = QLineEdit(ctx.settings.overleaf_url)
        self.overleaf.setToolTip(_("Utilisée par « LaTeX ▾ → Ouvrir Overleaf » après une preuve réussie."))
        self.overleaf.editingFinished.connect(self._overleaf)
        orow.addWidget(self.overleaf, 1)
        n.lay.addLayout(orow)
        n.lay.addWidget(button(_("Relancer l'assistant de démarrage"), tip=_("Refaire la configuration guidée"),
                               slot=lambda: ctx.navigate.emit("wizard")))
        lrow = QHBoxLayout()
        lrow.addWidget(label(_("Langue de l'interface et des réponses :")))
        self.lang = QComboBox()
        for code, name in LANGS.items():
            self.lang.addItem(name, code)
        self.lang.setCurrentIndex(max(0, self.lang.findData(ctx.settings.language)))
        self.lang.activated.connect(lambda i: ctx.request_language(self.lang.itemData(i)))
        lrow.addWidget(self.lang)
        lrow.addStretch(1)
        n.lay.addLayout(lrow)
        grid.addWidget(n, 1, 0, 1, 2)
        pc = Card()
        pc.lay.addWidget(label(_("Votre profil (mémoire de l'assistant)"), "H2"))
        pc.lay.addWidget(label(_("Lu avant chaque traduction et chaque explication : qui vous êtes, votre public, vos "
                                 "notations. Le prouveur, lui, n'en tient pas compte (son format est fixé pour rester fiable)."),
                               "Muted", wrap=True))
        self.profile = QPlainTextEdit(ctx.settings.profile)
        self.profile.setMaximumHeight(110)
        self.profile.setPlaceholderText(_("Exemple : « Je suis enseignant en lycée. Mes élèves de terminale débutent : "
                                          "explications simples, sans jargon. ℕ commence à 0. Les suites sont indexées "
                                          "par n ≥ 0. »"))
        self.profile.textChanged.connect(self._profile)
        pc.lay.addWidget(self.profile)
        grid.addWidget(pc, 2, 0, 1, 2)
        from .updates_ui import UpdatesCard
        self.updates_card = UpdatesCard(ctx)
        grid.addWidget(self.updates_card, 3, 0, 1, 2)
        lay.addLayout(grid)

        # health checks
        h = Card()
        hr = QHBoxLayout()
        hr.addWidget(label(_("État de l'installation"), "H2"))
        hr.addStretch(1)
        hr.addWidget(button(_("Tout revérifier"), tip=_("Relancer toutes les vérifications"), slot=self.run_checks))
        h.lay.addLayout(hr)
        self.checks_box = QVBoxLayout()
        h.lay.addLayout(self.checks_box)
        self.busy = BusyBar()
        self.busy.cancelled.connect(self._cancel_repair)
        h.lay.addWidget(self.busy)
        self.repair_log = QPlainTextEdit()
        self.repair_log.setReadOnly(True)
        self.repair_log.setFont(theme.mono_font(9))
        self.repair_log.setMaximumHeight(160)
        self.repair_log.hide()
        h.lay.addWidget(self.repair_log)
        lay.addWidget(h)
        lay.addStretch(1)

        self.repair_proc: QProcess | None = None
        ctx.gpu.stats.connect(self._gpu)
        ctx.gpu.unavailable.connect(self._nogpu)
        ctx.workspacesChanged.connect(self.run_checks)
        ctx.modelsChanged.connect(self.run_checks)
        ctx.server.stateChanged.connect(self.run_checks)
        ctx.settingsChanged.connect(self._sync_offline)

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        self.ctx.gpu.set_interval(1500)
        self.ctx.gpu.poll()
        self._disk()
        self.run_checks()

    def hideEvent(self, e):  # noqa: N802
        super().hideEvent(e)
        self.ctx.gpu.set_interval(5000)

    def _sync_offline(self):
        self.offline.setChecked(self.ctx.settings.offline)

    def _profile(self):
        self.ctx.settings.profile = self.profile.toPlainText()
        self.ctx.save_later()

    def _overleaf(self):
        self.ctx.settings.overleaf_url = self.overleaf.text().strip() or "http://127.0.0.1"
        self.ctx.save_later()

    def _timeout(self, v):
        self.ctx.settings.compile_timeout_s = v
        self.ctx.save_later()

    def _gpu(self, d: dict):
        self.gpu_name.setText(f"{d['name']}")
        self.vram.setRange(0, d["total"])
        self.vram.setValue(d["used"])
        self.vram.setFormat(f"{d['used'] / 1024:.1f} / {d['total'] / 1024:.1f} Go")
        self.gpu_misc.setText(_("Température : {t} °C — Utilisation : {u} %").format(t=d['temp'], u=d['util']))
        self._disk()

    def _nogpu(self, msg: str):
        self.gpu_name.setText(_("⚠️ Carte NVIDIA non disponible : le modèle utilisera le processeur (lent)."))
        self.vram.setValue(0)
        self.gpu_misc.setText(msg[:200])

    def _disk(self):
        u = shutil.disk_usage(Path.home())
        self.disk.setRange(0, 1000)
        self.disk.setValue(int(u.used / u.total * 1000))
        self.disk.setFormat(f"{u.used / 1e9:.0f} / {u.total / 1e9:.0f} Go")
        self.disk_lbl.setText(_("Libre : {n:.0f} Go").format(n=u.free / 1e9) + (_("  ⚠️ presque plein") if u.free < 20e9 else ""))
        try:
            mem = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo") if ":" in l}
            tot, avail = mem["MemTotal"], mem["MemAvailable"]
            self.ram.setRange(0, tot)
            self.ram.setValue(tot - avail)
            self.ram.setFormat(f"{(tot - avail) / 2**20:.1f} / {tot / 2**20:.1f} Go")
        except (OSError, KeyError, ValueError):
            pass

    # ------------------------------------------------------------ health checks
    def checks(self) -> list[tuple[bool, str, str, str | None, str | None]]:
        """(ok, title, detail, repair label, repair id)"""
        ctx = self.ctx
        out = []
        b = llama_bin("llama-server")
        out.append((b.exists(), _("Moteur d'IA (llama-server)"), config.tilde(b) if b.exists() else _("Programme introuvable."),
                    None if b.exists() else "Recompiler", "build_llama"))
        gpu = ctx.gpu_ok
        out.append((gpu is not False, _("Carte graphique NVIDIA"),
                    (ctx.gpu.last or {}).get("name", _("Détection…")) if gpu is not False else
                    _("Non détectée : fonctionnement sur processeur (lent). Redémarrez l'ordinateur si cela persiste."),
                    None, None))
        good = [m for m in ctx.models if not isinstance(m, tuple)]
        bad = [m for m in ctx.models if isinstance(m, tuple)]
        good = [m for m in good if not ctx.is_formalizer(m.path) and not ctx.is_explainer(m.path)]
        out.append((bool(good), _("Modèle d'IA installé (prouveur)"),
                    _("{n} modèle(s) prêt(s)").format(n=len(good)) + (_(", {n} illisible(s)").format(n=len(bad)) if bad else "") if good else
                    _("Aucun modèle utilisable."), None if good else _("Ouvrir Modèles"), "goto_models"))
        fm = ctx.formalizer_model()
        out.append((fm is not None, _("Traducteur français → Lean (Goedel-Formalizer)"),
                    fm.name if fm else _("Absent : sans lui, écrivez l'énoncé directement en Lean. Avec Internet : "
                    "« Modèles » → mradermacher/Goedel-Formalizer-V2-8B-GGUF."),
                    None if fm else _("Ouvrir Modèles"), "goto_models"))
        em = ctx.explainer_model()
        out.append((em is not None, _("Modèle d'explication en français (Qwen3-8B)"),
                    em.name if em else _("Absent : le bouton « Expliquer » est indisponible. Avec Internet : "
                    "« Modèles » → Qwen/Qwen3-8B-GGUF."), None if em else _("Ouvrir Modèles"), "goto_models"))
        for w in ctx.workspaces:
            if w.readonly:
                continue
            ok = not w.problems
            out.append((ok, _("Espace Lean : {ws}").format(ws=_(w.label)), _("Prêt") if ok else " ".join(w.problems),
                        None if ok else _("Installer / réparer"), f"ws:{w.key}"))
        users = [w for w in ctx.workspaces if w.readonly]
        if users:
            out.append((True, _("Projets Lean existants détectés"), ", ".join(w.path.name for w in users) +
                        _(" (utilisés en lecture seule)"), None, None))
        orphan = config.SERVER_PID_FILE.exists() and ctx.server.state == "stopped"
        out.append((not orphan, _("Processus fantômes"), "Aucun" if not orphan else _("Un ancien serveur semble encore actif."),
                    None if not orphan else "Nettoyer", "orphans"))
        free = ctx.disk_free_gb()
        out.append((free > 20, _("Espace disque"), _("{n:.0f} Go libres").format(n=free), None, None))
        return out

    def run_checks(self, *_a):
        if not self.isVisible():
            return
        while self.checks_box.count():
            it = self.checks_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                while it.layout().count():
                    w = it.layout().takeAt(0).widget()
                    if w:
                        w.deleteLater()
        for ok, title, detail, rlabel, rid in self.checks():
            row = QWidget()
            row.setObjectName("Clear")
            r = QHBoxLayout(row)
            r.setContentsMargins(0, 2, 0, 2)
            icon = label("✅" if ok else "⚠️")
            icon.setFixedWidth(28)
            r.addWidget(icon)
            t = label(f"<b>{title}</b><br><span style='color:{theme.MUTED}'>{detail}</span>", wrap=True)
            t.setTextFormat(Qt.RichText)
            r.addWidget(t, 1)
            if rlabel:
                r.addWidget(button(rlabel, "Primary", slot=lambda _c=False, i=rid: self.repair(i)))
            self.checks_box.addWidget(row)

    def repair(self, rid: str):
        if rid == "goto_models":
            self.ctx.navigate.emit("models")
        elif rid == "orphans":
            kill_orphan_server()
            self.ctx.toast.emit(_("Processus nettoyés."), None, None)
            self.run_checks()
        elif rid == "build_llama":
            src = config.STATION_DIR / "vendor" / "llama.cpp"
            self._run_repair(_("Compilation du moteur d'IA (≈ 15 min)…"), "cmake",
                             ["--build", str(src / "build"), "-j", "8", "--target", "llama-server"], src)
        elif rid.startswith("ws:"):
            w = self.ctx.workspace(rid[3:])
            if w is None:
                return
            if self.ctx.settings.offline and not (w.path / ".lake" / "packages").is_dir():
                self.ctx.banner.emit(Friendly(_("Connexion Internet nécessaire"),
                                              _("L'installation de cet espace télécharge Mathlib une seule fois. "
                                              "Désactivez le mode hors-ligne ci-dessus puis recliquez sur « Installer / réparer »."),
                                              [], "info"), "")
                return
            # both scripts are idempotent: clone/download what is missing, then build
            script = config.STATION_DIR / "scripts" / ("build_prover49.sh" if w.key == "lean-prover49" else "setup_current.sh")
            self._run_repair(_("Installation / compilation de « {ws} » (peut être long)…").format(ws=_(w.label)), "bash", [str(script)],
                             config.STATION_DIR)

    def _run_repair(self, text: str, prog: str, args: list[str], cwd: Path):
        if self.repair_proc is not None:
            return
        p = QProcess(self)
        p.setWorkingDirectory(str(cwd))
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PATH", f"{Path.home() / '.elan' / 'bin'}:{env.value('PATH')}")
        p.setProcessEnvironment(env)
        p.setProcessChannelMode(QProcess.MergedChannels)
        p.readyReadStandardOutput.connect(lambda: self.repair_log.appendPlainText(
            bytes(p.readAllStandardOutput()).decode(errors="replace").rstrip()))
        p.finished.connect(lambda code, _s: self._repair_done(p, code))
        self.repair_proc = p
        self.repair_log.clear()
        self.repair_log.show()
        self.busy.start(text)
        prog2, a2 = guarded(prog, args)
        p.start(prog2, a2)

    def _cancel_repair(self):
        if self.repair_proc:
            self.repair_proc.kill()

    def _repair_done(self, p: QProcess, code: int):
        self.repair_proc = None
        p.deleteLater()
        self.busy.stop()
        if code == 0:
            self.ctx.toast.emit(_("Réparation terminée."), None, None)
        else:
            self.ctx.banner.emit(Friendly(_("La réparation n'a pas abouti"),
                                          _("Vérifiez la connexion Internet (si nécessaire) puis réessayez. Le journal "
                                          "est affiché sous les vérifications."), [], "warn"), "")
        self.ctx.refresh_workspaces()
