"""Update checks for Lean/Mathlib (lean-current workspace) and the Goedel models.

Only two kinds of hosts are contacted, and only when the user allowed it (weekly check, on by default,
or the « Vérifier maintenant » button): GitHub (Mathlib tags, through `git ls-remote`) and Hugging Face
(model listings). Nothing is sent besides the HTTP request itself.

The pure functions (`newest_stable`, `model_candidates`, ...) are unit-tested on recorded data; the
network functions are thin wrappers around them. Installing is done by `scripts/updater.py`, which
verifies the new version before switching and only then deletes the obsolete one."""
from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import config

MATHLIB_GIT = "https://github.com/leanprover-community/mathlib4"
HF_API = "https://huggingface.co/api"
GOEDEL_ORG = "Goedel-LM"
GGUF_ORG = "mradermacher"          # community GGUF quantisations already used by the installer
QUANT = "Q4_K_M"
CHECK_EVERY_S = 7 * 24 * 3600
UA = {"User-Agent": "lean-ai-station-update-check"}
STATE_FILE = config.CONFIG_DIR / "updates.json"

# roles we keep up to date, and how their files are named (8B only: fits the 8 GB card)
FAMILIES = {"prover": "Prover", "formalizer": "Formalizer"}
OFFICIAL_RE = re.compile(r"^Goedel-(Prover|Formalizer)-V(\d+(?:\.\d+)?)-8B$")
FILE_RE = re.compile(r"^Goedel-(Prover|Formalizer)-V(\d+(?:\.\d+)?)-8B\.(Q\d\w*)\.gguf$")


@dataclass
class Candidate:
    kind: str                  # "mathlib" | "model"
    key: str                   # stable id, e.g. "mathlib", "prover", "formalizer"
    title: str                 # short display name of the new version
    current: str
    latest: str
    installable: bool = True
    note: str = ""             # why it cannot be installed yet, or what changes
    size: int = 0              # bytes to download (models), 0 = unknown
    spec: dict = field(default_factory=dict)   # arguments for scripts/updater.py

    def args(self) -> list[str]:
        if self.kind == "mathlib":
            return ["install-mathlib", self.spec["tag"]]
        return ["install-model", self.spec["repo"], self.spec["file"], self.spec["sha256"], self.key]


# ---------------------------------------------------------------- versions
def vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v))


def newest_stable(tags: list[str]) -> str | None:
    """Newest `v4.N.M` tag, release candidates and other tags excluded."""
    stable = [t for t in tags if re.fullmatch(r"v4\.\d+\.\d+", t)]
    return max(stable, key=vtuple) if stable else None


def installed_mathlib(ws_dir: Path | None = None) -> str | None:
    ws_dir = ws_dir or config.WORKSPACES_DIR / "lean-current"
    try:
        m = re.search(r'^\s*rev\s*=\s*"([^"]+)"', (ws_dir / "lakefile.toml").read_text(), re.M)
    except OSError:
        return None
    return m.group(1) if m else None


def mathlib_candidate(tags: list[str], current: str | None) -> Candidate | None:
    new = newest_stable(tags)
    if not new or not current or not re.fullmatch(r"v4\.\d+\.\d+", current) or vtuple(new) <= vtuple(current):
        return None
    return Candidate("mathlib", "mathlib", f"Lean + Mathlib {new}", current, new,
                     spec={"tag": new})


# ---------------------------------------------------------------- models
def installed_models(models_dir: Path | None = None) -> dict[str, dict]:
    """role -> {version, file, sha256} of the installed Goedel model (Q4_K_M preferred)."""
    models_dir = models_dir or config.MODELS_DIR
    out: dict[str, dict] = {}
    for p in sorted(models_dir.glob("Goedel-*-8B.*.gguf")):
        m = FILE_RE.match(p.name)
        if not m:
            continue
        role = "prover" if m.group(1) == "Prover" else "formalizer"
        try:
            sha = p.with_name(p.name + ".sha256").read_text().split()[0]
        except (OSError, IndexError):
            sha = ""
        cur = out.get(role)
        rank = (vtuple(m.group(2)), m.group(3) == QUANT)
        if cur is None or rank > (vtuple(cur["version"]), cur["quant"] == QUANT):
            out[role] = {"version": m.group(2), "file": p.name, "sha256": sha, "quant": m.group(3)}
    return out


def gguf_file(tree: list[dict], name: str) -> dict | None:
    """Entry for `name` in a Hugging Face tree listing, with its LFS sha256 and size."""
    for e in tree or []:
        if e.get("path") == name and e.get("lfs"):
            return {"sha256": e["lfs"].get("oid", ""), "size": int(e["lfs"].get("size") or e.get("size") or 0)}
    return None


def model_candidates(official: list[str], installed: dict[str, dict], tree_of) -> list[Candidate]:
    """`official`: model ids published by Goedel-LM; `tree_of(repo)`: HF tree listing or None (missing repo)."""
    out = []
    for role, fam in FAMILIES.items():
        inst = installed.get(role)
        if not inst:
            continue
        newer = []
        for mid in official:
            m = OFFICIAL_RE.match(mid.split("/")[-1])
            if m and m.group(1) == fam and vtuple(m.group(2)) > vtuple(inst["version"]):
                newer.append((vtuple(m.group(2)), m.group(2), mid.split("/")[-1]))
        if newer:
            _v, ver, name = max(newer)
            repo, file = f"{GGUF_ORG}/{name}-GGUF", f"{name}.{QUANT}.gguf"
            info = gguf_file(tree_of(repo), file)
            c = Candidate("model", role, name, f"V{inst['version']}", f"V{ver}", installable=info is not None,
                          note="" if info else "gguf_pending", size=info["size"] if info else 0,
                          spec={"repo": repo, "file": file, "sha256": info["sha256"] if info else ""})
            out.append(c)
            continue
        # same version: has the installed file been re-published (new quantisation fixes)?
        name = inst["file"].rsplit(".", 2)[0]
        repo = f"{GGUF_ORG}/{name}-GGUF"
        info = gguf_file(tree_of(repo), inst["file"])
        if info and inst["sha256"] and info["sha256"] != inst["sha256"]:
            out.append(Candidate("model", role, name, inst["sha256"][:8], info["sha256"][:8], note="revision",
                                 size=info["size"], spec={"repo": repo, "file": inst["file"], "sha256": info["sha256"]}))
    return out


# ---------------------------------------------------------------- network (only called when allowed)
def _get_json(url: str, timeout: float = 20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_mathlib_tags(timeout: float = 30) -> list[str]:
    r = subprocess.run(["git", "ls-remote", "--tags", MATHLIB_GIT, "v4.*"], capture_output=True, text=True,
                       timeout=timeout, env={"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin"})
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "git ls-remote failed")
    return [ln.split("refs/tags/")[-1] for ln in r.stdout.splitlines() if "refs/tags/" in ln and "^{}" not in ln]


def fetch_official_models() -> list[str]:
    return [m["id"] for m in _get_json(f"{HF_API}/models?author={GOEDEL_ORG}&limit=200")]


def fetch_tree(repo: str):
    try:
        return _get_json(f"{HF_API}/models/{repo}/tree/main")
    except urllib.error.HTTPError as e:
        if e.code in (401, 404):
            return None
        raise


def check_all() -> dict:
    """Run every check. Returns {"t", "candidates": [...], "errors": [...]} (also saved as the last state)."""
    cands, errors = [], []
    try:
        c = mathlib_candidate(fetch_mathlib_tags(), installed_mathlib())
        if c:
            cands.append(c)
    except Exception as e:  # noqa: BLE001 (network: report, never crash)
        errors.append(f"Mathlib: {e}")
    try:
        cands += model_candidates(fetch_official_models(), installed_models(), fetch_tree)
    except Exception as e:  # noqa: BLE001
        errors.append(f"Hugging Face: {e}")
    state = {"t": time.time(), "candidates": [asdict(c) for c in cands], "errors": errors}
    save_state(state)
    return state


# ---------------------------------------------------------------- persisted state
def load_state() -> dict:
    return config.read_json(STATE_FILE)[0]


def save_state(state: dict) -> None:
    config.atomic_write_text(STATE_FILE, json.dumps(state, indent=1, ensure_ascii=False))


def candidates_from(state: dict) -> list[Candidate]:
    out = []
    for d in state.get("candidates", []):
        try:
            out.append(Candidate(**d))
        except TypeError:
            pass
    return out


def due(state: dict, now: float | None = None) -> bool:
    return (now or time.time()) - float(state.get("t") or 0) >= CHECK_EVERY_S
