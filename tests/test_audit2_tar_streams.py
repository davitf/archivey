"""Second audit, reproducers: TAR and single-file compressed streams.

Each test asserts the promised behaviour and is marked ``xfail(strict=True)`` with the
defect it reproduces, so the file stays green until a fix lands and then flags the
marker for removal. The promise each test checks is named in its docstring.
"""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import os
import random
import struct
import tarfile
import zlib
from pathlib import Path

import pytest

from archivey import (
    AcceleratorMode,
    ArchiveFormat,
    ArchiveyConfig,
    ExtractionPolicy,
    open_archive,
)
from archivey.diagnostics import DiagnosticCode
from archivey.exceptions import ArchiveyError, CorruptionError
from tests.conftest import requires
from tests.test_audit_tar_streams import _TRAILER, _gnu_sparse, _member

_ACCEL_ON = ArchiveyConfig(
    use_rapidgzip=AcceleratorMode.ON, use_indexed_bzip2=AcceleratorMode.ON
)


def _read_single(
    data: bytes,
    fmt: ArchiveFormat,
    *,
    config: ArchiveyConfig | None = None,
    streaming: bool = False,
    seekable_members: bool = False,
) -> tuple[bytes, dict[DiagnosticCode, int]]:
    """The one member of a single-file stream, and the diagnostics the read left."""
    with open_archive(
        io.BytesIO(data),
        format=fmt,
        config=config,
        streaming=streaming,
        seekable_members=seekable_members and not streaming,
    ) as ar:
        if streaming:
            out = b""
            for _member_, stream in ar.stream_members():
                assert stream is not None
                out = stream.read()
        else:
            with ar.open(ar.members()[0]) as stream:
                out = stream.read()
        return out, dict(ar.diagnostics.counts)


# ---------------------------------------------------------------------------
# T14: xz with a check type liblzma does not implement is read unverified, silently
# ---------------------------------------------------------------------------


def _xz_with_check_id(raw: bytes, check_id: int) -> bytes:
    """``raw`` as a one-block xz whose stream flags name ``check_id``.

    Built as CRC32 (a 4-byte check field), then the header and footer flags rewritten
    with their CRCs fixed. IDs 2 and 3 are reserved with a 4-byte field too, so the
    block layout stays valid; liblzma knows the size but not the algorithm.
    """
    data = bytearray(lzma.compress(raw, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC32))
    flags = bytes([0, check_id])
    data[6:8] = flags
    data[8:12] = struct.pack("<I", zlib.crc32(flags))
    data[-4:-2] = flags
    data[-12:-8] = struct.pack("<I", zlib.crc32(bytes(data[-8:-2])))
    return bytes(data)


def _flip_in_stored_body(compressed: bytes, raw: bytes) -> bytes:
    """``compressed`` with one bit flipped inside ``raw``'s verbatim-stored bytes."""
    middle = len(raw) // 2
    at = compressed.find(raw[middle : middle + 100])
    assert at >= 0, "fixture premise: the body is stored verbatim (LZMA2 raw chunk)"
    damaged = bytearray(compressed)
    damaged[at + 50] ^= 0x01
    return bytes(damaged)


@pytest.mark.xfail(
    strict=True,
    reason="T14: xz with an unimplemented check ID is decoded unverified, with no "
    "error and no DIGEST_UNVERIFIABLE",
)
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("check_id", [2, 3])
def test_xz_unsupported_check_type_is_not_silent(
    check_id: int, streaming: bool
) -> None:
    """D6b: 'every integrity check the archive offers must have run'. The stream
    declares a check; liblzma cannot compute it and skips it without telling
    CPython (which never asks for LZMA_TELL_UNSUPPORTED_CHECK), so a flipped byte in
    the body reads as good data. ``xz -t`` says "Unsupported type of integrity
    check; not verifying file integrity" and exits 2. ``lzma_error_to_archivey``
    already maps "Unsupported integrity check" to UnsupportedFeatureError, but that
    error is never raised."""
    raw = random.Random(1).randbytes(5000)
    damaged = _flip_in_stored_body(_xz_with_check_id(raw, check_id), raw)
    try:
        out, diagnostics = _read_single(damaged, ArchiveFormat.XZ, streaming=streaming)
    except ArchiveyError:
        return
    assert out != raw, "fixture premise: the flipped byte reaches the output"
    assert DiagnosticCode.DIGEST_UNVERIFIABLE in diagnostics, (
        "damaged xz content returned with no error and no diagnostic"
    )


@pytest.mark.xfail(
    strict=True,
    reason="T14: a .tar.xz with an unimplemented check ID serves damaged member "
    "data silently",
)
@pytest.mark.parametrize("streaming", [False, True])
def test_tar_xz_unsupported_check_type_is_not_silent(streaming: bool) -> None:
    """The same gap through the TAR reader: the member reads back damaged, and
    neither the member read nor the end scan says anything."""
    payload = random.Random(2).randbytes(200_000)
    tar = io.BytesIO()
    with tarfile.open(fileobj=tar, mode="w") as t:
        info = tarfile.TarInfo("a")
        info.size = len(payload)
        t.addfile(info, io.BytesIO(payload))
    raw_tar = tar.getvalue()
    damaged = _flip_in_stored_body(_xz_with_check_id(raw_tar, 2), payload)
    try:
        with open_archive(
            io.BytesIO(damaged), format=ArchiveFormat.TAR_XZ, streaming=streaming
        ) as ar:
            read = [s.read() for _m, s in ar.stream_members() if s is not None]
            diagnostics = dict(ar.diagnostics.counts)
    except ArchiveyError:
        return
    assert read != [payload], "fixture premise: the flipped byte reaches the member"
    assert DiagnosticCode.DIGEST_UNVERIFIABLE in diagnostics


# ---------------------------------------------------------------------------
# T15: the bzip2 accelerator never checks a stream's combined CRC
# ---------------------------------------------------------------------------

_BZ2_BLOCK_MAGIC = f"{0x314159265359:048b}"
_BZ2_EOS_MAGIC = f"{0x177245385090:048b}"


def _bits(data: bytes) -> str:
    return "".join(f"{b:08b}" for b in data)


def _from_bits(bits: str) -> bytes:
    bits += "0" * (-len(bits) % 8)
    return bytes(int(bits[i : i + 8], 2) for i in range(0, len(bits), 8))


def _bz2_without_a_block() -> tuple[bytes, bytes]:
    """A four-block ``.bz2`` and the same stream with its second block cut out.

    Blocks are bit-aligned and each carries its own CRC, so the three that remain
    still decode and pass their checks. Only the stream's combined CRC, in the
    end-of-stream marker, covers the block sequence.
    """
    payload = b"".join(random.Random(i).randbytes(60_000) for i in range(6))
    good = bz2.compress(payload, 1)
    bits = _bits(good)
    starts = []
    at = bits.find(_BZ2_BLOCK_MAGIC)
    while at != -1:
        starts.append(at)
        at = bits.find(_BZ2_BLOCK_MAGIC, at + 1)
    assert len(starts) >= 3, "fixture premise: several blocks"
    return payload, _from_bits(bits[: starts[1]] + bits[starts[2] :])


def _bz2_with_bad_combined_crc() -> tuple[bytes, bytes]:
    payload = random.Random(3).randbytes(300_000)
    bits = list(_bits(bz2.compress(payload)))
    at = "".join(bits).rfind(_BZ2_EOS_MAGIC) + 48 + 5
    bits[at] = "1" if bits[at] == "0" else "0"
    return payload, _from_bits("".join(bits))


def test_bz2_fixtures_are_refused_by_the_standard_library() -> None:
    """Premise for T15: both damaged streams fail ``bz2`` and archivey's own
    standard-library path."""
    for _payload, damaged in (_bz2_without_a_block(), _bz2_with_bad_combined_crc()):
        with pytest.raises(OSError):
            bz2.decompress(damaged)
        with pytest.raises(CorruptionError):
            _read_single(damaged, ArchiveFormat.BZ2)


@requires("rapidgzip")
@pytest.mark.xfail(
    strict=True,
    reason="T15: the bzip2 accelerator skips the combined stream CRC, so a .bz2 "
    "missing a whole block reads short with no error",
)
def test_bz2_accelerator_refuses_a_stream_with_a_block_removed() -> None:
    """compressed-streams: 'An accelerator preserves the error contract of the path
    it replaces'; bzip2.md says every block's CRC and the combined CRC are checked on
    read. Under the default config a caller who declares ``seekable_members=True``
    gets the accelerator (AUTO), and 100 000 of the 360 000 bytes are silently
    missing from the result."""
    payload, damaged = _bz2_without_a_block()
    try:
        out, _diagnostics = _read_single(
            damaged, ArchiveFormat.BZ2, config=ArchiveyConfig(), seekable_members=True
        )
    except CorruptionError:
        return
    assert out == payload, f"read {len(out)} of {len(payload)} bytes with no error"


@requires("rapidgzip")
@pytest.mark.xfail(
    strict=True,
    reason="T15: the bzip2 accelerator accepts a wrong combined stream CRC",
)
def test_bz2_accelerator_checks_the_combined_crc() -> None:
    """The narrow form: one bit flipped in the end-of-stream CRC."""
    _payload, damaged = _bz2_with_bad_combined_crc()
    with pytest.raises(CorruptionError):
        _read_single(
            damaged, ArchiveFormat.BZ2, config=_ACCEL_ON, seekable_members=True
        )


# ---------------------------------------------------------------------------
# T16: the gzip accelerator accepts members the standard library refuses
# ---------------------------------------------------------------------------


def _gzip_cases() -> dict[str, bytes]:
    payload = random.Random(4).randbytes(200_000)
    member = gzip.compress(payload, mtime=0)
    header = bytearray(member[:10])
    header[3] |= 0x02  # FHCRC
    crc16 = (zlib.crc32(bytes(header)) & 0xFFFF) ^ 0x0001
    wrong_isize = member[:-4] + struct.pack("<I", len(payload) + 7)
    return {
        "header-crc-mismatch": bytes(header) + struct.pack("<H", crc16) + member[10:],
        "reserved-flag-bit": member[:3] + bytes([0x20]) + member[4:],
        "first-member-isize": wrong_isize + member,
    }


@requires("rapidgzip")
@pytest.mark.xfail(
    strict=True,
    reason="T16: rapidgzip accepts a gzip header CRC mismatch, reserved FLG bits "
    "and a wrong ISIZE on a non-final member",
)
@pytest.mark.parametrize("case", sorted(_gzip_cases()))
def test_gzip_accelerator_refuses_what_the_stdlib_refuses(case: str) -> None:
    """compressed-streams: 'An accelerator preserves the error contract of the path
    it replaces'. The standard-library path raises CorruptionError on each (zlib:
    "header crc mismatch", "unknown header flags set", "incorrect length check");
    under ``use_rapidgzip=ON`` each reads as good data. RFC 1952 §2.3.1.2 requires
    an error for reserved flag bits."""
    data = _gzip_cases()[case]
    with pytest.raises(CorruptionError):
        _read_single(data, ArchiveFormat.GZ)
    with pytest.raises(CorruptionError):
        _read_single(data, ArchiveFormat.GZ, config=_ACCEL_ON, seekable_members=True)


# ---------------------------------------------------------------------------
# T17: accelerator member streams seek differently from the stdlib ones
# ---------------------------------------------------------------------------


def _single_member_stream_positions(
    data: bytes, fmt: ArchiveFormat, config: ArchiveyConfig
) -> tuple[object, object]:
    with open_archive(
        io.BytesIO(data), format=fmt, config=config, seekable_members=True
    ) as ar:
        with ar.open(ar.members()[0]) as stream:
            past_end = stream.seek(1500)
            stream.seek(100)
            try:
                underflow: object = stream.seek(-500, io.SEEK_CUR)
            except Exception as exc:  # noqa: BLE001 - the type is what is compared
                underflow = type(exc)
    return past_end, underflow


@requires("rapidgzip")
@pytest.mark.xfail(
    strict=True,
    reason="T17: under rapidgzip, seek past the end returns the size, and a gzip "
    "SEEK_CUR underflow raises a raw ValueError; the stdlib path does neither",
)
@pytest.mark.parametrize("codec", ["gz", "bz2"])
def test_accelerated_member_stream_seeks_like_the_stdlib_one(codec: str) -> None:
    """An accelerator changes speed, not behaviour. On a 1000-byte member the
    stdlib path (and ``io.BytesIO``) returns 1500 for ``seek(1500)`` and clamps
    ``seek(-500, SEEK_CUR)`` from 100 to 0. rapidgzip returns 1000 for the first; the
    gzip child raises ``ValueError: negative seek position -400`` for the second."""
    payload = b"x" * 1000
    if codec == "gz":
        data, fmt = gzip.compress(payload), ArchiveFormat.GZ
    else:
        data, fmt = bz2.compress(payload), ArchiveFormat.BZ2
    off = ArchiveyConfig(
        use_rapidgzip=AcceleratorMode.OFF, use_indexed_bzip2=AcceleratorMode.OFF
    )
    assert _single_member_stream_positions(data, fmt, off) == (1500, 0)
    assert _single_member_stream_positions(data, fmt, _ACCEL_ON) == (1500, 0)


# ---------------------------------------------------------------------------
# T18: a TAR uid/gid outside uid_t reaches os.chown as a raw OverflowError
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="POSIX ownership")
@pytest.mark.xfail(
    strict=True,
    reason="T18: a TAR uid/gid outside uid_t aborts extract_all(TRUSTED) as root "
    "with a raw OverflowError",
)
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("uid", [2**40, -2], ids=["2**40", "-2"])
def test_tar_out_of_range_uid_does_not_abort_trusted_extraction(
    uid: int, streaming: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """error-handling: nothing the library raises is a builtin. A PAX ``uid`` record
    (or a base-256 uid field) can hold any integer; the TAR reader passes it through
    unchecked, and ``_apply_metadata`` catches only ``OSError`` around ``os.chown``,
    which raises ``OverflowError`` before any syscall. ``geteuid`` is patched to 0,
    which is all the TRUSTED-as-root branch checks; the overflow happens before the
    kernel could refuse a non-root caller."""
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    data = _member("f", b"x", pax_headers={"uid": str(uid)}) + _TRAILER
    try:
        with open_archive(io.BytesIO(data), streaming=streaming) as ar:
            ar.extract_all(tmp_path / "out", policy=ExtractionPolicy.TRUSTED)
    except ArchiveyError:
        pass


# ---------------------------------------------------------------------------
# T19: a sparse member's real size is not held to the T3 bound
# ---------------------------------------------------------------------------


def _sparse_huge(kind: str, realsize: int) -> bytes:
    if kind == "old-gnu":
        return (
            _gnu_sparse("sp", [(0, 10)], realsize, b"A" * 10)
            + _member("b", b"bye")
            + _TRAILER
        )
    pax = {
        "GNU.sparse.major": "1",
        "GNU.sparse.minor": "0",
        "GNU.sparse.name": "sp",
        "GNU.sparse.realsize": str(realsize),
    }
    body = b"1\n0\n5\n".ljust(512, b"\0") + b"BBBBB"
    return _member("GNUSparseFile.0/sp", body, pax_headers=pax) + _TRAILER


@pytest.mark.xfail(
    strict=True,
    reason="T19: a sparse real size of 2**70 lists, then read() raises a raw "
    "OverflowError from tarfile's hole fill",
)
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("kind", ["old-gnu", "pax-1.0"])
def test_tar_sparse_realsize_past_any_file_is_typed(kind: str, streaming: bool) -> None:
    """error-handling: archive content never surfaces as a builtin exception. T3
    refused a plain size of 2**63 or more; a sparse member's logical size comes from
    the GNU ``realsize`` field or ``GNU.sparse.realsize`` instead, and nothing bounds
    it. tarfile fills the trailing hole with ``NUL * length``, which raises
    ``OverflowError: cannot fit 'int' into an index-sized integer`` (and at 2**63
    exactly, ``MemoryError``). A 2 KiB archive; a chunked read(65536) works."""
    data = _sparse_huge(kind, 2**70)
    try:
        with open_archive(io.BytesIO(data), streaming=streaming) as ar:
            for member, stream in ar.stream_members():
                if stream is not None and member.name == "sp":
                    stream.read()
    except ArchiveyError:
        pass
