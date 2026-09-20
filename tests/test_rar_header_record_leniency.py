"""A malformed *optional* RAR5 extra record drops the record, not the archive.

The extra area of a RAR5 FILE header is a list of optional records. The parser has
always ignored a record whose type it does not implement; a record whose type it
*does* implement but whose body it cannot parse used to raise out of
``open_archive``, discarding every member that parsed — over a checksum. ``unrar``
7.00 lists such an archive. These pin the new posture and its one exception.

Found by the #315 sweep (batch S16, finding R2-K7).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from archivey import (
    ArchiveyConfig,
    DiagnosticCode,
    DiagnosticPolicy,
    DiagnosticRaisedError,
    MemberHeaderRecordContext,
    open_archive,
)
from archivey.exceptions import CorruptionError
from archivey.internal.backends.rar_parser import (
    _MAX_SKIPPED_HEADER_RECORDS,
    load_vint,
)
from archivey.types import HashAlgorithm, MemberType
from tests.atheris_fuzz.crc_fixup import fixup_rar_header_crcs

_FIXTURES = Path(__file__).parent / "fixtures" / "rar"

# A record type no RAR version defines and the parser therefore ignores. Used to
# fill the bytes a shrunk record orphans, so the walk still reaches every later
# record and the mutation touches exactly one of them.
_UNIMPLEMENTED_XTYPE = 100

_XTYPE_CRYPT = 1
_XTYPE_HASH = 2
_XTYPE_TIME = 3
_XTYPE_VERSION = 4
_XTYPE_REDIR = 5


class _Record:
    """One FHEXTRA record located inside a fixture's bytes."""

    def __init__(self, size_at: int, size: int, body_at: int, xtype: int) -> None:
        self.size_at = size_at  # offset of the record's own ``xsize`` vint
        self.size = size
        self.body_at = body_at
        self.xtype = xtype


def _extra_records(data: bytes) -> list[_Record]:
    """Every FHEXTRA record in every RAR5 FILE header, in file order.

    Offsets are found rather than hard-coded because the fixtures are regenerated
    by ``scripts/gen_rar_fixtures.py`` and their layout is not pinned.
    """
    found: list[_Record] = []
    pos = 8  # past the RAR5 signature
    while pos < len(data):
        pos += 4  # header CRC32
        header_size, pos = load_vint(data, pos)
        header_start, header_end = pos, pos + header_size
        header_type, p = load_vint(data, header_start)
        if header_type != 2:  # not FILE
            pos = header_end
            continue
        header_flags, p = load_vint(data, p)
        if header_flags & 0x0001:  # has extra area
            _extra_size, p = load_vint(data, p)
        data_size = 0
        if header_flags & 0x0002:  # has data area
            data_size, p = load_vint(data, p)
        file_flags, p = load_vint(data, p)
        _unpacked_size, p = load_vint(data, p)
        _attributes, p = load_vint(data, p)
        if file_flags & 0x0002:  # mtime
            p += 4
        if file_flags & 0x0004:  # CRC32
            p += 4
        _compression, p = load_vint(data, p)
        _host_os, p = load_vint(data, p)
        name_length, p = load_vint(data, p)
        p += name_length
        while p < header_end - 1:  # the parser allows one padding byte
            size_at = p
            size, p = load_vint(data, p)
            xtype, _ = load_vint(data, p)
            found.append(_Record(size_at, size, p, xtype))
            p += size
        pos = header_end + data_size
    return found


def _only_record(data: bytes, xtype: int) -> _Record:
    matches = [r for r in _extra_records(data) if r.xtype == xtype]
    if len(matches) != 1:
        pytest.fail(
            f"expected exactly one record of type {xtype}, found {len(matches)}"
        )
    return matches[0]


def _shrink_record(data: bytes, record: _Record, new_size: int) -> bytes:
    """Cut one record's declared size so its own body read runs off the end.

    The bytes the cut orphans become a filler record of an unimplemented type, so
    the walk lands on every *later* record exactly where it did before and the
    mutation is confined to the one record under test. The enclosing header's CRC
    is recomputed, so the archive stays structurally valid — which is the whole
    point: this is a well-formed header carrying one bad record, not a corrupt file.
    """
    gap = record.size - new_size
    assert record.size < 128 and new_size >= 1, "both sizes must be one-byte vints"
    assert gap >= 2, "the gap must hold a filler record's size and type vints"
    buf = bytearray(data)
    buf[record.size_at] = new_size
    buf[record.body_at + new_size] = gap - 1  # the filler's own xsize
    buf[record.body_at + new_size + 1] = _UNIMPLEMENTED_XTYPE
    return fixup_rar_header_crcs(bytes(buf), broken=False)


def _retype_record(data: bytes, record: _Record, new_type: int) -> bytes:
    """Relabel a record as a type the parser does not implement, size unchanged."""
    assert new_type < 128, "the type vint must stay one byte wide"
    buf = bytearray(data)
    buf[record.body_at] = new_type
    return fixup_rar_header_crcs(bytes(buf), broken=False)


@pytest.fixture(scope="module")
def short_hash_archive(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """``blake2sp.rar`` with its BLAKE2sp record too short for the digest it declares."""
    data = (_FIXTURES / "blake2sp.rar").read_bytes()
    record = _only_record(data, _XTYPE_HASH)
    path = tmp_path_factory.mktemp("rar-short-hash") / "short_hash.rar"
    path.write_bytes(_shrink_record(data, record, new_size=3))
    return path


def test_a_short_checksum_record_lists_the_member_without_the_digest(
    short_hash_archive: Path,
) -> None:
    """The record is dropped, the member survives, and the digest is absent — not
    truncated, not guessed. Before this change the whole archive raised."""
    original = _FIXTURES / "blake2sp.rar"
    with open_archive(original) as archive:
        (intact,) = archive.members()
        assert HashAlgorithm.BLAKE2SP in intact.hashes, (
            "the fixture must carry the digest, or the test below proves nothing"
        )

    with open_archive(short_hash_archive) as archive:
        (member,) = archive.members()
        assert member.name == intact.name
        assert HashAlgorithm.BLAKE2SP not in member.hashes
        # The TIME record sits after the damaged one; a dropped record must not
        # cost the walk the records behind it.
        assert member.modified == intact.modified

        (diagnostic,) = member.diagnostics
        assert diagnostic.code is DiagnosticCode.MEMBER_HEADER_RECORD_SKIPPED
        context = diagnostic.context
        assert isinstance(context, MemberHeaderRecordContext)
        assert context.member_name == member.name
        assert context.record == "hash"
        assert context.record_id == _XTYPE_HASH
        assert context.reason


def test_a_short_checksum_record_still_refuses_the_archive_under_strict(
    short_hash_archive: Path,
) -> None:
    """Leniency is the default, not the only option: the code is in
    ``ARCHIVE_INTEGRITY_CODES``, so a caller who wants the old refusal asks for it."""
    with pytest.raises(DiagnosticRaisedError) as raised:
        with open_archive(
            short_hash_archive,
            config=ArchiveyConfig(diagnostic_policy=DiagnosticPolicy.strict()),
        ) as archive:
            archive.members()
    assert raised.value.diagnostic.code is DiagnosticCode.MEMBER_HEADER_RECORD_SKIPPED


def test_a_short_encryption_record_still_refuses_the_archive(tmp_path: Path) -> None:
    """The one exception. Dropping this record would leave the encryption parameters
    unset, and a member with no encryption parameters is presented as *plaintext* —
    a wrong answer rather than a missing one."""
    data = (_FIXTURES / "encryption__.rar").read_bytes()
    record = next(r for r in _extra_records(data) if r.xtype == _XTYPE_CRYPT)
    path = tmp_path / "short_crypt.rar"
    path.write_bytes(_shrink_record(data, record, new_size=3))

    with pytest.raises(CorruptionError):
        with open_archive(path, password="password") as archive:
            archive.members()


def test_an_unimplemented_record_type_lists_silently(tmp_path: Path) -> None:
    """The pre-existing tolerance for unknown record types must not become a
    diagnostic, or every archive written by a newer RAR would report one."""
    data = (_FIXTURES / "blake2sp.rar").read_bytes()
    record = _only_record(data, _XTYPE_TIME)
    path = tmp_path / "unknown_type.rar"
    path.write_bytes(_retype_record(data, record, _UNIMPLEMENTED_XTYPE))

    with open_archive(path) as archive:
        (member,) = archive.members()
        assert not member.diagnostics
        assert archive.diagnostics.counts == {}


@pytest.mark.skipif(shutil.which("unrar") is None, reason="needs the unrar CLI")
def test_unrar_lists_the_short_checksum_archive_too(short_hash_archive: Path) -> None:
    """The oracle for why refusing it was wrong: RARLAB's own tool reads this file."""
    result = subprocess.run(
        ["unrar", "l", "-p-", str(short_hash_archive)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "store.txt" in result.stdout


def test_a_short_redirect_record_lists_a_symlink_as_a_plain_file(
    tmp_path: Path,
) -> None:
    """The failure direction for a dropped `redir`, pinned because it is what decides
    whether this record belongs in the fatal set with encryption.

    It does not: the member loses its link nature and lists as an empty regular file,
    so extracting writes a file where a symlink was. That is the *under*-privileged
    direction — nothing is created pointing somewhere it should not. Contrast the
    encryption record, where the drop would present ciphertext as plaintext.
    """
    data = (_FIXTURES / "symlinks_solid__.rar").read_bytes()
    record = next(r for r in _extra_records(data) if r.xtype == _XTYPE_REDIR)
    path = tmp_path / "short_redir.rar"
    path.write_bytes(_shrink_record(data, record, new_size=1))

    with open_archive(path) as archive:
        members = {m.name: m for m in archive.members()}
        damaged = members["subdir/link_to_file1.txt"]
        assert damaged.type is MemberType.FILE
        assert damaged.link_target is None
        assert [d.code for d in damaged.diagnostics] == [
            DiagnosticCode.MEMBER_HEADER_RECORD_SKIPPED
        ]
        # The siblings keep their targets: one bad record costs one record.
        assert members["symlink_to_file1.txt"].link_target == "file1.txt"


def test_a_short_version_record_does_not_promote_a_stale_revision(
    tmp_path: Path,
) -> None:
    """The other candidate for the fatal set, and the same answer.

    A `-ver` revision carries its index in the version record, and RAR5 has it
    nowhere else — so dropping it costs the `;n` suffix and the member collides by
    name with the live revision. It does *not* become the live one: duplicate names
    are resolved last-entry-wins archive-wide, and the live revision is written last.
    So the archive lists one extra non-current entry, which is how every duplicate
    name already presents, rather than serving stale bytes as current.
    """
    data = (_FIXTURES / "file_version__.rar").read_bytes()
    record = next(r for r in _extra_records(data) if r.xtype == _XTYPE_VERSION)
    path = tmp_path / "short_version.rar"
    path.write_bytes(_shrink_record(data, record, new_size=1))

    with open_archive(path) as archive:
        members = archive.members()
        current = [m for m in members if m.is_current]
        assert [m.name for m in current] == ["file.txt"]
        assert not current[0].diagnostics, (
            "the live revision must be the untouched one, not the damaged member"
        )
        damaged = next(m for m in members if m.diagnostics)
        assert damaged.name == "file.txt" and not damaged.is_current


def _zero_extra_area(data: bytes) -> bytes:
    """Replace every FHEXTRA record body with zeros, header CRC recomputed.

    Each zero byte is an ``xsize == 0`` record: one attacker byte, one skip.
    """
    records = _extra_records(data)
    assert records, "fixture must have an extra area to zero"
    start = records[0].size_at
    last = records[-1]
    end = last.body_at + last.size
    buf = bytearray(data)
    buf[start:end] = b"\x00" * (end - start)
    return fixup_rar_header_crcs(bytes(buf), broken=False)


def test_a_zeroed_extra_area_does_not_retain_one_skip_per_byte(tmp_path: Path) -> None:
    """Leniency is not a listing-cost bomb: the extra-area walk stops.

    ``blake2sp.rar``'s extra area is 46 bytes. Filling it with zeros used to
    retain 45 skipped records — one per byte — because ``xsize == 0`` advances
    ``pos`` by one and records a skip. ``max_members`` cannot see that: it is
    one member. The cap is the bound; the one-bad-record tests above stay at
    exactly one diagnostic.
    """
    data = (_FIXTURES / "blake2sp.rar").read_bytes()
    records = _extra_records(data)
    extra_bytes = (records[-1].body_at + records[-1].size) - records[0].size_at
    assert extra_bytes > _MAX_SKIPPED_HEADER_RECORDS, (
        "the fixture extra area must be larger than the cap, or this test "
        "cannot fail against an unbounded walk"
    )
    path = tmp_path / "zero_extra.rar"
    path.write_bytes(_zero_extra_area(data))

    with open_archive(path) as archive:
        (member,) = archive.members()
    assert 1 <= len(member.diagnostics) <= _MAX_SKIPPED_HEADER_RECORDS
    assert all(
        d.code is DiagnosticCode.MEMBER_HEADER_RECORD_SKIPPED
        for d in member.diagnostics
    )
