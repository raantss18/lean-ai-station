"""Pure logic for the prove loop: Goedel-Prover-V2 prompts, code extraction, statement
pinning and the acceptance verdict. Prompt texts and error formatting are verbatim ports of
Goedel-Prover-V2 `src/utils.py` (DeepSeekCoTHandler, get_error_str)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

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
        raise StatementError("L'éditeur est vide : écrivez un énoncé ou choisissez un exemple.")
    if not re.search(r"(?m)^\s*import\s", src):
        src = GOEDEL_HEADER + src
    # lemma/example -> theorem (same meaning; Goedel's pipeline matches `theorem`)
    m = re.search(r"(?m)^(\s*(?:@\[[^\]]*\]\s*)?(?:private\s+|protected\s+)?)(theorem|lemma|example)\b", src)
    if not m:
        raise StatementError("Aucun « theorem », « lemma » ou « example » trouvé dans l'éditeur.")
    kw = m.group(2)
    if kw == "lemma":
        src = src[: m.start(2)] + "theorem" + src[m.end(2):]
    elif kw == "example":
        src = src[: m.start(2)] + "theorem exercice" + src[m.end(2):]
    head, sep, _rest = src.partition(":= by")
    if not sep:
        head, sep, _rest = src.partition(":=")
        if not sep:
            raise StatementError("L'énoncé doit se terminer par « := by sorry ».")
    return head.rstrip() + " := by sorry\n"


def theorem_name(statement: str) -> str:
    m = re.search(r"\btheorem\s+([^\s:({\[⦃]+)", remove_comments(statement))
    if not m:
        raise StatementError("Impossible de trouver le nom du théorème.")
    return m.group(1)


def initial_prompt(statement: str) -> str:
    formal = statement.split(":= by")[0] + ":= by sorry"
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
    m_stmt = re.search(r"(?:^|\s)theorem\s.*?:=\s*by\s*sorry", stmt, re.DOTALL)
    if not m_stmt:
        raise StatementError("Énoncé mal formé (il faut « theorem … := by sorry »).")
    stmt_theorem_start = m_stmt.start() if stmt[m_stmt.start()] not in " \n\t" else m_stmt.start() + 1
    prefix, signature = stmt[:stmt_theorem_start], stmt[stmt_theorem_start: m_stmt.end()]
    signature = signature[: signature.rfind("sorry")]

    code = remove_comments(model_code)
    m_code = re.search(r"(?:^|\s)theorem\s+.*?:=\s*by", code, re.DOTALL)
    if not m_code:
        raise StatementError("La réponse du modèle ne contient pas de « theorem … := by ».")
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
        return Verdict(False, f"Lean a trouvé {n} erreur{'s' if n > 1 else ''}.", errors, reasons, axioms, sorry)
    if returncode not in (0, None) and not errors:
        return Verdict(False, "Lean s'est arrêté anormalement.", errors, reasons, axioms, sorry)
    if sorry:
        return Verdict(False, "Refusé : la preuve contient « sorry ».", errors, reasons or ["« sorry »"], axioms, True,
                       "The proof uses `sorry`, which is not allowed. Give a complete proof.")
    if reasons:
        return Verdict(False, "Refusé : " + ", ".join(reasons) + ".", errors, reasons, axioms, sorry,
                       "The proof uses forbidden constructs (sorry/admit/axiom/apply?/exact?/#exit/meta-programming). "
                       "Give a complete proof without them.")
    if name:
        if axioms is None:
            return Verdict(False, "Refusé : impossible de contrôler les axiomes utilisés.", errors, reasons, axioms, sorry,
                           "The file did not finish elaborating the theorem. Give a complete, self-contained proof.")
        bad = [a for a in axioms if a not in ALLOWED_AXIOMS]
        if bad:
            return Verdict(False, "Refusé : axiomes non standard (" + ", ".join(bad) + ").", errors, reasons, axioms, sorry,
                           f"The proof depends on non-standard axioms ({', '.join(bad)}). Avoid native_decide and sorry.")
        return Verdict(True, "Preuve acceptée par Lean ✔", errors, reasons, axioms, sorry)
    return Verdict(True, "Lean accepte le fichier ✔", errors, reasons, axioms, sorry)


def errors_for_feedback(code: str, verdict: Verdict) -> str:
    if verdict.errors:
        return get_error_str(code, [e.as_goedel() for e in verdict.errors])
    # non-error rejection: explain in Goedel style without positions
    return "\nError 1:\n\nError Message: " + (verdict.feedback_en or "The proof was rejected.") + "\n"
