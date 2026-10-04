import json

from lean_ai_station import dossiers as ds
from lean_ai_station import leancheck as lc


def test_slug_and_title():
    assert ds.slugify("Somme de deux entiers pairs !") == "somme_de_deux_entiers_pairs"
    assert ds.slugify("3 est impair") == "thm_3_est_impair"
    assert ds.slugify("∀∃") == "thm"
    assert ds.title_from_problem("Montrer que la somme de deux entiers pairs est paire.") == \
        "La somme de deux entiers pairs est paire."
    assert ds.title_from_problem("Prove that $x^2 \\ge 0$ for all reals") == "… for all reals"


def test_dossier_versions_and_restore(tmp_path):
    st = ds.DossierStore(tmp_path)
    d = st.new("Montrer que 2 + 2 = 4.", "lean-prover49")
    assert d.theorem.startswith("thm_2_2_4_") or d.theorem.endswith(d.id[-4:])
    s0 = d.add_statement("theorem a : 2 + 2 = 4 := by sorry")
    p0 = d.add_proof("theorem a : 2 + 2 = 4 := by norm_num")
    assert d.proof_is_current
    s1 = d.add_statement("theorem a : (2 : ℝ) + 2 = 4 := by sorry", source="user")
    assert not d.proof_is_current                   # the proof belongs to the previous statement
    d.restore("proof", p0)
    assert d.cur_statement == s0 and d.proof_is_current
    d.restore("statement", s1)
    assert d.statement.startswith("theorem a : (2 : ℝ)")
    d.add_event("user", "plus court", stage="proof")
    st.save(d)
    d2 = st.load(d.id)
    assert d2.to_json() == d.to_json()


def test_store_list_delete_undelete_and_corrupt_file(tmp_path):
    st = ds.DossierStore(tmp_path)
    a, b = st.new("A"), st.new("B")
    a.updated, b.updated = 1.0, 2.0
    st.save(a)
    st.save(b)
    (tmp_path / "broken.json").write_text("{ not json")
    assert [d.title for d in st.list()] == ["B", "A"]
    assert st.delete(a.id) and [d.title for d in st.list()] == ["B"]
    assert st.undelete(a.id) and len(st.list()) == 2


PROOF = ("import Mathlib\nimport Aesop\n\nset_option maxHeartbeats 400000\n\nopen BigOperators Real Nat Topology Rat\n\n"
         "theorem somme_pairs_ab12 (a b : ℕ) (ha : Even a) (hb : Even b) : Even (a + b) := by\n  exact Even.add ha hb\n")


def test_library_add_dedupe_relevance(tmp_path):
    lib = ds.Library(tmp_path / "lib.json")
    e = lib.add_proof(PROOF, "Somme de deux pairs", "lean-prover49", "d1")
    assert e and e.name == "somme_pairs_ab12" and "import" not in e.code
    assert lib.add_proof(PROOF, "doublon", "lean-prover49") is None
    lib2 = ds.Library(tmp_path / "lib.json")
    assert [x.name for x in lib2.entries] == ["somme_pairs_ab12"]
    near = "theorem t (a b c : ℕ) (ha : Even a) (hb : Even b) (hc : Even c) : Even (a + b + c) := by sorry"
    far = "theorem t (x : ℝ) (hx : 0 < x) : Real.sqrt (x ^ 2) = x := by sorry"
    assert [x.name for x in lib2.relevant(near, "lean-prover49")] == ["somme_pairs_ab12"]
    assert lib2.relevant(far, "lean-prover49") == []
    assert lib2.relevant(near, "lean-current") == []           # other Lean version: never mixed


def test_with_lemmas_places_results_above_target_and_prompt_keeps_them(tmp_path):
    lib = ds.Library(tmp_path / "lib.json")
    e = lib.add_proof(PROOF, "Somme de deux pairs", "lean-prover49")
    st = lc.prepare_statement("theorem cible (a b c : ℕ) (ha : Even a) (hb : Even b) (hc : Even c) : "
                              "Even (a + b + c) := by sorry")
    full = ds.with_lemmas(st, [e, e])
    assert full.count("theorem somme_pairs_ab12") == 1
    assert full.index("somme_pairs_ab12") < full.index("theorem cible")
    assert lc.theorem_name(full) == "cible"
    assert "exact Even.add ha hb" in lc.initial_prompt(full)


def test_router():
    r = ds.route
    assert r("ajoute l'hypothèse n > 0", True, True) == "statement"
    assert r("utilise des réels au lieu des entiers", True, True) == "statement"
    assert r("une preuve plus courte", True, True) == "proof"
    assert r("prove it by induction", True, False) == "proof"
    assert r("explique l'étape 3 plus en détail", True, True) == "explanation"
    assert r("why does omega work here?", True, True) in ("explanation", "proof")
    assert r("explique", True, False) == "explanation"
    assert r("n'importe quoi", True, True) == "proof"
    assert r("explique la preuve", False, False) == "statement"      # nothing proven yet


def test_dossier_json_is_readable(tmp_path):
    st = ds.DossierStore(tmp_path)
    d = st.new("x")
    d.add_event("info", "é ∀ ℝ")
    st.save(d)
    assert "é ∀ ℝ" in (tmp_path / f"{d.id}.json").read_text(encoding="utf-8")
    json.loads((tmp_path / f"{d.id}.json").read_text(encoding="utf-8"))


def test_library_dependencies_are_not_duplicated_and_come_first(tmp_path):
    lib = ds.Library(tmp_path / "lib.json")
    a = lib.add_proof(PROOF, "Somme de deux pairs", "w")
    st = lc.prepare_statement("theorem trois_pairs (a b c : ℕ) (ha : Even a) (hb : Even b) (hc : Even c) : "
                              "Even (a + b + c) := by sorry")
    proved = ds.with_lemmas(st, [a]).replace(":= by sorry", ":= by\n  exact somme_pairs_ab12 _ _ (somme_pairs_ab12 _ _ ha hb) hc")
    b = lib.add_proof(proved, "Trois pairs", "w", used=[a.name])
    assert b.deps == ["somme_pairs_ab12"] and "theorem somme_pairs_ab12" not in b.code
    full = ds.with_lemmas(lc.prepare_statement("theorem q (x : ℕ) : Even (x + x) := by sorry"), ds.closure(lib, [b]))
    assert full.index("theorem somme_pairs_ab12") < full.index("theorem trois_pairs") < full.index("theorem q")
