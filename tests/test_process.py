"""Process-level robustness: double launch, kill -9 of the GUI (power-loss simulation), orphan cleanup."""
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "bin" / "lean-ai-station"


def _env(tmp_path, **extra):
    e = dict(os.environ)
    e.update({"LAS_CONFIG_DIR": str(tmp_path / "cfg"), "LAS_CACHE_DIR": str(tmp_path / "cache"),
              "QT_QPA_PLATFORM": "offscreen", "LAS_STARTUP_PROBE": "stay"})
    e.update(extra)
    return e


def _start(env):
    p = subprocess.Popen([str(LAUNCHER)], env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    t0 = time.time()
    for line in p.stdout:
        if line.startswith("STARTUP_SECONDS"):
            return p
        if time.time() - t0 > 60:
            break
    p.kill()
    raise AssertionError("app did not start")


def _children(pid):
    out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout.split()
    return [int(x) for x in out]


def test_double_launch_forwards_and_exits(tmp_path):
    env = _env(tmp_path, LAS_NO_AUTOLOAD="1")
    p1 = _start(env)
    try:
        t0 = time.time()
        r = subprocess.run([str(LAUNCHER)], env=env, capture_output=True, text=True, timeout=30)
        assert r.returncode == 0 and "déjà ouvert" in r.stdout
        assert time.time() - t0 < 10
        assert p1.poll() is None            # first instance untouched
    finally:
        p1.send_signal(signal.SIGTERM)
        p1.wait(15)
    assert p1.returncode == 0               # SIGTERM -> clean shutdown


def test_kill9_gui_recovers_cleanly(tmp_path):
    env = _env(tmp_path, LAS_NO_AUTOLOAD="1")
    cfg = tmp_path / "cfg"
    p1 = _start(env)
    session = cfg / "session.json"
    time.sleep(1.5)
    p1.send_signal(signal.SIGKILL)          # power-loss simulation
    p1.wait(10)
    assert (cfg / "running.lock").exists()  # stale lock left behind
    # relaunch: must start fine, detect the crash and keep working
    p2 = _start(env)
    try:
        assert p2.poll() is None
        lock = (cfg / "running.lock").read_text().split()[0]
        assert int(lock) != p1.pid
    finally:
        p2.send_signal(signal.SIGTERM)
        p2.wait(15)
    assert not (cfg / "running.lock").exists()
    if session.exists():
        import json
        json.loads(session.read_text())     # never half-written


@pytest.mark.slow
def test_kill9_gui_kills_llama_server(tmp_path):
    from lean_ai_station import config
    model = Path(os.environ.get("LAS_TEST_MODEL", str(config.MODELS_DIR / config.DEFAULT_MODEL_NAME)))
    if not model.exists():
        pytest.skip("model missing")
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    import json
    (cfg / "settings.json").write_text(json.dumps({"wizard_done": True, "model_path": str(model),
                                                   "server": {"port": 8791}}))
    p = _start(_env(tmp_path, LAS_NO_AUTOLOAD="0"))
    try:
        srv = None
        for _ in range(240):
            for c in _children(p.pid):
                if "llama-server" in Path(f"/proc/{c}/cmdline").read_bytes().decode(errors="replace"):
                    srv = c
            if srv and (cfg / "llama-server.pid").exists():
                break
            time.sleep(0.5)
        assert srv, "llama-server not started by the GUI"
    finally:
        p.send_signal(signal.SIGKILL)
        p.wait(10)
    for _ in range(50):
        if not Path(f"/proc/{srv}").exists():
            break
        time.sleep(0.1)
    assert not Path(f"/proc/{srv}").exists(), "orphan llama-server survived GUI kill -9"
