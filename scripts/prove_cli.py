#!/usr/bin/env python3
"""Headless prove runner (same services as the GUI) for benchmarks and acceptance tests.

Examples:
  prove_cli.py --ws lean-prover49 --example cercle --example pairs
  prove_cli.py --minif2f mathd_algebra_478 --attempts 4 --ctx 24576 --kv q8_0
Prints one JSON object per theorem (attempt stats, verdict) to stdout."""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
os.environ.setdefault("LAS_CONFIG_DIR", str(Path("/tmp") / f"las-cli-{os.getpid()}"))

from PySide6.QtCore import QCoreApplication, QTimer  # noqa: E402

from lean_ai_station import config  # noqa: E402
from lean_ai_station.examples import EXAMPLES  # noqa: E402
from lean_ai_station.services import LeanCompiler, LlamaServer, Prover  # noqa: E402
from lean_ai_station.workspaces import all_workspaces  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data" / "minif2f.jsonl"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", default="lean-prover49")
    ap.add_argument("--model", default=str(config.MODELS_DIR / config.DEFAULT_MODEL_NAME))
    ap.add_argument("--example", action="append", default=[])
    ap.add_argument("--minif2f", action="append", default=[])
    ap.add_argument("--statement", action="append", default=[])
    ap.add_argument("--attempts", type=int, default=8)
    ap.add_argument("--ctx", type=int, default=24576)
    ap.add_argument("--kv", default="q8_0")
    ap.add_argument("--ub", type=int, default=512)
    ap.add_argument("--max-tokens", type=int, default=16384)
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--timeout", type=int, default=300)
    a = ap.parse_args()

    app = QCoreApplication(sys.argv)
    ws = next((w for w in all_workspaces() if w.key == a.ws and not w.problems), None)
    if ws is None:
        print(json.dumps({"error": f"workspace {a.ws} not ready"}))
        return 2
    items = [(e.key, e.statement) for e in EXAMPLES if e.key in a.example]
    if a.minif2f:
        rows = {json.loads(l)["name"]: json.loads(l) for l in DATA.open()}
        for n in a.minif2f:
            fs = rows[n]["formal_statement"]
            items.append((n, fs.split(":= by")[0] + ":= by sorry"))
    items += [(f"stmt{i}", s) for i, s in enumerate(a.statement)]

    s = config.ServerSettings(port=a.port, ctx_size=a.ctx, kv_type=a.kv, ubatch_size=a.ub)
    srv = LlamaServer()
    comp = LeanCompiler()
    prover = Prover(srv, comp)
    sampling = config.SamplingSettings(a.temperature, 0.95, a.max_tokens)
    queue = list(items)
    t_load = time.monotonic()
    state = {"t0": 0.0, "name": ""}

    def next_item():
        if not queue:
            srv.shutdown_blocking()
            app.quit()
            return
        name, stmt = queue.pop(0)
        state.update(t0=time.monotonic(), name=name)
        prover.start(stmt, ws, a.attempts, sampling, a.timeout, srv.plan.ctx)

    def finished(ok, summary):
        rec = {"name": state["name"], "ok": ok, "summary": summary, "seconds": round(time.monotonic() - state["t0"], 1),
               "ws": ws.key, "ctx": srv.plan.ctx, "kv": a.kv, "final": prover.final_code if ok else "",
               "attempts": [{"kind": x.kind, "status": x.status, "summary": x.summary, "tokens": x.tokens,
                             "tps": round(x.tps, 1), "ttft": round(x.ttft or 0, 2), "gen_s": round(x.gen_seconds, 1),
                             "compile_s": round(x.compile_seconds, 1), "finish": x.finish_reason}
                            for x in prover.attempts]}
        print(json.dumps(rec, ensure_ascii=False), flush=True)
        QTimer.singleShot(0, next_item)

    def infra(kind, det):
        print(json.dumps({"name": state["name"], "infra_error": kind, "details": det[-500:]}), flush=True)
        srv.shutdown_blocking()
        app.exit(3)

    def ready(st):
        if st == LlamaServer.READY and not state["t0"]:
            print(json.dumps({"server_ready_s": round(time.monotonic() - t_load, 2), "offload": srv.offload,
                              "ctx": srv.plan.ctx}), flush=True)
            next_item()

    prover.finished.connect(finished)
    prover.infraError.connect(infra)
    srv.stateChanged.connect(ready)
    srv.failed.connect(lambda k, d: infra("server_" + k, d))
    srv.start(Path(a.model), s, 7700)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
