import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from lean_ai_station import leancheck as lc, texio
from lean_ai_station.services import ChatStream, LlamaServer

SRC = r"""\documentclass{article}\begin{document}
% \begin{theorem} commented out \end{theorem}
\begin{lemma}[Cauchy]\label{x} Pour tous $a,b$ réels, $2ab\le a^2+b^2$.\end{lemma}
\begin{theorem} Il y a une infinité de nombres premiers.\end{theorem}
\begin{proof} Euclide. \end{proof}\end{document}"""


def test_extract_statements_from_tex():
    items = texio.extract_statements(SRC)
    assert [i.title for i in items] == ["Lemme 1 — Cauchy", "Théorème 1"]
    assert "\\label" not in items[0].body and "$2ab\\le a^2+b^2$" in items[0].body
    assert all("commented" not in i.body for i in items)


def test_extract_falls_back_to_document_body():
    items = texio.extract_statements(r"\begin{document}\maketitle Soit $x$ un réel positif. \end{document}")
    assert len(items) == 1 and "Soit $x$ un réel positif." in items[0].body


def test_export_escapes_plain_text_and_keeps_latex():
    plain = texio.export_document("Montrer que 50% de n_1 & n_2 est #1", "theorem t : True := by sorry", "p", "t")
    assert r"50\% de n\_1 \& n\_2 est \#1" in plain
    fancy = texio.export_document("Soit $n_1 \\ge 2$.", "theorem t : True := by sorry", "p", "t")
    assert "Soit $n_1 \\ge 2$." in fancy and r"\_1" not in fancy.split("Énoncé formalisé")[0].split("\\begin{theorem}")[1]
    sneaky = texio.export_document("", "s", "x\n\\end{Verbatim}\n\\input{/etc/passwd}", "t")
    assert "\\end {Verbatim}\n\\input" in sneaky            # cannot close the verbatim block


@pytest.mark.skipif(not shutil.which("xelatex"), reason="xelatex not installed")
def test_exported_tex_compiles_with_xelatex(tmp_path):
    doc = texio.export_document("La somme de deux entiers pairs est paire, $a+b$.",
                                "theorem t (a b : ℕ) : Even a → Even b → Even (a + b) := by sorry",
                                "import Mathlib\ntheorem t (a b : ℕ) (ha : Even a) (hb : Even b) : Even (a + b) := by\n"
                                "  obtain ⟨k, hk⟩ := ha\n  rcases hb with ⟨l, hl⟩\n  exact ⟨k + l, by omega⟩ -- ∀ ∃ ℝ ≤",
                                "t", "Lean 4.9")
    (tmp_path / "t.tex").write_text(doc, encoding="utf-8")
    r = subprocess.run(["xelatex", "-interaction=nonstopmode", "-halt-on-error", "t.tex"], cwd=tmp_path,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and (tmp_path / "t.pdf").exists(), r.stdout[-800:]


# ---------------------------------------------------------------- loop detection
LOOP = ("-- We will use the fact that the determinant of a symmetric matrix is non-negative.\n"
        "-- However, since A is invertible, its determinant is non-zero.\n")


def test_loop_detector():
    assert lc.detect_loop("intro\n" + LOOP * 12)
    assert lc.detect_loop("Hello world. " * 3000)
    assert lc.detect_loop("  · norm_num\n" * 60) is None         # legit repeated tactic lines
    assert lc.detect_loop("have h : x = 1 := by linarith\n" * 3) is None
    assert lc.detect_loop("plain text " * 4) is None


class LoopHandler(BaseHTTPRequestHandler):
    sent = 0

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        try:
            self.wfile.write(b"data: " + json.dumps({"choices": [{"delta": {"content": "Début utile. "}}]}).encode() + b"\n\n")
            for _ in range(5000):                      # would run "forever" if the client did not stop it
                LoopHandler.sent += 1
                self.wfile.write(b"data: " + json.dumps({"choices": [{"delta": {"content": LOOP}}]}).encode() + b"\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def test_chat_stream_stops_a_loop(qtbot):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), LoopHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        s = ChatStream(f"http://127.0.0.1:{httpd.server_address[1]}")
        with qtbot.waitSignal(s.done, timeout=30000) as blk:
            s.start([{"role": "user", "content": "x"}], 0.6, 0.95, 4000)
        d = blk.args[0]
        assert d["loop"] and d["finish_reason"] == "loop"
        assert d["text"].startswith("Début utile.") and d["text"].count("We will use the fact") <= 2
        assert LoopHandler.sent < 1500                  # stopped early, not after all 5000 chunks
    finally:
        httpd.shutdown()


# ---------------------------------------------------------------- explanation helpers
def test_explain_prompt_skips_header_and_mentions_original_statement():
    code = "import Mathlib\nimport Aesop\n\nset_option maxHeartbeats 400000\n\ntheorem t : 1 = 1 := by\n  rfl\n"
    p = lc.explain_prompt(code, "Montrer que 1 = 1.")
    assert "import Mathlib" not in p and "theorem t : 1 = 1" in p and "Montrer que 1 = 1." in p
    assert "EN FRANÇAIS" in p and "n'invente aucune étape" in p


def test_clean_model_text_removes_think_blocks():
    assert lc.clean_model_text("<think>\n\n</think>\n\nBonjour") == "Bonjour"
    assert lc.clean_model_text("<think>réflexion qui ne finit pas") == ""
    assert lc.clean_model_text("Texte") == "Texte"


def test_has_real_proof():
    assert not lc.has_real_proof("theorem t : 1 = 1 := by sorry")
    assert not lc.has_real_proof("-- rien\n")
    assert lc.has_real_proof("theorem t : 1 = 1 := by\n  rfl")
    assert lc.has_real_proof("theorem t : 1 = 1 := rfl")


def test_markdown_and_math_for_display_and_latex():
    md = "**Énoncé.** Si $a \\ge 2^{10}$ et $x_1 \\in \\mathbb{N}$ (50%, n_1).\n\n1. On écrit `rcases`.\n2. Donc $a+b$.\n- fin"
    shown = texio.display_markdown(md)
    assert "a ≥ 2¹⁰" in shown and "x₁ ∈ ℕ" in shown and "$" not in shown
    tex = texio.markdown_to_latex(md)
    assert r"\textbf{Énoncé.}" in tex and r"50\%" in tex and r"n\_1" in tex and "$a \\ge 2^{10}$" in tex
    assert r"\begin{enumerate}" in tex and r"\texttt{rcases}" in tex and r"\begin{itemize}" in tex


@pytest.mark.skipif(not shutil.which("xelatex"), reason="xelatex not installed")
def test_export_with_explanation_compiles(tmp_path):
    expl = ("**Énoncé.** Si $a$ et $b$ sont pairs, $a+b$ l'est (100% sûr, f_1).\n\n1. On écrit $a = 2k$ et `rcases`.\n"
            "2. Donc $a+b = 2(k+m) \\ge 0$.\n- fin & suite #1")
    doc = texio.export_document("Soit $n$.", "theorem t : 1 = 1 := by sorry", "theorem t : 1 = 1 := by\n  rfl", "t",
                                "Lean 4.9", explanation=expl)
    (tmp_path / "t.tex").write_text(doc, encoding="utf-8")
    r = subprocess.run(["xelatex", "-interaction=nonstopmode", "-halt-on-error", "t.tex"], cwd=tmp_path,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-800:]
    assert "Explication de la preuve" in doc and "Rédigée par une IA" in doc


DATA = Path(__file__).resolve().parent / "data"


def test_rambling_detected_on_a_real_prover_answer():
    """Real Goedel-Prover output (2026-10-04) that recycles the same paragraphs with small variations."""
    sample = (DATA / "prover_rambling.txt").read_text()
    text = sample[:12600] + sample[7600:12600] * 3               # the user's run went on like this for 16k tokens
    assert lc.detect_loop(text) is None                 # not an exact repetition…
    hit = lc.detect_rambling(text)
    assert hit and hit[0] >= 12                                  # …but caught by the fuzzy detector
    assert lc.detect_rambling(sample) is None                    # a shorter circling phase is tolerated (D22)


def test_rambling_ignores_repeated_tactics_and_varied_prose():
    proof = "```lean4\ntheorem t : True := by\n" + "  · norm_num [Nat.mul_mod, Nat.add_mod, Nat.pow_mod]\n" * 600 + "```"
    assert lc.detect_rambling("Plan: case analysis on n % 11.\n" + proof) is None
    prose = "\n".join(f"Step {i}: we bound the term number {i} by {i * i + 3} using the inequality of rank {i}."
                      for i in range(400))
    assert lc.detect_rambling(prose) is None


class RamblingHandler(BaseHTTPRequestHandler):
    sent = 0

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        text = (DATA / "prover_rambling.txt").read_text()
        text = text[:12600] + text[7600:12600] * 8          # keeps recycling the same paragraphs
        try:
            for i in range(0, len(text), 4):                  # ≈ one token per chunk, like llama-server
                RamblingHandler.sent += 1
                self.wfile.write(b"data: " + json.dumps({"choices": [{"delta": {"content": text[i:i + 4]}}]}).encode()
                                 + b"\n\n")
        except (BrokenPipeError, ConnectionResetError):
            pass


def test_chat_stream_stops_rambling(qtbot):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), RamblingHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        s = ChatStream(f"http://127.0.0.1:{httpd.server_address[1]}")
        with qtbot.waitSignal(s.done, timeout=60000) as blk:
            s.start([{"role": "user", "content": "x"}], 1.0, 0.95, 16000)
        d = blk.args[0]
        assert d["loop"] and d["finish_reason"] == "loop"
        assert RamblingHandler.sent < 7500                  # stopped well before the end (≈ 13 150 chunks)
    finally:
        httpd.shutdown()


def test_exact_loop_detector_spares_repeated_tactic_blocks_of_a_correct_proof():
    """Real answer accepted by Lean (amc12b_2021_p3): its proof repeats a 121-character tactic block 5 times.
    The 1.0 detector stopped it at 7 600 characters (D22)."""
    text = (DATA / "prover_accepted_repeated_tactics.txt").read_text()
    for pos in range(2000, len(text) + 160, 160):
        assert lc.detect_loop(text[:pos]) is None, pos
    # a real runaway inside code (imo_1959_p1: `(Nat.gcd_eq_left (Nat.gcd_eq_right …` until the token limit) is caught
    runaway = "```lean4\ntheorem t : True := by\n  exact " + "(Nat.gcd_eq_left (Nat.gcd_eq_right " * 100
    assert lc.detect_loop(runaway)
