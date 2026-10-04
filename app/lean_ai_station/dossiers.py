"""Dossiers (one proof = one conversation thread with versions), the library of proven results, and the router that
decides which stage a follow-up request should redo. Pure Python, no Qt: everything here is unit-tested."""
from __future__ import annotations

import json
import re
import time
import unicodedata
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import config, leancheck

DOSSIERS_DIR = config.CONFIG_DIR / "dossiers"
LIBRARY_FILE = config.CONFIG_DIR / "library.json"


# ---------------------------------------------------------------- names
def slugify(text: str, max_len: int = 28) -> str:
    """ASCII Lean identifier from a title: « Somme de deux pairs ! » -> somme_de_deux_pairs."""
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
    if len(t) > max_len:                       # cut at a word boundary
        t = t[:max_len].rsplit("_", 1)[0] if "_" in t[:max_len] else t[:max_len]
    if not t:
        return "thm"
    return "thm_" + t if t[0].isdigit() else t


def title_from_problem(problem: str, max_len: int = 60) -> str:
    t = re.sub(r"\s+", " ", re.sub(r"\$[^$]*\$", "…", problem)).strip()
    t = re.sub(r"^(montrer|démontrer|prouver|prove|show)\s+(que|that)\s+", "", t, flags=re.I)
    t = t[:1].upper() + t[1:]
    return (t[: max_len - 1] + "…") if len(t) > max_len else (t or "Sans titre")


# ---------------------------------------------------------------- dossier
@dataclass
class Event:
    kind: str          # user | statement | proof | explanation | info | error
    text: str          # what the thread shows (plain text / Markdown)
    t: float = field(default_factory=time.time)
    ref: int | None = None            # index of the version this event produced (statements/proofs/explanations)
    stage: str = ""                   # for user events: statement | proof | explanation | auto


@dataclass
class Version:
    code: str                         # Lean code (statement/proof) or Markdown (explanation)
    t: float = field(default_factory=time.time)
    source: str = "ai"                # ai | user
    ok: bool = True                   # statement compiles / proof accepted
    parent: int | None = None         # statement index for a proof, proof index for an explanation
    note: str = ""                    # request that produced this version


@dataclass
class Dossier:
    id: str
    title: str
    problem: str = ""
    understood: str = ""              # the problem rewritten as a precise statement (« comprendre » step)
    workspace: str = ""
    theorem: str = ""                 # Lean name of the target theorem (unique per dossier)
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    events: list[Event] = field(default_factory=list)
    statements: list[Version] = field(default_factory=list)
    proofs: list[Version] = field(default_factory=list)
    explanations: list[Version] = field(default_factory=list)
    cur_statement: int = -1
    cur_proof: int = -1
    cur_explanation: int = -1
    lemmas_used: list[str] = field(default_factory=list)

    # -- current artefacts
    @property
    def statement(self) -> str:
        return self.statements[self.cur_statement].code if 0 <= self.cur_statement < len(self.statements) else ""

    @property
    def proof(self) -> str:
        return self.proofs[self.cur_proof].code if 0 <= self.cur_proof < len(self.proofs) else ""

    @property
    def explanation(self) -> str:
        if 0 <= self.cur_explanation < len(self.explanations):
            return self.explanations[self.cur_explanation].code
        return ""

    @property
    def proof_is_current(self) -> bool:
        """True when the current proof proves the current statement."""
        return bool(self.proof) and self.proofs[self.cur_proof].parent == self.cur_statement

    # -- mutations (each one is recorded in the thread)
    def touch(self):
        self.updated = time.time()

    def add_event(self, kind: str, text: str, ref: int | None = None, stage: str = "") -> Event:
        e = Event(kind, text, ref=ref, stage=stage)
        self.events.append(e)
        self.touch()
        return e

    def add_statement(self, code: str, source: str = "ai", ok: bool = True, note: str = "") -> int:
        self.statements.append(Version(code, source=source, ok=ok, note=note))
        self.cur_statement = len(self.statements) - 1
        self.touch()
        return self.cur_statement

    def add_proof(self, code: str, note: str = "") -> int:
        self.proofs.append(Version(code, parent=self.cur_statement, note=note))
        self.cur_proof = len(self.proofs) - 1
        self.touch()
        return self.cur_proof

    def add_explanation(self, text: str, note: str = "") -> int:
        self.explanations.append(Version(text, parent=self.cur_proof, note=note))
        self.cur_explanation = len(self.explanations) - 1
        self.touch()
        return self.cur_explanation

    def restore(self, kind: str, index: int):
        """« Revenir à cette version »: make an older version current again (nothing is deleted)."""
        if kind == "statement" and 0 <= index < len(self.statements):
            self.cur_statement = index
        elif kind == "proof" and 0 <= index < len(self.proofs):
            self.cur_proof = index
            self.cur_statement = self.proofs[index].parent if self.proofs[index].parent is not None else self.cur_statement
        elif kind == "explanation" and 0 <= index < len(self.explanations):
            self.cur_explanation = index
        self.touch()

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=1)

    @classmethod
    def from_dict(cls, d: dict) -> "Dossier":
        d = dict(d)
        d["events"] = [Event(**e) for e in d.get("events", [])]
        for k in ("statements", "proofs", "explanations"):
            d[k] = [Version(**v) for v in d.get(k, [])]
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


class DossierStore:
    """One JSON file per dossier (atomic writes). Deleting moves the file to .trash so it can be restored."""

    def __init__(self, root: Path | None = None):
        self.root = Path(root or DOSSIERS_DIR)
        self.trash = self.root / ".trash"

    def new(self, problem: str = "", workspace: str = "", title: str = "") -> Dossier:
        did = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        title = title or title_from_problem(problem)
        d = Dossier(id=did, title=title, problem=problem, workspace=workspace,
                    theorem=f"{slugify(title)}_{did[-4:]}")
        return d

    def save(self, d: Dossier) -> None:
        config.atomic_write_text(self.root / f"{d.id}.json", d.to_json())

    def load(self, did: str) -> Dossier | None:
        try:
            return Dossier.from_dict(json.loads((self.root / f"{did}.json").read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return None

    def list(self) -> list[Dossier]:
        """Most recently updated first; unreadable files are skipped (never crash the list)."""
        out = []
        if self.root.is_dir():
            for f in self.root.glob("*.json"):
                d = self.load(f.stem)
                if d is not None:
                    out.append(d)
        return sorted(out, key=lambda d: d.updated, reverse=True)

    def delete(self, did: str) -> bool:
        src = self.root / f"{did}.json"
        if not src.exists():
            return False
        self.trash.mkdir(parents=True, exist_ok=True)
        src.replace(self.trash / src.name)
        return True

    def undelete(self, did: str) -> bool:
        src = self.trash / f"{did}.json"
        if not src.exists():
            return False
        src.replace(self.root / src.name)
        return True


# ---------------------------------------------------------------- library of proven results
@dataclass
class LibEntry:
    name: str                 # Lean name of the main theorem
    title: str
    statement: str            # signature of the main theorem (`theorem name … := by`), for display
    code: str                 # declarations to paste above a new theorem (helpers + main theorem, no header)
    workspace: str            # results are only reused with the same Lean/Mathlib version
    dossier: str = ""
    deps: list[str] = field(default_factory=list)   # library results this proof uses (inserted first)
    t: float = field(default_factory=time.time)


_HEADER_RE = re.compile(r"^\s*(import|set_option|open)\b")


def strip_header(code: str) -> str:
    return "\n".join(l for l in code.split("\n") if not _HEADER_RE.match(l)).strip()


def main_signature(code: str, name: str) -> str:
    m = re.search(rf"\btheorem\s+{re.escape(name)}\b.*?:=", code, re.DOTALL)
    return re.sub(r"\s+", " ", m.group(0)).strip() if m else ""


_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.']*|[ℕℤℚℝℂ∣≤≥≠√∑∏]|\^|%")
_STOP = {"theorem", "by", "sorry", "fun", "have", "show", "exact", "intro", "Real", "Nat", "Int", "at", "with",
         "Type", "Prop", "true", "false", "h", "x", "y", "n", "a", "b", "c", "k", "m"}


def tokens(code: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(leancheck.remove_comments(code)) if t not in _STOP}


class Library:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or LIBRARY_FILE)
        self.entries: list[LibEntry] = []
        self.load()

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.entries = [LibEntry(**e) for e in data]
        except (OSError, ValueError, TypeError):
            self.entries = []

    def save(self):
        config.atomic_write_text(self.path, json.dumps([asdict(e) for e in self.entries], ensure_ascii=False, indent=1))

    def get(self, name: str) -> LibEntry | None:
        return next((e for e in self.entries if e.name == name), None)

    def add_proof(self, proof_code: str, title: str, workspace: str, dossier: str = "",
                  used: list[str] | None = None) -> LibEntry | None:
        """Store a Lean-accepted proof. Returns None if this exact result is already in the library.

        `used`: library results that were pasted above the theorem; their code is not duplicated, they become deps."""
        name = leancheck.theorem_name(proof_code)
        body = strip_header(proof_code)
        used = [u for u in (used or []) if self.get(u)]
        if used:
            drop: set[str] = set()
            for u in used:
                drop |= leancheck.declared_names(self.get(u).code)
            body = re.sub(r"(?m)^-- library: .*\n", "", body)
            blocks = re.split(r"(?m)^(?=(?:noncomputable\s+)?(?:theorem|lemma|def|abbrev)\s)", body)
            body = "\n".join(b.rstrip() for b in blocks
                              if b.strip() and not (leancheck.declared_names(b) & drop)).strip()
        sig = main_signature(body, name)
        for e in self.entries:
            if e.workspace == workspace and (e.name == name or e.statement == sig):
                return None
        e = LibEntry(name=name, title=title, statement=sig, code=body, workspace=workspace, dossier=dossier, deps=used)
        self.entries.append(e)
        self.save()
        return e

    def remove(self, name: str) -> LibEntry | None:
        for i, e in enumerate(self.entries):
            if e.name == name:
                self.entries.pop(i)
                self.save()
                return e
        return None

    def restore(self, e: LibEntry):
        self.entries.append(e)
        self.save()

    def relevant(self, statement: str, workspace: str, k: int = 3, min_score: float = 0.25,
                 exclude: set[str] | None = None) -> list[LibEntry]:
        """Up to k results of the same Lean version whose statement shares enough vocabulary with `statement`."""
        target = tokens(statement)
        if not target:
            return []
        scored = []
        for e in self.entries:
            if e.workspace != workspace or (exclude and e.name in exclude):
                continue
            other = tokens(e.statement)
            if not other:
                continue
            score = len(target & other) / len(target | other)
            if score >= min_score:
                scored.append((score, e))
        return [e for _s, e in sorted(scored, key=lambda x: -x[0])[:k]]


def closure(lib: "Library", entries: list[LibEntry]) -> list[LibEntry]:
    """Entries plus everything they depend on, dependencies first."""
    out: list[LibEntry] = []
    seen: set[str] = set()

    def visit(e: LibEntry, depth: int = 0):
        if e.name in seen or depth > 20:
            return
        seen.add(e.name)
        for d in e.deps:
            de = lib.get(d)
            if de:
                visit(de, depth + 1)
        out.append(e)
    for e in entries:
        visit(e)
    return out


def with_lemmas(statement: str, entries: list[LibEntry]) -> str:
    """Insert library results (with their proofs) right above the target theorem of `statement`."""
    if not entries:
        return statement
    starts = [m.start() for m in re.finditer(r"(?m)^theorem\s", statement)]
    if not starts:
        return statement
    cut = starts[-1]
    known = leancheck.declared_names(statement[:cut])
    blocks = []
    for e in entries:
        names = leancheck.declared_names(e.code)
        if names & known:
            continue                                   # already present (or a clashing helper name): skip
        known |= names
        blocks.append(f"-- library: {e.title}\n{e.code.strip()}\n")
    return statement[:cut] + "\n".join(blocks) + ("\n" if blocks else "") + statement[cut:]


# ---------------------------------------------------------------- router for follow-up requests
_EXPLAIN = r"expli|détaill|detail|clarif|pourquoi|why|comprend|understand|étape|step|reformul|rephrase|simplif.*(texte|explication)|plus clair|clearer|en anglais|in english|en français|in french|vulgaris|intuition"
_PROOF = r"preuve|proof|prouve|prove|démontr|demontr|démonstr|demonstr|réessa|reessa|essaie|essaye|retry|try again|recommence|encore|utilis|use |using|indice|hint|lemme|lemma|théorème des|theorem|tactique|tactic|plus court|shorter|plus simple|simpler|autre méthode|another method|other approach|récurrence|induction|sans |without |linarith|nlinarith|omega|ring|simp\b|norm_num"
_STATEMENT = r"énoncé|enonce|statement|hypoth|suppos|assum|ajoute|add |retire|remove|enlève|change|remplace|replace|réel|real|entier|integer|naturel|natural|positif|positive|strict|inégalité|inequality|domaine|condition|traduction|translation|n ?[><≥≤]|mauvais|wrong|incorrect|faux"


def route_scores(request: str) -> dict[str, int]:
    r = request.lower()
    return {"statement": len(re.findall(_STATEMENT, r)), "proof": len(re.findall(_PROOF, r)),
            "explanation": len(re.findall(_EXPLAIN, r))}


def route(request: str, has_proof: bool, has_explanation: bool) -> str:
    """Decide which stage a follow-up request should redo: statement | proof | explanation.

    Without a proof (the search failed), a request about the statement re-translates it; anything else — « réessaie »,
    « utilise le théorème des valeurs intermédiaires » — is a new proof search with the request given to the prover
    as a hint (before 1.1.2 it was always re-translated, so the prover never saw the user's guidance)."""
    score = route_scores(request)
    if not has_proof:
        return "statement" if score["statement"] > score["proof"] else "proof"
    if not has_explanation and score["explanation"] and not score["proof"] and not score["statement"]:
        return "explanation"
    best = max(score, key=lambda k: (score[k], {"statement": 1, "proof": 2, "explanation": 0}[k]))
    return best if score[best] else "proof"
