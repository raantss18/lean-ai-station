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


def _help_shot(win, out):
    from lean_ai_station.ui.help import HelpDialog
    d = HelpDialog(win)
    d.show()
    QApplication.processEvents()
    d.grab().save(str(Path(out) / "06b_aide.png"))
    d.close()


def _import_tex(lean, path):
    # pick the first statement without opening the chooser dialog
    from lean_ai_station import texio
    items = texio.extract_statements(Path(path).read_text())
    lean.nl_edit.setPlainText(items[0].body)


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
    home = win.pages["home"]
    PROBLEM = "Montrer que la somme de deux entiers pairs est paire."
    TEX = Path(out) / "demo.tex"
    TEX.write_text(r"\documentclass{article}\begin{document}\begin{lemma}[Cauchy] Pour tous réels $a,b$, $2ab \le a^2+b^2$."
                   r"\end{lemma}\begin{theorem} Soit $n$ un entier ; $n(n+1)$ est pair.\end{theorem}\end{document}")
    steps = [
        ("load", lambda: ctx.refresh_models(then=lambda: ctx.refresh_workspaces()), lambda: bool(ctx.workspaces), 30),
        ("wizard0", seq(lambda: win.navigate("wizard"), shot("01_assistant_bienvenue")), T, 1),
        ("wizard1", lambda: wiz._next(), lambda: wiz.next.isEnabled(), 30),
        ("wizard1s", shot("02_assistant_detection"), T, 1),
        ("wizard2", seq(lambda: wiz._next(), shot("03_assistant_choix")), T, 1),
        ("wizard3", seq(lambda: setattr(ctx.settings, "workspace", a.ws), lambda: wiz._next()), T, 2),
        ("wizard3s", shot("04_assistant_autotest"), T, 1),
        ("wizard4", lambda: None, lambda: wiz.pages.currentIndex() == 4, 400),
        ("wizard4s", shot("05_assistant_pret"), T, 1),
        ("home", seq(lambda: wiz._finish(), lambda: win.navigate("home"),
                     lambda: home.nl.setPlainText(PROBLEM), shot("06_accueil")), T, 1),
        ("help", seq(lambda: _help_shot(win, out)), T, 1),
        ("translate", lambda: home._go(), lambda: lean._pending_translate or ctx.formalizer.running, 60),
        ("translate_live", shot("07_traduction_en_cours"), lambda: not ctx.formalizer.running and not lean._pending_translate, 900),
        ("translate_done", seq(lambda: win.navigate("lean"), shot("08_enonce_a_relire")), T, 1),
        ("prove", lambda: lean.ok_prove_btn.click(), lambda: lean._pending_prove or ctx.prover.running, 60),
        ("prove_live", shot("09_preuve_en_cours"), lambda: not ctx.prover.running and not lean._pending_prove, 1200),
        ("prove_done", shot("10_preuve_trouvee"), T, 1),
        ("tex_export", lambda: (TEXT_OUT.write_text(lean.latex_source(), encoding="utf-8")), T, 1),
        ("tex_import", seq(lambda: lean.nl_edit.clear(), lambda: _import_tex(lean, TEX)), T, 1),
        ("tex_import_s", shot("11_import_tex"), T, 1),
        ("verify_err", seq(lambda: lean.editor.setPlainText(
            "import Mathlib\n\ntheorem faux (a b : ℕ) : a + b = a * b := by\n  ring\n"), lambda: lean.verify()),
         lambda: not ctx.verifier.busy, 300),
        ("verify_err_s", shot("12_verification_erreur"), T, 1),
        ("chat_empty", seq(lambda: win.navigate("chat"), lambda: chat.new_chat(), shot("13_chat_vide")), T, 1),
        ("models", seq(lambda: win.navigate("models"), shot("14_modeles")), T, 1),
        ("server", seq(lambda: win.navigate("server"), shot("15_serveur")), T, 1),
        ("system", lambda: win.navigate("system"), T, 3),
        ("system_s", shot("16_systeme"), T, 1),
        ("crash", seq(lambda: os.kill(ctx.server.proc.processId(), 9) if ctx.server.proc else None),
         lambda: ctx.server.state == "stopped", 30),
        ("crash_s", seq(lambda: win.navigate("lean"), shot("17_erreur_serveur_arrete")), T, 1),
        ("recover", lambda: win.banner._act("restart_server"), lambda: ctx.server.state == "ready", 120),
        ("quit", lambda: win.close(), T, 1),
    ]
    TEXT_OUT = Path(out) / "export_exemple.tex"
    runner = Runner(app, steps)
    runner.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
