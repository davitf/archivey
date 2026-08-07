#!/usr/bin/env python3
# ruff: noqa: E402,BLE001
"""Targeted behavioural repros for simplicity-consistency findings.

Run from the repo root ([all] config):

    uv run --no-sync python review/simplicity-consistency/repro/repros.py

Pins today's behaviour (evidence / red half). Does not change the library.
"""

from __future__ import annotations

import io
import tarfile
import tempfile
import zipfile
from pathlib import Path

from archivey import extract, open_archive, open_stream
from archivey.diagnostics import DiagnosticCode
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    EncryptionError,
    StreamNotSeekableError,
)


def _pipe(data: bytes) -> io.RawIOBase:
    class Pipe(io.RawIOBase):
        def __init__(self, blob: bytes) -> None:
            self._buf = io.BytesIO(blob)

        def readable(self) -> bool:
            return True

        def read(self, size: int = -1) -> bytes:  # type: ignore[override]
            return self._buf.read(size)

        def seekable(self) -> bool:
            return False

    return Pipe(data)


def r1_pipe_refusal_uniformity() -> None:
    """Pipe + streaming=False is loud for every format; streaming=True diverges by format law."""
    print("\n[R1] Pipe refusal uniformity")

    # ZIP (index at EOF)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.txt", b"hi")
    zip_bytes = buf.getvalue()

    try:
        open_archive(_pipe(zip_bytes))
        print("  ZIP RA pipe: UNEXPECTED OK")
    except StreamNotSeekableError as e:
        print(
            f"  ZIP RA pipe: StreamNotSeekableError (expected) msg_has_format={('ZIP' in str(e) or 'zip' in str(e).lower())}"
        )

    try:
        open_archive(_pipe(zip_bytes), streaming=True)
        print("  ZIP streaming pipe: UNEXPECTED OK")
    except StreamNotSeekableError as e:
        print(
            f"  ZIP streaming pipe: StreamNotSeekableError (expected format-law) — {e.__class__.__name__}"
        )

    with tempfile.TemporaryDirectory() as td:
        try:
            extract(_pipe(zip_bytes), td)
            print("  ZIP extract(pipe): UNEXPECTED OK")
        except StreamNotSeekableError:
            print(
                "  ZIP extract(pipe): StreamNotSeekableError (loud; auto-stream still cannot)"
            )

    # TAR (front-to-back)
    tbuf = io.BytesIO()
    with tarfile.open(fileobj=tbuf, mode="w") as t:
        info = tarfile.TarInfo("a.txt")
        payload = b"hi"
        info.size = len(payload)
        t.addfile(info, io.BytesIO(payload))
    tar_bytes = tbuf.getvalue()

    try:
        open_archive(_pipe(tar_bytes))
        print("  TAR RA pipe: UNEXPECTED OK")
    except StreamNotSeekableError:
        print("  TAR RA pipe: StreamNotSeekableError (expected)")

    with open_archive(_pipe(tar_bytes), streaming=True) as r:
        n = sum(1 for _ in r)
        print(f"  TAR streaming pipe: OK n={n}")

    with tempfile.TemporaryDirectory() as td:
        report = extract(_pipe(tar_bytes), td)
        print(f"  TAR extract(pipe): OK results={len(report.results)} (auto-stream)")


def r2_tar_corrupt_final_header() -> None:
    """RA vs streaming honesty for a corrupt final TAR header (seed A / P3).

    Uses the suite fixture shape (reject block where EOF marker should be), not a
    naive append — ``tests/test_tar._tar_corrupt_final_header``.
    """
    print("\n[R2] TAR corrupt final header — RA vs streaming honesty")
    import sys
    from pathlib import Path as P

    repo = P(__file__).resolve().parents[3]
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from tests.test_tar import _tar_corrupt_final_header

    data = _tar_corrupt_final_header()

    try:
        with open_archive(io.BytesIO(data)) as r:
            names = [m.name for m in r.members()]
            print(f"  RA: UNEXPECTED OK names={names}")
    except CorruptionError as e:
        print(f"  RA: CorruptionError (format-law residual / P3) — {e}")
    except ArchiveyError as e:
        print(f"  RA: {type(e).__name__}: {e}")

    try:
        with open_archive(io.BytesIO(data), streaming=True) as r:
            names = [m.name for m in r]
            diags = r.diagnostics
        print(
            f"  streaming: OK names={names} total_diags={diags.total_count} "
            f"codes={[getattr(c, 'value', c) for c in diags.counts]}"
        )
        if DiagnosticCode.ARCHIVE_EOF_MARKER_MISSING in diags.counts:
            print("  streaming: ARCHIVE_EOF_MARKER_MISSING (soft; not CorruptionError)")
    except CorruptionError as e:
        print(f"  streaming: CorruptionError (would be RA parity) — {e}")
    except ArchiveyError as e:
        print(f"  streaming: {type(e).__name__}: {e}")


def r3_encryption_shape_divergence() -> None:
    """Wrong-password caller-visible shapes differ by format where format allows."""
    print("\n[R3] Encryption / wrong-password shapes (fixture-dependent)")

    fixtures = Path("tests/fixtures")
    candidates = [
        ("zip_zipcrypto", list(fixtures.glob("**/encrypted*.zip"))[:2]),
        (
            "7z",
            list(fixtures.glob("**/encrypted*.7z"))[:2]
            + list(fixtures.glob("**/*password*.7z"))[:2],
        ),
        (
            "rar",
            list(fixtures.glob("**/encrypted*.rar"))[:2]
            + list(fixtures.glob("**/*password*.rar"))[:2],
        ),
    ]
    # Also search sample cache patterns via known fixture dirs used in tests
    for label, paths in candidates:
        if not paths:
            print(
                f"  {label}: no fixtures found under tests/fixtures (see test_password.py)"
            )
            continue
        for p in paths[:1]:
            print(f"  {label}: trying {p}")
            try:
                with open_archive(p, password="WRONG") as r:
                    members = [m for m in r.members() if m.type.name == "FILE"]
                    if not members:
                        print("    open OK but no FILE members")
                        continue
                    try:
                        data = r.read(members[0])
                        print(
                            f"    read returned {len(data)} bytes (possible garbage path)"
                        )
                        print(f"    diagnostics={dict(r.diagnostics.counts)}")
                    except EncryptionError as e:
                        print(f"    EncryptionError on read: {e}")
                    except ArchiveyError as e:
                        print(f"    {type(e).__name__} on read: {e}")
            except EncryptionError as e:
                print(f"    EncryptionError on open: {e}")
            except ArchiveyError as e:
                print(f"    {type(e).__name__} on open: {e}")


def r4_memberstreams_vs_open_stream_vocab() -> None:
    print("\n[R4] Vocabulary: MemberStreams booleans vs open_stream(seekable=)")
    import inspect

    from archivey import open_archive as oa
    from archivey import open_stream as os_

    sig_a = inspect.signature(oa)
    sig_s = inspect.signature(os_)
    print(
        f"  open_archive params: {[p for p in sig_a.parameters if p in ('seekable_members', 'concurrent_members', 'streaming')]}"
    )
    print(f"  open_stream params: {[p for p in sig_s.parameters if 'seek' in p]}")
    print(
        "  Same concept (member stream seekability), two spellings — freeze-cost if left."
    )


def r5_cli_extraction_progress_import() -> None:
    print("\n[R5] CLI ExtractionProgress import paths")
    import ast
    from pathlib import Path as P

    for rel in (
        "src/archivey/cli/extract_cmd.py",
        "src/archivey/cli/progress.py",
        "src/archivey/cli/test_cmd.py",
    ):
        tree = ast.parse(P(rel).read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and "ExtractionProgress" in [a.name for a in (node.names or [])]
            ):
                print(f"  {rel}: from {node.module} import ExtractionProgress")


def r6_open_stream_vs_open_archive_gz() -> None:
    print("\n[R6] open_stream vs open_archive for the same .gz")
    payload = b"hello gz"
    import gzip

    raw = gzip.compress(payload)
    with open_archive(io.BytesIO(raw)) as r:
        m = next(m for m in r.members() if m.type.name == "FILE")
        print(
            f"  open_archive: type={type(r).__name__} member={m.name!r} hashes={m.hashes}"
        )
    with open_stream(io.BytesIO(raw)) as s:
        data = s.read()
        print(f"  open_stream: got {data!r} (no ArchiveMember / no CostReceipt)")


def r7_zip_closed_valueerror_leak() -> None:
    print("\n[R7] ZIP post-close ValueError leak?")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.txt", b"x")
    data = buf.getvalue()
    r = open_archive(io.BytesIO(data))
    m = next(m for m in r.members() if m.type.name == "FILE")
    r.close()
    try:
        r.open(m)
        print("  open after close: UNEXPECTED OK")
    except Exception as e:
        print(f"  open after close: {_n(e)}")


def r8_volumes_valueerror_leak() -> None:
    """Raw ValueError from resolve_source / ConcatenatedFile crosses open_archive."""
    print("\n[R8] volumes.py ValueError across public open_archive (F1)")
    try:
        open_archive([])
        print("  empty seq: UNEXPECTED OK")
    except Exception as e:
        print(
            f"  empty seq: {_n(e)}  (want ArchiveyUsageError, not builtins.ValueError)"
        )

    try:
        open_archive([_pipe(b"7z\xbc\xaf\x27\x1cxxxx"), _pipe(b"yyyy")])
        print("  nonseek vols: UNEXPECTED OK")
    except Exception as e:
        print(
            f"  nonseek vols: {_n(e)}  "
            "(want StreamNotSeekableError, not builtins.ValueError)"
        )


def r9_encoding_silent_discard() -> None:
    """encoding= is honored on ZIP/TAR; silently ignored on 7z/RAR/directory."""
    print("\n[R9] encoding= silent discard (F2)")
    seven = Path("tests/fixtures/sevenzip/lz4.7z")
    if seven.exists():
        with open_archive(seven, encoding="cp932") as r:
            print(f"  7z+encoding=cp932: OK format={r.format} (silent discard)")
    rar = Path("tests/fixtures/rar/encryption__.rar")
    if rar.exists():
        with open_archive(rar, encoding="cp932") as r:
            print(f"  RAR+encoding=cp932: OK format={r.format} (silent discard)")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "a.txt").write_text("hi", encoding="utf-8")
        with open_archive(root, encoding="cp932") as r:
            names = [m.name for m in r.members()]
            print(f"  directory+encoding=cp932: OK names={names} (silent discard)")


def r10_zip_already_closed_misclassified() -> None:
    """Underlying ZipFile closed while reader live → CorruptionError (F3)."""
    print("\n[R10] ZIP already-closed misclassified as CorruptionError (F3)")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.txt", b"hello")
    buf.seek(0)
    with open_archive(buf) as r:
        r._archive.close()  # type: ignore[attr-defined]
        try:
            with r.open("a.txt") as s:
                s.read()
            print("  UNEXPECTED OK")
        except Exception as e:
            print(f"  {_n(e)}")
            print("  (want ArchiveyUsageError; today CorruptionError)")


def r11_compressed_size_path_gate() -> None:
    """single-file compressed_size still Path-only after #225 CRC fix (F6)."""
    print("\n[R11] single-file compressed_size Path gate (F6)")
    import gzip

    payload = b"hello world" * 100
    raw = gzip.compress(payload)
    with open_archive(io.BytesIO(raw)) as r:
        m = next(iter(r.members()))
        print(f"  BytesIO: compressed_size={m.compressed_size} hashes={bool(m.hashes)}")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "x.gz"
        p.write_bytes(raw)
        with open_archive(p) as r:
            m = next(iter(r.members()))
            print(
                f"  Path:    compressed_size={m.compressed_size} hashes={bool(m.hashes)}"
            )


def r12_password_header_vs_data() -> None:
    """Header-encrypted RAR fails at open; data-encrypt lists without password (F15)."""
    print("\n[R12] password laziness header vs data (F15)")
    header = Path("tests/fixtures/rar/encrypted_header__.rar")
    data = Path("tests/fixtures/rar/encryption__.rar")
    if header.exists():
        try:
            with open_archive(header) as r:
                list(r.members())
            print("  header-enc no pw: UNEXPECTED OK")
        except EncryptionError as e:
            print(f"  header-enc no pw: EncryptionError at open/list (eager) — {e}")
    if data.exists():
        with open_archive(data) as r:
            n = len(list(r.members()))
            print(f"  data-enc no pw: list OK n={n} (lazy)")
            try:
                m = next(m for m in r.members() if m.is_encrypted)
                r.read(m)
                print("  data-enc read: UNEXPECTED OK")
            except EncryptionError:
                print("  data-enc read: EncryptionError (lazy confirm)")
            except StopIteration:
                print("  data-enc: no encrypted FILE member")


def r13_is_current_last_wins_and_rar_history() -> None:
    print("\n[R13] is_current last-wins + RAR path;n (F12 spot-check)")
    # ZIP duplicates via two same-name writes
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("dup.txt", b"first")
        z.writestr("dup.txt", b"second")
    buf.seek(0)
    with open_archive(buf) as r:
        ms = [m for m in r.members() if m.name == "dup.txt"]
        print(
            f"  ZIP dups is_current={[m.is_current for m in ms]} (want [False, True])"
        )
    rar_hist = list(Path("tests/fixtures/rar").glob("*ver*"))[:2]
    for p in rar_hist:
        try:
            with open_archive(p) as r:
                flagged = [(m.name, m.is_current) for m in r.members() if ";" in m.name]
                if flagged:
                    print(f"  {p.name}: history rows {flagged[:4]}")
        except ArchiveyError as e:
            print(f"  {p.name}: {_n(e)}")


def r14_install_hints_no_7z_extra() -> None:
    print("\n[R14] install hints — no archivey[7z] leftover (F11)")
    from archivey.internal.streams import codecs as codecs_mod

    hits = []
    for name in dir(codecs_mod):
        obj = getattr(codecs_mod, name)
        req = getattr(obj, "requirement", None)
        if req is not None and hasattr(req, "install_hint"):
            hint = req.install_hint
            if "[7z]" in hint:
                hits.append((name, hint))
            else:
                print(f"  {name}: {hint}")
    if not hits:
        print("  no [7z] hints in StreamCodec requirements (OK)")
    else:
        print(f"  LEFTOVER: {hits}")


def _n(e: BaseException) -> str:
    return f"{type(e).__module__}.{type(e).__name__}: {e}"


def main() -> None:
    r1_pipe_refusal_uniformity()
    r2_tar_corrupt_final_header()
    r3_encryption_shape_divergence()
    r4_memberstreams_vs_open_stream_vocab()
    r5_cli_extraction_progress_import()
    r6_open_stream_vs_open_archive_gz()
    r7_zip_closed_valueerror_leak()
    r8_volumes_valueerror_leak()
    r9_encoding_silent_discard()
    r10_zip_already_closed_misclassified()
    r11_compressed_size_path_gate()
    r12_password_header_vs_data()
    r13_is_current_last_wins_and_rar_history()
    r14_install_hints_no_7z_extra()
    print("\nDone.")


if __name__ == "__main__":
    main()
