"""Main window: sidebar navigation, global banner, toast, status bar."""
from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QMainWindow, QStackedWidget, QVBoxLayout, QWidget

from .. import __version__
from ..i18n import LANGS, _, language
from . import theme
from .help import HelpDialog
from .home import HomePage
from .lean_page import LeanPage
from .library_page import LibraryPage
from .models_page import ModelsPage
from .server_page import ServerPage
from .system_page import SystemPage
from .widgets import Banner, Toast, button, label, shortcut
from .wizard import Wizard

NAV = [("home", "🏠  Accueil", "Ctrl+1"), ("lean", "∀  Lean", "Ctrl+2"), ("library", "📚  Bibliothèque", "Ctrl+3"),
       ("models", "📦  Modèles", "Ctrl+4"), ("server", "🖥  Serveur", "Ctrl+5"), ("system", "⚙  Système", "Ctrl+6")]


class MainWindow(QMainWindow):
    def __init__(self, ctx, icon: QIcon | None = None):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(_("Lean AI Station"))
        if icon:
            self.setWindowIcon(icon)
        self.resize(1360, 860)
        self.setMinimumSize(1024, 680)
        self.setAcceptDrops(True)
        root = QWidget()
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        side = QWidget()
        side.setObjectName("Sidebar")
        side.setFixedWidth(196)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(10, 14, 10, 10)
        sl.addWidget(label(_("Lean AI Station"), "AppTitle"))
        self.group = QButtonGroup(self)
        self.nav_btns = {}
        for key, text, keys in NAV:
            b = button(_(text), tip=f"{_(text).split('  ')[1]} ({keys})")
            b.setCheckable(True)
            b.clicked.connect(lambda _c=False, k=key: self.navigate(k))
            self.group.addButton(b)
            self.nav_btns[key] = b
            sl.addWidget(b)
            shortcut(self, keys, lambda k=key: self.navigate(k))
        sl.addStretch(1)
        self.help_btn = button(_("❓  Aide"), tip=_("Lean pour les débutants : à quoi ça sert, comment faire (F1)"),
                               slot=self.show_help)
        self.help_btn.setStyleSheet("text-align: left; padding: 10px 14px; border: none; background: transparent;")
        sl.addWidget(self.help_btn)
        other = "en" if language() == "fr" else "fr"
        self.lang_btn = button(f"🌐  {LANGS[other]}", tip=_("Changer la langue de l'interface et des réponses de l'IA"),
                               slot=lambda _c=False: self.ctx.request_language(other))
        self.lang_btn.setStyleSheet("text-align: left; padding: 10px 14px; border: none; background: transparent;")
        sl.addWidget(self.lang_btn)
        shortcut(self, "F1", self.show_help)
        self.side = side
        h.addWidget(side)

        main = QWidget()
        ml = QVBoxLayout(main)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)
        self.banner = Banner()
        bw = QWidget()
        bl = QVBoxLayout(bw)
        bl.setContentsMargins(20, 10, 20, 0)
        bl.addWidget(self.banner)
        ml.addWidget(bw)
        self.stack = QStackedWidget()
        ml.addWidget(self.stack, 1)
        # status bar
        sb = QWidget()
        sb.setObjectName("StatusBar")
        sbl = QHBoxLayout(sb)
        sbl.setContentsMargins(14, 5, 14, 5)
        self.st_model = label()
        self.st_model.setToolTip(_("État du modèle d'IA (cliquez sur « Serveur » pour les détails)"))
        self.st_gpu = label()
        self.st_net = label()
        self.st_net.setToolTip(_("Mode hors-ligne : modifiable dans « Système »"))
        sbl.addWidget(self.st_model)
        sbl.addStretch(1)
        sbl.addWidget(self.st_gpu)
        sbl.addSpacing(18)
        sbl.addWidget(self.st_net)
        sbl.addSpacing(18)
        sbl.addWidget(label(f"v{__version__}"))
        ml.addWidget(sb)
        h.addWidget(main, 1)

        self.pages = {"home": HomePage(ctx), "lean": LeanPage(ctx), "library": LibraryPage(ctx), "models": ModelsPage(ctx),
                      "server": ServerPage(ctx), "system": SystemPage(ctx), "wizard": Wizard(ctx)}
        for p in self.pages.values():
            self.stack.addWidget(p)
        self.toast = Toast(root)
        self.pages["wizard"].finished.connect(lambda: self.navigate("home"))

        ctx.banner.connect(self.banner.show_message)
        ctx.toast.connect(self._toast)
        ctx.navigate.connect(self.navigate)
        self.banner.action.connect(self._banner_action)
        ctx.server.stateChanged.connect(self._status)
        ctx.gpu.stats.connect(self._status)
        ctx.settingsChanged.connect(self._status)
        self._status()

        geo = ctx.session.get("geometry")
        if geo:
            self.restoreGeometry(QByteArray.fromBase64(geo.encode()))

    # ------------------------------------------------------------
    def navigate(self, key: str):
        if key == "wizard":
            self.pages["wizard"].restart()
        if self.stack.currentWidget() is self.pages["wizard"] and key != "wizard" and not self.ctx.settings.wizard_done:
            return  # finish or skip the wizard first
        self.stack.setCurrentWidget(self.pages[key])
        self.side.setEnabled(key != "wizard")
        if key in self.nav_btns:
            self.nav_btns[key].setChecked(True)
            self.ctx.session["page"] = key
            self.ctx.save_later()
        self.ctx.gpu.set_interval(1500 if key == "system" else 5000)

    def _banner_action(self, act: str):
        if act == "restart_server":
            self.ctx.load_model(self.ctx.default_model())
        elif act.startswith("goto_"):
            self.navigate(act[5:])

    def _toast(self, text, undo, on_expire):
        self.toast.show_toast(text, undo, on_expire=on_expire)

    def _status(self, *_a):
        srv = self.ctx.server
        if srv.state == "ready" and srv.plan:
            self.st_model.setText(_("🟢 {model}  ·  {n} couches GPU").format(model=srv.plan.model.name, n=srv.offload or srv.plan.gpu_layers))
        elif srv.state == "starting":
            self.st_model.setText(_("🟡 Chargement du modèle…"))
        elif srv.state == "stopping":
            self.st_model.setText(_("🟠 Arrêt du modèle…"))
        else:
            self.st_model.setText(_("⚪ Modèle non chargé (chargement automatique au besoin)"))
        g = self.ctx.gpu.last
        self.st_gpu.setText(_("GPU {u:.1f}/{t:.1f} Go · {temp} °C · {util} %").format(u=g['used'] / 1024, t=g['total'] / 1024, temp=g['temp'], util=g['util'])
                            if g else _("GPU : —"))
        self.st_net.setText(_("🔒 Hors-ligne") if self.ctx.settings.offline else _("🌐 En ligne"))

    def show_help(self):
        HelpDialog(self).exec()

    def dragEnterEvent(self, e):  # noqa: N802
        if any(u.toLocalFile().endswith((".lean", ".tex")) for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):  # noqa: N802
        files = [u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile().endswith((".lean", ".tex"))]
        if files:
            self.open_path(files[0])

    def open_path(self, path: str):
        if path.endswith(".lean"):
            self.pages["lean"].load_file(path)
        elif path.endswith(".tex"):
            self.pages["lean"].load_tex(path)

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        if self.toast.isVisible():
            self.toast._place()

    def closeEvent(self, e):  # noqa: N802
        self.ctx.session["geometry"] = bytes(self.saveGeometry().toBase64()).decode()
        if getattr(self, "replaced", False):          # language switch: a new window takes over, keep everything running
            super().closeEvent(e)
            return
        self.ctx.pipeline.cancel()
        self.ctx.formalizer.cancel()
        self.ctx.explainer.cancel()
        self.ctx.prover.cancel()
        for c in (self.ctx.compiler, self.ctx.verifier):
            c.cancel()
        self.pages["wizard"]._cancel_tests()
        self.ctx.server.shutdown_blocking()
        self.ctx.save_now()
        super().closeEvent(e)
