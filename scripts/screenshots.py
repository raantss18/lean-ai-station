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
os.environ["LAS_NO_UPDATE_CHECK"] = "1"

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


def _help_shot(win, out, name):
    from lean_ai_station.ui.help import HelpDialog
    d = HelpDialog(win)
    d.show()
    QApplication.processEvents()
    d.grab().save(str(Path(out) / f"{name}.png"))
    d.close()


def _import_tex(lean, path):
    # pick the first statement without opening the chooser dialog
    from lean_ai_station import texio
    items = texio.extract_statements(Path(path).read_text())
    lean.new_dossier()
    lean.input.setPlainText(items[0].body)


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
    from lean_ai_station import dossiers as ds
    ctx.pipeline.store = ds.DossierStore(Path(os.environ["LAS_CONFIG_DIR"]) / "dossiers")
    ctx.pipeline.library = ds.Library(Path(os.environ["LAS_CONFIG_DIR"]) / "library.json")
    win = MainWindow(ctx)
    ctx.main_window = win

    def rebuild(_code):
        old = ctx.main_window
        new = MainWindow(ctx)
        new.resize(1360, 860)
        new.show()
        ctx.main_window = new
        old.replaced = True
        old.close()
    ctx.languageChanged.connect(rebuild)
    win.resize(1360, 860)
    win.show()
    ctx.gpu.start(2000)
    lean, wiz = win.pages["lean"], win.pages["wizard"]

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
    lib = win.pages["library"]
    p = ctx.pipeline
    PROBLEM = "Montrer que la somme de deux entiers pairs est paire."
    TEX = Path(out) / "demo.tex"
    TEX.write_text(r"\documentclass{article}\begin{document}\begin{lemma}[Cauchy] Pour tous réels $a,b$, $2ab \le a^2+b^2$."
                   r"\end{lemma}\end{document}")
    ctx.settings.profile = ("Je suis enseignant en lycée ; mes élèves de terminale débutent : explications simples, "
                            "sans jargon. ℕ commence à 0.")

    def lang(code):
        def f():
            ctx.request_language(code)
        return f

    def cur():
        return ctx.main_window
    steps = [
        ("load", lambda: ctx.refresh_models(then=lambda: ctx.refresh_workspaces()), lambda: bool(ctx.workspaces), 30),
        ("wizard0", seq(lambda: win.navigate("wizard"), shot("01_assistant_bienvenue")), T, 1),
        ("wizard1", lambda: wiz._next(), lambda: wiz.next.isEnabled(), 30),
        ("wizard1s", shot("02_assistant_detection"), T, 1),
        ("wizard2", seq(lambda: wiz._next(), shot("03_assistant_choix")), T, 1),
        ("wizard3", seq(lambda: setattr(ctx.settings, "workspace", a.ws), lambda: wiz._next()), T, 2),
        ("wizard4", lambda: None, lambda: wiz.pages.currentIndex() == 4, 400),
        ("wizard4s", shot("04_assistant_pret"), T, 1),
        ("home", seq(lambda: wiz._finish(), lambda: win.navigate("home"),
                     lambda: home.nl.setPlainText(PROBLEM), shot("05_accueil")), T, 1),
        ("help", seq(lambda: _help_shot(win, out, "06_aide")), T, 1),
        ("chain", lambda: home._go(), lambda: p.stage == "proof", 600),
        ("chain_s", shot("07_traduction_puis_preuve"), lambda: p.stage == "explanation", 1200),
        ("chain_e", lambda: None, lambda: not p.busy, 600),
        ("done", seq(lambda: lean.tabs.setCurrentIndex(2), shot("08_prouve_et_explique")), T, 1),
        ("done_proof", seq(lambda: lean.tabs.setCurrentIndex(1), shot("09_onglet_preuve")), T, 1),
        ("follow", seq(lambda: lean.input.setPlainText("Donne une preuve plus courte"), lambda: lean.send()),
         lambda: p.busy, 30),
        ("follow_e", lambda: None, lambda: not p.busy, 1800),
        ("follow_s", seq(lambda: lean.tabs.setCurrentIndex(0), shot("10_demande_de_suivi")), T, 1),
        ("library", seq(lambda: win.navigate("library"), lambda: lib.table.selectRow(0), shot("11_bibliotheque")), T, 1),
        ("tex", seq(lambda: win.navigate("lean"), lambda: _import_tex(lean, TEX), shot("12_import_tex")), T, 1),
        ("verify_err", seq(lambda: lean.editor.setPlainText(
            "import Mathlib\n\ntheorem faux (a b : ℕ) : a + b = a * b := by\n  ring\n"), lambda: lean.verify()),
         lambda: not ctx.verifier.busy, 300),
        ("verify_err_s", shot("13_verification_erreur"), T, 1),
        ("models", seq(lambda: win.navigate("models"), shot("14_modeles")), T, 1),
        ("server", seq(lambda: win.navigate("server"), shot("15_serveur")), T, 1),
        ("system", lambda: win.navigate("system"), T, 3),
        ("system_s", shot("16_systeme_profil"), T, 1),
        ("crash", seq(lambda: os.kill(ctx.server.proc.processId(), 9) if ctx.server.proc else None),
         lambda: ctx.server.state == "stopped", 30),
        ("crash_s", seq(lambda: win.navigate("lean"), shot("17_erreur_serveur_arrete")), T, 1),
        ("recover", lambda: win.banner._act("restart_server"), lambda: ctx.server.state == "ready", 120),
        ("en", lang("en"), lambda: ctx.settings.language == "en", 10),
        ("en_home", seq(lambda: cur().navigate("home"), lambda: cur().pages["home"].nl.setPlainText(
            "Prove that the sum of two even integers is even."), lambda: cur().grab().save(str(Path(out) / "18_en_home.png"))), T, 2),
        ("en_lean", seq(lambda: cur().navigate("lean"), lambda: cur().grab().save(str(Path(out) / "19_en_dossier.png"))), T, 2),
        ("en_help", lambda: _help_shot(cur(), out, "20_en_help"), T, 1),
        ("fr", lang("fr"), lambda: ctx.settings.language == "fr", 10),
        ("quit", lambda: cur().close(), T, 1),
    ]
    runner = Runner(app, steps)
    runner.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
