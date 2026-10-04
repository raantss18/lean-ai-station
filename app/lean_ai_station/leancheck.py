"""Pure logic for the prove loop: Goedel-Prover-V2 prompts, code extraction, statement
pinning and the acceptance verdict. Prompt texts and error formatting are verbatim ports of
Goedel-Prover-V2 `src/utils.py` (DeepSeekCoTHandler, get_error_str)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .i18n import _

GOEDEL_HEADER = "import Mathlib\nimport Aesop\n\nset_option maxHeartbeats 400000\n\nopen BigOperators Real Nat Topology Rat\n\n"
ALLOWED_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}
# Tokens that are never acceptable in a submitted proof (checked on comment-free text).
FORBIDDEN = [
    (r"\bsorry\b", "« sorry » (preuve laissée en suspens)"),
    (r"\badmit\b", "« admit » (preuve laissée en suspens)"),
    (r"(?m)^\s*axiom\b|\baxiom\s+\w", "déclaration d'un nouvel axiome"),
    (r"\bapply\?|\bexact\?", "« apply? / exact? » (recherche non finalisée)"),
    (r"#exit\b", "« #exit »"),
    (r"\bimplemented_by\b|@\[extern|\bunsafe\b", "code non vérifié (unsafe / extern)"),
    (r"(?m)^\s*(elab|macro|macro_rules|syntax|run_cmd|run_tac)\b", "méta-programmation"),
    (r"debug\.skipKernelTC", "désactivation du noyau"),
]

INITIAL_TEMPLATE = (
    "Complete the following Lean 4 code:\n\n```lean4\n{}```\n\n"
    "Before producing the Lean 4 code to formally prove the given theorem, provide a detailed proof plan "
    "outlining the main proof steps and strategies.\nThe plan should highlight key ideas, intermediate lemmas, "
    "and proof structures that will guide the construction of the final formal proof."
)
CORRECTION_TEMPLATE = (
    "The proof (Round {round}) is not correct. Following is the compilation error message, where we use "
    "<error></error> to signal the position of the error.\n\n{errors}"
    "\n\nBefore producing the Lean 4 code to formally prove the given theorem, provide a detailed analysis of the error message."
)


def remove_comments(text: str) -> str:
    text = re.sub(r"/-.*?-/", "", text, flags=re.DOTALL)
    return "\n".join(line.split("--", 1)[0] for line in text.split("\n")).strip()


class StatementError(ValueError):
    pass


def prepare_statement(source: str) -> str:
    """Normalise user input into a Lean file whose target theorem ends with `:= by sorry`.

    Accepts `theorem`, `lemma` or `example`; adds the Goedel header if there is no import."""
    src = source.replace("\r\n", "\n").strip()
    if not src:
        raise StatementError(_("L'éditeur est vide : écrivez un énoncé ou choisissez un exemple."))
    if not re.search(r"(?m)^\s*import\s", src):
        src = GOEDEL_HEADER + src
    # the target is the LAST declaration: lemmas (e.g. from the library, with their proofs) may precede it.
    # lemma/example -> theorem (same meaning; Goedel's pipeline matches `theorem`)
    decls = list(re.finditer(r"(?m)^(\s*(?:@\[[^\]]*\]\s*)?(?:private\s+|protected\s+)?)(theorem|lemma|example)\b", src))
    if not decls:
        raise StatementError(_("Aucun « theorem », « lemma » ou « example » trouvé dans l'éditeur."))
    m = decls[-1]
    kw = m.group(2)
    if kw == "lemma":
        src = src[: m.start(2)] + "theorem" + src[m.end(2):]
    elif kw == "example":
        src = src[: m.start(2)] + "theorem exercice" + src[m.end(2):]
    before, target = src[: m.start(2)], src[m.start(2):]
    head, sep, _rest = target.partition(":= by")
    if not sep:
        head, sep, _rest = target.partition(":=")
        if not sep:
            raise StatementError(_("L'énoncé doit se terminer par « := by sorry »."))
    return before + head.rstrip() + " := by sorry\n"


def theorem_name(statement: str) -> str:
    """Name of the target (= last) theorem."""
    names = re.findall(r"\btheorem\s+([^\s:({\[⦃]+)", remove_comments(statement))
    if not names:
        raise StatementError(_("Impossible de trouver le nom du théorème."))
    return names[-1]


def declared_names(code: str) -> set[str]:
    return set(re.findall(r"(?m)^\s*(?:noncomputable\s+)?(?:theorem|lemma|def|abbrev)\s+([^\s:({\[⦃]+)",
                          remove_comments(code)))


def initial_prompt(statement: str) -> str:
    # Goedel: statement.split(":= by")[0] + ":= by sorry"; rsplit = same text for a single theorem, and keeps the
    # complete preceding lemmas when library results are placed above the target theorem.
    formal = statement.rsplit(":= by", 1)[0] + ":= by sorry"
    return INITIAL_TEMPLATE.format(formal)


def correction_prompt(round_idx: int, error_str: str) -> str:
    return CORRECTION_TEMPLATE.format(round=round_idx, errors=error_str)


def extract_code(output: str) -> str | None:
    """Last ```lean4 block of a model answer (thinking part ignored)."""
    text = output.split("</think>")[-1] if "</think>" in output else output
    for pat in (r"```lean4\n(.*?)\n```", r"```lean4\n(.*?)```", r"```lean\n(.*?)```"):
        found = re.findall(pat, text, re.DOTALL)
        if found:
            return found[-1]
    return None


def assemble_proof(statement: str, model_code: str) -> str:
    """Pin the user's statement: keep its header + theorem signature, take only what follows the
    model's `theorem ... := by`, plus auxiliary lemmas the model declared before the theorem."""
    stmt = remove_comments(statement)
    starts = [mm.start(1) for mm in re.finditer(r"(?:^|\s)(theorem)\s", stmt)]
    tail = stmt[starts[-1]:] if starts else ""
    m_sig = re.match(r"theorem\s.*?:=\s*by\s*sorry", tail, re.DOTALL)
    if not m_sig:
        raise StatementError(_("Énoncé mal formé (il faut « theorem … := by sorry »)."))
    prefix, signature = stmt[: starts[-1]], tail[: m_sig.end()]
    signature = signature[: signature.rfind("sorry")]

    code = remove_comments(model_code)
    m_code = re.search(r"(?:^|\s)theorem\s+.*?:=\s*by", code, re.DOTALL)
    if not m_code:
        raise StatementError(_("La réponse du modèle ne contient pas de « theorem … := by »."))
    name = theorem_name(statement)
    # find the model's theorem with the same name if present (helpers may also be theorems)
    for mm in re.finditer(r"(?:^|\s)theorem\s+([^\s:({\[⦃]+).*?:=\s*by", code, re.DOTALL):
        if mm.group(1) == name:
            m_code = mm
            break
    body = code[m_code.end():]
    before = code[: m_code.start()]
    helpers = "\n".join(
        line for line in before.split("\n")
        if not re.match(r"\s*(import|set_option|open)\b", line)
    ).strip()
    # drop helper declarations already present in the statement (library lemmas the model copied back)
    known = declared_names(prefix)
    if helpers and known:
        blocks = re.split(r"(?m)^(?=(?:noncomputable\s+)?(?:theorem|lemma|def|abbrev)\s)", helpers)
        helpers = "\n".join(b.rstrip() for b in blocks if b.strip() and not (declared_names(b) & known)).strip()
    parts = [prefix.rstrip(), helpers, signature.rstrip() + body]
    return "\n\n".join(p for p in parts if p).rstrip() + "\n"


def forbidden_reasons(code: str) -> list[str]:
    text = remove_comments(code)
    return [label for pat, label in FORBIDDEN if re.search(pat, text)]


@dataclass
class LeanMessage:
    severity: str
    line: int
    col: int
    end_line: int | None
    end_col: int | None
    text: str
    kind: str = ""

    def as_goedel(self) -> dict:
        return {"pos": {"line": self.line, "column": self.col},
                "endPos": None if self.end_line is None else {"line": self.end_line, "column": self.end_col},
                "data": self.text}


def parse_lean_json(stdout: str) -> list[LeanMessage]:
    msgs = []
    for raw in stdout.splitlines():
        raw = raw.strip()
        if not raw.startswith("{"):
            continue
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        pos = d.get("pos") or {"line": 1, "column": 0}
        end = d.get("endPos")
        msgs.append(LeanMessage(
            severity=d.get("severity", "error"), line=int(pos.get("line", 1)), col=int(pos.get("column", 0)),
            end_line=None if not end else int(end.get("line", 1)), end_col=None if not end else int(end.get("column", 0)),
            text=str(d.get("data", "")), kind=str(d.get("kind", "")),
        ))
    return msgs


def get_error_str(code: str, errors: list[dict], error_thres: bool = True) -> str:
    """Verbatim port of Goedel-Prover-V2 get_error_str."""
    err_str = ""
    code_lines = code.split("\n")
    error_num_thres = 8 if error_thres else len(errors)
    for i, error in enumerate(errors[:error_num_thres]):
        start_line = error["pos"]["line"] - 1
        start_col = error["pos"]["column"]
        if start_line >= len(code_lines):
            start_line = len(code_lines) - 1
        if error["endPos"] is None:
            end_line = start_line
            end_col = len(code_lines[start_line])
        else:
            end_line = min(error["endPos"]["line"] - 1, len(code_lines) - 1)
            end_col = error["endPos"]["column"]
        err_str += f"\nError {i + 1}:\n"
        err_str += "\nCorresponding Code:\n```lean4\n"
        error_code = ""
        for ii in range(-4, 0):
            if start_line + ii >= 0:
                error_code += f"{code_lines[start_line + ii]}\n"
        if start_line != end_line:
            error_code += code_lines[start_line][:start_col] + "<error>" + code_lines[start_line][start_col:] + "\n"
            if not error_thres:
                for j in range(start_line + 1, end_line):
                    error_code += f"{code_lines[j]}\n"
            else:
                show_line = 6
                j = start_line
                for j in range(start_line + 1, min(end_line, start_line + show_line)):
                    error_code += f"{code_lines[j]}\n"
                if end_line > start_line + show_line:
                    leading_spaces = len(code_lines[j]) - len(code_lines[j].lstrip(" "))
                    error_code += "\n" + " " * leading_spaces + "... --[Truncated]-- ...\n"
            error_code += code_lines[end_line][:end_col] + "</error>" + code_lines[end_line][end_col:] + "\n"
        else:
            error_code += (code_lines[start_line][:start_col] + "<error>" + code_lines[start_line][start_col:end_col]
                           + "</error>" + code_lines[start_line][end_col:] + "\n")
        if end_line + 1 < len(code_lines):
            error_code += f"{code_lines[end_line + 1]}\n"
        err_str += error_code
        err_str += "\n```\n"
        err_str += f"\nError Message: {error['data']}\n"
    if len(errors) > error_num_thres:
        err_str += f"\n... [Omitted {len(errors) - error_num_thres} more errors] ...\n"
    return err_str


SORRY_RE = re.compile(r"declaration uses ['`]sorry['`]")
AXIOMS_RE = re.compile(r"'(?P<name>[^']+)' depends on axioms: \[(?P<ax>[^\]]*)\]", re.DOTALL)
NO_AXIOMS_RE = re.compile(r"'(?P<name>[^']+)' does not depend on any axioms")


@dataclass
class Verdict:
    ok: bool
    summary: str                     # plain French, one line
    errors: list[LeanMessage] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    axioms: list[str] | None = None
    has_sorry: bool = False
    feedback_en: str = ""            # what the model is told when there is no positioned error


def with_axiom_probe(code: str, name: str) -> str:
    return code.rstrip() + f"\n\n#print axioms {name}\n"


def judge(code: str, messages: list[LeanMessage], name: str | None, returncode: int | None) -> Verdict:
    """Decide acceptance. `name` given => strict proof mode (axiom probe expected)."""
    errors = [m for m in messages if m.severity == "error"]
    sorry = any(m.kind == "hasSorry" or SORRY_RE.search(m.text) for m in messages)
    reasons = forbidden_reasons(code.rsplit("#print axioms", 1)[0]) if name else []
    axioms = None
    if name:
        for m in messages:
            mm = AXIOMS_RE.search(m.text)
            if mm and mm.group("name").split(".")[-1] == name.split(".")[-1]:
                axioms = [a.strip() for a in mm.group("ax").split(",") if a.strip()]
            elif NO_AXIOMS_RE.search(m.text):
                axioms = []
    if errors:
        n = len(errors)
        msg = _("Lean a trouvé 1 erreur.") if n == 1 else _("Lean a trouvé {n} erreurs.").format(n=n)
        return Verdict(False, msg, errors, reasons, axioms, sorry)
    if returncode not in (0, None) and not errors:
        return Verdict(False, _("Lean s'est arrêté anormalement."), errors, reasons, axioms, sorry)
    if sorry:
        return Verdict(False, _("Refusé : la preuve contient « sorry »."), errors, reasons or ["« sorry »"], axioms, True,
                       "The proof uses `sorry`, which is not allowed. Give a complete proof.")
    if reasons:
        return Verdict(False, _("Refusé : {why}.").format(why=", ".join(_(r) for r in reasons)), errors, reasons, axioms, sorry,
                       "The proof uses forbidden constructs (sorry/admit/axiom/apply?/exact?/#exit/meta-programming). "
                       "Give a complete proof without them.")
    if name:
        if axioms is None:
            return Verdict(False, _("Refusé : impossible de contrôler les axiomes utilisés."), errors, reasons, axioms, sorry,
                           "The file did not finish elaborating the theorem. Give a complete, self-contained proof.")
        bad = [a for a in axioms if a not in ALLOWED_AXIOMS]
        if bad:
            return Verdict(False, _("Refusé : axiomes non standard ({ax}).").format(ax=", ".join(bad)), errors, reasons, axioms, sorry,
                           f"The proof depends on non-standard axioms ({', '.join(bad)}). Avoid native_decide and sorry.")
        return Verdict(True, _("Preuve acceptée par Lean ✔"), errors, reasons, axioms, sorry)
    return Verdict(True, _("Lean accepte le fichier ✔"), errors, reasons, axioms, sorry)


def errors_for_feedback(code: str, verdict: Verdict) -> str:
    if verdict.errors:
        return get_error_str(code, [e.as_goedel() for e in verdict.errors])
    # non-error rejection: explain in Goedel style without positions
    return "\nError 1:\n\nError Message: " + (verdict.feedback_en or "The proof was rejected.") + "\n"


# ---------------------------------------------------------------- natural language -> Lean (Goedel-Formalizer-V2)
FORMALIZE_TEMPLATE = (
    "Please autoformalize the following natural language problem statement in Lean 4. "
    "Use the following theorem name: {name}\n"
    "The natural language statement is: \n"
    "{text}"
    "Think before you provide the lean statement."
)   # verbatim from the Goedel-Formalizer-V2-8B model card (including the missing newline before « Think »)
DEFAULT_THEOREM_NAME = "mon_probleme"


def formalize_prompt(text: str, name: str = DEFAULT_THEOREM_NAME) -> str:
    return FORMALIZE_TEMPLATE.format(name=name, text=text.strip())


def normalize_formal_statement(code: str) -> str:
    """Model output -> a Lean file with the standard header and a single `theorem … := by sorry`."""
    std_open = GOEDEL_HEADER.split("open ")[1].split("\n")[0].strip()     # "BigOperators Real Nat Topology Rat"
    lines = [l for l in code.replace("\r\n", "\n").split("\n")
             if not re.match(r"\s*(import\s|set_option\s+maxHeartbeats)", l)
             and l.strip() != "open " + std_open]
    return prepare_statement("\n".join(lines).strip())


def detect_loop(text: str, min_chars: int = 600, min_reps: int = 5, max_unit: int = 1500) -> tuple[int, int] | None:
    """Detect a generation stuck repeating the same block: returns (period, repetitions) or None.

    The end of `text` must be one block repeated ≥ 5 times. The *shortest* such period decides: a short period
    (< 60 chars, e.g. a repeated tactic line like `· norm_num`) needs 1500 characters of evidence because legitimate
    proofs repeat such lines; a longer block needs 600. Inside a Lean code block (odd number of ``` fences) correct
    proofs repeat whole tactic blocks (`<;> (try norm_num) <;> …` five times in an answer Lean accepted, D22), so
    there it takes 12 repetitions and 3000 characters. Pass the whole answer so that the fences can be counted."""
    n = len(text)
    in_code = text.count("```") % 2 == 1
    for unit in range(4, min(max_unit, n // min_reps) + 1):
        if text[-unit:] != text[-2 * unit:-unit]:
            continue
        reps = 2
        while (reps + 1) * unit <= n and text[-(reps + 1) * unit:-reps * unit] == text[-unit:]:
            reps += 1
        if reps < (12 if in_code else min_reps):
            continue
        need = min_chars if unit >= 60 else 2 * min_chars + 300
        if in_code:
            need = max(need, 5 * min_chars)
        return (unit, reps) if reps * unit >= need else None
    return None


_SENT_SPLIT = re.compile(r"(?<=[.!?;:])\s+|\n+")


def _sentences(prose: str) -> list[str]:
    out = []
    for raw in _SENT_SPLIT.split(prose):
        t = re.sub(r"[\s`*_]+", " ", raw).strip().lower()
        if len(t) >= 30:
            out.append(t)
    return out


def detect_rambling(text: str, window: int = 8000, min_chars: int = 20000, min_ratio: float = 0.6,
                    min_repeats: int = 12) -> tuple[int, int] | None:
    """Detect reasoning that goes round in circles with small variations (not caught by `detect_loop`).

    Code blocks are ignored (proofs legitimately repeat tactic lines). In the last `window` characters of prose,
    count the sentences already written earlier in the answer: when at least `min_ratio` of them (and at least
    `min_repeats`) are repeats, the model is recycling its own paragraphs. Returns (repeated, total) or None.

    Calibrated on real Goedel-Prover answers (DECISIONS D22): the model sometimes circles for a while and then
    escapes with a correct proof (seen up to ≈ 16 000 characters), so nothing is judged before 20 000 characters of
    prose (≈ 6 000 tokens); a run still recycling then is stopped instead of running to the 16 384-token limit."""
    if len(text) < min_chars:
        return None
    prose = re.sub(r"```.*?(?:```|\Z)", "\n", text, flags=re.S)
    if len(prose) < min_chars:
        return None
    cut = len(prose) - window
    head, tail = prose[:cut], prose[cut:]
    tail = tail.split("\n", 1)[1] if "\n" in tail else tail          # start at a line boundary
    seen = set(_sentences(head))
    recent = _sentences(tail)
    if len(recent) < min_repeats:
        return None
    rep = sum(1 for t in recent if t in seen)
    return (rep, len(recent)) if rep >= min_repeats and rep >= min_ratio * len(recent) else None


# ---------------------------------------------------------------- Lean proof -> plain-language explanation (general model)
EXPLAIN_PROMPTS = {
    "fr": (
        "Tu es un professeur de mathématiques. Voici un théorème écrit en Lean 4, avec une preuve que Lean a vérifiée.\n"
        "Explique-le EN FRANÇAIS à un lecteur qui connaît les mathématiques mais ne connaît pas Lean :\n"
        "1. énonce d'abord le théorème en langage mathématique courant ;\n"
        "2. donne l'idée de la preuve en une ou deux phrases ;\n"
        "3. commente ensuite les étapes dans l'ordre, sous forme de liste numérotée, en disant ce que fait chaque "
        "tactique en termes mathématiques (par exemple « omega : calcul sur les entiers », « rcases : on extrait un témoin »).\n"
        "Règles : réponds uniquement en français ; paragraphes simples ; formules en LaTeX entre $...$ ; "
        "ne recopie pas le code Lean ; n'invente aucune étape qui n'est pas dans la preuve.\n"
        "{profile}{nl}"
        "\n```lean4\n{code}\n```"),
    "en": (
        "You are a mathematics teacher. Here is a theorem written in Lean 4, with a proof that Lean has verified.\n"
        "Explain it IN ENGLISH to a reader who knows mathematics but does not know Lean:\n"
        "1. first state the theorem in ordinary mathematical language;\n"
        "2. give the idea of the proof in one or two sentences;\n"
        "3. then go through the steps in order, as a numbered list, saying what each tactic does in mathematical terms "
        "(for example « omega: arithmetic on integers », « rcases: we extract a witness »).\n"
        "Rules: answer in English only; plain paragraphs; formulas in LaTeX between $...$; do not copy the Lean code; "
        "do not invent any step that is not in the proof.\n"
        "{profile}{nl}"
        "\n```lean4\n{code}\n```"),
}
EXPLAIN_PROMPT = EXPLAIN_PROMPTS["fr"]


def _body(lean_code: str) -> str:
    code = lean_code.strip()
    k = code.find("theorem")
    return code[k:] if k > 0 else code       # skip the import/open header: noise for the explanation


def explain_prompt(lean_code: str, nl_statement: str = "", lang: str = "fr", profile: str = "") -> str:
    fr = lang != "en"
    nl = ((f"\nÉnoncé d'origine, en langage naturel : {nl_statement.strip()}\n" if fr else
           f"\nOriginal statement, in natural language: {nl_statement.strip()}\n") if nl_statement.strip() else "")
    prof = ((f"Adapte-toi à ce lecteur : {profile.strip()}\n" if fr else f"Adapt to this reader: {profile.strip()}\n")
            if profile.strip() else "")
    return EXPLAIN_PROMPTS["fr" if fr else "en"].format(nl=nl, code=_body(lean_code), profile=prof)


def explain_messages(lean_code: str, nl_statement: str = "", lang: str = "fr", profile: str = "",
                     previous: str = "", request: str = "") -> list[dict]:
    """Conversation for the explanation model; a follow-up request continues from the previous explanation."""
    msgs = [{"role": "user", "content": explain_prompt(lean_code, nl_statement, lang, profile)}]
    if previous and request:
        follow = ("Réécris l'explication en tenant compte de cette demande, toujours en français : " if lang != "en" else
                  "Rewrite the explanation taking this request into account, still in English: ")
        msgs += [{"role": "assistant", "content": previous}, {"role": "user", "content": follow + request.strip()}]
    return msgs


UNDERSTAND_PROMPT = (
    "You prepare a mathematics problem for an automatic Lean 4 formalization model. That model works best on a "
    "precise, self-contained statement written in English.\n"
    "Rewrite the user's request below as ONE precise mathematical statement to prove:\n"
    "- introduce every object with its type and every hypothesis (for example « Let K be a field and V a "
    "finite-dimensional vector space over K »);\n"
    "- if the request names a known theorem or result (in any language), state that theorem in its standard "
    "textbook form, with all its hypotheses; when a name has several meanings, choose the most elementary one "
    "(the one taught first at school or university);\n"
    "- if the request names a result you do not know for sure, or is not a mathematical statement, answer "
    "exactly UNKNOWN;\n"
    "- if the request is already a precise statement, translate it into English without changing its meaning "
    "(keep its numbers, variables and formulas);\n"
    "- keep formulas in LaTeX between $...$;\n"
    "- do not prove it, do not name it, do not comment, do not add anything else.\n"
    "Answer with the statement only.\n"
    "{profile}"
    "\nUser request:\n{problem}"
)


def understand_messages(problem: str, profile: str = "") -> list[dict]:
    """Conversation for the general model: user's words -> precise, self-contained statement (given to the formalizer)."""
    prof = f"Conventions of the user (respect them): {profile.strip()}\n" if profile.strip() else ""
    return [{"role": "user", "content": UNDERSTAND_PROMPT.format(profile=prof, problem=problem.strip())}]


def is_unknown(understood: str) -> bool:
    return understood.strip().strip(".").upper() == "UNKNOWN"


def formalize_input(problem: str, profile: str = "", previous: str = "", request: str = "") -> str:
    """The « natural language statement » given to Goedel-Formalizer (whose prompt template stays verbatim)."""
    text = problem.strip()
    if profile.strip():
        text += f"\n\nConventions to respect (from the user): {profile.strip()}"
    if previous.strip() and request.strip():
        text += (f"\n\nA previous formalization was:\n```lean4\n{_body(previous)}\n```\n"
                 f"The user asks for this change: {request.strip()}\n"
                 "Give the corrected Lean 4 statement (same theorem name).\n")
    elif request.strip():
        text += f"\n\nAdditional instruction from the user: {request.strip()}\n"
    return text


REFINE_PROOF = (
    "The proof above is correct and verified by Lean. The user now asks: {request}\n"
    "Write a new complete Lean 4 proof of exactly the same theorem that satisfies this request.\n\n"
    "Before producing the Lean 4 code to formally prove the given theorem, provide a detailed proof plan outlining "
    "the main proof steps and strategies."
)


def refine_messages(statement: str, previous_proof: str, request: str) -> list[dict]:
    """Prover conversation for a follow-up on an already verified proof (same style as Goedel's correction rounds)."""
    return [{"role": "user", "content": initial_prompt(statement)},
            {"role": "assistant", "content": f"```lean4\n{previous_proof.strip()}\n```"},
            {"role": "user", "content": REFINE_PROOF.format(request=request.strip())}]


def clean_model_text(text: str) -> str:
    """Remove Qwen3 « <think> … </think> » blocks (also an unterminated one) and surrounding whitespace."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"<think>.*$", "", text, flags=re.DOTALL)
    return text.strip()


def has_real_proof(code: str) -> bool:
    """True when the text contains a theorem whose proof is not just `sorry`."""
    body = remove_comments(code)
    m = re.search(r"\btheorem\b.*?:=\s*(?:by\b)?(.*)$", body, re.DOTALL)
    if not m:
        return False
    rest = re.sub(r"\bsorry\b", "", m.group(1)).strip()
    return len(rest) > 2
