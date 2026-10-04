"""Declaration index used to answer « unknown constant » errors with real Mathlib names (D25)."""
import pytest

from lean_ai_station import config, names
from lean_ai_station.services import LeanCompiler, Prover
from lean_ai_station.workspaces import all_workspaces

SRC = """
/-! # Doc
theorem not_a_real_one : True := trivial
-/
namespace Polynomial
variable {R : Type*} [Semiring R]

section Roots
theorem exists_root_of_degree_eq_one (h : degree p = 1) : ∃ x, IsRoot p x := by
  sorry
@[simp] lemma eval_add' {p q : R[X]} : (p + q).eval x = p.eval x + q.eval x := by simp
end Roots

protected theorem _root_.Nat.my_lemma (n : ℕ) : n ≤ n := le_rfl
alias exists_root_deg_one := exists_root_of_degree_eq_one
instance : Inhabited R[X] := ⟨0⟩
end Polynomial

-- theorem commented_out : True := trivial
def top_level (n : ℕ) : ℕ := n
"""


def test_parse_source_tracks_namespaces_sections_and_comments():
    got = dict(names.parse_source(SRC))
    assert set(got) == {"Polynomial.exists_root_of_degree_eq_one", "Polynomial.eval_add'", "Nat.my_lemma",
                        "Polynomial.exists_root_deg_one", "top_level"}
    assert got["Polynomial.exists_root_of_degree_eq_one"] == "(h : degree p = 1) : ∃ x, IsRoot p x"


def test_suggestions_and_feedback_note():
    idx = names.NameIndex(names.parse_source(SRC) + [("Complex.exists_root", "{f : ℂ[X]} (hf : 0 < degree f) : ∃ z, IsRoot f z")])
    assert idx.suggest("Polynomial.exists_root_of_odd_degree", 2)[0] == "Polynomial.exists_root_of_degree_eq_one"
    assert idx.suggest("Polynomial.exists_root", 1) == ["Complex.exists_root"]          # same last name, other namespace
    fb = "Error Message: unknown constant 'Polynomial.exists_root_of_odd_degree'\n"
    note = idx.feedback_note(fb)
    assert note.startswith("\n\nNote on unknown names:") and "does not exist in this version of Mathlib" in note
    assert "- `Polynomial.exists_root_of_degree_eq_one` (h : degree p = 1) : ∃ x, IsRoot p x" in note
    assert idx.feedback_note("Error Message: type mismatch") == ""
    assert names.missing_names("error: Unknown identifier `exists_root_odd`") == ["exists_root_odd"]   # Lean 4.34
    # Lean 4.9 wording when the namespace is also a type: the name is only inside the <error> markers
    fb49 = ("Corresponding Code:\n```lean4\n  exact <error>Polynomial.exists_root_of_odd_degree hP</error>\n```\n\n"
            "Error Message: invalid field notation, type is not of the form (C ...) where C is a constant\n")
    assert "`Polynomial.exists_root_of_odd_degree` does not exist" in idx.feedback_note(fb49)
    ok_field = ("<error>Polynomial.exists_root_of_degree_eq_one h</error>\n\nError Message: type mismatch\n")
    assert idx.feedback_note(ok_field) == ""
    assert "no declaration has a close name" in idx.feedback_note("unknown identifier 'zzqx_wvyk'")


@pytest.fixture(scope="module")
def ws49():
    w = next((w for w in all_workspaces() if w.key == "lean-prover49" and not w.problems), None)
    if w is None:
        pytest.skip("lean-prover49 not built")
    return w


def test_real_index_of_the_prover_workspace(ws49, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    idx = names.build(ws49)
    assert len(idx) > 100_000
    assert "exists_linearIndependent_extension" in idx and "Nat.succ_le_iff" in idx
    # the names invented by the prover in the user's dossier (cubic polynomial, 2026-10-04)
    assert "Polynomial.exists_root_of_odd_degree" not in idx
    assert "Polynomial.exists_root_of_degree_eq_one" in idx.suggest("Polynomial.exists_root_of_odd_degree")
    assert names.build(ws49).sig == idx.sig                        # cached copy is identical


def test_prover_feedback_names_existing_lemmas(qtbot, ws49):
    """End to end with the real Lean compiler: the correction prompt lists real names for an invented one."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from lean_ai_station.services import LlamaServer
    sent = []
    answers = ["```lean4\ntheorem t (a b : ℕ) (h : a = b) : b = a := by\n  exact Eq.symm_of_eq_reversed h\n```",
               "```lean4\ntheorem t (a b : ℕ) (h : a = b) : b = a := by\n  exact h.symm\n```"]

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):  # noqa: N802
            sent.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            text = answers.pop(0)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b"data: " + json.dumps({"choices": [{"delta": {"content": text}}]}).encode() + b"\n\n")
            self.wfile.write(b'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\ndata: [DONE]\n\n')
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        assert names.get(ws49, wait=True) is not None
        srv = LlamaServer()
        srv.port, srv.state = httpd.server_address[1], LlamaServer.READY
        p = Prover(srv, LeanCompiler())
        with qtbot.waitSignal(p.finished, timeout=300_000) as blk:
            p.start("theorem t (a b : ℕ) (h : a = b) : b = a := by sorry", ws49, 3, config.SamplingSettings(), 240, 24576)
        assert blk.args[0]
        correction = sent[1]["messages"][-1]["content"]
        assert "unknown" in correction and "Note on unknown names:" in correction
        assert "`Eq.symm_of_eq_reversed` does not exist" in correction and "- `Eq.symm" in correction
    finally:
        httpd.shutdown()
