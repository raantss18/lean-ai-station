#!/usr/bin/env python3
"""Drive the real GUI (offscreen) through every screen and state and save screenshots.

Usage: screenshots.py OUTDIR [--ws lean-prover49]   (needs the model + a ready workspace)"""
import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("LAS_CONFIG_DIR", f"/tmp/las-shots-{os.getpid()}")
os.environ["LAS_NO_AUTOLOAD"] = "1"

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lean_ai_station import config  # noqa: E402
from lean_ai_station.errors import friendly  # noqa: E402
from lean_ai_station.examples import EXAMPLES, VERIFY_SAMPLE  # noqa: E402
from lean_ai_station.ui import theme  # noqa: E402
from lean_ai_station.ui.context import AppContext  # noqa: E402
from lean_ai_station.ui.main_window import MainWindow  # noqa: E402


class Runner:
    """Sequential steps; each step = (name, action, wait_condition, timeout_s)."""

    def __init__(self, app, steps):
        self.app, self.steps, self.i = app, steps, 0
        self.t0 = 0.0
        self.timer = QTimer()
        self.timer.timeout.connect(self.poll)

    def start(self):
        self.run_current()
        self.timer.start(200)

    def run_current(self):
        if self.i >= len(self.steps):
            self.timer.stop()
            self.app.quit()
            return
        name, action, _cond, _to = self.steps[self.i]
        print(f"[step] {name}", flush=True)
        action()
        self.t0 = time.monotonic()

    def poll(self):
        if self.i >= len(self.steps):
            return
        name, _a, cond, to = self.steps[self.i]
        if cond() or time.monotonic() - self.t0 > to:
            if time.monotonic() - self.t0 > to and not cond():
                print(f"[timeout] {name}", flush=True)
            self.i += 1
            self.run_current()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--ws", default="lean-prover49")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)
    theme.apply(app)
    s = config.Settings()
    ctx = AppContext(s, {})
    win = MainWindow(ctx)
    win.resize(1360, 860)
    win.show()
    ctx.gpu.start(2000)
    lean, chat, wiz = win.pages["lean"], win.pages["chat"], win.pages["wizard"]

    def shot(name):
        def f():
            app.processEvents()
            win.grab().save(str(out / f"{name}.png"))
        return f

    def seq(*fs):
        def f():
            for x in fs:
                x()
        return f
    T = lambda: True  # noqa: E731
    steps = [
        ("load", lambda: ctx.refresh_models(then=lambda: ctx.refresh_workspaces()), lambda: bool(ctx.workspaces), 30),
        ("wizard0", seq(lambda: win.navigate("wizard"), shot("01_wizard_bienvenue")), T, 1),
        ("wizard1", lambda: wiz._next(), lambda: wiz.next.isEnabled(), 30),
        ("wizard1s", shot("02_wizard_detection"), T, 1),
        ("wizard2", seq(lambda: wiz._next(), shot("03_wizard_choix")), T, 1),
        ("wizard3", seq(lambda: setattr(ctx.settings, "workspace", a.ws), lambda: wiz._next()), T, 2),
        ("wizard3s", shot("04_wizard_autotest_en_cours"), T, 1),
        ("wizard4", lambda: None, lambda: wiz.pages.currentIndex() == 4, 400),
        ("wizard4s", shot("05_wizard_pret"), T, 1),
        ("home", seq(lambda: wiz._finish(), lambda: win.navigate("home"), shot("06_accueil")), T, 1),
        ("lean_empty", seq(lambda: win.navigate("lean"), lambda: lean.editor.setPlainText(""),
                           shot("07_lean_vide")), T, 1),
        ("verify_ok", seq(lambda: lean.editor.setPlainText(VERIFY_SAMPLE), lambda: lean.verify()), T, 1),
        ("verify_busy", shot("08_lean_verification_en_cours"), lambda: not ctx.verifier.busy, 300),
        ("verify_ok_s", shot("09_lean_verification_ok"), T, 1),
        ("verify_err", seq(lambda: lean.editor.setPlainText(
            "import Mathlib\n\ntheorem faux (a b : ℕ) : a + b = a * b := by\n  ring\n"), lambda: lean.verify()),
         lambda: not ctx.verifier.busy, 300),
        ("verify_err_s", shot("10_lean_verification_erreur"), T, 1),
        ("prove", seq(lambda: lean.prove_statement(EXAMPLES[4].statement)), lambda: len(lean.reasoning.toPlainText()) > 400, 300),
        ("prove_live", shot("11_lean_preuve_en_cours"), lambda: not ctx.prover.running and not lean._pending_prove, 900),
        ("prove_done", shot("12_lean_preuve_trouvee"), T, 1),
        ("chat_empty", seq(lambda: win.navigate("chat"), lambda: chat.new_chat(), shot("13_chat_vide")), T, 1),
        ("chat_send", seq(lambda: chat.input.setPlainText("Qu'est-ce qu'une preuve par récurrence ? Réponds brièvement."),
                          lambda: chat.send()), lambda: chat.stream is None and not getattr(chat, "_waiting", False), 300),
        ("chat_done", shot("14_chat_reponse"), T, 1),
        ("models", seq(lambda: win.navigate("models"), shot("15_modeles")), T, 1),
        ("server", seq(lambda: win.navigate("server"), shot("16_serveur_en_marche")), T, 1),
        ("system", lambda: win.navigate("system"), T, 3),
        ("system_s", shot("17_systeme"), T, 1),
        ("crash", seq(lambda: os.kill(ctx.server.proc.processId(), 9) if ctx.server.proc else None),
         lambda: ctx.server.state == "stopped", 30),
        ("crash_s", seq(lambda: win.navigate("lean"), shot("18_erreur_serveur_arrete")), T, 1),
        ("recover", lambda: win.banner._act("restart_server"), lambda: ctx.server.state == "ready", 120),
        ("recover_s", seq(lambda: win.navigate("server"), shot("19_serveur_redemarre")), T, 1),
        ("quit", lambda: win.close(), T, 1),
    ]
    runner = Runner(app, steps)
    runner.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
