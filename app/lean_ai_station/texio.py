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
                    verified_with: str = "", date: _dt.date | None = None) -> str:
    """Full .tex source: informal statement, formal statement and verified proof (compile with XeLaTeX/LuaLaTeX)."""
    date = date or _dt.date.today()
    nl = nl_statement.strip()
    if nl and not looks_like_latex(nl):
        nl = escape_plain(nl)
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

\section*{{Preuve formelle}}
{note}
\begin{{Verbatim}}[breaklines=true,frame=single,fontsize=\small,rulecolor=\color{{black!25}}]
{_lean_body(lean_proof.strip())}
\end{{Verbatim}}

\end{{document}}
"""
