"""Check the prototype BCJ2 decoder against archives the 7z CLI writes.

Run from the repo root, with the `7z` CLI on PATH:

    uv run python openspec/changes/sevenzip-bcj2-decode/prototype/check_against_7zip.py

It writes BCJ2 archives into a temp directory, resolves each BCJ2 folder's coder
tree (the same resolution design.md D1 proposes for the pipeline), decodes the three
LZMA-family branches in memory with stdlib ``lzma``, then runs ``Bcj2DecoderStream``
over them with several input block sizes and read patterns. The output is compared
with the file the archive was made from, and the leftover input per stream is
printed. Timings are the BCJ2 stage alone.

Throwaway: the real implementation replaces the in-memory branch decode with the
pipeline's own streams. Not wired into the test suite.
"""

from __future__ import annotations

import io
import itertools
import lzma
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import bcj2  # noqa: E402
from bcj2 import Bcj2DecoderStream  # noqa: E402

from archivey.internal.backends.sevenzip_parser import SevenZipFolder  # noqa: E402
from archivey.internal.backends.sevenzip_pipeline import (  # noqa: E402
    parse_sevenzip_archive,
)

_BCJ2 = bytes.fromhex("0303011b")
_LZMA_IDS = {bytes.fromhex("030101"): lzma.FILTER_LZMA1, b"\x21": lzma.FILTER_LZMA2}
_decode_props = lzma._decode_filter_properties  # type: ignore[attr-defined]

# (archive name, 7z switches, input files). -mx9 lets 7-Zip choose BCJ2 itself;
# the explicit form forces it onto inputs 7-Zip would not pick it for.
_FORCED = [
    "-m0=BCJ2",
    "-m1=LZMA2",
    "-m2=LZMA",
    "-m3=LZMA",
    "-mb0s0:1",
    "-mb0s1:2",
    "-mb0s2:3",
]
_CASES = [
    ("mx9", ["-mx9"], ["exe"]),
    ("mx9-solid", ["-mx9"], ["exe", "exe_e8", "exe_0f"]),
    ("forced-edges", [*_FORCED, "-ms=off"], ["exe_e8", "exe_0f"]),
    ("forced-random", _FORCED, ["random"]),
]


def _inputs(work: Path) -> dict[str, Path]:
    exe = Path(shutil.which("git") or sys.executable).resolve()
    data = exe.read_bytes()
    files = {
        "exe": data,
        "exe_e8": data + b"\xe8",  # output ends on an opcode: no bit may be decoded
        "exe_0f": data + b"\x0f",  # output ends on half a Jcc
        "random": random.Random(0).randbytes(300_000),
    }
    paths = {}
    for name, content in files.items():
        paths[name] = work / f"{name}.bin"
        paths[name].write_bytes(content)
        # 7-Zip 23.01 on Linux picks BCJ2 at -mx9 only for a file with the execute
        # bit set; the same bytes at 0644 get plain LZMA2.
        paths[name].chmod(0o755)
    return paths


def _bcj2_branches(archive: Path):
    """Yield (folder, [main, call, jump, rc] bytes, unpack size) per BCJ2 folder."""
    with archive.open("rb") as f:
        parsed = parse_sevenzip_archive(f)
        f.seek(0)
        raw = f.read()
    packs, pos = [], parsed.pack_pos
    for size in parsed.pack_sizes:
        packs.append(raw[pos : pos + size])
        pos += size
    first_pack = 0
    for folder in parsed.folders:
        mine = packs[first_pack : first_pack + len(folder.packed_indices)]
        first_pack += len(folder.packed_indices)
        root = _root_coder(folder)
        if folder.coders[root].method != _BCJ2:
            continue
        in_base = _in_bases(folder)
        branches = [
            _decode_input(folder, mine, in_base, in_base[root] + k) for k in range(4)
        ]
        yield folder, branches, folder.unpack_sizes[root]


def _in_bases(folder: SevenZipFolder) -> list[int]:
    bases, n = [], 0
    for coder in folder.coders:
        bases.append(n)
        n += coder.num_in_streams
    return bases


def _root_coder(folder: SevenZipFolder) -> int:
    bound = {out for _, out in folder.bind_pairs}
    (root,) = [i for i in range(len(folder.coders)) if i not in bound]
    return root


def _decode_input(folder, packs, in_base, in_index) -> bytes:
    if in_index in folder.packed_indices:
        return packs[folder.packed_indices.index(in_index)]
    (out,) = [o for i, o in folder.bind_pairs if i == in_index]
    coder = folder.coders[out]
    data = _decode_input(folder, packs, in_base, in_base[out])
    filters = [_decode_props(_LZMA_IDS[coder.method], coder.properties)]
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filters)
    return decoder.decompress(data, folder.unpack_sizes[out])


def main() -> int:
    rnd = random.Random(1)
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        paths = _inputs(work)
        for name, switches, members in _CASES:
            archive = work / f"{name}.7z"
            subprocess.run(
                ["7z", "a", *switches, str(archive), *(str(paths[m]) for m in members)],
                check=True,
                capture_output=True,
            )
            expected = [paths[m].read_bytes() for m in members]
            for folder, branches, size in _bcj2_branches(archive):
                for block, pattern in [
                    (64 * 1024, "whole"),
                    (4096, "mixed"),
                    (7, "mixed"),
                    (1, "mixed"),
                ]:
                    bcj2._BLOCK = block
                    stream = Bcj2DecoderStream(
                        *map(io.BytesIO, branches), unpack_size=size
                    )
                    start = time.perf_counter()
                    if pattern == "whole":
                        out = stream.read(size)
                    else:
                        parts = []
                        while chunk := stream.read(
                            rnd.choice([1, 3, 17, 4096, 70_000])
                        ):
                            parts.append(chunk)
                        out = b"".join(parts)
                    elapsed = time.perf_counter() - start
                    # A solid folder holds its members in 7-Zip's order, not ours.
                    ok = any(
                        out == b"".join(p)
                        for n in range(1, len(expected) + 1)
                        for p in itertools.permutations(expected, n)
                    )
                    failures += not ok
                    print(
                        f"{name:14} block={block:<6} {pattern:5} {'OK ' if ok else 'BAD'} "
                        f"{size / 1e6 / elapsed:6.1f} MB/s  leftover={stream.leftover()}"
                    )
                bcj2._BLOCK = 64 * 1024
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
