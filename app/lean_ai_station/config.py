"""Paths and persistent settings (atomic JSON in ~/.config/lean-ai-station)."""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

HOME = Path.home()
STATION_DIR = Path(os.environ.get("LAS_STATION_DIR", Path(__file__).resolve().parents[2]))  # repo root
CONFIG_DIR = Path(os.environ.get("LAS_CONFIG_DIR", HOME / ".config" / "lean-ai-station"))
MODELS_DIR = Path(os.environ.get("LAS_MODELS_DIR", HOME / "models"))
CACHE_DIR = Path(os.environ.get("LAS_CACHE_DIR", HOME / ".cache" / "lean-ai-station"))
LLAMA_BIN_DIR = Path(os.environ.get("LAS_LLAMA_BIN", STATION_DIR / "vendor" / "llama.cpp" / "build" / "bin"))
WORKSPACES_DIR = STATION_DIR / "workspaces"
ELAN_BIN = HOME / ".elan" / "bin"

SETTINGS_FILE = CONFIG_DIR / "settings.json"
SESSION_FILE = CONFIG_DIR / "session.json"
SERVER_PID_FILE = CONFIG_DIR / "llama-server.pid"
RUN_LOCK_FILE = CONFIG_DIR / "running.lock"
LOG_DIR = CONFIG_DIR / "logs"

DEFAULT_MODEL_NAME = "Goedel-Prover-V2-8B.Q4_K_M.gguf"


@dataclass
class ServerSettings:
    port: int = 8765
    ctx_size: int = 24576         # BENCH.md: answers reach ~16k tokens; 24k + q8_0 KV = 7.0 GiB VRAM
    gpu_layers: int = 99          # 99 = everything on GPU
    batch_size: int = 2048
    ubatch_size: int = 512
    kv_type: str = "q8_0"          # f16 | q8_0 | q4_0
    flash_attn: bool = True
    threads: int = 8


@dataclass
class SamplingSettings:
    temperature: float = 1.0       # Goedel-Prover-V2 inference default
    top_p: float = 0.95
    max_tokens: int = 16384


@dataclass
class Settings:
    wizard_done: bool = False
    offline: bool = True           # network hard-blocked in-app by default
    model_path: str = ""
    workspace: str = ""
    prove_attempts: int = 8
    translate_attempts: int = 3
    pause_after_translation: bool = False   # user choice (v1.1): fully automatic chain by default
    profile: str = ""                       # « mémoire » read by the translator and the explainer
    language: str = "fr"
    check_updates: bool = True              # weekly Lean/Mathlib + Goedel check (GitHub, Hugging Face only)
    overleaf_url: str = "http://127.0.0.1"
    compile_timeout_s: int = 180
    autoload_model: bool = True
    server: ServerSettings = field(default_factory=ServerSettings)
    sampling: SamplingSettings = field(default_factory=SamplingSettings)


def _from_dict(cls, data: dict):
    kwargs = {}
    names = {f.name: f for f in fields(cls)}
    for k, v in (data or {}).items():
        if k not in names:
            continue
        default = getattr(cls(), k)
        if hasattr(default, "__dataclass_fields__") and isinstance(v, dict):
            kwargs[k] = _from_dict(type(default), v)
        elif isinstance(default, bool):
            kwargs[k] = bool(v)
        elif isinstance(default, (int, float, str)) and isinstance(v, (int, float, str)):
            try:
                kwargs[k] = type(default)(v)
            except (TypeError, ValueError):
                pass
    return cls(**kwargs)


def atomic_write_text(path: Path, text: str) -> None:
    """Write via temp file + fsync + rename so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: Path) -> tuple[dict, bool]:
    """Return (data, was_corrupt). A corrupt file is kept aside as *.corrupt-<ts>."""
    try:
        return json.loads(path.read_text(encoding="utf-8")), False
    except FileNotFoundError:
        return {}, False
    except (OSError, ValueError):
        try:
            path.rename(path.with_name(f"{path.name}.corrupt-{int(time.time())}"))
        except OSError:
            pass
        return {}, True


def load_settings() -> tuple[Settings, bool]:
    data, corrupt = read_json(SETTINGS_FILE)
    return _from_dict(Settings, data), corrupt


def save_settings(s: Settings) -> None:
    atomic_write_text(SETTINGS_FILE, json.dumps(asdict(s), indent=2, ensure_ascii=False))


def load_session() -> dict:
    return read_json(SESSION_FILE)[0]


def save_session(data: dict) -> None:
    atomic_write_text(SESSION_FILE, json.dumps(data, indent=1, ensure_ascii=False))


def tilde(p: Path | str) -> str:
    """Display form of a path: ~/… instead of the full home directory."""
    s = str(p)
    h = str(HOME)
    return "~" + s[len(h):] if s == h or s.startswith(h + "/") else s


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, LOG_DIR, CACHE_DIR, CACHE_DIR / "tmp", MODELS_DIR):
        d.mkdir(parents=True, exist_ok=True)
