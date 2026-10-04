"""Minimal GGUF header reader: validates the file and extracts the metadata the UI needs."""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from .i18n import _

FILE_TYPES = {
    0: "F32", 1: "F16", 2: "Q4_0", 3: "Q4_1", 7: "Q8_0", 8: "Q5_0", 9: "Q5_1", 10: "Q2_K",
    11: "Q3_K_S", 12: "Q3_K_M", 13: "Q3_K_L", 14: "Q4_K_S", 15: "Q4_K_M", 16: "Q5_K_S",
    17: "Q5_K_M", 18: "Q6_K", 19: "IQ2_XXS", 20: "IQ2_XS", 21: "Q2_K_S", 22: "IQ3_XS",
    23: "IQ3_XXS", 24: "IQ1_S", 25: "IQ4_NL", 26: "IQ3_S", 27: "IQ3_M", 28: "IQ2_S",
    29: "IQ2_M", 30: "IQ4_XS", 31: "IQ1_M", 32: "BF16",
}

# value type ids -> struct format (scalars)
_SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
_STRING, _ARRAY = 8, 9


class GGUFError(Exception):
    pass


@dataclass
class GGUFInfo:
    path: Path
    size: int
    name: str
    arch: str
    quant: str
    n_layers: int
    ctx_train: int
    n_embd: int
    n_head_kv: int
    head_dim: int

    def kv_bytes(self, ctx: int, kv_type: str = "q8_0") -> int:
        """Approximate KV-cache size for a context length."""
        per = {"f16": 2.0, "q8_0": 34 / 32, "q4_0": 18 / 32}.get(kv_type, 2.0)
        return int(2 * self.n_layers * self.n_head_kv * self.head_dim * ctx * per)


class _Reader:
    def __init__(self, f):
        self.f = f

    def read(self, n: int) -> bytes:
        b = self.f.read(n)
        if len(b) != n:
            raise GGUFError(_("fichier tronqué"))
        return b

    def scalar(self, t: int):
        fmt = _SCALARS[t]
        return struct.unpack(fmt, self.read(struct.calcsize(fmt)))[0]

    def string(self) -> str:
        (n,) = struct.unpack("<Q", self.read(8))
        if n > 1 << 24:
            raise GGUFError(_("chaîne invalide"))
        return self.read(n).decode("utf-8", "replace")

    def value(self, t: int, keep: bool = True):
        if t in _SCALARS:
            return self.scalar(t)
        if t == _STRING:
            return self.string()
        if t == _ARRAY:
            (et,) = struct.unpack("<I", self.read(4))
            (n,) = struct.unpack("<Q", self.read(8))
            if et in _SCALARS:  # skip quickly
                self.f.seek(n * struct.calcsize(_SCALARS[et]), 1)
                return None
            for _i in range(n):
                self.value(et, keep=False)
            return None
        raise GGUFError(_("type inconnu {t}").format(t=t))


def read_info(path: str | Path) -> GGUFInfo:
    path = Path(path)
    size = path.stat().st_size
    wanted_suffixes = ("block_count", "context_length", "embedding_length", "attention.head_count_kv",
                       "attention.head_count", "attention.key_length")
    meta: dict = {}
    with open(path, "rb") as f:
        r = _Reader(f)
        if r.read(4) != b"GGUF":
            raise GGUFError(_("ce n'est pas un fichier GGUF"))
        (version,) = struct.unpack("<I", r.read(4))
        if version not in (2, 3):
            raise GGUFError(_("version GGUF {v} non prise en charge").format(v=version))
        _n_tensors, n_kv = struct.unpack("<QQ", r.read(16))
        if n_kv > 100000:
            raise GGUFError(_("en-tête corrompu"))
        for _i in range(n_kv):
            key = r.string()
            (t,) = struct.unpack("<I", r.read(4))
            v = r.value(t)
            if key.startswith("general.") or key.endswith(wanted_suffixes):
                meta[key] = v
            if key.startswith("tokenizer.") and "general.architecture" in meta and "general.file_type" in meta:
                arch = meta["general.architecture"]
                if f"{arch}.block_count" in meta:
                    break  # everything we need precedes the tokenizer block
    arch = meta.get("general.architecture", "?")
    n_embd = int(meta.get(f"{arch}.embedding_length", 0) or 0)
    n_head = int(meta.get(f"{arch}.attention.head_count", 0) or 0)
    head_dim = int(meta.get(f"{arch}.attention.key_length", 0) or (n_embd // n_head if n_head else 128))
    ft = meta.get("general.file_type")
    quant = FILE_TYPES.get(ft, "?") if ft is not None else _quant_from_name(path.name)
    return GGUFInfo(
        path=path, size=size, name=str(meta.get("general.name") or path.stem), arch=arch, quant=quant,
        n_layers=int(meta.get(f"{arch}.block_count", 0) or 0),
        ctx_train=int(meta.get(f"{arch}.context_length", 0) or 0),
        n_embd=n_embd, n_head_kv=int(meta.get(f"{arch}.attention.head_count_kv", n_head) or 0), head_dim=head_dim,
    )


def _quant_from_name(name: str) -> str:
    up = name.upper()
    for q in sorted(FILE_TYPES.values(), key=len, reverse=True):
        if q in up:
            return q
    return "?"


def find_models(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    out = [p for p in root.rglob("*.gguf") if p.is_file() and "mmproj" not in p.name.lower()]
    return sorted(out, key=lambda p: p.name.lower())
