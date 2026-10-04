"""Lean workspaces: discovery and offline environment computation.

The compile environment is computed directly from the project layout (toolchain sysroot +
`.lake/**/build/lib/lean`), exactly what `lake env` would export, so checking a proof never runs
`lake` (no network, no rebuild, no writes into user projects)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .i18n import _

SCAN_ROOTS = [Path.home() / "GitHub", Path.home() / "Documents", Path.home() / "lean", Path.home() / "Lean",
              Path.home() / "Projects", Path.home() / "projets"]


@dataclass
class Workspace:
    key: str                 # stable identifier stored in settings
    label: str               # French display name
    path: Path
    toolchain: str
    readonly: bool           # user project: never build/modify
    description: str = ""
    problems: list[str] = field(default_factory=list)

    @property
    def sysroot(self) -> Path | None:
        return toolchain_dir(self.toolchain)

    @property
    def lean_bin(self) -> Path | None:
        s = self.sysroot
        return s / "bin" / "lean" if s else None

    def lean_path(self) -> list[Path]:
        roots = [self.path]
        pk = self.path / ".lake" / "packages"
        if pk.is_dir():
            roots += sorted(p for p in pk.iterdir() if p.is_dir())
        out = []
        for r in roots:
            # Lake ≥ 4.10 stores oleans in .lake/build/lib/lean; older Lake (Lean 4.9) in .lake/build/lib
            new, old = r / ".lake" / "build" / "lib" / "lean", r / ".lake" / "build" / "lib"
            if new.is_dir():
                out.append(new)
            elif old.is_dir():
                out.append(old)
        return out

    def env(self) -> dict[str, str]:
        e = {k: v for k, v in os.environ.items() if not k.startswith(("LEAN", "LAKE", "ELAN"))}
        s = self.sysroot
        if s:
            e["LEAN_SYSROOT"] = str(s)
            e["LD_LIBRARY_PATH"] = f"{s / 'lib'}:{s / 'lib' / 'lean'}"
        e["LEAN_PATH"] = ":".join(str(p) for p in self.lean_path())
        e["LEAN_NUM_THREADS"] = e.get("LEAN_NUM_THREADS", "4")
        return e

    def has_mathlib(self) -> bool:
        return any((p / "Mathlib.olean").exists() for p in self.lean_path())

    def check(self) -> list[str]:
        """Plain-French problems; empty list = ready."""
        problems = []
        if not self.path.is_dir():
            self.problems = [_("Le dossier de l'espace de travail est introuvable (pas encore installé ?).")]
            return self.problems
        if not self.lean_bin or not self.lean_bin.exists():
            problems.append(_("La version de Lean « {tc} » n'est pas installée.").format(tc=self.toolchain))
        if not self.has_mathlib():
            problems.append(_("Mathlib n'est pas compilé dans cet espace."))
        self.problems = problems
        return problems


def toolchain_dir(toolchain: str) -> Path | None:
    tc = toolchain.strip()
    if not tc:
        return None
    root = Path.home() / ".elan" / "toolchains"
    if ":" in tc:
        d = root / tc.replace("/", "--").replace(":", "---")
        return d if d.is_dir() else None
    # channel alias like "stable": resolve through elan's settings is not offline-safe; pick newest stable dir
    if tc in ("stable", "leanprover/lean4:stable"):
        cands = sorted((p for p in root.glob("leanprover--lean4---v*") if "rc" not in p.name), key=_vkey)
        return cands[-1] if cands else None
    d = root / tc
    return d if d.is_dir() else None


def _vkey(p: Path):
    return [int(x) for x in re.findall(r"\d+", p.name)[:3]]


def _read_toolchain(d: Path) -> str:
    try:
        return (d / "lean-toolchain").read_text().strip()
    except OSError:
        return ""


def builtin_workspaces() -> list[Workspace]:
    out = []
    p49 = config.WORKSPACES_DIR / "lean-prover49"
    out.append(Workspace("lean-prover49", "Prouveur (Lean 4.9, Mathlib de Goedel)", p49, _read_toolchain(p49), False,
                         "Version exacte utilisée pour entraîner Goedel-Prover-V2 : meilleurs résultats du modèle."))
    cur = config.WORKSPACES_DIR / "lean-current"
    out.append(Workspace("lean-current", "Lean actuel (Mathlib récent)", cur, _read_toolchain(cur), False,
                         "Lean et Mathlib récents, pour le travail quotidien et les cours."))
    return out


def discover_user_workspaces(max_depth: int = 5) -> list[Workspace]:
    """Find existing Lake projects with a built Mathlib (read-only use)."""
    found: list[Workspace] = []
    own = config.WORKSPACES_DIR.resolve()
    for root in SCAN_ROOTS:
        if not root.is_dir():
            continue
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            d = Path(dirpath)
            if len(d.parts) - base_depth >= max_depth:
                dirnames[:] = []
            dirnames[:] = [n for n in dirnames if not n.startswith(".") and n not in ("node_modules", "build")]
            if "lean-toolchain" in filenames and ("lakefile.lean" in filenames or "lakefile.toml" in filenames):
                dirnames[:] = []
                if own in d.resolve().parents or d.resolve() == own:
                    continue
                ws = Workspace("user:" + str(d), f"{d.name} (projet existant, lecture seule)", d,
                               _read_toolchain(d), True, str(d))
                if (d / ".lake" / "packages" / "mathlib").is_dir() and ws.has_mathlib():
                    found.append(ws)
    return found


def all_workspaces(scan_user: bool = True) -> list[Workspace]:
    ws = builtin_workspaces()
    if scan_user:
        ws += discover_user_workspaces()
    for w in ws:
        w.check()
    return ws
