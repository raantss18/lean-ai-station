import json
import struct

import pytest

from lean_ai_station import config
from lean_ai_station.gguf import GGUFError, read_info
from lean_ai_station.services import plan_launch


def _gguf(path, arch="qwen3", n_layers=36, file_type=15, extra_tokens=1000):
    def s(x):
        b = x.encode()
        return struct.pack("<Q", len(b)) + b
    kv = [("general.architecture", 8, s(arch)), ("general.name", 8, s("Test Model")),
          ("general.file_type", 4, struct.pack("<I", file_type)),
          (f"{arch}.block_count", 4, struct.pack("<I", n_layers)),
          (f"{arch}.context_length", 4, struct.pack("<I", 40960)),
          (f"{arch}.embedding_length", 4, struct.pack("<I", 4096)),
          (f"{arch}.attention.head_count", 4, struct.pack("<I", 32)),
          (f"{arch}.attention.head_count_kv", 4, struct.pack("<I", 8)),
          ("tokenizer.ggml.tokens", 9, struct.pack("<IQ", 8, extra_tokens) + b"".join(s(f"t{i}") for i in range(extra_tokens)))]
    body = b"GGUF" + struct.pack("<IQQ", 3, 0, len(kv))
    for k, t, v in kv:
        body += s(k) + struct.pack("<I", t) + v
    path.write_bytes(body + b"\0" * 1024)
    return path


def test_gguf_reader(tmp_path):
    info = read_info(_gguf(tmp_path / "m.gguf"))
    assert (info.arch, info.quant, info.n_layers, info.n_head_kv, info.head_dim, info.ctx_train) == \
        ("qwen3", "Q4_K_M", 36, 8, 128, 40960)
    # Qwen3-8B KV at 16k ctx, q8_0 ≈ 1.2 GiB
    assert 1.1e9 < info.kv_bytes(16384, "q8_0") < 1.4e9


def test_gguf_corrupt_and_truncated(tmp_path):
    bad = tmp_path / "bad.gguf"
    bad.write_bytes(b"NOPE" + b"\0" * 100)
    with pytest.raises(GGUFError):
        read_info(bad)
    good = _gguf(tmp_path / "g.gguf").read_bytes()
    trunc = tmp_path / "t.gguf"
    trunc.write_bytes(good[:60])
    with pytest.raises(GGUFError):
        read_info(trunc)


def test_plan_launch_fallbacks(tmp_path):
    m = _gguf(tmp_path / "m.gguf")
    s = config.ServerSettings(ctx_size=16384)
    # pretend the file is 5 GB
    import os
    os.truncate(m, 5_000_000_000)
    p = plan_launch(m, s, 7400)
    assert p.gpu_layers == 99 and p.ctx == 16384 and not p.note
    p = plan_launch(m, s, 6400)          # little VRAM: context shrinks first
    assert p.ctx < 16384 and p.gpu_layers == 99 and "contexte" in p.note
    p = plan_launch(m, s, 3000)          # far too small: partial offload
    assert 0 < p.gpu_layers < 36 and "couches" in p.note
    p = plan_launch(m, s, None)          # no GPU: CPU
    assert p.gpu_layers == 0 and "processeur" in p.note
    os.truncate(m, 0)


def test_settings_roundtrip_and_corrupt_recovery(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    monkeypatch.setattr(config, "SETTINGS_FILE", f)
    s = config.Settings()
    s.prove_attempts = 5
    s.server.port = 9999
    config.save_settings(s)
    s2, corrupt = config.load_settings()
    assert not corrupt and s2.prove_attempts == 5 and s2.server.port == 9999
    f.write_text('{"prove_attempts": 3, "server": {"port": ')   # torn write
    s3, corrupt = config.load_settings()
    assert corrupt and s3.prove_attempts == config.Settings().prove_attempts
    assert list(tmp_path.glob("settings.json.corrupt-*"))
    f.write_text(json.dumps({"prove_attempts": "x", "unknown": 1, "server": {"port": 1234}}))
    s4, _ = config.load_settings()
    assert s4.server.port == 1234 and s4.prove_attempts == config.Settings().prove_attempts


def test_free_vram_ignores_own_server_even_with_stale_snapshot(qapp):
    from lean_ai_station.services import GpuMonitor
    g = GpuMonitor()
    # snapshot taken while our server (pid 4242) held 6400 MiB, desktop 484 MiB
    g.last = {"name": "RTX", "total": 8188, "used": 6884, "free": 1304, "temp": 60, "util": 0,
              "apps": {4242: 6400, 77: 94}}
    assert g.free_excluding(4242) == 7704          # what a reload of our model can use
    assert g.free_excluding(None) == 1304
    assert g.free_excluding(999) == 1304
