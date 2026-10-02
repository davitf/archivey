"""Second cross-format audit: the public reading API, parity between backends, and the
``list`` / ``info`` / ``test`` CLI verbs.

These are regression tests for the second audit's findings: each test asserts the
promised behaviour, and the section headings name the finding (``C<n>``, ``X<n>``).
Fixtures are built in the test from bytes, from the declarative corpus, or from the
committed RAR fixtures; nothing new is committed.
"""

from __future__ import annotations

import gzip
import io
import os
import struct
import subprocess
import tarfile
import time
import zipfile
import zlib
from collections.abc import Callable
from pathlib import Path

import pytest

from archivey import ArchiveyConfig, open_archive
from archivey.cli.exit_codes import EXIT_FAIL, EXIT_OK
from archivey.cli.main import main
from archivey.diagnostics import (
    ArchiveEofContext,
    Diagnostic,
    DiagnosticCode,
    DiagnosticDisposition,
    DiagnosticPolicy,
    DiagnosticSummary,
)
from archivey.exceptions import (
    ArchiveyUsageError,
    CorruptionError,
    DiagnosticRaisedError,
    EncryptionError,
    LinkTargetNotFoundError,
    PackageNotInstalledError,
    ReadError,
    TruncatedError,
)
from archivey.internal.backends.rar_parser import (
    parse_rar_archive,
    parse_rar_volumes,
)
from archivey.types import ArchiveMember, MemberType
from tests.conftest import requires, requires_binary
from tests.sample_archives import CORPUS, corpus_archive_path

_RAR_FIXTURES = Path(__file__).parent / "fixtures" / "rar"


# ---------------------------------------------------------------------------
# C1: a truncated RAR lists a silent prefix
# ---------------------------------------------------------------------------


def _vint(buf: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = buf[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, pos


def _rar5_blocks(data: bytes) -> list[tuple[int, int, int, int]]:
    """``(block_pos, block_type, header_end, data_size)`` for every RAR5 block."""
    pos = 8
    blocks = []
    while pos < len(data):
        header_size, start = _vint(data, pos + 4)
        p = start
        block_type, p = _vint(data, p)
        flags, p = _vint(data, p)
        data_size = 0
        if flags & 0x01:
            _, p = _vint(data, p)
        if flags & 0x02:
            data_size, p = _vint(data, p)
        blocks.append((pos, block_type, start + header_size, data_size))
        pos = start + header_size + data_size
    return blocks


def _rar4_blocks(data: bytes) -> list[tuple[int, int, int, int]]:
    """``(block_pos, block_type, header_end, data_size)`` for every RAR 2.9/4 block."""
    pos = 7
    blocks = []
    while pos < len(data):
        _crc, block_type, flags, header_size = struct.unpack_from("<HBHH", data, pos)
        add = struct.unpack_from("<I", data, pos + 7)[0] if flags & 0x8000 else 0
        blocks.append((pos, block_type, pos + header_size, add))
        pos += header_size + add
    return blocks


def _cut_inside_third_file_data(
    data: bytes, blocks: list[tuple[int, int, int, int]], file_type: int
) -> bytes:
    files = [b for b in blocks if b[1] == file_type and b[3] > 0]
    _pos, _type, header_end, data_size = files[1]
    return data[: header_end + data_size // 2]


@pytest.mark.parametrize(
    ("fixture", "walker", "file_type"),
    [
        pytest.param("basic_nonsolid__.rar", _rar5_blocks, 2, id="rar5"),
        pytest.param("basic_nonsolid__rar4.rar", _rar4_blocks, 0x74, id="rar4"),
    ],
)
def test_rar_cut_inside_member_data_reports_truncation(
    fixture: str,
    walker: Callable[[bytes], list[tuple[int, int, int, int]]],
    file_type: int,
) -> None:
    data = (_RAR_FIXTURES / fixture).read_bytes()
    cut = _cut_inside_third_file_data(data, walker(data), file_type)
    with open_archive(io.BytesIO(data)) as reader:
        full = len(reader.members())
    with open_archive(io.BytesIO(cut)) as reader:
        report = reader.members_report()
    # Precondition: the listing really is a strict prefix.
    assert len(report) < full
    # TAR parity: a member whose data runs past the end is a truncated archive.
    assert isinstance(report.error, CorruptionError)


def _tar_two_members() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for name in ("a", "b"):
            info = tarfile.TarInfo(name)
            info.size = 10
            tf.addfile(info, io.BytesIO(b"x" * 10))
    return buf.getvalue()


def test_tar_cut_at_header_boundary_is_reported_control() -> None:
    """Control for the RAR test below: TAR reports the same cut."""
    with open_archive(io.BytesIO(_tar_two_members()[:1024])) as reader:
        reader.members_report()
        counts = reader.diagnostics.counts
    assert counts.get(DiagnosticCode.ARCHIVE_EOF_MARKER_MISSING, 0) == 1


def test_rar5_cut_at_header_boundary_is_not_silent() -> None:
    data = (_RAR_FIXTURES / "basic_nonsolid__.rar").read_bytes()
    files = [b for b in _rar5_blocks(data) if b[1] == 2]
    cut = data[: files[1][0]]  # MAIN + the first FILE block, nothing after
    with open_archive(io.BytesIO(cut)) as reader:
        report = reader.members_report()
        counts = reader.diagnostics.counts
    assert [m.name for m in report] == ["file1.txt"]  # precondition
    assert (
        report.error is not None
        or counts.get(DiagnosticCode.ARCHIVE_EOF_MARKER_MISSING, 0) >= 1
    )


def _eof_marker_diagnostics(summary: DiagnosticSummary) -> list[Diagnostic]:
    return [
        d
        for d in summary.retained
        if d.code is DiagnosticCode.ARCHIVE_EOF_MARKER_MISSING
    ]


def _rar5_without_endarc(data: bytes) -> bytes:
    blocks = _rar5_blocks(data)
    assert blocks[-1][1] == 5  # precondition: the writer put ENDARC last
    return data[: blocks[-1][0]]


@pytest.mark.parametrize("streaming", [False, True], ids=["random", "streaming"])
def test_rar5_without_endarc_warns_once_and_lists(streaming: bool) -> None:
    data = (_RAR_FIXTURES / "basic_nonsolid__.rar").read_bytes()
    with open_archive(io.BytesIO(data)) as reader:
        full = [m.name for m in reader.members()]
    cut = _rar5_without_endarc(data)
    with open_archive(io.BytesIO(cut), streaming=streaming) as reader:
        names = [m.name for m, _ in reader.stream_members()]
        diagnostics = _eof_marker_diagnostics(reader.diagnostics)
    assert names == full
    assert len(diagnostics) == 1
    context = diagnostics[0].context
    assert isinstance(context, ArchiveEofContext)
    assert context.format == "rar"
    assert context.expected_marker == "end_of_archive_block"
    assert context.observed_kind == "absent"


def test_rar5_complete_archive_emits_no_eof_warning() -> None:
    with open_archive(_RAR_FIXTURES / "basic_nonsolid__.rar") as reader:
        reader.members_report()
        counts = reader.diagnostics.counts
    assert counts.get(DiagnosticCode.ARCHIVE_EOF_MARKER_MISSING, 0) == 0


def test_rar5_cut_at_header_boundary_refused_under_strict() -> None:
    data = (_RAR_FIXTURES / "basic_nonsolid__.rar").read_bytes()
    config = ArchiveyConfig(diagnostic_policy=DiagnosticPolicy.strict())
    with open_archive(io.BytesIO(_rar5_without_endarc(data)), config=config) as reader:
        with pytest.raises(DiagnosticRaisedError):
            reader.members()


def test_rar5_volume_set_endarc(tmp_path: Path) -> None:
    parts = [(_RAR_FIXTURES / f"tinyvol.part{n}.rar").read_bytes() for n in (1, 2)]
    for n, part in enumerate(parts, 1):
        (tmp_path / f"tinyvol.part{n}.rar").write_bytes(part)
    with open_archive(tmp_path / "tinyvol.part1.rar") as reader:
        full = [m.name for m in reader.members()]
        assert (
            reader.diagnostics.counts.get(DiagnosticCode.ARCHIVE_EOF_MARKER_MISSING, 0)
            == 0
        )
    # Volume 1 cut before its ENDARC: the split member still continues into
    # volume 2, so the set lists in full, and the missing block is reported.
    (tmp_path / "tinyvol.part1.rar").write_bytes(_rar5_without_endarc(parts[0]))
    with open_archive(tmp_path / "tinyvol.part1.rar") as reader:
        assert [m.name for m in reader.members()] == full
        diagnostics = _eof_marker_diagnostics(reader.diagnostics)
    assert len(diagnostics) == 1
    assert "volume(s) 1 " in diagnostics[0].message


# With header encryption (``rar a -hp``) every header is a salt (RAR3, 8 bytes) or IV
# (RAR5, 16 bytes) and then whole 16-byte AES blocks. The last block of both
# fixtures is the end-of-archive header: RAR5 16 + 16 bytes, RAR3 8 + 16 bytes.
# Cutting 1-16 bytes left a partial AES block that the walk took as a clean end.
_HP_PASSWORD = "header_password"


def _hp_fixture(name: str) -> bytes:
    return (_RAR_FIXTURES / name).read_bytes()


def _assert_truncated_listing(data: bytes, members: int, streaming: bool) -> None:
    with open_archive(
        io.BytesIO(data), password=_HP_PASSWORD, streaming=streaming
    ) as reader:
        report = reader.members_report()
        assert len(report.members) == members
        assert isinstance(report.error, TruncatedError)
        assert "encrypted header" in str(report.error)
        assert not _eof_marker_diagnostics(reader.diagnostics)
    with open_archive(
        io.BytesIO(data), password=_HP_PASSWORD, streaming=streaming
    ) as reader:
        # members() is random-access only; scan_members() is the streaming listing.
        with pytest.raises(TruncatedError):
            reader.scan_members() if streaming else reader.members()


@requires("cryptography")
@pytest.mark.parametrize("streaming", [False, True], ids=["random", "streaming"])
@pytest.mark.parametrize(
    "cut",
    # 1-16: inside the end block's ciphertext; 17-31: inside its IV.
    [1, 2, 8, 15, 16, 17, 24, 31],
)
def test_rar5_header_encrypted_cut_in_last_header_is_truncated(
    cut: int, streaming: bool
) -> None:
    data = _hp_fixture("encrypted_header__.rar")
    _assert_truncated_listing(data[:-cut], members=6, streaming=streaming)


@requires("cryptography")
@pytest.mark.parametrize("cut", [33, 40, 48, 63])
def test_rar5_header_encrypted_cut_inside_a_verified_member_header_is_truncated(
    cut: int,
) -> None:
    """Past the end block, the cut lands in the last directory's 64-byte header. The
    fixture's password check value proves the key, so a header that runs out of
    bytes part-way is a cut, not a wrong password."""
    data = _hp_fixture("encrypted_header__.rar")
    _assert_truncated_listing(data[:-cut], members=5, streaming=False)


@requires("cryptography")
def test_rar5_header_encrypted_cut_at_a_header_boundary_warns() -> None:
    """Without any of the end block's 32 bytes, the walk ends at a header boundary:
    the same warning as a plain RAR5 without its end block."""
    data = _hp_fixture("encrypted_header__.rar")
    with open_archive(io.BytesIO(data[:-32]), password=_HP_PASSWORD) as reader:
        report = reader.members_report()
        assert report.error is None
        assert len(report.members) == 6
        assert len(_eof_marker_diagnostics(reader.diagnostics)) == 1


@requires("cryptography")
@pytest.mark.parametrize("streaming", [False, True], ids=["random", "streaming"])
@pytest.mark.parametrize(
    "cut",
    # 1-16: inside the end block's ciphertext; 17-23: inside its salt.
    [1, 8, 16, 17, 23],
)
def test_rar4_header_encrypted_cut_in_last_header_is_truncated(
    cut: int, streaming: bool
) -> None:
    data = _hp_fixture("encrypted_header__rar4.rar")
    _assert_truncated_listing(data[:-cut], members=6, streaming=streaming)


@requires("cryptography")
@pytest.mark.parametrize("streaming", [False, True], ids=["random", "streaming"])
@pytest.mark.parametrize(
    ("length", "members"),
    # Both lengths end inside a FILE header past its first cipher block, after
    # encrypted headers whose CRC16 matched.
    [(250, 2), (480, 4)],
)
def test_rar4_header_encrypted_cut_after_a_proven_key_is_truncated(
    length: int, members: int, streaming: bool
) -> None:
    """RAR3 has no password check value, but a decrypted header whose CRC16 matches
    proves the key: the walk already takes a mismatch as a wrong password. From then
    on, a header that runs out of bytes part-way is a cut, as in RAR5 with a verified
    check value."""
    data = _hp_fixture("encrypted_header__rar4.rar")
    _assert_truncated_listing(data[:length], members=members, streaming=streaming)


@requires("cryptography")
@pytest.mark.parametrize("length", [44, 60, 91])
def test_rar4_header_encrypted_cut_inside_the_first_encrypted_header_is_a_wrong_password(
    length: int,
) -> None:
    """Inside the first encrypted header (bytes 44-91 of the fixture) no header has
    decrypted yet, so nothing proves the key, and a garbage header size from a wrong
    key also runs to the end of the file. That stays a wrong-password error."""
    data = _hp_fixture("encrypted_header__rar4.rar")
    with pytest.raises(EncryptionError, match="wrong password"):
        with open_archive(io.BytesIO(data[:length]), password=_HP_PASSWORD) as reader:
            reader.members()


def _rar3_reencrypt_header(
    data: bytes, salt_at: int, length: int, edit: Callable[[bytearray], None]
) -> bytes:
    """Decrypt the ``length`` ciphertext bytes of the RAR3 header whose salt starts at
    ``salt_at``, apply ``edit`` to the plaintext and encrypt it again with the same
    key, so a test can change a field the cipher would otherwise scramble."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    from archivey.internal.backends.rar_parser import RarKdfCache

    salt = data[salt_at : salt_at + 8]
    start = salt_at + 8
    key, iv = RarKdfCache().rar3(_HP_PASSWORD, salt)
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    plain = bytearray(cipher.decryptor().update(data[start : start + length]))
    edit(plain)
    encrypted = cipher.encryptor().update(bytes(plain))
    return data[:start] + encrypted + data[start + length :]


# encrypted_header__rar4.rar: the first encrypted header (file1.txt) is its salt at
# 20 and 64 ciphertext bytes; the second (empty_file.txt) is its salt at 124 and 64
# ciphertext bytes; the end block is the last 24 bytes.
_RAR4_HP_SECOND_HEADER = 124


# The same damaged bytes with a wrong password: nothing proves that key, so the
# failure is still a wrong password and a password list goes on to the next one.
_WRONG_PASSWORD_CASE = pytest.param(
    "nope", EncryptionError, r"wrong password\?", id="wrong-password"
)


@requires("cryptography")
@pytest.mark.parametrize(
    ("password", "error", "message"),
    [
        pytest.param(
            _HP_PASSWORD,
            CorruptionError,
            "RAR3 FILE header CRC mismatch",
            id="right-password",
        ),
        _WRONG_PASSWORD_CASE,
    ],
)
def test_rar4_header_encrypted_damage_after_a_proven_key_is_corruption(
    password: str, error: type[Exception], message: str
) -> None:
    """Once a CRC16 match has proved the password, a later header whose CRC16 does
    not match is damage. Bit 0 of byte 164 is in the second FILE header's
    ciphertext, past the first encrypted header that proved the key."""
    data = bytearray(_hp_fixture("encrypted_header__rar4.rar"))
    data[164] ^= 1
    with pytest.raises(error, match=message):
        parse_rar_archive(io.BytesIO(bytes(data)), password=password)


@requires("cryptography")
@pytest.mark.parametrize(
    ("password", "error", "message"),
    [
        pytest.param(
            _HP_PASSWORD,
            CorruptionError,
            "Invalid RAR3 header size: 3",
            id="right-password",
        ),
        _WRONG_PASSWORD_CASE,
    ],
)
def test_rar4_header_encrypted_bad_size_after_a_proven_key_is_corruption(
    password: str, error: type[Exception], message: str
) -> None:
    """A proven password and a header size below the 7-byte minimum that does not
    run to the end of the file: the structural error, not a wrong password."""

    def shrink(plain: bytearray) -> None:
        struct.pack_into("<H", plain, 5, 3)

    data = _rar3_reencrypt_header(
        _hp_fixture("encrypted_header__rar4.rar"),
        _RAR4_HP_SECOND_HEADER,
        64,
        shrink,
    )
    with pytest.raises(error, match=message):
        parse_rar_archive(io.BytesIO(data), password=password)


@requires("cryptography")
def test_rar4_header_encrypted_later_volume_cut_in_its_first_header_is_truncated() -> (
    None
):
    """A set has one password, so a CRC16 match in volume 1 proves it for volume 2.
    Volume 2 cut inside its own first encrypted header is then a cut, after volume
    1's members.

    The set is built from the single-volume fixture: volume 1 is that archive with its
    end block re-encrypted to carry the next-volume flag, and volume 2 is the same
    archive cut at byte 60. ``rar`` 7 cannot write RAR 1.5-4 (no ``-ma4``), so no
    committed RAR 1.5-4 ``-hp`` volume set exists."""
    complete = _hp_fixture("encrypted_header__rar4.rar")

    def next_volume(plain: bytearray) -> None:
        flags = struct.unpack_from("<H", plain, 3)[0] | 0x0001  # ENDARC_NEXT_VOLUME
        struct.pack_into("<H", plain, 3, flags)
        struct.pack_into("<H", plain, 0, zlib.crc32(plain[2:7]) & 0xFFFF)

    volume1 = _rar3_reencrypt_header(complete, len(complete) - 24, 16, next_volume)
    archive = parse_rar_volumes(
        [io.BytesIO(volume1), io.BytesIO(complete[:60])], password=_HP_PASSWORD
    )
    assert len(archive.members) == 6
    assert archive.truncated is not None
    # Byte 20 of volume 2, not of the set: volume 1 is 588 bytes.
    assert (
        "encrypted header that starts at byte 20 (volume 2 of the set"
        in archive.truncated
    )


@requires("cryptography")
@pytest.mark.parametrize(
    "name", ["encrypted_header__.rar", "encrypted_header__rar4.rar"]
)
def test_header_encrypted_cut_inside_packed_data_is_truncated(name: str) -> None:
    """A cut inside the first member's packed data leaves the walk past the end of
    the file where the next header's salt or IV should be: the truncated listing
    a plain RAR gives, not an error at open."""
    data = _hp_fixture(name)
    with open_archive(io.BytesIO(data), password=_HP_PASSWORD) as reader:
        first = reader.members()[0]
    # The fixtures' first member, file1.txt, has 32 bytes of packed data; keep 10.
    assert first.name == "file1.txt"
    offset = {"encrypted_header__.rar": 190, "encrypted_header__rar4.rar": 92}[name]
    with open_archive(io.BytesIO(data[: offset + 10]), password=_HP_PASSWORD) as reader:
        report = reader.members_report()
        assert [m.name for m in report.members] == ["file1.txt"]
        assert isinstance(report.error, TruncatedError)
        assert "packed data" in str(report.error)


# ---------------------------------------------------------------------------
# C2: lzip listing runs a pure-Python GF(2) matrix exponentiation per member
# ---------------------------------------------------------------------------


def _lzip_member(payload: bytes) -> bytes:
    body = lzma_raw(payload)
    header = b"LZIP\x01\x0c"  # version 1, 4 KiB dictionary
    size = len(header) + len(body) + 20
    return header + body + struct.pack("<IQQ", zlib.crc32(payload), len(payload), size)


def lzma_raw(payload: bytes) -> bytes:
    import lzma

    return lzma.compress(
        payload,
        format=lzma.FORMAT_RAW,
        filters=[
            {"id": lzma.FILTER_LZMA1, "dict_size": 1 << 12, "lc": 3, "lp": 0, "pb": 2}
        ],
    )


def test_lzip_listing_cost_does_not_scale_with_declared_sizes() -> None:
    # One real member, then 400 trailer-only members (header + trailer, 26 bytes)
    # each declaring 2**62 bytes. The backward index scan reads only trailers and
    # member magics, so these parse; nothing is decoded.
    fake = b"LZIP\x01\x0c" + struct.pack("<IQQ", 0, 1 << 62, 26)
    blob = _lzip_member(b"hello\n") + fake * 400  # 10 KiB
    start = time.perf_counter()
    with open_archive(io.BytesIO(blob)) as reader:
        reader.members()
    elapsed = time.perf_counter() - start
    # Linear work over 10 KiB of trailers is milliseconds (it was ~1.8 s when
    # crc32_combine rebuilt its GF(2) matrices per call).
    assert elapsed < 0.5, f"listing a 10 KiB .lz took {elapsed:.2f} s"


# ---------------------------------------------------------------------------
# C3: DiagnosticPolicy takes any value and fails open
# ---------------------------------------------------------------------------


def _tar_with_trailing_garbage() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo("a")
        info.size = 1
        tf.addfile(info, io.BytesIO(b"x"))
    return buf.getvalue() + b"GARBAGE" * 10


def test_diagnostic_policy_raise_control() -> None:
    """Control: the enum spelling raises on the trailing data."""
    policy = DiagnosticPolicy(default=DiagnosticDisposition.RAISE)
    config = ArchiveyConfig(diagnostic_policy=policy)
    with pytest.raises(DiagnosticRaisedError):
        with open_archive(io.BytesIO(_tar_with_trailing_garbage()), config=config) as r:
            r.members()


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"default": "raise"}, id="default-str"),
        pytest.param(
            {"overrides": {DiagnosticCode.ARCHIVE_TRAILING_DATA: "raise"}},
            id="override-value-str",
        ),
        pytest.param(
            # The member name, a spelling every coerced enum argument accepts. The
            # lower-case value happens to work only because the enum mixes in str.
            {"overrides": {"ARCHIVE_TRAILING_DATA": DiagnosticDisposition.RAISE}},
            id="override-key-name",
        ),
    ],
)
def test_diagnostic_policy_string_spelling_is_refused_or_honoured(
    kwargs: dict[str, object],
) -> None:
    try:
        policy = DiagnosticPolicy(**kwargs)  # type: ignore[arg-type]
    except ArchiveyUsageError:
        return  # refused at construction: acceptable
    config = ArchiveyConfig(diagnostic_policy=policy)
    with pytest.raises(DiagnosticRaisedError):
        with open_archive(io.BytesIO(_tar_with_trailing_garbage()), config=config) as r:
            r.members()


# ---------------------------------------------------------------------------
# C4: a seek back to the start re-arms the CRC
# ---------------------------------------------------------------------------


def _damaged_stored_zip(path: Path) -> bytes:
    payload = b"LINE-abcde\n" * 3000
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("m.txt", payload)
    data = bytearray(path.read_bytes())
    at = data.find(payload[:200])
    data[at + 5000] ^= 0x20  # the stored CRC no longer matches
    path.write_bytes(bytes(data))
    return payload


def _seek_end_then_start(stream: io.RawIOBase) -> bytes:
    stream.seek(0, io.SEEK_END)  # the size-probe idiom
    stream.seek(0)
    return stream.read()


def _partial_then_start(stream: io.RawIOBase) -> bytes:
    stream.read(10)
    stream.seek(0)
    return stream.read()


# Only the partial-read rewind: newer 3.12 and 3.13+ zipfile seek a stored member on
# the file directly and stop checking its CRC after seek-to-end, so that case is not a
# stable control across patch releases.
def test_stdlib_zipfile_rechecks_crc_after_rewind_control(tmp_path: Path) -> None:
    """Control: stdlib ``zipfile`` resets its running CRC on a rewind and raises."""
    path = tmp_path / "damaged.zip"
    _damaged_stored_zip(path)
    with zipfile.ZipFile(path) as zf, zf.open("m.txt") as stream:
        with pytest.raises(zipfile.BadZipFile):
            _partial_then_start(stream)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "ops",
    [
        pytest.param(_seek_end_then_start, id="seek-end-then-0"),
        pytest.param(_partial_then_start, id="read-then-seek-0"),
    ],
)
def test_full_read_after_rewind_to_start_is_verified(
    ops: Callable[[io.RawIOBase], bytes], tmp_path: Path
) -> None:
    path = tmp_path / "damaged.zip"
    _damaged_stored_zip(path)
    with open_archive(path, seekable_members=True) as reader:
        with reader.open("m.txt") as stream:
            with pytest.raises(CorruptionError):
                ops(stream)  # type: ignore[arg-type]


def _bad_crc_deflated_zip(path: Path, payload: bytes) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("m.txt", payload)
    data = bytearray(path.read_bytes())
    bad = struct.pack("<I", zlib.crc32(payload) ^ 1)
    data[14:18] = bad  # local header CRC
    central = data.find(b"PK\x01\x02")
    data[central + 16 : central + 20] = bad
    path.write_bytes(bytes(data))


def _damaged_copy_7z(path: Path, payload: bytes) -> None:
    import py7zr

    with py7zr.SevenZipFile(path, "w", filters=[{"id": py7zr.FILTER_COPY}]) as zf:
        zf.writestr(payload, "m.txt")
    data = bytearray(path.read_bytes())
    data[data.find(payload[:200]) + 5000] ^= 0x20
    path.write_bytes(bytes(data))


@pytest.mark.parametrize(
    ("build", "name"),
    [
        pytest.param(_bad_crc_deflated_zip, "damaged.zip", id="zip-deflated"),
        pytest.param(
            _damaged_copy_7z, "damaged.7z", id="7z-copy", marks=requires("py7zr")
        ),
    ],
)
def test_rewind_after_a_mid_member_seek_rearms_the_check(
    build: Callable[[Path, bytes], None], name: str, tmp_path: Path
) -> None:
    """Sibling of C4: a seek into the member, then back to 0, then a chunked read."""
    payload = b"LINE-abcde\n" * 3000
    path = tmp_path / name
    build(path, payload)
    with open_archive(path, seekable_members=True) as reader:
        with reader.open("m.txt") as stream:
            stream.seek(5000)
            stream.read(10)
            stream.seek(0)
            with pytest.raises(CorruptionError):
                while stream.read(4096):
                    pass


# ---------------------------------------------------------------------------
# X4: `archivey test` re-opens every 7z / RAR4 symlink and follows it
# ---------------------------------------------------------------------------


def _corpus_symlinks_7z(tmp_path: Path) -> Path:
    entry = next(e for e in CORPUS if e.id == "symlinks")
    return corpus_archive_path(entry, "7z", tmp_path)


def _fixture_rar4_symlinks(tmp_path: Path) -> Path:
    del tmp_path
    return _RAR_FIXTURES / "symlinks_solid__rar4.rar"


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(_corpus_symlinks_7z, id="7z-corpus", marks=requires("py7zr")),
        pytest.param(
            _fixture_rar4_symlinks, id="rar4-fixture", marks=requires_binary("unrar")
        ),
    ],
)
@pytest.mark.skipif(os.name == "nt", reason="the 7z corpus builder needs symlinks")
def test_cli_test_on_intact_symlink_to_directory(
    build: Callable[[Path], Path], tmp_path: Path
) -> None:
    archive = build(tmp_path)
    out, err = io.StringIO(), io.StringIO()
    assert main(["test", str(archive)], out=out, err=err) == EXIT_OK


def _symlink_cycle_tree(root: Path) -> list[str]:
    root.mkdir()
    (root / "f").write_bytes(b"hi\n")
    os.symlink("b", root / "a")
    os.symlink("a", root / "b")
    return ["f", "a", "b"]


@pytest.mark.skipif(os.name == "nt", reason="needs POSIX symlinks")
def test_cli_test_zip_symlink_cycle_control(tmp_path: Path) -> None:
    """Control: the same content as a ZIP tests clean."""
    names = _symlink_cycle_tree(tmp_path / "src")
    archive = tmp_path / "loop.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for name in names:
            src = tmp_path / "src" / name
            if src.is_symlink():
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = 0o120777 << 16
                zf.writestr(info, os.readlink(src))
            else:
                zf.write(src, name)
    assert main(["test", str(archive)], out=io.StringIO(), err=io.StringIO()) == 0


@requires_binary("7z")
@pytest.mark.skipif(os.name == "nt", reason="needs POSIX symlinks")
def test_cli_test_7z_symlink_cycle_is_not_a_failure(tmp_path: Path) -> None:
    names = _symlink_cycle_tree(tmp_path / "src")
    archive = tmp_path / "loop.7z"
    made = subprocess.run(
        ["7z", "a", "-snl", "-bd", "-bso0", str(archive), *names],
        cwd=tmp_path / "src",
        check=False,
    )
    if made.returncode != 0:
        pytest.skip("this 7z cannot store a symlink cycle (macOS builds refuse it)")
    err = io.StringIO()
    assert main(["test", str(archive)], out=io.StringIO(), err=err) == EXIT_OK, (
        err.getvalue()
    )


class _LinkOpenStub:
    """A reader whose ``open()`` reads the link's target, then raises ``exc``."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def open(self, member: ArchiveMember) -> None:
        member.link_target = "target.txt"
        raise self.exc


@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(LinkTargetNotFoundError("Link target not found"), id="absent"),
        pytest.param(ReadError("Link cycle detected at 'a'"), id="cycle"),
        pytest.param(
            ArchiveyUsageError(
                "Cannot open member 'd': type is 'directory' (not a file)"
            ),
            id="directory",
        ),
    ],
)
def test_cli_test_ignores_where_a_link_points(exc: Exception) -> None:
    from archivey.cli.test_cmd import _verify_link

    link = ArchiveMember(type=MemberType.SYMLINK, name="l")
    _verify_link(_LinkOpenStub(exc), link)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(PackageNotInstalledError("pyppmd is not installed"), id="pkg"),
        pytest.param(CorruptionError("CRC mismatch"), id="corrupt"),
        pytest.param(ReadError("some other read error"), id="read"),
        pytest.param(ArchiveyUsageError("The reader is closed"), id="usage"),
    ],
)
def test_cli_test_raises_other_errors_from_a_link_open(exc: Exception) -> None:
    """Only the three errors about where a link points are ignored once its target
    is read; a target that cannot be opened, or a usage error, is not a clean link."""
    from archivey.cli.test_cmd import _verify_link

    link = ArchiveMember(type=MemberType.SYMLINK, name="l")
    with pytest.raises(type(exc)):
        _verify_link(_LinkOpenStub(exc), link)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# X5: an archive-chosen name crashes `list` / `info` on a non-UTF-8 stdout
# ---------------------------------------------------------------------------


def _zip_with_cjk_name_and_comment(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("日本語.txt", b"hi")
        zf.writestr("b.txt", b"x")
        zf.comment = "日本語".encode()


@pytest.mark.parametrize("verb", ["list", "info"])
def test_cli_output_survives_unencodable_member_text(verb: str, tmp_path: Path) -> None:
    archive = tmp_path / "jp.zip"
    _zip_with_cjk_name_and_comment(archive)
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="ascii")  # errors="strict", like a real stdout
    code = main([verb, str(archive)], out=out, err=io.StringIO())
    out.flush()
    assert code in (EXIT_OK, EXIT_FAIL)
    if verb == "list":
        assert b"b.txt" in raw.getvalue()


# ---------------------------------------------------------------------------
# X6: `archivey test` exits 0 when it could not check the stored digest
# ---------------------------------------------------------------------------


def test_cli_test_fails_when_the_stream_digest_went_unchecked(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        payload = b"hello world\n" * 100
        info = tarfile.TarInfo("a.txt")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    raw = buf.getvalue() + b"\0" * (2 << 20)
    # Stored DEFLATE blocks, so a flipped content byte still decodes; only the
    # gzip trailer's CRC-32 can see it.
    data = bytearray(gzip.compress(raw, compresslevel=0))
    at = data.find(b"hello world\nhello")
    data[at + 100] ^= 0x01
    archive = tmp_path / "damaged.tar.gz"
    archive.write_bytes(bytes(data))
    with pytest.raises(OSError):  # precondition: the stdlib sees the damage
        gzip.decompress(bytes(data))
    err = io.StringIO()
    assert main(["test", str(archive)], out=io.StringIO(), err=err) == EXIT_FAIL, (
        err.getvalue()
    )
    # The summary says why the run failed: nothing failed, one digest went unchecked.
    assert "1 OK, 0 failed, 1 not verified" in err.getvalue()


# ---------------------------------------------------------------------------
# C5: three TAR combinations archivey reads have no ArchiveFormat name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stream", ["LZIP", "ZLIB", "BROTLI"])
def test_every_readable_tar_combination_has_a_display_name(stream: str) -> None:
    from archivey.types import ArchiveFormat, ContainerFormat, StreamFormat

    fmt = ArchiveFormat(ContainerFormat.TAR, StreamFormat[stream])
    assert fmt.display_name == f"TAR_{stream}"
