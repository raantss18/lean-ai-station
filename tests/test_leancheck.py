import ast
import json
from pathlib import Path

import pytest

from lean_ai_station import leancheck as lc

REF = Path(__file__).resolve().parent.parent / "docs" / "goedel_utils_reference.py"


def _goedel_fn(name):
    """Load a function verbatim from the Goedel-Prover-V2 reference utils.py (without its heavy imports)."""
    tree = ast.parse(REF.read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    ns = {"re": __import__("re")}
    exec(compile(ast.Module([fn], []), str(REF), "exec"), ns)
    return ns[name]


def test_prepare_statement_variants():
    s = lc.prepare_statement("lemma l (a : ℕ) : a + 0 = a := by\n  simp")
    assert s.startswith("import Mathlib") and "theorem l (a : ℕ) : a + 0 = a := by sorry" in s
    assert "theorem exercice : 1 = 1 := by sorry" in lc.prepare_statement("example : 1 = 1 := by rfl")
    assert lc.prepare_statement("import Mathlib\ntheorem t : True := trivial").endswith("theorem t : True := by sorry\n")
    with pytest.raises(lc.StatementError):
        lc.prepare_statement("   ")
    with pytest.raises(lc.StatementError):
        lc.prepare_statement("def f := 1")


def test_initial_prompt_matches_goedel_readme():
    st = lc.prepare_statement("theorem t (x : ℝ) : x = x := by sorry")
    p = lc.initial_prompt(st)
    assert p.startswith("Complete the following Lean 4 code:\n\n```lean4\nimport Mathlib")
    assert p.endswith("structures that will guide the construction of the final formal proof.")
    assert "theorem t (x : ℝ) : x = x := by sorry```" in p


def test_correction_prompt_matches_goedel():
    p = lc.correction_prompt(0, "\nError 1: ...")
    assert p.startswith("The proof (Round 0) is not correct. Following is the compilation error message, where we use "
                        "<error></error> to signal the position of the error.\n\n\nError 1: ...")
    assert p.endswith("provide a detailed analysis of the error message.")


def test_extract_code_takes_last_block_after_thinking():
    out = "<think>```lean4\nWRONG\n```</think>\nplan\n```lean4\nA\n```\nmore\n```lean4\nB\n```"
    assert lc.extract_code(out) == "B"
    assert lc.extract_code("no code") is None


def test_assemble_pins_the_user_statement():
    st = lc.prepare_statement("theorem t (a b : ℕ) (h : a = b) : b = a := by sorry")
    # the model "cheats" by weakening the statement: the original signature must be kept
    cheat = "theorem t (a b : ℕ) (h : a = b) : True := by\n  trivial"
    full = lc.assemble_proof(st, cheat)
    assert "theorem t (a b : ℕ) (h : a = b) : b = a := by" in full
    assert ": True" not in full


def test_assemble_keeps_helper_lemmas():
    st = lc.prepare_statement("theorem t : 2 = 2 := by sorry")
    full = lc.assemble_proof(st, "import Mathlib\nlemma aux : 1 = 1 := rfl\n\ntheorem t : 2 = 2 := by\n  rfl")
    assert "lemma aux : 1 = 1 := rfl" in full and full.rstrip().endswith("rfl")


@pytest.mark.parametrize("code,bad", [
    ("theorem t : 1 = 1 := by\n  sorry", True),
    ("theorem t : 1 = 1 := by\n  admit", True),
    ("axiom cheat : False\ntheorem t : 1 = 2 := cheat.elim", True),
    ("theorem t : 1 = 1 := by exact?", True),
    ("theorem t : 1 = 1 := by\n  rfl -- not sorry", False),
    ("/- sorry -/ theorem t : 1 = 1 := by rfl", False),
])
def test_forbidden(code, bad):
    assert bool(lc.forbidden_reasons(code)) is bad


def _msg(sev, text, kind="", line=1):
    return json.dumps({"severity": sev, "pos": {"line": line, "column": 0}, "endPos": None, "data": text, "kind": kind})


def test_judge_accepts_clean_proof():
    out = _msg("information", "'t' depends on axioms: [propext, Classical.choice, Quot.sound]")
    v = lc.judge("theorem t : 1 = 1 := by rfl\n\n#print axioms t\n", lc.parse_lean_json(out), "t", 0)
    assert v.ok


@pytest.mark.parametrize("text", ["declaration uses 'sorry'", "declaration uses `sorry`"])
def test_judge_rejects_sorry_both_lean_versions(text):
    out = "\n".join([_msg("warning", text), _msg("information", "'t' depends on axioms: [sorryAx]")])
    v = lc.judge("theorem t : 1 = 1 := by\n  sorry\n#print axioms t", lc.parse_lean_json(out), "t", 0)
    assert not v.ok and v.has_sorry


def test_judge_rejects_errors_and_nonstandard_axioms_and_missing_probe():
    v = lc.judge("x", lc.parse_lean_json(_msg("error", "unsolved goals")), "t", 1)
    assert not v.ok and len(v.errors) == 1
    v = lc.judge("theorem t := by native_decide\n#print axioms t",
                 lc.parse_lean_json(_msg("information", "'t' depends on axioms: [propext, Lean.ofReduceBool]")), "t", 0)
    assert not v.ok and "Lean.ofReduceBool" in v.summary
    v = lc.judge("theorem t := by rfl", [], "t", 0)
    assert not v.ok


def test_judge_no_axioms():
    v = lc.judge("theorem t : True := trivial\n#print axioms t",
                 lc.parse_lean_json(_msg("information", "'t' does not depend on any axioms")), "t", 0)
    assert v.ok


def test_error_string_identical_to_goedel():
    ref = _goedel_fn("get_error_str")
    code = "\n".join(f"line {i} with some text" for i in range(30))
    errors = [{"pos": {"line": 12, "column": 3}, "endPos": {"line": 12, "column": 9}, "data": "type mismatch"},
              {"pos": {"line": 3, "column": 0}, "endPos": {"line": 15, "column": 4}, "data": "unsolved goals\n⊢ x"},
              {"pos": {"line": 20, "column": 2}, "endPos": None, "data": "unknown identifier"}]
    for thres in (True, False):
        assert lc.get_error_str(code, errors, thres) == ref(code, errors, thres)
