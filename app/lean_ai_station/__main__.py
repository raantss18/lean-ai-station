"""Entry point: single instance, crash recovery, orphan cleanup, then the main window."""
from __future__ import annotations

import time as _time

T_START = _time.monotonic()

import os
import signal
import sys
import time
import zlib
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from . import config
from .i18n import _
from .errors import friendly
from .services import kill_orphan_server

INSTANCE_NAME = f"lean-ai-station-{os.getuid()}-{zlib.crc32(str(config.CONFIG_DIR).encode()):08x}"
ASSETS = Path(__file__).parent / "assets"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def previous_session_crashed() -> bool:
    try:
        pid = int(config.RUN_LOCK_FILE.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return False
    return pid != os.getpid() and not _pid_alive(pid)


def clean_tmp():
    d = config.CACHE_DIR / "tmp"
    now = time.time()
    for f in d.glob("check_*.lean"):
        try:
            if now - f.stat().st_mtime > 600:
                f.unlink()
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    config.ensure_dirs()
    app = QApplication(argv)
    app.setApplicationName("Lean AI Station")
    app.setDesktopFileName("lean-ai-station")

    # --- single instance: forward to the running window and quit
    files = [os.path.abspath(a) for a in argv[1:] if a.endswith((".lean", ".tex")) and os.path.isfile(a)]
    sock = QLocalSocket()
    sock.connectToServer(INSTANCE_NAME)
    if sock.waitForConnected(300):
        sock.write(("open " + files[0] + "\n").encode() if files else b"show\n")
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        print(_("Lean AI Station est déjà ouvert : fenêtre existante mise au premier plan."))
        return 0
    QLocalServer.removeServer(INSTANCE_NAME)
    server = QLocalServer()
    server.listen(INSTANCE_NAME)

    crashed = previous_session_crashed()
    config.atomic_write_text(config.RUN_LOCK_FILE, f"{os.getpid()}\n")
    kill_orphan_server()
    clean_tmp()

    from .ui import theme
    from .ui.context import AppContext
    from .ui.main_window import MainWindow

    theme.apply(app)
    settings, corrupt = config.load_settings()
    from .i18n import set_language
    set_language(os.environ.get("LAS_LANG") or settings.language)
    session = config.load_session()
    icon = QIcon(str(ASSETS / "icon.svg"))
    app.setWindowIcon(icon)
    ctx = AppContext(settings, session)
    holder = {"win": MainWindow(ctx, icon)}
    ctx.main_window = holder["win"]

    class _Win:                       # always the current window (it is rebuilt when the language changes)
        def __getattr__(self, name):
            return getattr(holder["win"], name)
    win = _Win()

    def rebuild(_code: str):
        old = holder["win"]
        page = ctx.session.get("page", "home")
        geo = old.saveGeometry()
        new = MainWindow(ctx, icon)
        new.restoreGeometry(geo)
        holder["win"] = ctx.main_window = new
        new.navigate(page if page in new.nav_btns else "home")
        new.show()
        old.replaced = True
        old.close()
        old.deleteLater()
    ctx.languageChanged.connect(rebuild)

    def raise_window():
        conn = server.nextPendingConnection()
        if conn:
            conn.waitForReadyRead(500)
            msg = bytes(conn.readAll()).decode("utf-8", "replace").strip()
            conn.deleteLater()
            if msg.startswith("open "):
                QTimer.singleShot(0, lambda p=msg[5:]: win.open_path(p))
        win.showNormal()
        win.raise_()
        win.activateWindow()
    server.newConnection.connect(raise_window)

    # Unix signals -> clean shutdown (timer lets Python run its handlers)
    signal.signal(signal.SIGTERM, lambda *_c: app.closeAllWindows())
    signal.signal(signal.SIGINT, lambda *_c: app.closeAllWindows())
    tick = QTimer()
    tick.start(1000)
    tick.timeout.connect(lambda: None)

    ctx.gpu.start(5000)

    def startup_done():
        if os.environ.get("LAS_STARTUP_PROBE"):
            # measurement hook (tests/benchmarks): time to a fully populated, interactive window
            print(f"STARTUP_SECONDS {_time.monotonic() - T_START:.3f}", flush=True)
            if os.environ["LAS_STARTUP_PROBE"] == "exit":
                QTimer.singleShot(0, app.quit)
        if not settings.wizard_done:
            win.navigate("wizard")
            return
        win.navigate(session.get("page", "home") if session.get("page") in win.nav_btns else "home")
        if files:
            win.open_path(files[0])
        if settings.autoload_model and ctx.default_model() and os.environ.get("LAS_NO_AUTOLOAD") != "1":
            QTimer.singleShot(300, lambda: ctx.load_model() if ctx.server.state == "stopped" else None)

    ctx.refresh_models(then=lambda: ctx.refresh_workspaces(then=startup_done))
    win.navigate("home")
    win.show()
    if crashed:
        QTimer.singleShot(200, lambda: ctx.banner.emit(friendly("crash_recovered"), ""))
    elif corrupt:
        QTimer.singleShot(200, lambda: ctx.banner.emit(friendly("crash_recovered"), _("settings.json illisible : "
                                                       "réglages par défaut rétablis (copie conservée).")))
    QTimer.singleShot(400, ctx.check_disk)
    QTimer.singleShot(15000, ctx.updates.maybe_check)

    rc = app.exec()
    try:
        config.RUN_LOCK_FILE.unlink()
    except OSError:
        pass
    server.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
