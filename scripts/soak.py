#!/usr/bin/env python3
"""Soak test: repeated prove cycles through the real GUI (offscreen) for N minutes.

Samples every 15 s: GUI RSS, llama-server RSS, VRAM used, and the worst event-loop lag
(a 50 ms timer measures how late it fires: lag > 200 ms would be a visible freeze).
Usage: soak.py --minutes 30 --ws lean-prover49 --out bench/soak.json"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("LAS_CONFIG_DIR", f"/tmp/las-soak-{os.getpid()}")
os.environ["LAS_NO_AUTOLOAD"] = "1"

import psutil  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lean_ai_station import config  # noqa: E402
from lean_ai_station.examples import EXAMPLES  # noqa: E402
from lean_ai_station.ui import theme  # noqa: E402
from lean_ai_station.ui.context import AppContext  # noqa: E402
from lean_ai_station.ui.main_window import MainWindow  # noqa: E402


def vram_used() -> int:
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True).stdout
    return int(out.split()[0]) if out.strip() else -1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=30)
    ap.add_argument("--ws", default="lean-prover49")
    ap.add_argument("--out", default="bench/soak.json")
    a = ap.parse_args()

    app = QApplication(sys.argv)
    theme.apply(app)
    s = config.Settings()
    s.wizard_done, s.workspace, s.prove_attempts = True, a.ws, 4
    ctx = AppContext(s, {})
    win = MainWindow(ctx)
    win.resize(1360, 860)
    win.show()
    me = psutil.Process()
    deadline = time.monotonic() + a.minutes * 60
    samples, cycles = [], []
    lag = {"max": 0.0, "last": time.monotonic()}
    examples = [e for e in EXAMPLES if e.key in ("logique", "pairs", "identite", "amgm", "cercle", "triangulaire")]
    state = {"i": 0, "t0": 0.0}

    def tick():
        now = time.monotonic()
        lag["max"] = max(lag["max"], now - lag["last"] - 0.05)
        lag["last"] = now
    t = QTimer()
    t.timeout.connect(tick)
    t.start(50)

    def sample():
        srv_rss = 0
        if ctx.server.proc and ctx.server.proc.processId() > 0:
            try:
                srv_rss = psutil.Process(ctx.server.proc.processId()).memory_info().rss
            except psutil.Error:
                pass
        import ctypes
        import gc
        rss_raw = me.memory_info().rss
        if os.environ.get("SOAK_TRIM"):
            ctypes.CDLL("libc.so.6").malloc_trim(0)     # returns freed heap pages: separates leaks from fragmentation
        samples.append({"t": round(time.monotonic() - start, 1), "gui_rss_mb": round(me.memory_info().rss / 2**20, 1),
                        "gui_rss_untrimmed_mb": round(rss_raw / 2**20, 1), "py_objects": len(gc.get_objects()),
                        "qobjects": len(win.findChildren(__import__("PySide6.QtCore", fromlist=["QObject"]).QObject)) +
                        len(ctx.findChildren(__import__("PySide6.QtCore", fromlist=["QObject"]).QObject)),
                        "server_rss_mb": round(srv_rss / 2**20, 1), "vram_mb": vram_used(),
                        "max_lag_ms": round(lag["max"] * 1000, 1), "threads": me.num_threads()})
        lag["max"] = 0.0
        print(json.dumps(samples[-1]), flush=True)
    st = QTimer()
    st.timeout.connect(sample)

    def next_cycle():
        if time.monotonic() > deadline:
            finish()
            return
        ex = examples[state["i"] % len(examples)]
        state["i"] += 1
        state["t0"] = time.monotonic()
        state["name"] = ex.key
        win.pages["home"]._prove(ex.statement)

    def done(ok, summary):
        cycles.append({"name": state["name"], "ok": ok, "seconds": round(time.monotonic() - state["t0"], 1),
                       "attempts": len(ctx.prover.attempts)})
        print(json.dumps(cycles[-1]), flush=True)
        QTimer.singleShot(500, next_cycle)

    def finish():
        st.stop()
        sample()
        gui = [x["gui_rss_mb"] for x in samples]
        vram = [x["vram_mb"] for x in samples[1:]]
        res = {"minutes": a.minutes, "cycles": len(cycles), "ok": sum(c["ok"] for c in cycles),
               "gui_rss_first_mb": gui[1] if len(gui) > 1 else gui[0], "gui_rss_last_mb": gui[-1],
               "gui_rss_max_mb": max(gui), "vram_min_mb": min(vram) if vram else None,
               "vram_max_mb": max(vram) if vram else None,
               "worst_lag_ms": max(x["max_lag_ms"] for x in samples), "samples": samples, "cycle_log": cycles}
        Path(a.out).write_text(json.dumps(res, indent=1))
        print(json.dumps({k: v for k, v in res.items() if k not in ("samples", "cycle_log")}), flush=True)
        win.close()
        app.quit()

    ctx.prover.finished.connect(done)
    ctx.prover.infraError.connect(lambda k, d: (print(json.dumps({"infra": k, "d": d[-300:]}), flush=True),
                                                 QTimer.singleShot(2000, next_cycle)))
    start = time.monotonic()
    ctx.refresh_models(then=lambda: ctx.refresh_workspaces(then=lambda: (sample(), st.start(15000), next_cycle())))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
