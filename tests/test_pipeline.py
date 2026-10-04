"""Dossier pipeline (translate → prove → explain + follow-ups + library) with a scripted fake model server and the
real Lean compiler. The fake answers according to the prompt it receives (formalizer / prover / explainer)."""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from lean_ai_station import config, dossiers as ds
from lean_ai_station.ui.context import AppContext
from lean_ai_station.services import LlamaServer
from lean_ai_station.workspaces import all_workspaces

REQUESTS: list[dict] = []
BEHAVIOUR: dict = {}


def answer(body: dict) -> str:
    msgs = body["messages"]
    first = msgs[0]["content"]
    if first.startswith("Please autoformalize"):
        name = re.search(r"Use the following theorem name: (\S+)", first).group(1)
        if BEHAVIOUR.get("bad_statement"):
            return f"```lean4\ntheorem {name} (a b : ℕ) (ha : Even a : Even (a + b) := by sorry\n```"
        concl = "Even (a + b + 0)" if "previous formalization" in first else "Even (a + b)"
        return (f"<think>ok</think>\n```lean4\nimport Mathlib\n\ntheorem {name} (a b : ℕ) (ha : Even a) (hb : Even b) : "
                f"{concl} := by sorry\n```")
    if first.startswith("Complete the following Lean 4 code"):
        stmt = re.search(r"```lean4\n(.*?)```", first, re.DOTALL).group(1)
        sig = stmt[stmt.rindex("theorem"):].replace(":= by sorry", ":= by")
        tactic = "simpa using Even.add ha hb" if len(msgs) > 1 else "exact Even.add ha hb"
        if "+ 0" in sig:
            tactic = "simpa using Even.add ha hb"
        return f"<think>plan</think>\n```lean4\n{sig}\n  {tactic}\n```"
    return "<think>\n\n</think>\n\n**Énoncé.** La somme de deux entiers pairs est paire.\n\n1. On utilise `Even.add`."


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        REQUESTS.append(body)
        text = answer(body)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for i in range(0, len(text), 9):
            self.wfile.write(b"data: " + json.dumps({"choices": [{"delta": {"content": text[i:i + 9]}}]}).encode() + b"\n\n")
        end = {"choices": [{"delta": {}, "finish_reason": "stop"}], "timings": {"predicted_n": 50, "predicted_per_second": 40}}
        self.wfile.write(b"data: " + json.dumps(end).encode() + b"\n\ndata: [DONE]\n\n")


@pytest.fixture(scope="module")
def ws():
    w = next((w for w in all_workspaces() if w.key == "lean-prover49" and not w.problems), None)
    if w is None:
        w = next((w for w in all_workspaces() if not w.problems and not w.readonly), None)
    if w is None:
        pytest.skip("no built Lean workspace")
    return w


@pytest.fixture
def ctx(qtbot, tmp_path, ws, monkeypatch):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    REQUESTS.clear()
    BEHAVIOUR.clear()
    s = config.Settings()
    s.wizard_done, s.workspace = True, ws.key
    c = AppContext(s, {})
    c.workspaces = [ws]
    c.pipeline.store = ds.DossierStore(tmp_path / "dossiers")
    c.pipeline.library = ds.Library(tmp_path / "library.json")
    c.server.port = httpd.server_address[1]
    c.server.state = LlamaServer.READY
    monkeypatch.setattr(c, "role_model", lambda role: Path(f"/models/{role}.gguf"))
    monkeypatch.setattr(c, "ensure_model", lambda then, role="prover": then())     # one fake server plays all roles
    yield c
    httpd.shutdown()


def wait_idle(qtbot, c, timeout=300_000):
    qtbot.waitUntil(lambda: not c.pipeline.busy, timeout=timeout)


def kinds(d):
    return [e.kind for e in d.events]


def test_full_automatic_chain(qtbot, ctx):
    p = ctx.pipeline
    p.start("Montrer que la somme de deux entiers pairs est paire.")
    qtbot.waitUntil(lambda: p.busy, timeout=5000)
    wait_idle(qtbot, ctx)
    d = p.dossier
    assert kinds(d) == ["user", "statement", "proof", "info", "explanation"], [(e.kind, e.text) for e in d.events]
    assert d.statement.rstrip().endswith("Even (a + b) := by sorry") and d.theorem in d.statement
    assert "exact Even.add ha hb" in d.proof and d.proof_is_current
    assert d.explanation.startswith("**Énoncé.**")
    roles = [r["messages"][0]["content"].split(" ")[0] for r in REQUESTS]
    assert roles == ["Please", "Complete", "Tu"]                         # formalizer → prover → explainer
    assert p.store.load(d.id).to_json() == d.to_json()                     # persisted
    assert [e.name for e in p.library.entries] == [d.theorem]              # library updated


def test_follow_up_on_the_proof_uses_history(qtbot, ctx):
    p = ctx.pipeline
    p.start("Montrer que la somme de deux entiers pairs est paire.")
    qtbot.waitUntil(lambda: p.busy, timeout=5000)
    wait_idle(qtbot, ctx)
    REQUESTS.clear()
    stage = p.request("Donne une preuve plus courte avec simpa")
    assert stage == "proof"
    wait_idle(qtbot, ctx)
    d = p.dossier
    prover_req = REQUESTS[0]["messages"]
    assert len(prover_req) == 3 and "The user now asks: Donne une preuve plus courte" in prover_req[2]["content"]
    assert "exact Even.add ha hb" in prover_req[1]["content"]                # previous verified proof in the history
    assert len(d.proofs) == 2 and "simpa using" in d.proof and len(d.explanations) == 2


def test_follow_up_on_the_statement_reruns_everything(qtbot, ctx):
    p = ctx.pipeline
    p.start("Montrer que la somme de deux entiers pairs est paire.")
    qtbot.waitUntil(lambda: p.busy, timeout=5000)
    wait_idle(qtbot, ctx)
    REQUESTS.clear()
    assert p.request("ajoute + 0 à la conclusion de l'énoncé") == "statement"
    wait_idle(qtbot, ctx)
    d = p.dossier
    f = REQUESTS[0]["messages"][0]["content"]
    assert "A previous formalization was" in f and "ajoute + 0" in f
    assert "Even (a + b + 0)" in d.statement and d.proof_is_current and len(d.statements) == 2


def test_explanation_follow_up_and_language(qtbot, ctx):
    from lean_ai_station import i18n
    p = ctx.pipeline
    p.start("Montrer que la somme de deux entiers pairs est paire.")
    qtbot.waitUntil(lambda: p.busy, timeout=5000)
    wait_idle(qtbot, ctx)
    REQUESTS.clear()
    i18n.set_language("en")
    try:
        assert p.request("explain step 1 in more detail") == "explanation"
        wait_idle(qtbot, ctx)
    finally:
        i18n.set_language("fr")
    m = REQUESTS[0]["messages"]
    assert len(REQUESTS) == 1 and m[0]["content"].startswith("You are a mathematics teacher") and len(m) == 3
    assert "explain step 1" in m[2]["content"]


def test_library_result_is_offered_in_the_next_dossier(qtbot, ctx):
    p = ctx.pipeline
    p.start("Montrer que la somme de deux entiers pairs est paire.")
    qtbot.waitUntil(lambda: p.busy, timeout=5000)
    wait_idle(qtbot, ctx)
    first = p.dossier.theorem
    REQUESTS.clear()
    p.start("Montrer encore que la somme de deux pairs est paire.")
    qtbot.waitUntil(lambda: p.busy, timeout=5000)
    wait_idle(qtbot, ctx)
    prover_prompt = next(r for r in REQUESTS if r["messages"][0]["content"].startswith("Complete"))["messages"][0]["content"]
    assert f"theorem {first}" in prover_prompt                     # previous result pasted above the new theorem
    assert p.dossier.lemmas_used == [first] and p.dossier.proof_is_current


def test_pause_option_and_failed_translation(qtbot, ctx):
    p = ctx.pipeline
    ctx.settings.pause_after_translation = True
    p.start("Montrer que la somme de deux entiers pairs est paire.")
    qtbot.waitUntil(lambda: p.busy, timeout=5000)
    wait_idle(qtbot, ctx)
    assert kinds(p.dossier) == ["user", "statement", "info"] and not p.dossier.proof
    ctx.settings.pause_after_translation = False
    BEHAVIOUR["bad_statement"] = True
    p.start("Problème qui se traduit mal")
    qtbot.waitUntil(lambda: p.busy, timeout=5000)
    wait_idle(qtbot, ctx)
    assert kinds(p.dossier)[-1] == "error" and not p.dossier.proof
    assert not any(r["messages"][0]["content"].startswith("Complete") for r in REQUESTS[-3:])


def test_cancel_and_restore(qtbot, ctx):
    p = ctx.pipeline
    p.start("Montrer que la somme de deux entiers pairs est paire.")
    qtbot.waitUntil(lambda: p.stage == "proof", timeout=120_000)
    p.cancel()
    assert not p.busy and kinds(p.dossier)[-1] == "info"
    p.prove_current()
    qtbot.waitUntil(lambda: p.busy, timeout=5000)
    wait_idle(qtbot, ctx)
    p.request("ajoute + 0 à la conclusion de l'énoncé")
    wait_idle(qtbot, ctx)
    p.restore("statement", 0)
    assert p.dossier.statement.rstrip().endswith("Even (a + b) := by sorry") and not p.dossier.proof_is_current
    p.restore("proof", 0)
    assert p.dossier.proof_is_current
