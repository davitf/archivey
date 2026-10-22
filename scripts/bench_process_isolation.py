"""Measure what ``ArchiveyConfig.process_isolation`` costs, per MiB and per decoder.

The ``ProcessIsolation`` docstrings and the security page quote these figures. Each
archive below is read in full under ``NONE``, ``KNOWN_CRASHES`` and ``NATIVE_DECODERS``,
with the accelerators off, each case in a fresh interpreter (a warm-up read, then the
best of three). Single streams of several sizes give the cost per MiB of output; ZIP and
7z archives of one member and of many give the cost per decoder. A solid 7z folder is
read by one decoder, so it pays the per-decoder cost once.

The 7z archives need a ``7z`` program on ``PATH`` (py7zr's PPMd writer produces
archives that fail their own CRC); without one their rows are skipped. The payload is
random words, which gzip compresses about two to one.

Usage::

    uv run --no-sync python scripts/bench_process_isolation.py

Wall times on one machine; they are for comparing the columns.
"""

from __future__ import annotations

import bz2
import gzip
import lzma
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

_MIB = 1 << 20
_MODES = ("none", "known_crashes", "native_decoders")


def _text(size: int) -> bytes:
    rng = random.Random(1234)
    letters = "abcdefghijklmnopqrstuvwxyz"
    words = ["".join(rng.choices(letters, k=rng.randint(2, 10))) for _ in range(5000)]
    out: list[str] = []
    total = 0
    while total < size:
        line = " ".join(rng.choices(words, k=12)) + f" {rng.randint(0, 10**9)}\n"
        out.append(line)
        total += len(line)
    return "".join(out).encode()[:size]


def _zstd_compress(data: bytes) -> bytes | None:
    try:
        from compression import zstd  # type: ignore[import-not-found]
    except ImportError:
        try:
            from backports import zstd  # type: ignore[import-not-found,no-redef]
        except ImportError:
            return None
    return zstd.compress(data)


def _write_7z(
    path: Path, members: dict[str, bytes], method: str, *, solid: bool
) -> bool:
    program = shutil.which("7z") or shutil.which("7zz") or shutil.which("7za")
    if program is None:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        for name, data in members.items():
            Path(tmp, name).write_bytes(data)
        subprocess.run(
            [
                program,
                "a",
                f"-m0={method}",
                f"-ms={'on' if solid else 'off'}",
                str(path),
            ]
            + sorted(members),
            cwd=tmp,
            check=True,
            capture_output=True,
        )
    return True


def _build(root: Path) -> list[Path]:
    payload = _text(64 * _MIB)
    paths: list[Path] = []

    def single(name: str, data: bytes | None) -> None:
        if data is not None:
            (root / name).write_bytes(data)
            paths.append(root / name)

    for mib in (1, 16, 64):
        part = payload[: mib * _MIB]
        single(f"{mib:02d}M.gz", gzip.compress(part, 6, mtime=0))
        single(f"{mib:02d}M.zst", _zstd_compress(part))
        if mib <= 16:
            single(f"{mib:02d}M.bz2", bz2.compress(part, 9))
            single(f"{mib:02d}M.xz", lzma.compress(part))
    for count in (1, 1024):
        name = f"deflate-{count}x-8M.zip"
        each = 8 * _MIB // count
        with zipfile.ZipFile(root / name, "w", zipfile.ZIP_DEFLATED) as zf:
            for i in range(count):
                zf.writestr(f"m{i:05}.txt", payload[i * each : (i + 1) * each])
        paths.append(root / name)
    small = {f"m{i:05}.txt": payload[i * 4096 : (i + 1) * 4096] for i in range(1024)}
    for method, solid in (("LZMA2", False), ("PPMd", False), ("PPMd", True)):
        name = f"{method.lower()}-{'solid' if solid else 'nonsolid'}-1024x4K.7z"
        if _write_7z(root / name, small, method, solid=solid):
            paths.append(root / name)
    return paths


def _time_one(path: str, mode: str) -> float:
    """Best of three full reads after a warm-up, in this process."""
    from archivey import open_archive
    from archivey.config import AcceleratorMode, ArchiveyConfig, ProcessIsolation

    config = ArchiveyConfig(
        process_isolation=ProcessIsolation(mode),
        use_rapidgzip=AcceleratorMode.OFF,
        use_indexed_bzip2=AcceleratorMode.OFF,
    )

    def read_all() -> None:
        with open_archive(path, config=config) as reader:
            for _member, stream in reader.stream_members():
                if stream is not None:
                    while stream.read(_MIB):
                        pass

    read_all()
    best = float("inf")
    for _ in range(3):
        start = time.perf_counter()
        read_all()
        best = min(best, time.perf_counter() - start)
    return best


def main() -> None:
    if len(sys.argv) == 4 and sys.argv[1] == "--one":
        print(_time_one(sys.argv[2], sys.argv[3]))
        return
    with tempfile.TemporaryDirectory() as tmp:
        paths = _build(Path(tmp))
        print(f"{'archive':32}" + "".join(f"{mode:>17}" for mode in _MODES))
        for path in paths:
            times = [
                float(
                    subprocess.run(
                        [sys.executable, __file__, "--one", str(path), mode],
                        check=True,
                        capture_output=True,
                        text=True,
                        env={**os.environ, "PYTHONWARNINGS": "ignore"},
                    ).stdout.split()[-1]
                )
                for mode in _MODES
            ]
            base = times[0]
            cells = "".join(f"{t * 1000:9.0f} ms {t / base:4.2f}x" for t in times)
            print(f"{path.name:32}{cells}")


if __name__ == "__main__":
    main()
