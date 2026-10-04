"""Prove loop with a scripted fake OpenAI-compatible server (no GPU) and the real Lean compiler."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from lean_ai_station import config
from lean_ai_station.services import LeanCompiler, LlamaServer, Prover
from lean_ai_station.workspaces import all_workspaces

WRONG = "<think>easy</think>\nPlan.\n```lean4\ntheorem t (a b : ℕ) (h : a = b) : b = a := by\n  exact h\n```"
RIGHT = "<think>fix</think>\nCorrected.\n```lean4\ntheorem t (a b : ℕ) (h : a = b) : b = a := by\n  exact h.symm\n```"


class Fake(BaseHTTPRequestHandler):
    script: list = []
    requests: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Fake.requests.append(body)
        kind, text = Fake.script.pop(0)
        if kind == "ctx_error":
            data = json.dumps({"error": {"code": 400, "message": "the request exceeds the available context size"}})
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data.encode())
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for i in range(0, len(text), 7):
            chunk = {"choices": [{"delta": {"content": text[i:i + 7]}, "finish_reason": None}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        end = {"choices": [{"delta": {}, "finish_reason": "stop"}],
               "timings": {"predicted_n": len(text) // 4, "predicted_per_second": 40.0, "prompt_n": 100}}
        self.wfile.write(f"data: {json.dumps(end)}\n\ndata: [DONE]\n\n".encode())


@pytest.fixture
def fake_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    srv = LlamaServer()
    srv.port = httpd.server_address[1]
    srv.state = LlamaServer.READY
    yield srv
    httpd.shutdown()


@pytest.fixture(scope="module")
def ws():
    ready = [w for w in all_workspaces() if not w.problems and not w.readonly]
    if not ready:
        pytest.skip("no built Lean workspace")
    return ready[-1]


def test_wrong_then_corrected(qtbot, fake_server, ws):
    Fake.requests, Fake.script = [], [("ctx_error", ""), ("ok", WRONG), ("ok", RIGHT)]
    # first request has a correction history so the context-overflow retry path is exercised
    p = Prover(fake_server, LeanCompiler())
    with qtbot.waitSignal(p.finished, timeout=300_000) as blk:
        p.start("theorem t (a b : ℕ) (h : a = b) : b = a := by sorry", ws, 4, config.SamplingSettings(), 240, 16384)
        p.messages = p.messages + [{"role": "assistant", "content": "old"}, {"role": "user", "content": "old feedback"}]
    ok, summary = blk.args
    assert ok, [(a.status, a.summary, a.errors_text[:200]) for a in p.attempts]
    # the context error did not consume an attempt: attempt 1 = wrong proof, attempt 2 = fix
    assert [a.status for a in p.attempts] == ["refusé", "accepté"]
    assert "exact h.symm" in p.final_code
    # the correction request carries Goedel's feedback format with <error> markers
    last_user = Fake.requests[-1]["messages"][-1]["content"]
    assert last_user.startswith("The proof (Round 0) is not correct.") and "<error>" in last_user
    assert Fake.requests[-1]["messages"][-2]["role"] == "assistant"


def test_statement_cannot_be_weakened(qtbot, fake_server, ws):
    cheat = "```lean4\ntheorem t (a b : ℕ) (h : a = b) : True := by\n  trivial\n```"
    Fake.requests, Fake.script = [], [("ok", cheat)]
    p = Prover(fake_server, LeanCompiler())
    with qtbot.waitSignal(p.finished, timeout=300_000) as blk:
        p.start("theorem t (a b : ℕ) (h : a = b) : b = a := by sorry", ws, 1, config.SamplingSettings(), 240, 16384)
    ok, _ = blk.args
    assert not ok and p.attempts[0].status == "refusé"


def test_sorry_answer_rejected(qtbot, fake_server, ws):
    Fake.requests, Fake.script = [], [("ok", "```lean4\ntheorem t (a b : ℕ) (h : a = b) : b = a := by\n  sorry\n```")]
    p = Prover(fake_server, LeanCompiler())
    with qtbot.waitSignal(p.finished, timeout=300_000) as blk:
        p.start("theorem t (a b : ℕ) (h : a = b) : b = a := by sorry", ws, 1, config.SamplingSettings(), 240, 16384)
    assert not blk.args[0] and "sorry" in p.attempts[0].summary


GOOD_STMT = ("<think>translate</think>\nHere:\n```lean4\nimport Mathlib\n\ntheorem mon_probleme (a b : ℕ) (ha : Even a) "
             "(hb : Even b) : Even (a + b) := by sorry\n```")
BAD_STMT = "```lean4\ntheorem mon_probleme (a b : ℕ) (ha : Even a : Even (a + b) := by sorry\n```"
LOOP_TEXT = "<think>" + ("-- We will use the fact that the determinant is non-negative.\n-- However, A is invertible.\n" * 90)


def test_formalizer_retries_until_lean_accepts(qtbot, fake_server, ws):
    from lean_ai_station.services import Formalizer
    Fake.requests, Fake.script = [], [("ok", LOOP_TEXT), ("ok", BAD_STMT), ("ok", GOOD_STMT)]
    f = Formalizer(fake_server, LeanCompiler())
    with qtbot.waitSignal(f.finished, timeout=300_000) as blk:
        f.start("Montrer que la somme de deux entiers pairs est paire.", ws, 4, 240)
    ok, summary = blk.args
    assert ok, [(a.status, a.summary, a.errors_text[:120]) for a in f.attempts]
    assert [a.status for a in f.attempts] == ["refusé", "refusé", "accepté"]
    assert "tournait en rond" in f.attempts[0].summary and "Lean refuse" in f.attempts[1].summary
    assert f.statement.rstrip().endswith("Even (a + b) := by sorry")
    assert f.statement.startswith("import Mathlib\nimport Aesop")           # standard header enforced
    first = Fake.requests[0]["messages"][0]["content"]
    assert first.startswith("Please autoformalize the following natural language problem statement in Lean 4.")
    assert "Montrer que la somme de deux entiers pairs est paire.Think before you provide the lean statement." in first
    assert Fake.requests[0]["top_k"] == 20


def test_formalizer_gives_up_but_keeps_last_statement(qtbot, fake_server, ws):
    from lean_ai_station.services import Formalizer
    Fake.requests, Fake.script = [], [("ok", BAD_STMT), ("ok", BAD_STMT)]
    f = Formalizer(fake_server, LeanCompiler())
    with qtbot.waitSignal(f.finished, timeout=300_000) as blk:
        f.start("n'importe quoi", ws, 2, 240)
    ok, summary = blk.args
    assert not ok and f.statement and f.errors                  # the user can still fix it by hand
    assert "corrigez-la" in summary


def test_prover_restarts_after_a_loop(qtbot, fake_server, ws):
    Fake.requests, Fake.script = [], [("ok", LOOP_TEXT), ("ok", RIGHT)]
    p = Prover(fake_server, LeanCompiler())
    with qtbot.waitSignal(p.finished, timeout=300_000) as blk:
        p.start("theorem t (a b : ℕ) (h : a = b) : b = a := by sorry", ws, 4, config.SamplingSettings(), 240, 16384)
    assert blk.args[0]
    assert "tournait en rond" in p.attempts[0].summary
    assert len(Fake.requests[1]["messages"]) == 1                # restarted from the original task, no poisoned history
