#!/usr/bin/env python3
"""Check and install updates of Lean/Mathlib (lean-current) and of the Goedel models.

usage: updater.py check
       updater.py install-mathlib TAG
       updater.py install-model REPO FILE SHA256 ROLE

Every install builds the new version next to the old one, verifies it, switches, and only then deletes the
obsolete version (directory, Lean toolchain no longer used anywhere in the home folder, model files).
If anything fails before the switch, the old version is untouched.

Progress protocol (read by the GUI): `STEP <key> [detail]`, then `DONE <json>` or `FAIL <key> <detail>`."""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from lean_ai_station import config, updates  # noqa: E402
from lean_ai_station.workspaces import Workspace  # noqa: E402

ELAN = config.ELAN_BIN
ENV = {**os.environ, "PATH": f"{ELAN}:{os.environ.get('PATH', '')}"}
SKIP_DIRS = {".git", ".lake", ".cache", ".elan", ".local", "node_modules", ".npm", ".cargo", ".rustup", ".venv",
             "venv", "__pycache__", "models"}
TEST_LEAN = "import Mathlib\n\nexample (a b : ℕ) (ha : Even a) (hb : Even b) : Even (a + b) := Even.add ha hb\n"


class Fail(Exception):
    def __init__(self, key: str, detail: str = ""):
        super().__init__(detail)
        self.key, self.detail = key, detail


def step(key: str, detail: str = "") -> None:
    print(f"STEP {key} {detail}".rstrip(), flush=True)


def run(cmd: list[str], key: str, cwd: Path | None = None, tries: int = 1, timeout: int = 7200) -> str:
    out = ""
    for i in range(tries):
        r = subprocess.run(cmd, cwd=cwd, env=ENV, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout + r.stderr)[-4000:]
        if r.returncode == 0:
            return out
        time.sleep(min(30, 5 * (i + 1)))
    raise Fail(key, out.strip()[-1500:])


def free_gb(p: Path) -> float:
    while not p.exists():
        p = p.parent
    return shutil.disk_usage(p).free / 1e9


def toolchain_users(toolchain: str, ignore: list[Path], home: Path = Path.home(), max_depth: int = 7) -> list[Path]:
    """Every `lean-toolchain` file in the home folder that pins `toolchain` (outside `ignore`)."""
    users, ign = [], [p.resolve() for p in ignore]
    base = len(home.parts)
    for dirpath, dirnames, filenames in os.walk(home):
        d = Path(dirpath)
        if len(d.parts) - base >= max_depth:
            dirnames[:] = []
        dirnames[:] = [n for n in dirnames if n not in SKIP_DIRS and not (n.startswith(".") and len(d.parts) == base)]
        if "lean-toolchain" in filenames:
            if any(d.resolve() == i or i in d.resolve().parents for i in ign):
                continue
            try:
                if (d / "lean-toolchain").read_text().strip() == toolchain:
                    users.append(d)
            except OSError:
                pass
    return users


def elan_default() -> str:
    try:
        m = re.search(r'default_toolchain\s*=\s*"([^"]+)"', (Path.home() / ".elan" / "settings.toml").read_text())
        return m.group(1) if m else ""
    except OSError:
        return ""


# ---------------------------------------------------------------- Lean + Mathlib
def install_mathlib(tag: str, ws_root: Path = config.WORKSPACES_DIR) -> dict:
    cur = ws_root / "lean-current"
    nxt, old = ws_root / "lean-current.next", ws_root / "lean-current.old"
    if not re.fullmatch(r"v4\.\d+\.\d+", tag):
        raise Fail("bad_tag", tag)
    if free_gb(ws_root) < 15:
        raise Fail("disk", f"{free_gb(ws_root):.0f} GB")
    old_tc = (cur / "lean-toolchain").read_text().strip() if (cur / "lean-toolchain").exists() else ""
    old_rev = updates.installed_mathlib(cur) or "?"
    try:
        tc = _build_next(tag, cur, nxt)
    except BaseException:
        shutil.rmtree(nxt, ignore_errors=True)      # failed before the switch: old version untouched
        raise
    step("switch")
    shutil.rmtree(old, ignore_errors=True)
    if cur.exists():
        cur.rename(old)
    nxt.rename(cur)
    step("cleanup")
    shutil.rmtree(old, ignore_errors=True)
    removed = [f"Mathlib {old_rev}"]
    if old_tc and old_tc != tc and old_tc != elan_default():
        users = toolchain_users(old_tc, ignore=[cur])
        if users:
            print("KEEP toolchain", old_tc, "used by", ", ".join(config.tilde(u) for u in users[:5]), flush=True)
        else:
            try:
                run([str(ELAN / "elan"), "toolchain", "uninstall", old_tc], "cleanup")
                removed.append("Lean " + old_tc.split(":")[-1])
            except Fail:
                pass
    return {"kind": "mathlib", "tag": tag, "toolchain": tc, "removed": removed}


def _build_next(tag: str, cur: Path, nxt: Path) -> str:
    step("toolchain", tag)
    url = f"https://raw.githubusercontent.com/leanprover-community/mathlib4/{tag}/lean-toolchain"
    with urllib.request.urlopen(urllib.request.Request(url, headers=updates.UA), timeout=30) as r:
        tc = r.read().decode().strip()
    if not re.fullmatch(r"leanprover/lean4:v[\w.\-]+", tc):
        raise Fail("toolchain", tc)
    shutil.rmtree(nxt, ignore_errors=True)
    nxt.mkdir(parents=True)
    (nxt / "lean-toolchain").write_text(tc + "\n")
    lakefile = (cur / "lakefile.toml").read_text() if (cur / "lakefile.toml").exists() else (
        'name = "LeanCurrent"\ndefaultTargets = ["LeanCurrent"]\n\n[[require]]\nname = "mathlib"\n'
        f'git = "{updates.MATHLIB_GIT}"\nrev = "v0"\n\n[[lean_lib]]\nname = "LeanCurrent"\n')
    (nxt / "lakefile.toml").write_text(re.sub(r'(?m)^(\s*rev\s*=\s*)"[^"]*"', rf'\1"{tag}"', lakefile))
    (nxt / "LeanCurrent.lean").write_text("import Mathlib\n")
    if not (Path.home() / ".elan" / "toolchains" / tc.replace("/", "--").replace(":", "---")).is_dir():
        run([str(ELAN / "elan"), "toolchain", "install", tc], "toolchain", tries=3)
    step("deps")
    run([str(ELAN / "lake"), "update"], "deps", cwd=nxt, tries=3)
    step("cache")
    run([str(ELAN / "lake"), "exe", "cache", "get"], "cache", cwd=nxt, tries=3)
    step("build")
    run([str(ELAN / "lake"), "build"], "build", cwd=nxt)
    step("verify")
    verify_workspace(nxt, tc)
    return tc


def verify_workspace(path: Path, tc: str) -> None:
    ws = Workspace("verify", "verify", path, tc, False)
    if not ws.lean_bin or not ws.lean_bin.exists() or not ws.has_mathlib():
        raise Fail("verify", "Mathlib build missing")
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "Check.lean"
        f.write_text(TEST_LEAN)
        r = subprocess.run([str(ws.lean_bin), str(f)], env=ws.env(), capture_output=True, text=True, timeout=600)
    if r.returncode != 0 or "error" in (r.stdout + r.stderr):
        raise Fail("verify", (r.stdout + r.stderr)[-1500:])


# ---------------------------------------------------------------- models
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def smoke_test(model: Path) -> None:
    """Load the model on the processor with the bundled llama-server and generate a few tokens."""
    exe = config.LLAMA_BIN_DIR / "llama-server"
    if not exe.exists():
        raise Fail("smoke", "llama-server missing")
    port = free_port()
    log = tempfile.TemporaryFile(mode="w+")
    p = subprocess.Popen([str(exe), "-m", str(model), "-ngl", "0", "-c", "1024", "--port", str(port),
                          "--host", "127.0.0.1", "-np", "1"], stdout=subprocess.DEVNULL, stderr=log, text=True)
    try:
        deadline = time.time() + 300
        while time.time() < deadline:
            if p.poll() is not None:
                log.seek(0)
                raise Fail("smoke", log.read()[-1500:])
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
                    if r.status == 200:
                        break
            except Exception:  # noqa: BLE001 (still loading)
                time.sleep(2)
        else:
            raise Fail("smoke", "timeout")
        body = json.dumps({"prompt": "theorem one_add_one : 1 + 1 = 2 := by", "n_predict": 8}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{port}/completion", body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            out = json.loads(r.read())
        if not out.get("content", "").strip():
            raise Fail("smoke", "empty answer")
    finally:
        p.terminate()
        try:
            p.wait(20)
        except subprocess.TimeoutExpired:
            p.kill()
        log.close()


def install_model(repo: str, file: str, sha: str, role: str, models_dir: Path = config.MODELS_DIR) -> dict:
    if not updates.FILE_RE.match(file) or not re.fullmatch(r"[0-9a-f]{64}", sha) or role not in updates.FAMILIES:
        raise Fail("bad_model", file)
    if free_gb(models_dir) < 8:
        raise Fail("disk", f"{free_gb(models_dir):.0f} GB")
    stage = models_dir / ".update"
    stage.mkdir(parents=True, exist_ok=True)
    tmp = stage / file
    for f in (tmp, tmp.with_name(file + ".sha256")):
        f.unlink(missing_ok=True)
    step("download", file)
    url = f"https://huggingface.co/{repo}/resolve/main/{file}"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "pdownload.py"), url, str(tmp), sha, "--workers", "12"])
    if r.returncode != 0:
        raise Fail("download", f"rc={r.returncode}")
    step("smoke")
    smoke_test(tmp)
    step("switch")
    dest = models_dir / file
    os.replace(tmp, dest)
    os.replace(tmp.with_name(file + ".sha256"), dest.with_name(file + ".sha256"))
    step("cleanup")
    fam = updates.FAMILIES[role]
    removed = []
    for p in models_dir.glob(f"Goedel-{fam}-V*-8B.*.gguf"):
        m = updates.FILE_RE.match(p.name)
        if m and p.name != file and updates.vtuple(m.group(2)) < updates.vtuple(updates.FILE_RE.match(file).group(2)):
            p.unlink(missing_ok=True)
            p.with_name(p.name + ".sha256").unlink(missing_ok=True)
            removed.append(p.name)
    shutil.rmtree(stage, ignore_errors=True)
    return {"kind": "model", "role": role, "path": str(dest), "removed": removed}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    try:
        if argv[0] == "check":
            state = updates.check_all()
            print(json.dumps(state, indent=1, ensure_ascii=False))
            return 0
        if argv[0] == "install-mathlib" and len(argv) == 2:
            res = install_mathlib(argv[1])
        elif argv[0] == "install-model" and len(argv) == 5:
            res = install_model(*argv[1:])
        else:
            print(__doc__)
            return 2
    except Fail as e:
        print(f"FAIL {e.key} {e.detail}".rstrip(), flush=True)
        return 1
    except Exception as e:  # noqa: BLE001 (network or disk: report as a failure, old version untouched)
        print(f"FAIL error {type(e).__name__}: {e}", flush=True)
        return 1
    print("DONE " + json.dumps(res, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
