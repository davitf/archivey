"""Check the prototype BCJ2 decoder against archives the 7z CLI writes.

Run from the repo root, with the `7z` CLI on PATH:

    uv run python openspec/changes/sevenzip-bcj2-decode/prototype/check_against_7zip.py

It writes BCJ2 archives into a temp directory, resolves each BCJ2 folder's coder
tree (the same resolution design.md D1 proposes for the pipeline), decrypts and
decodes the branches in memory (archivey's AES stage, stdlib ``lzma``), then runs
``Bcj2DecoderStream`` over them with several input block sizes and read patterns.
Each folder's output must equal its members' files concatenated in the archive's
own file-table order. The leftover input per stream is printed. Timings are the BCJ2
stage alone. Three negative checks follow: a truncated ``call`` stream, a truncated
``main`` stream, and a ``main`` stream with a 16 MiB tail, which must be refused
after reading one byte of the tail.

Throwaway: the real implementation replaces the in-memory branch decode with the
pipeline's own streams. Not wired into the test suite.
"""

from __future__ import annotations

import io
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

from archivey.exceptions import CorruptionError, TruncatedError  # noqa: E402
from archivey.internal.backends.sevenzip_aes import SevenZipKeyCache  # noqa: E402
from archivey.internal.backends.sevenzip_parser import SevenZipFolder  # noqa: E402
from archivey.internal.backends.sevenzip_pipeline import (  # noqa: E402
    parse_sevenzip_archive,
)
from archivey.internal.streams.crypto import open_aes_decrypt_stream  # noqa: E402

_BCJ2 = bytes.fromhex("0303011b")
_AES = bytes.fromhex("06f10701")
_LZMA_IDS = {bytes.fromhex("030101"): lzma.FILTER_LZMA1, b"\x21": lzma.FILTER_LZMA2}
_decode_props = lzma._decode_filter_properties  # type: ignore[attr-defined]
_PASSWORD = "Secret"

# (archive name, 7z switches, input files, password). -mx9 lets 7-Zip choose BCJ2
# itself; the explicit form forces it onto inputs 7-Zip would not pick it for.
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
    ("mx9", ["-mx9"], ["exe"], None),
    ("mx9-solid", ["-mx9"], ["exe", "exe_e8", "exe_0f"], None),
    # Eight coders: an AES coder on each of the four pack streams.
    ("mx9-encrypted", ["-mx9", f"-p{_PASSWORD}", "-mhe=on"], ["exe"], _PASSWORD),
    ("forced-edges", [*_FORCED, "-ms=off"], ["exe_e8", "exe_0f"], None),
    ("forced-random", _FORCED, ["random"], None),
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


def _bcj2_branches(archive: Path, password: str | None):
    """Yield ([main, call, jump, rc] bytes, unpack size, member names) per BCJ2 folder.

    Member names are in the archive's file-table order, which is the order their
    bytes appear in the folder's output.
    """
    kdf_password = password.encode("utf-16le") if password else None
    with archive.open("rb") as f:
        parsed = parse_sevenzip_archive(f, password=kdf_password)
        f.seek(0)
        raw = f.read()
    packs, pos = [], parsed.pack_pos
    for size in parsed.pack_sizes:
        packs.append(raw[pos : pos + size])
        pos += size
    keys = SevenZipKeyCache()
    first_pack = 0
    for index, folder in enumerate(parsed.folders):
        mine = packs[first_pack : first_pack + len(folder.packed_indices)]
        first_pack += len(folder.packed_indices)
        root = _root_coder(folder)
        if folder.coders[root].method != _BCJ2:
            continue
        in_base = _in_bases(folder)

        def decode(in_index: int, folder=folder, mine=mine, in_base=in_base) -> bytes:
            if in_index in folder.packed_indices:
                return mine[folder.packed_indices.index(in_index)]
            (out,) = [o for i, o in folder.bind_pairs if i == in_index]
            coder = folder.coders[out]
            data = decode(in_base[out])
            size = folder.unpack_sizes[out]
            if coder.method == _AES:
                assert kdf_password is not None and coder.properties is not None
                params = keys.aes_params_from_properties(kdf_password, coder.properties)
                return open_aes_decrypt_stream(io.BytesIO(data), params).read(size)
            filters = [_decode_props(_LZMA_IDS[coder.method], coder.properties)]
            decoder = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=filters)
            return decoder.decompress(data, size)

        branches = [decode(in_base[root] + k) for k in range(4)]
        names = [rec.filename for rec in parsed.files if rec.folder_index == index]
        yield branches, folder.unpack_sizes[root], names


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


class _ReadCounter(io.BytesIO):
    """A BytesIO that counts the bytes handed out."""

    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.handed_out = 0

    def read(self, n: int | None = -1, /) -> bytes:
        chunk = super().read(n)
        self.handed_out += len(chunk)
        return chunk


def _negative_checks(branches: list[bytes], size: int) -> int:
    """Refusals the real implementation must keep (design D5). Returns failure count."""
    main, call, jump, rc = branches
    failures = 0
    for label, args, expected in [
        ("call cut by 1 byte", (main, call[:-1], jump, rc), TruncatedError),
        ("main cut by 1 byte", (main[:-1], call, jump, rc), TruncatedError),
    ]:
        try:
            Bcj2DecoderStream(*map(io.BytesIO, args), unpack_size=size).read(size)
            ok = False
        except expected:
            ok = True
        failures += not ok
        print(f"negative: {label:34} {'OK ' if ok else 'BAD'} ({expected.__name__})")
    tail = 16 * 2**20
    counted = _ReadCounter(main + b"\x00" * tail)
    stream = Bcj2DecoderStream(
        counted, io.BytesIO(call), io.BytesIO(jump), io.BytesIO(rc), unpack_size=size
    )
    try:
        stream.read(size)
        ok = False
    except CorruptionError:
        # Blocks already buffered count too: the check must not read past them.
        ok = counted.handed_out - len(main) <= bcj2._BLOCK + 1
    failures += not ok
    extra = counted.handed_out - len(main)
    print(
        f"negative: {'main with a 16 MiB tail':34} {'OK ' if ok else 'BAD'} "
        f"(CorruptionError after {extra} bytes of the tail)"
    )
    return failures


def main() -> int:
    rnd = random.Random(1)
    failures = 0
    negative_input = None
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        paths = _inputs(work)
        by_name = {path.name: path for path in paths.values()}
        for name, switches, members, password in _CASES:
            archive = work / f"{name}.7z"
            subprocess.run(
                ["7z", "a", *switches, str(archive), *(str(paths[m]) for m in members)],
                check=True,
                capture_output=True,
                stdin=subprocess.DEVNULL,
            )
            seen = 0
            for branches, size, names in _bcj2_branches(archive, password):
                seen += 1
                expected = b"".join(by_name[n].read_bytes() for n in names)
                negative_input = negative_input or (branches, size)
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
                    ok = out == expected
                    failures += not ok
                    print(
                        f"{name:14} block={block:<6} {pattern:5} {'OK ' if ok else 'BAD'} "
                        f"{size / 1e6 / elapsed:6.1f} MB/s  leftover={stream.leftover()}"
                    )
                bcj2._BLOCK = 64 * 1024
            if not seen:
                failures += 1
                print(f"{name:14} BAD: 7-Zip wrote no BCJ2 folder")
    assert negative_input is not None
    failures += _negative_checks(*negative_input)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
