#!/usr/bin/env python3
"""Headless natural-language → Lean translation (same services as the GUI). One JSON object per problem.

Example: translate_cli.py --ws lean-prover49 --text "Montrer que la somme de deux entiers pairs est paire." """
import argparse, json, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
os.environ.setdefault("LAS_CONFIG_DIR", f"/tmp/las-tr-{os.getpid()}")
from PySide6.QtCore import QCoreApplication, QTimer  # noqa: E402
from lean_ai_station import config  # noqa: E402
from lean_ai_station.services import Formalizer, LeanCompiler, LlamaServer  # noqa: E402
from lean_ai_station.workspaces import all_workspaces  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--ws", default="lean-prover49")
ap.add_argument("--text", action="append", required=True)
ap.add_argument("--tries", type=int, default=3)
ap.add_argument("--port", type=int, default=8789)
a = ap.parse_args()
app = QCoreApplication(sys.argv)
ws = next(w for w in all_workspaces() if w.key == a.ws and not w.problems)
srv = LlamaServer(); f = Formalizer(srv, LeanCompiler(), None)
queue = list(a.text); state = {}
def nxt():
    if not queue:
        srv.shutdown_blocking(); app.quit(); return
    state["text"] = queue.pop(0); state["t0"] = time.monotonic()
    f.start(state["text"], ws, a.tries, 300)
def done(ok, summary):
    print(json.dumps({"text": state["text"], "ok": ok, "summary": summary, "seconds": round(time.monotonic() - state["t0"], 1),
                      "statement": f.statement.split("theorem", 1)[-1] if f.statement else "",
                      "attempts": [{"status": x.status, "tokens": x.tokens, "tps": round(x.tps, 1), "gen_s": round(x.gen_seconds, 1),
                                    "summary": x.summary, "errors": x.errors_text[:200]} for x in f.attempts]}, ensure_ascii=False), flush=True)
    QTimer.singleShot(0, nxt)
f.finished.connect(done)
f.infraError.connect(lambda k, d: (print(json.dumps({"infra": k, "details": d[-300:]})), srv.shutdown_blocking(), app.exit(3)))
srv.stateChanged.connect(lambda st: st == "ready" and not state and nxt())
srv.failed.connect(lambda k, d: (print("FAILED", k, d[-300:]), app.exit(3)))
model = next(p for p in config.MODELS_DIR.glob("*Formalizer*Q4_K_M.gguf"))
srv.start(model, config.ServerSettings(port=a.port), 7700)
sys.exit(app.exec())
