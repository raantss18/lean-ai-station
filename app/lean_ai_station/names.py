"""Index of the declarations available in a Lean workspace, to answer « unknown constant » errors with real names.

The prover often cites lemmas that do not exist in the workspace's Mathlib (`Polynomial.exists_root_of_odd_degree`).
Lean only says « unknown constant »; the model then guesses another name. This index lists the declarations of the
sources that are actually compiled in the workspace (Mathlib, Batteries, Aesop…, Lean's `Init`), with the first line of
their signature, so the correction feedback can name existing lemmas close to the missing one (DECISIONS D25).

Built from the sources with a light parser (namespaces + declaration keywords): fast (a few seconds), no Lean process,
cached per workspace and toolchain in ~/.cache/lean-ai-station/names/."""
from __future__ import annotations

import difflib
import hashlib
import os
import re
import threading
from pathlib import Path

from . import config

_DECL = re.compile(
    r"^(?:@\[[^\]]*\]\s*)?(?:(?:private|protected|noncomputable|nonrec|partial|unsafe)\s+)*"
    r"(theorem|lemma|def|abbrev|instance|structure|class|inductive|alias)\s+([^\s:({\[⦃⟨]+)(.*)$")
_NS = re.compile(r"^namespace\s+(\S+)")
_END = re.compile(r"^end(?:\s+(\S+))?\s*$")
_SECTION = re.compile(r"^(?:noncomputable\s+)?section(?:\s+(\S+))?\s*$")
_UNKNOWN = re.compile(r"[Uu]nknown (?:constant|identifier) [`'‘]([^`'’]+)[`'’]")   # Lean 4.9 and Lean 4.34 wordings
_BLOCK = re.compile(r"<error>\s*@?([A-Za-z_][\w.'₀-₉]*)(.*?)</error>.*?Error Message: ([^\n]*)", re.S)
_TOKEN = re.compile(r"[A-Za-z0-9]+")


def parse_source(text: str) -> list[tuple[str, str]]:
    """(full name, signature head) for the declarations of one Lean file."""
    out: list[tuple[str, str]] = []
    stack: list[tuple[str, str]] = []           # ("ns" | "sec", name)
    lines = text.split("\n")
    in_comment = 0
    for i, raw in enumerate(lines):
        line = raw.strip()
        if in_comment or line.startswith("/-"):
            in_comment += line.count("/-") - line.count("-/")
            in_comment = max(in_comment, 0)
            continue
        if not line or line.startswith("--"):
            continue
        m = _NS.match(line)
        if m:
            stack.append(("ns", m.group(1)))
            continue
        m = _SECTION.match(line)
        if m:
            stack.append(("sec", m.group(1) or ""))
            continue
        m = _END.match(line)
        if m and stack:
            # `end Foo.Bar` closes `namespace Foo.Bar`; a bare `end` closes the innermost section
            stack.pop()
            continue
        m = _DECL.match(line)
        if not m:
            continue
        kind, name, rest = m.groups()
        if kind == "instance" and (name.startswith(("(", "[", ":")) or not re.match(r"[\w.]", name)):
            continue                                   # anonymous instance
        if name.startswith("_root_."):
            full = name[len("_root_."):]
        else:
            prefix = ".".join(n for k, n in stack if k == "ns")
            full = f"{prefix}.{name}" if prefix else name
        sig = rest
        j = i + 1
        while ":=" not in sig and "where" not in sig and j < len(lines) and j < i + 4:
            sig += " " + lines[j].strip()
            j += 1
        sig = re.sub(r"\s+", " ", sig.split(":=")[0].split(" where")[0]).strip()
        out.append((full, sig[:220]))
    return out


def source_roots(ws) -> list[Path]:
    """Source directories of the modules that are compiled in this workspace (what `import Mathlib` can reach)."""
    roots: list[Path] = []
    for lib in ws.lean_path():
        pkg = next((p for p in lib.parents if p.name == ".lake"), None)
        if pkg is None:
            continue
        pkg = pkg.parent
        for d in sorted(lib.iterdir()):
            if d.is_dir() and (pkg / d.name).is_dir() and d.name not in ("Cache", "LeanSearchClient"):
                roots.append(pkg / d.name)
    if ws.sysroot and (ws.sysroot / "src" / "lean" / "Init").is_dir():
        roots.append(ws.sysroot / "src" / "lean" / "Init")
    return roots


class NameIndex:
    def __init__(self, entries: list[tuple[str, str]]):
        self.sig = dict(entries)
        self.by_last: dict[str, list[str]] = {}
        self.by_token: dict[str, set[str]] = {}
        for full in self.sig:
            self.by_last.setdefault(full.rsplit(".", 1)[-1], []).append(full)
            for t in set(_tokens(full)):
                self.by_token.setdefault(t, set()).add(full)

    def __len__(self):
        return len(self.sig)

    def __contains__(self, name: str) -> bool:
        return name in self.sig

    def suggest(self, missing: str, n: int = 5) -> list[str]:
        """Existing declarations whose names are closest to `missing`."""
        last = missing.rsplit(".", 1)[-1]
        same_last = [f for f in self.by_last.get(last, []) if f != missing]
        toks = set(_tokens(missing))
        cands: dict[str, int] = {}
        for t in toks:
            if len(self.by_token.get(t, ())) > 20000:     # « Nat », « of »… say nothing
                continue
            for f in self.by_token.get(t, ()):
                cands[f] = cands.get(f, 0) + 1
        need = 2 if len(toks) >= 3 else 1
        pool = [f for f, k in cands.items() if k >= need]
        ns = missing.rsplit(".", 1)[0] if "." in missing else ""

        def score(f: str) -> float:
            r = difflib.SequenceMatcher(None, missing.lower(), f.lower()).ratio()
            jac = len(toks & set(_tokens(f))) / max(1, len(toks | set(_tokens(f))))
            return r + jac + (0.25 if ns and f.startswith(ns + ".") else 0)
        ranked = sorted(pool, key=score, reverse=True)
        out = []
        for f in same_last + ranked:
            if f not in out and f != missing:
                out.append(f)
            if len(out) >= n:
                break
        return out

    def feedback_note(self, feedback: str, n: int = 5) -> str:
        """Extra paragraph for the correction prompt, naming existing declarations close to the unknown ones."""
        missing = missing_names(feedback, self)
        parts = []
        for name in missing[:3]:
            sugg = self.suggest(name, n)
            if not sugg:
                parts.append(f"`{name}` does not exist in this version of Mathlib, and no declaration has a close name: "
                             "prove the needed fact directly instead of citing a lemma.")
                continue
            lines = "\n".join(f"- `{s}` {self.sig[s]}".rstrip() for s in sugg)
            parts.append(f"`{name}` does not exist in this version of Mathlib. Existing declarations with close names "
                         f"(check that their statements fit before using them):\n{lines}")
        return ("\n\nNote on unknown names:\n" + "\n\n".join(parts)) if parts else ""


def missing_names(feedback: str, idx: NameIndex | None = None) -> list[str]:
    """Names that Lean rejected as non-existent in a feedback text (checked against the index when there is one)."""
    out: list[str] = []
    for name in _UNKNOWN.findall(feedback):
        if name not in out and (idx is None or name not in idx.sig):
            out.append(name)
    if idx is not None:
        # Lean 4.9 reports `Real.foo_bar x` (Real being a type) as « invalid field notation », without the name
        for name, _rest, msg in _BLOCK.findall(feedback):
            if ("invalid field notation" in msg or "unknown" in msg) and "." in name and name not in idx.sig \
                    and name not in out:
                out.append(name)
    return out


def avoid_note(bad: list[str], idx: NameIndex | None = None) -> str:
    """Reminder added to a fresh sample: names already rejected by Lean in this search (D25)."""
    if not bad:
        return ""
    items = []
    for name in bad[:8]:
        sugg = idx.suggest(name, 3) if idx is not None else []
        items.append(f"`{name}`" + (f" (existing names nearby: {', '.join(f'`{s}`' for s in sugg)})" if sugg else ""))
    return ("\n\nWarning from earlier attempts: these names do not exist in this version of Mathlib, do not use "
            "them: " + "; ".join(items) + ". If the fact you need is not in Mathlib, prove it directly.")


def _tokens(name: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(name)]


# ---------------------------------------------------------------- cache + background loading
_LOCK = threading.Lock()
_INDEXES: dict[str, NameIndex] = {}
_LOADING: set[str] = set()


def _cache_file(ws) -> Path:
    stamps = []
    for f in ("lake-manifest.json", "lean-toolchain"):
        try:
            stamps.append((ws.path / f).stat().st_mtime_ns)
        except OSError:
            stamps.append(0)
    key = hashlib.sha1(f"{ws.path}|{ws.toolchain}|{ws.lean_path()}|{stamps}".encode()).hexdigest()[:16]
    return config.CACHE_DIR / "names" / f"{ws.key.replace('/', '_').replace(':', '_')}-{key}.tsv"


def build(ws) -> NameIndex:
    f = _cache_file(ws)
    if f.exists():
        entries = [tuple(l.split("\t", 1)) for l in f.read_text(encoding="utf-8").splitlines() if "\t" in l]
        return NameIndex(entries)
    entries: list[tuple[str, str]] = []
    for root in source_roots(ws):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in (".lake", "test", "tests")]
            for fn in filenames:
                if fn.endswith(".lean"):
                    try:
                        entries += parse_source((Path(dirpath) / fn).read_text(encoding="utf-8", errors="replace"))
                    except OSError:
                        pass
    f.parent.mkdir(parents=True, exist_ok=True)
    config.atomic_write_text(f, "\n".join(f"{n}\t{s.replace(chr(9), ' ')}" for n, s in entries) + "\n")
    return NameIndex(entries)


def get(ws, wait: bool = False) -> NameIndex | None:
    """The workspace's index if ready; otherwise start building it in the background (or build now if `wait`)."""
    k = str(ws.path)
    with _LOCK:
        if k in _INDEXES:
            return _INDEXES[k]
        if not wait and k in _LOADING:
            return None
        if not wait:
            _LOADING.add(k)
            threading.Thread(target=_load, args=(ws, k), daemon=True).start()
            return None
    _load(ws, k)
    return _INDEXES.get(k)


def _load(ws, k: str) -> None:
    try:
        idx = build(ws)
    except Exception:  # noqa: BLE001 (a missing index only means no suggestions)
        idx = None
    with _LOCK:
        _LOADING.discard(k)
        if idx is not None:
            _INDEXES[k] = idx
