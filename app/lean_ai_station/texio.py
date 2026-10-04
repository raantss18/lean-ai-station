"""LaTeX input/output: pull problem statements out of a .tex file, and export a verified proof as a .tex document
that compiles with XeLaTeX/LuaLaTeX (Overleaf-ready: upload the file, or paste it in a project)."""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass

ENVS = ("theorem", "thm", "lemma", "lem", "proposition", "prop", "corollary", "cor", "exercise", "exercice", "exo",
        "problem", "probleme", "question", "claim", "conjecture", "enonce", "énoncé")
_ENV_RE = re.compile(r"\\begin\{(" + "|".join(ENVS) + r")(\*?)\}(\[[^\]]*\])?(.*?)\\end\{\1\2\}", re.DOTALL | re.IGNORECASE)
_LABELS = {"theorem": "Théorème", "thm": "Théorème", "lemma": "Lemme", "lem": "Lemme", "proposition": "Proposition",
           "prop": "Proposition", "corollary": "Corollaire", "cor": "Corollaire", "exercise": "Exercice",
           "exercice": "Exercice", "exo": "Exercice", "problem": "Problème", "probleme": "Problème",
           "question": "Question", "claim": "Affirmation", "conjecture": "Conjecture", "enonce": "Énoncé", "énoncé": "Énoncé"}


@dataclass
class TexStatement:
    title: str          # e.g. « Lemme 2 — Cauchy-Schwarz »
    body: str           # LaTeX source of the statement (comments and \label removed)


def strip_comments(tex: str) -> str:
    return "\n".join(re.sub(r"(?<!\\)%.*$", "", line) for line in tex.split("\n"))


def clean_body(body: str) -> str:
    body = re.sub(r"\\label\{[^}]*\}", "", body)
    body = re.sub(r"\\(?:vspace|hspace)\*?\{[^}]*\}", "", body)
    return re.sub(r"[ \t]+\n", "\n", body).strip()


def extract_statements(tex: str) -> list[TexStatement]:
    """Theorem-like environments of a .tex file; if there are none, the whole document body as one candidate."""
    tex = strip_comments(tex)
    out: list[TexStatement] = []
    counts: dict[str, int] = {}
    for m in _ENV_RE.finditer(tex):
        env = m.group(1).lower()
        counts[env] = counts.get(env, 0) + 1
        label = _LABELS.get(env, env.capitalize())
        opt = (m.group(3) or "").strip("[]").strip()
        title = f"{label} {counts[env]}" + (f" — {opt}" if opt else "")
        body = clean_body(m.group(4))
        if body:
            out.append(TexStatement(title, body))
    if not out:
        doc = re.search(r"\\begin\{document\}(.*?)(\\end\{document\}|$)", tex, re.DOTALL)
        body = clean_body(doc.group(1) if doc else tex)
        body = re.sub(r"\\(maketitle|tableofcontents|newpage|clearpage)\b", "", body).strip()
        if body:
            out.append(TexStatement("Tout le document", body[:6000]))
    return out


def looks_like_latex(text: str) -> bool:
    return bool(re.search(r"\$|\\[a-zA-Z]+|\\\(|\\\[", text))


_ESC = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
        "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def escape_plain(text: str) -> str:
    return "".join(_ESC.get(c, c) for c in text)


def _lean_body(code: str) -> str:
    return code.replace("\\end{Verbatim}", "\\end {Verbatim}")


def export_document(nl_statement: str, lean_statement: str, lean_proof: str, theorem_name: str,
                    verified_with: str = "", date: _dt.date | None = None, explanation: str = "") -> str:
    """Full .tex source: informal statement, formal statement and verified proof (compile with XeLaTeX/LuaLaTeX)."""
    date = date or _dt.date.today()
    nl = nl_statement.strip()
    if nl and not looks_like_latex(nl):
        nl = escape_plain(nl)
    explain_section = (f"\\section*{{Explication de la preuve}}\n"
                       f"\\emph{{Rédigée par une IA à partir de la preuve Lean ; la preuve Lean ci-dessous fait foi.}}\n\n"
                       f"{markdown_to_latex(explanation)}\n\n" if explanation.strip() else "")
    informal = (f"\\begin{{theorem}}\n{nl}\n\\end{{theorem}}\n\n" if nl else "")
    note = (f"Preuve vérifiée par Lean ({verified_with}) le {date.strftime('%d/%m/%Y')}." if verified_with
            else f"Preuve vérifiée par Lean le {date.strftime('%d/%m/%Y')}.")
    return rf"""% !TEX program = xelatex
% Document généré par Lean AI Station. Compiler avec XeLaTeX (sur Overleaf : Menu → Compilateur → XeLaTeX).
\documentclass[11pt]{{article}}
\usepackage[a4paper,margin=2.5cm]{{geometry}}
\usepackage{{fontspec}}
\setmonofont{{DejaVu Sans Mono}}[Scale=MatchLowercase]
\usepackage[french]{{babel}}
\usepackage{{amsmath,amssymb,amsthm}}
\usepackage{{fvextra}}
\usepackage[dvipsnames]{{xcolor}}
\theoremstyle{{plain}}
\newtheorem{{theorem}}{{Théorème}}

\title{{Énoncé et preuve vérifiée par Lean}}
\author{{}}
\date{{}}

\begin{{document}}
\maketitle

\section*{{Énoncé}}
{informal}\section*{{Énoncé formalisé en Lean 4}}
\begin{{Verbatim}}[breaklines=true,frame=single,fontsize=\small,rulecolor=\color{{black!25}}]
{_lean_body(lean_statement.strip())}
\end{{Verbatim}}

{explain_section}\section*{{Preuve formelle}}
{note}
\begin{{Verbatim}}[breaklines=true,frame=single,fontsize=\small,rulecolor=\color{{black!25}}]
{_lean_body(lean_proof.strip())}
\end{{Verbatim}}

\end{{document}}
"""


# ---------------------------------------------------------------- model text (light Markdown + $LaTeX$) -> display / LaTeX
_MATH_RE = re.compile(r"(\$\$.+?\$\$|\$.+?\$|\\\(.+?\\\)|\\\[.+?\\\])", re.DOTALL)
_UNI = {r"\geq": "≥", r"\ge": "≥", r"\leq": "≤", r"\le": "≤", r"\neq": "≠", r"\ne": "≠", r"\cdot": "·", r"\times": "×",
        r"\in": "∈", r"\notin": "∉", r"\mid": "∣", r"\forall": "∀", r"\exists": "∃", r"\to": "→", r"\rightarrow": "→",
        r"\Rightarrow": "⇒", r"\Leftrightarrow": "⇔", r"\iff": "⇔", r"\land": "∧", r"\lor": "∨", r"\neg": "¬",
        r"\subseteq": "⊆", r"\cup": "∪", r"\cap": "∩", r"\infty": "∞", r"\pm": "±", r"\sqrt": "√", r"\sum": "∑",
        r"\mathbb{N}": "ℕ", r"\mathbb{Z}": "ℤ", r"\mathbb{Q}": "ℚ", r"\mathbb{R}": "ℝ", r"\mathbb{C}": "ℂ",
        r"\alpha": "α", r"\beta": "β", r"\gamma": "γ", r"\delta": "δ", r"\varepsilon": "ε", r"\epsilon": "ε",
        r"\lambda": "λ", r"\pi": "π", r"\equiv": "≡", r"\ldots": "…", r"\dots": "…", r"\,": " ", r"\;": " ", r"\!": ""}
_SUP = str.maketrans("0123456789+-n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻ⁿ")
_SUB = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")


def latex_math_to_unicode(m: str) -> str:
    m = m.strip().strip("$").strip()
    m = re.sub(r"^\\[(\[]|\\[)\]]$", "", m)
    m = re.sub(r"\\(?:text|mathrm|operatorname)\{([^}]*)\}", r"\1", m)
    m = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1)/(\2)", m)
    for k in sorted(_UNI, key=len, reverse=True):
        m = m.replace(k, _UNI[k])
    m = re.sub(r"\^\{?([0-9+\-n]+)\}?", lambda x: x.group(1).translate(_SUP), m)
    m = re.sub(r"_\{?([0-9]+)\}?", lambda x: x.group(1).translate(_SUB), m)
    return m.replace("\\left", "").replace("\\right", "").replace("{", "").replace("}", "")


def display_markdown(text: str) -> str:
    """Markdown for the GUI (QTextBrowser.setMarkdown): $math$ turned into readable Unicode."""
    return _MATH_RE.sub(lambda mo: latex_math_to_unicode(mo.group(0)), text)


def markdown_to_latex(text: str) -> str:
    """Light Markdown (bold, italic, `code`, numbered/bulleted lists) + $math$ → safe LaTeX body."""
    out: list[str] = []
    state = None                                    # open list environment

    def inline(s: str) -> str:
        parts = _MATH_RE.split(s)
        res = []
        for i, part in enumerate(parts):
            if i % 2:                               # math segment: keep verbatim
                res.append(part)
                continue
            part = re.sub(r"`([^`]+)`", lambda m: "\x00T" + m.group(1) + "\x00E", part)
            part = re.sub(r"\*\*(.+?)\*\*", lambda m: "\x00B" + m.group(1) + "\x00E", part)
            part = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", lambda m: "\x00I" + m.group(1) + "\x00E", part)
            part = escape_plain(part)
            part = (part.replace("\x00T", r"\texttt{").replace("\x00B", r"\textbf{").replace("\x00I", r"\emph{")
                    .replace("\x00E", "}"))
            res.append(part)
        return "".join(res)

    def close():
        nonlocal state
        if state:
            out.append(f"\\end{{{state}}}")
            state = None

    for raw in text.strip().split("\n"):
        line = raw.rstrip()
        h = re.match(r"\s{0,3}#{1,6}\s+(.*)", line)
        num = re.match(r"\s*\d+[.)]\s+(.*)", line)
        bul = re.match(r"\s*[-*•]\s+(.*)", line)
        if h:
            close()
            out.append(f"\\paragraph{{{inline(h.group(1))}}}")
        elif num or bul:
            env = "enumerate" if num else "itemize"
            if state != env:
                close()
                out.append(f"\\begin{{{env}}}")
                state = env
            out.append(f"\\item {inline((num or bul).group(1))}")
        elif not line.strip():
            close()
            out.append("")
        else:
            if state:
                out[-1] += " " + inline(line.strip())     # continuation of the previous item
            else:
                out.append(inline(line.strip()))
    close()
    return "\n".join(out).strip()
