#!/usr/bin/env python3
"""Parallel, resumable HTTP range downloader with SHA-256 verification (stdlib only).

usage: pdownload.py URL DEST SHA256 [--workers 8] [--segment-mb 32]
Segments are stored in DEST.parts/; re-running resumes. Only used during setup (online)."""
import argparse
import concurrent.futures as cf
import hashlib
import os
import sys
import time
import urllib.request
from pathlib import Path

UA = {"User-Agent": "lean-ai-station-setup/1.0"}


def head_size(url: str) -> int:
    req = urllib.request.Request(url, headers={**UA, "Range": "bytes=0-0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        cr = r.headers.get("Content-Range", "")
        return int(cr.split("/")[-1])


def fetch(url: str, start: int, end: int, part: Path) -> int:
    want = end - start + 1
    for attempt in range(1, 100):
        have = part.stat().st_size if part.exists() else 0
        if have >= want:
            return want
        try:
            req = urllib.request.Request(url, headers={**UA, "Range": f"bytes={start + have}-{end}"})
            with urllib.request.urlopen(req, timeout=60) as r, open(part, "ab") as f:
                while True:
                    b = r.read(1 << 16)
                    if not b:
                        break
                    f.write(b)
        except Exception as e:  # network flakiness: retry with backoff
            time.sleep(min(30, 2 * attempt))
            continue
    raise RuntimeError(f"segment {start}-{end} failed")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("dest")
    ap.add_argument("sha256")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--segment-mb", type=int, default=32)
    a = ap.parse_args()
    dest = Path(a.dest)
    if dest.exists() and dest.with_suffix(dest.suffix + ".sha256").exists():
        print("already complete", dest)
        return 0
    size = head_size(a.url)
    seg = a.segment_mb << 20
    parts_dir = Path(str(dest) + ".parts")
    parts_dir.mkdir(parents=True, exist_ok=True)
    ranges = [(i, s, min(s + seg, size) - 1) for i, s in enumerate(range(0, size, seg))]
    t0 = time.time()
    with cf.ThreadPoolExecutor(a.workers) as ex:
        futs = {ex.submit(fetch, a.url, s, e, parts_dir / f"{i:05d}"): i for i, s, e in ranges}
        done = 0
        for fu in cf.as_completed(futs):
            fu.result()
            done += 1
            got = sum(p.stat().st_size for p in parts_dir.iterdir())
            print(f"{done}/{len(ranges)} segments  {got / 1e9:.2f}/{size / 1e9:.2f} GB  "
                  f"{got / max(time.time() - t0, 1) / 1e6:.2f} MB/s", flush=True)
    h = hashlib.sha256()
    tmp = Path(str(dest) + ".tmp")
    with open(tmp, "wb") as out:
        for i, _, _ in ranges:
            data = (parts_dir / f"{i:05d}").read_bytes()
            h.update(data)
            out.write(data)
    if h.hexdigest() != a.sha256:
        print("CHECKSUM MISMATCH", h.hexdigest(), file=sys.stderr)
        tmp.unlink()
        for p in parts_dir.iterdir():
            p.unlink()
        return 2
    os.replace(tmp, dest)
    Path(str(dest) + ".sha256").write_text(f"{a.sha256}  {dest.name}\n")
    for p in parts_dir.iterdir():
        p.unlink()
    parts_dir.rmdir()
    print("OK", dest, f"{time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
