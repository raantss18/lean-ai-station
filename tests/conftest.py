"""Isolate every test run: fresh config/cache dirs, never touch the real profile."""
import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="las-test-"))
os.environ.setdefault("LAS_CONFIG_DIR", str(_TMP / "config"))
os.environ.setdefault("LAS_CACHE_DIR", str(_TMP / "cache"))
os.environ.setdefault("LAS_NO_AUTOLOAD", "1")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
