"""Slow end-to-end tests with the real llama-server, model and Lean (marked `slow`).

Environment:
  LAS_TEST_MODEL   GGUF path (default: ~/models/Goedel-Prover-V2-8B.Q4_K_M.gguf)
  LAS_TEST_WS      workspace key (default: lean-prover49, else first ready one)
  LAS_TEST_PROVE   "0" to skip tests that need a real theorem prover model
"""
import os
import signal
import time
from pathlib import Path

import pytest

from lean_ai_station import config, leancheck
from lean_ai_station.services import ChatStream, LeanCompiler, LlamaServer, Prover
from lean_ai_station.workspaces import all_workspaces

pytestmark = pytest.mark.slow
MODEL = Path(os.environ.get("LAS_TEST_MODEL", str(config.MODELS_DIR / config.DEFAULT_MODEL_NAME)))
NEED_PROVER = os.environ.get("LAS_TEST_PROVE", "1") != "0"


def _ws():
    want = os.environ.get("LAS_TEST_WS")
    ws = all_workspaces()
    if want:   # explicit request: never fall back silently to another workspace
        w = next((w for w in ws if w.key == want), None)
        assert w is not None and not w.problems, f"workspace {want} not ready: {w and w.problems}"
        return w
    ready = [w for w in ws if not w.problems]
    if not ready:
        pytest.skip("no ready Lean workspace")
    return next((w for w in ready if w.key == "lean-prover49"), ready[0])


@pytest.fixture(scope="module")
def ws():
    return _ws()


@pytest.fixture
def server(qtbot):
    if not MODEL.exists():
        pytest.skip(f"model missing: {MODEL}")
    s = LlamaServer()
    st = config.ServerSettings()
    s.start(MODEL, st, 7300)
    qtbot.waitUntil(lambda: s.state == LlamaServer.READY, timeout=240_000)
    yield s
    s.shutdown_blocking()


def _compile(qtbot, ws, code, name, timeout=300):
    c = LeanCompiler()
    with qtbot.waitSignal(c.finished, timeout=(timeout + 30) * 1000) as blk:
        c.compile(code, ws, timeout, name)
    return blk.args[0]


# ---------------------------------------------------------------- Lean only
def test_correct_proof_accepted(qtbot, ws):
    code = leancheck.with_axiom_probe(leancheck.GOEDEL_HEADER +
                                      "theorem t (a b : ℕ) : a + b = b + a := by\n  omega\n", "t")
    r = _compile(qtbot, ws, code, "t")
    assert r.verdict.ok, r.stdout


def test_wrong_proof_rejected(qtbot, ws):
    code = leancheck.with_axiom_probe(leancheck.GOEDEL_HEADER + "theorem t (a b : ℕ) : a + b = b * a := by\n  omega\n", "t")
    r = _compile(qtbot, ws, code, "t")
    assert not r.verdict.ok and r.verdict.errors


def test_sorry_proof_rejected(qtbot, ws):
    code = leancheck.with_axiom_probe(leancheck.GOEDEL_HEADER + "theorem t (a b : ℕ) : a + b = b + a := by\n  sorry\n", "t")
    r = _compile(qtbot, ws, code, "t")
    assert not r.verdict.ok and r.verdict.has_sorry


def test_native_decide_axiom_rejected(qtbot, ws):
    code = leancheck.with_axiom_probe(leancheck.GOEDEL_HEADER + "theorem t : 2 + 2 = 4 := by\n  native_decide\n", "t")
    r = _compile(qtbot, ws, code, "t")
    assert not r.verdict.ok


def test_compile_timeout_fires(qtbot, ws):
    code = "def loop : Nat → Nat\n  | 0 => 0\n  | n + 1 => loop n + 1\n#eval (List.range 400000000).foldl (· + ·) 0\n"
    t0 = time.monotonic()
    r = _compile(qtbot, ws, code, None, timeout=5)
    assert r.timed_out and time.monotonic() - t0 < 20


def test_kill_lean_midcompile(qtbot, ws):
    c = LeanCompiler()
    c.compile("import Mathlib\n#eval (List.range 400000000).foldl (· + ·) 0\n", ws, 300, None)
    qtbot.waitUntil(lambda: c.proc is not None and c.proc.processId() > 0, timeout=5000)
    pid = c.proc.processId()
    time.sleep(1.0)
    with qtbot.waitSignal(c.finished, timeout=15000) as blk:
        os.kill(pid, signal.SIGKILL)
    r = blk.args[0]
    assert not r.verdict.ok
    assert not Path(f"/proc/{pid}").exists()


# ---------------------------------------------------------------- server
def test_server_offloads_to_gpu(server):
    n, total = server.offload.split("/")
    assert int(n) == int(total) > 0


def test_stream_and_stats(qtbot, server):
    s = ChatStream(server.url)
    with qtbot.waitSignal(s.done, timeout=120_000) as blk:
        s.start([{"role": "user", "content": "Say hello."}], 0.0, 1.0, 64)
    d = blk.args[0]
    assert d["tokens"] > 0 and d["tps"] > 5 and d["ttft"] is not None


def test_kill_server_midgeneration_reports_error(qtbot, server):
    s = ChatStream(server.url)
    errs = []
    s.error.connect(lambda k, d: errs.append(k))
    fails = []
    server.failed.connect(lambda k, d: fails.append(k))
    s.start([{"role": "user", "content": "Count from 1 to 3000, one number per line."}], 0.7, 0.95, 4000)
    qtbot.waitUntil(lambda: len(s.text) > 50, timeout=60_000)
    os.kill(server.proc.processId(), signal.SIGKILL)
    qtbot.waitUntil(lambda: bool(errs), timeout=15_000)
    assert errs[0] in ("unreachable", "http")
    qtbot.waitUntil(lambda: bool(fails), timeout=15_000)
    assert fails[0] == "crashed" and server.state == LlamaServer.STOPPED


def test_missing_and_corrupt_model(qtbot, tmp_path):
    s = LlamaServer()
    fails = []
    s.failed.connect(lambda k, d: fails.append(k))
    s.start(tmp_path / "absent.gguf", config.ServerSettings(), 7000)
    assert fails == ["bad_model"]
    bad = tmp_path / "corrupt.gguf"
    bad.write_bytes(b"GGUF" + os.urandom(4096))
    s.start(bad, config.ServerSettings(), 7000)
    qtbot.waitUntil(lambda: len(fails) == 2, timeout=60_000)
    assert fails[1] in ("bad_model", "crashed")
    assert s.state == LlamaServer.STOPPED


# ---------------------------------------------------------------- prove loop
@pytest.mark.skipif(not NEED_PROVER, reason="LAS_TEST_PROVE=0")
@pytest.mark.parametrize("stmt", [
    "theorem easy (a b : ℕ) (h : a = b) : b = a := by sorry",
    "theorem mathd_algebra_478 (b h v : ℝ) (h₀ : 0 < b ∧ 0 < h ∧ 0 < v) (h₁ : v = 1 / 3 * (b * h))\n"
    "    (h₂ : b = 30) (h₃ : h = 13 / 2) : v = 65 := by sorry",
])
def test_full_prove_cycle(qtbot, server, ws, stmt):
    p = Prover(server, LeanCompiler())
    with qtbot.waitSignal(p.finished, timeout=1_800_000) as blk:
        p.start(stmt, ws, 8, config.SamplingSettings(), 300, server.plan.ctx)
    ok, summary = blk.args
    assert ok, (summary, [(a.status, a.summary) for a in p.attempts])
    assert "sorry" not in leancheck.remove_comments(p.final_code)
