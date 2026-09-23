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
from archivey.exceptions import CorruptionError, TruncatedError
from archivey.internal.backends import rar_unrar
from archivey.internal.backends.rar_parser import (
    _MAX_SKIPPED_HEADER_RECORDS,
    load_vint,
)
from archivey.internal.streams import verify
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


def test_a_zeroed_extra_area_costs_one_skip_not_one_per_byte(tmp_path: Path) -> None:
    """Leniency is not a listing-cost bomb: the walk stops on the first zero.

    ``blake2sp.rar``'s extra area is 46 bytes. Filling it with zeros used to
    retain 45 skipped records — one per byte — because a zero-size record
    advances the cursor by one and recorded a skip. ``max_members`` cannot see
    that: it is one member.

    The cap is no longer what stops this fixture, so this test does not pin the
    cap — ``test_stopping_the_walk_early_is_reported_rather_than_silent`` does,
    against records that are framed correctly. What is pinned here is the exit
    that made the cap necessary: exactly two diagnostics however many zero bytes
    follow, because the first one ends the walk.
    """
    data = (_FIXTURES / "blake2sp.rar").read_bytes()
    records = _extra_records(data)
    extra_bytes = (records[-1].body_at + records[-1].size) - records[0].size_at
    assert extra_bytes > 2, (
        "the fixture extra area must hold more zero bytes than the diagnostics "
        "asserted below, or this test cannot fail against a per-byte walk"
    )
    path = tmp_path / "zero_extra.rar"
    path.write_bytes(_zero_extra_area(data))

    with open_archive(path) as archive:
        (member,) = archive.members()
    # The one dropped record, plus the stand-in saying the rest went unread —
    # not one per zero byte, and not a function of the area's size at all.
    assert len(member.diagnostics) == 2, [d.message for d in member.diagnostics]
    assert all(
        d.code is DiagnosticCode.MEMBER_HEADER_RECORD_SKIPPED
        for d in member.diagnostics
    )
    assert "declared a size of zero" in member.diagnostics[-1].message


def test_stopping_the_walk_early_is_reported_rather_than_silent(
    tmp_path: Path,
) -> None:
    """A capped listing must not look like a complete one.

    Reaching the cap stops the extra area being read, so the records named are what
    was read and not all there was. Without a signal for that, a caller sees sixteen
    drops and cannot tell whether the seventeenth record was fine or never looked at
    — and deciding how far to trust a member's metadata turns on exactly that.

    Driven by records that are framed correctly and only unreadable in their
    bodies, since those are the ones the cap exists for: a broken *size* stops
    the walk on its own, long before any count matters.
    """
    data = (_FIXTURES / "blake2sp.rar").read_bytes()
    path = tmp_path / "capped_truncated.rar"
    path.write_bytes(
        _prepend_extra_bytes(data, _UNTYPED_RECORD * (_MAX_SKIPPED_HEADER_RECORDS + 1))
    )

    with open_archive(path) as archive:
        (member,) = archive.members()

    assert member._raw.skipped_header_records_truncated
    stand_ins = [d for d in member.diagnostics if d.context.list_truncated]
    assert len(stand_ins) == 1, (
        "abandoning the header is reported once, not once per record past the cap"
    )
    assert not stand_ins[0].context.record, (
        "the stand-in names no record; it reports that reading stopped"
    )
    named = [d for d in member.diagnostics if not d.context.list_truncated]
    assert len(named) == _MAX_SKIPPED_HEADER_RECORDS


def test_a_complete_listing_does_not_claim_it_was_cut_short(
    short_hash_archive: Path,
) -> None:
    """The ordinary case the spec scenario pins: one bad record, one diagnostic,
    and nothing claiming the header was abandoned."""
    with open_archive(short_hash_archive) as archive:
        (member,) = archive.members()
    assert not member._raw.skipped_header_records_truncated
    assert [d.context.list_truncated for d in member.diagnostics] == [False]


def test_an_unreadable_extra_size_vint_is_reported(tmp_path: Path) -> None:
    """A size vint that will not decode is not a silent end of extras.

    ``load_vint`` leaves ``pos`` unmoved on failure, so the walk has to stop.
    Stopping without a diagnostic left the member looking intact.
    """
    data = (_FIXTURES / "blake2sp.rar").read_bytes()
    records = _extra_records(data)
    start = records[0].size_at
    end = records[-1].body_at + records[-1].size
    buf = bytearray(data)
    buf[start:end] = b"\x80" * (end - start)
    path = tmp_path / "cont_vint_extra.rar"
    path.write_bytes(fixup_rar_header_crcs(bytes(buf), broken=False))

    with open_archive(path) as archive:
        (member,) = archive.members()
    assert member._raw.skipped_header_records_truncated
    assert any(d.context.list_truncated for d in member.diagnostics)
    assert any(
        d.code is DiagnosticCode.MEMBER_HEADER_RECORD_SKIPPED
        and not d.context.list_truncated
        for d in member.diagnostics
    )


def test_an_overrunning_extra_record_is_reported(tmp_path: Path) -> None:
    """An ``xsize`` past the extra area is not a silent end of extras."""
    data = (_FIXTURES / "blake2sp.rar").read_bytes()
    records = _extra_records(data)
    record = records[0]
    extra_end = records[-1].body_at + records[-1].size
    remaining_after_vint = extra_end - (record.size_at + 1)
    assert remaining_after_vint < 0x7F, (
        "the fixture extra must be smaller than a one-byte vint max, or this "
        "does not overrun"
    )
    buf = bytearray(data)
    buf[record.size_at] = 0x7F
    path = tmp_path / "overrun_extra.rar"
    path.write_bytes(fixup_rar_header_crcs(bytes(buf), broken=False))

    with open_archive(path) as archive:
        (member,) = archive.members()
    assert member._raw.skipped_header_records_truncated
    assert any(
        d.code is DiagnosticCode.MEMBER_HEADER_RECORD_SKIPPED
        and not d.context.list_truncated
        for d in member.diagnostics
    )


def _vint(value: int) -> bytes:
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _prepend_extra_bytes(data: bytes, prefix: bytes) -> bytes:
    """Insert raw bytes at the *front* of the first FILE header's extra area.

    Both size vints are rewritten and the header CRC recomputed, so the header
    stays structurally valid and the records that were already there keep their
    contents — they have simply moved further into the area. That is what makes
    this a test of *where* a record sits rather than of what it holds.
    """
    pos = 8  # past the RAR5 signature
    while pos < len(data):
        crc_at = pos
        pos += 4
        header_size, body_at = load_vint(data, pos)
        header_end = body_at + header_size
        header_type, p = load_vint(data, body_at)
        header_flags, p = load_vint(data, p)
        if header_type != 2:  # not FILE
            # The extra-area size vint comes *before* the data size, so a header
            # carrying both (a SERVICE ``CMT`` or ``QO``) reads its extra size as
            # its data size unless this steps over it. No committed fixture has
            # one today, which is why nothing failed; ``_extra_records`` above
            # walks the same headers and gets the order right.
            if header_flags & 0x0001:
                _extra_size, p = load_vint(data, p)
            data_size = 0
            if header_flags & 0x0002:
                data_size, _ = load_vint(data, p)
            pos = header_end + data_size
            continue
        assert header_flags & 0x0001, "fixture header must have an extra area"
        size_at = p
        extra_size, size_end = load_vint(data, p)
        p = size_end
        if header_flags & 0x0002:  # data area
            _data_size, p = load_vint(data, p)
        file_flags, p = load_vint(data, p)
        _unpacked, p = load_vint(data, p)
        _attributes, p = load_vint(data, p)
        if file_flags & 0x0002:  # mtime
            p += 4
        if file_flags & 0x0004:  # CRC32
            p += 4
        _compression, p = load_vint(data, p)
        _host_os, p = load_vint(data, p)
        name_length, p = load_vint(data, p)
        p += name_length
        extra_at = p
        body = data[body_at:header_end]

        def rel(off: int, base: int = body_at) -> int:
            return off - base

        new_body = (
            body[: rel(size_at)]
            + _vint(extra_size + len(prefix))
            + body[rel(size_end) : rel(extra_at)]
            + prefix
            + body[rel(extra_at) :]
        )
        rebuilt = (
            data[:crc_at]
            + b"\x00\x00\x00\x00"
            + _vint(len(new_body))
            + new_body
            + data[header_end:]
        )
        return fixup_rar_header_crcs(rebuilt, broken=False)
    pytest.fail("fixture has no RAR5 FILE header")


# ``xsize=1``, body ``0x80``: a one-byte body holding a lone vint continuation
# byte, so the record is framed correctly — the next record's offset is known —
# but it cannot name its own type. One dropped record, not a reason to stop.
_UNTYPED_RECORD = b"\x01\x80"


def test_a_zero_length_record_stops_the_walk(tmp_path: Path) -> None:
    """Size zero is not a malformed record, it is a malformed *size*.

    A record's body opens with its type vint, so one byte is the smallest a
    record can be — a type and no payload, which is a legal unimplemented
    record. A declared size of zero names nothing, which means the size vint is
    wrong and so is the offset it puts the next record at. Nothing after it can
    be trusted, so the walk stops and says it stopped.
    """
    data = (_FIXTURES / "blake2sp.rar").read_bytes()
    path = tmp_path / "zero_length_record.rar"
    path.write_bytes(_prepend_extra_bytes(data, b"\x00"))

    with open_archive(path) as archive:
        (member,) = archive.members()

    assert member._raw.skipped_header_records_truncated
    assert any(d.context.list_truncated for d in member.diagnostics)


@pytest.mark.skipif(shutil.which("unrar") is None, reason="needs the unrar CLI")
def test_unrar_gets_the_zero_length_record_wrong(tmp_path: Path) -> None:
    """Why the ``unrar`` oracle does not extend to this case.

    Every other leniency in this module is justified by RARLAB's own tool
    reading the file. Here it reads it and is *wrong*: one zero byte in front of
    an encrypted member's records and ``unrar l`` loses both the encryption
    record and the timestamp, then lists the member as plaintext and exits 0.
    ``*`` is its marker for an encrypted member.
    """
    data = (_FIXTURES / "encryption__.rar").read_bytes()
    clean = tmp_path / "clean.rar"
    clean.write_bytes(data)
    nulled = tmp_path / "zero_length_record.rar"
    nulled.write_bytes(_prepend_extra_bytes(data, b"\x00"))

    def row(path: Path) -> str:
        result = subprocess.run(
            ["unrar", "l", "-p-", "-cfg-", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return next(
            line.strip() for line in result.stdout.splitlines() if "secret.txt" in line
        )

    assert row(clean).startswith("*"), "unrar marks the untouched member encrypted"
    assert not row(nulled).startswith("*"), (
        "the finding this test exists for: one byte and unrar reports an AES "
        "member as plaintext, so 'unrar lists it' is not a reason to follow it"
    )


def test_a_record_whose_type_cannot_be_read_is_dropped_not_fatal(
    tmp_path: Path,
) -> None:
    """The other side of the rule: bad *body*, sound framing, keep walking.

    ``01 80`` declares a one-byte body holding a lone continuation byte, so the
    record cannot name its type — but its size is usable, the next record's
    offset is known, and nothing later is in doubt. That is a dropped record,
    not a reason to stop, and the encryption record behind it is still found.
    """
    data = (_FIXTURES / "encryption__.rar").read_bytes()
    path = tmp_path / "untyped_record.rar"
    path.write_bytes(_prepend_extra_bytes(data, _UNTYPED_RECORD))

    with open_archive(path, password="password") as archive:
        member = archive.members()[0]

    assert member.is_encrypted, "the walk carried on and reached the crypt record"
    assert not member._raw.skipped_header_records_truncated
    dropped = [
        d
        for d in member.diagnostics
        if d.code is DiagnosticCode.MEMBER_HEADER_RECORD_SKIPPED
    ]
    assert len(dropped) == 1
    assert dropped[0].context.record == "unknown"


def test_records_under_the_cap_do_not_hide_the_encryption_record(
    tmp_path: Path,
) -> None:
    """The walk runs to the end of the area, so position does not decide this.

    Pins the leniency side of the cap: a member may drop up to
    ``_MAX_SKIPPED_HEADER_RECORDS`` records and the encryption record behind
    them is still read. Without it, a parser that refused every archive carrying
    any malformed record would pass this module.
    """
    data = (_FIXTURES / "encryption__.rar").read_bytes()
    path = tmp_path / "buried_crypt_under_cap.rar"
    path.write_bytes(
        _prepend_extra_bytes(data, _UNTYPED_RECORD * (_MAX_SKIPPED_HEADER_RECORDS - 1))
    )

    with open_archive(path, password="password") as archive:
        member = archive.members()[0]

    assert member.is_encrypted
    assert not member._raw.skipped_header_records_truncated
    dropped = [
        d
        for d in member.diagnostics
        if d.code is DiagnosticCode.MEMBER_HEADER_RECORD_SKIPPED
    ]
    assert len(dropped) == _MAX_SKIPPED_HEADER_RECORDS - 1


@pytest.mark.parametrize(
    ("label", "prefix"),
    [
        ("cap", _UNTYPED_RECORD * (_MAX_SKIPPED_HEADER_RECORDS + 1)),
        ("zero_size", b"\x00"),
        ("overrun", b"\x7f"),
        ("unterminated_size", b"\x80" * 11),
    ],
)
def test_a_cut_short_header_never_reports_an_encrypted_member_as_plaintext(
    tmp_path: Path, label: str, prefix: bytes
) -> None:
    """The half of this that a truncation diagnostic does not fix.

    Each prefix stops the walk before it reaches the member's ``FHEXTRA_CRYPT``
    record, so ``file_encryption`` is never set. Reporting that as
    ``is_encrypted=False`` is a wrong answer rather than a missing one: under the
    default policy an AES member reads as plaintext, and the diagnostic saying
    the header was cut short does not change what the field says. A member whose
    header was not read to the end fails closed instead.

    The cheapest of these is one byte.
    """
    data = (_FIXTURES / "encryption__.rar").read_bytes()
    path = tmp_path / f"cut_short_{label}.rar"
    path.write_bytes(_prepend_extra_bytes(data, prefix))

    with open_archive(path, password="password") as archive:
        member = archive.members()[0]

    assert member._raw.skipped_header_records_truncated
    assert member._raw.encryption_unknown
    assert member.is_encrypted, (
        "the encryption record was never reached, so 'not encrypted' would be a "
        "claim the header does not support"
    )


def test_failing_closed_does_not_make_every_dropped_record_encrypted(
    short_hash_archive: Path,
) -> None:
    """Failing closed applies to a cut-short header, not to a dropped record.

    A member whose extra area was read to the end has been asked and answered:
    there was no encryption record. Without this, the guard above would pass
    against a parser that simply called everything encrypted.
    """
    with open_archive(short_hash_archive) as archive:
        (member,) = archive.members()
    assert not member._raw.skipped_header_records_truncated
    assert not member.is_encrypted


def _cut_short_plaintext_archive(tmp_path: Path) -> Path:
    """``blake2sp.rar`` with one zero-size record in front of its extra area.

    Nothing in it is encrypted, and ``store.txt`` is stored — so on ``main`` it
    both lists as plaintext and reads by slicing the source, with no ``unrar``
    anywhere. That is the member the tests below follow.
    """
    data = (_FIXTURES / "blake2sp.rar").read_bytes()
    path = tmp_path / "cut_short_plaintext.rar"
    path.write_bytes(_prepend_extra_bytes(data, b"\x00"))
    return path


def test_one_cut_short_member_does_not_report_the_archive_encrypted(
    tmp_path: Path,
) -> None:
    """Failing closed is a claim about a member, not about the archive.

    The member itself is presented as encrypted because its header never settled
    the question. The archive around it is a different question, and one damaged
    member does not answer it: ``ArchiveInfo.is_encrypted`` documents header-level
    encryption, the same predicate decides whether the caller's password is handed
    to every ``unrar`` spawn, and it is what relabels an empty read as a wrong
    password. Letting the member's fail-closed answer reach it changed all three
    for an archive with nothing encrypted in it.
    """
    with open_archive(_cut_short_plaintext_archive(tmp_path)) as archive:
        (member,) = [m for m in archive.members() if m.is_file]
        assert member.is_encrypted, "the member's own answer still fails closed"
        assert member._raw.encryption_unknown
        assert not archive.info.is_encrypted, (
            "nothing in this archive is encrypted; one unreadable header does "
            "not make it so"
        )


def _cut_short(tmp_path: Path, fixture: str, name: str) -> Path:
    """``fixture`` with one zero-size record in front of its first extra area."""
    path = tmp_path / name
    path.write_bytes(_prepend_extra_bytes((_FIXTURES / fixture).read_bytes(), b"\x00"))
    return path


def _no_rar_binaries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Take ``unrar`` and ``rar`` off PATH for the rest of the test."""
    empty = tmp_path / "empty_path"
    empty.mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setattr(rar_unrar, "_cached_unrar", {})


def test_a_surviving_checksum_settles_a_cut_short_stored_member(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A digest the damage did not reach is what decides, and no ``unrar`` is needed.

    The header stopped before its extra records were read to the end, so nothing
    in it says whether this member is encrypted — and ``unrar`` does not know
    either (it reads the same damaged header and drops the encrypted marker).
    What settles it is a checksum: RAR5 keeps CRC32 in the fixed FILE header,
    which a cut extra area cannot touch, and ciphertext does not match it. So the
    member is read after the checksum confirms its bytes, on any install.
    """
    _no_rar_binaries(monkeypatch, tmp_path)
    path = _cut_short(tmp_path, "stored_m0.rar", "cut_short_crc32.rar")

    with open_archive(path) as archive:
        (member,) = [m for m in archive.members() if m.is_file]
        assert member.is_encrypted, "the member's own answer still fails closed"
        assert member._raw.encryption_unknown
        assert HashAlgorithm.CRC32 in member.hashes
        assert archive.read(member) == b"stored payload"


def test_a_cut_short_stored_member_that_is_encrypted_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The checksum test is a real discriminator, not a formality.

    Same damage, same stored-and-sliceable shape, same surviving CRC32 — but this
    member really is AES-encrypted, and an encrypted RAR5 member's stored digests
    are key-tweaked, so the ciphertext matches neither the plaintext digest nor
    the tweaked one. Without this the slice would hand back ciphertext as file
    content, which is the fault the whole fail-closed path exists to prevent.
    """
    _no_rar_binaries(monkeypatch, tmp_path)
    path = _cut_short(tmp_path, "encryption_stored__.rar", "cut_short_enc.rar")

    with open_archive(path, password="password") as archive:
        (member,) = [m for m in archive.members() if m.is_file]
        assert member._raw.encryption_unknown, "the CRYPT record was never reached"
        assert HashAlgorithm.CRC32 in member.hashes
        with pytest.raises(CorruptionError, match="do not match the checksum"):
            archive.read(member)


def test_a_cut_short_stored_member_with_no_checksum_left_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With nothing left to check the bytes against, the member is not read.

    Which digest survives is the writer's choice, not ours: this archive carries
    BLAKE2sp, which RAR5 keeps *in* the extra area, so the same cut that hid the
    encryption question also destroyed the only answer to it. ``unrar`` hands
    such a member back unverified; the maintainer's ruling is to refuse, and the
    message names the damaged header rather than a missing package — installing
    ``unrar`` is a way out, not the cause.
    """
    _no_rar_binaries(monkeypatch, tmp_path)
    path = _cut_short_plaintext_archive(tmp_path)

    with open_archive(path) as archive:
        (member,) = [m for m in archive.members() if m.is_file]
        assert not member.hashes, "the cut took the only digest with it"
        with pytest.raises(CorruptionError) as raised:
            archive.read(member)
        assert "no usable checksum survived" in str(raised.value)
        # The cause, not a way out. This branch used to raise
        # ``PackageNotInstalledError``, which named the package while the header
        # was what went wrong; ``unrar`` must not appear in the message at all.
        assert "unrar" not in str(raised.value).lower(), raised.value


def test_a_checksum_this_install_cannot_compute_does_not_count_as_survival(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A digest that cannot be computed confirms nothing, and must not read as a pass.

    ``build_member_verifier`` drops an algorithm it has no hasher for, emitting
    ``DIGEST_UNVERIFIABLE`` and carrying on — right for a check running alongside a
    read the caller wanted anyway, and wrong here, where the read happens *in order
    to* settle the digest. A verifier left with nothing to check finds no fault, so
    without this the member would be confirmed by an empty verification and its
    bytes handed back.
    """
    _no_rar_binaries(monkeypatch, tmp_path)
    monkeypatch.setattr(verify, "_make_hasher", lambda key: None)
    path = _cut_short(tmp_path, "stored_m0.rar", "cut_short_crc32.rar")

    with open_archive(path) as archive:
        (member,) = [m for m in archive.members() if m.is_file]
        assert HashAlgorithm.CRC32 in member.hashes, "the digest is there to be had"
        with pytest.raises(CorruptionError, match="no usable checksum survived"):
            archive.read(member)


def test_a_truncated_member_is_truncated_whatever_its_header_said(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The confirmation pass interprets one verdict, and must not speak for the rest.

    It reads the member to check a digest, so every verdict the verifier can reach
    passes through it — including the short read that means the archive itself ends
    early. Relabelling that as "these bytes are encrypted or corrupt" gave the same
    physical damage two different answers depending on whether an unrelated header
    record happened to be readable, and hid truncation from a caller who branches on
    it to salvage what is there.
    """
    _no_rar_binaries(monkeypatch, tmp_path)
    intact = (_FIXTURES / "stored_m0.rar").read_bytes()
    cut = _prepend_extra_bytes(intact, b"\x00")

    verdicts = {}
    for label, data in (("intact header", intact), ("cut-short header", cut)):
        path = tmp_path / f"{label.replace(' ', '_')}.rar"
        # Drop the last 9 bytes: the member's data ends before its declared size.
        path.write_bytes(data[:-9])
        with open_archive(path) as archive:
            (member,) = [m for m in archive.members() if m.is_file]
            with pytest.raises(TruncatedError) as raised:
                archive.read(member)
        verdicts[label] = str(raised.value)

    assert "13 of 14" in verdicts["cut-short header"], verdicts["cut-short header"]
    assert "encrypted" not in verdicts["cut-short header"].lower()


def _graft_service_extra_area(data: bytes, extra: bytes) -> bytes:
    """Give the first SERVICE header an extra area holding ``extra``.

    ``comment__.rar``'s ``CMT`` header has none, so the flag, the size vint and the
    bytes are all added here and the header CRC recomputed. Note ``extra`` must be
    at least two bytes: the walk allows one byte of trailing padding (as rarfile
    does), so a one-byte area is never walked and grafting one changes nothing.
    """
    pos = 8  # past the RAR5 signature
    while pos < len(data):
        crc_at = pos
        header_size, body_at = load_vint(data, pos + 4)
        header_end = body_at + header_size
        header_type, p = load_vint(data, body_at)
        flags_at = p
        header_flags, p = load_vint(data, p)
        if header_type != 3:  # not SERVICE
            if header_flags & 0x0001:
                _extra_size, p = load_vint(data, p)
            data_size = 0
            if header_flags & 0x0002:
                data_size, _ = load_vint(data, p)
            pos = header_end + data_size
            continue
        assert not (header_flags & 0x0001), "this header already has an extra area"
        body = data[body_at:header_end]
        new_body = (
            body[: flags_at - body_at]
            + _vint(header_flags | 0x0001)
            + _vint(len(extra))
            + body[p - body_at :]
            + extra
        )
        rebuilt = (
            data[:crc_at]
            + b"\x00\x00\x00\x00"
            + _vint(len(new_body))
            + new_body
            + data[header_end:]
        )
        return fixup_rar_header_crcs(rebuilt, broken=False)
    pytest.fail("fixture has no RAR5 SERVICE header")


def test_a_cut_short_service_header_neither_speaks_nor_gets_sliced(
    tmp_path: Path,
) -> None:
    """A SERVICE header is not a member, and both halves of the rule forgot it.

    ``_parse_rar5_file_block`` parses ``CMT`` and ``QO`` headers too, so their
    extra areas get the same leniency — but nothing lists them, so the per-member
    diagnostics never ran for one, and the gates that slice their payload read the
    *definite* encryption answer rather than the fail-closed one. A ``CMT`` header
    whose walk stopped therefore had its bytes sliced and decoded straight into
    ``ArchiveInfo.comment``, with nothing emitted and nothing for a strict policy
    to refuse. Losing the comment is a missing answer; that was a wrong one.
    """
    data = _graft_service_extra_area(
        (_FIXTURES / "comment__.rar").read_bytes(), b"\x00\x00"
    )
    path = tmp_path / "cut_short_comment.rar"
    path.write_bytes(data)

    with open_archive(path) as archive:
        assert archive.info.comment is None, (
            "the comment is decoded from bytes the header never finished "
            "describing, so it must not be presented"
        )
        codes = [d.code for d in archive.diagnostics.retained]
        assert codes.count(DiagnosticCode.MEMBER_HEADER_RECORD_SKIPPED) == 2, codes

    strict = ArchiveyConfig(diagnostic_policy=DiagnosticPolicy.strict())
    with pytest.raises(DiagnosticRaisedError):
        with open_archive(path, config=strict) as archive:
            archive.members()


def test_an_undamaged_service_header_stays_quiet(tmp_path: Path) -> None:
    """The guard above must not fire on every archive that has a comment."""
    with open_archive(_FIXTURES / "comment__.rar") as archive:
        assert archive.info.comment == "This is a\nmulti-line comment"
        assert archive.diagnostics.total_count == 0


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [
        (b"\x00", "declared a size of zero"),
        (b"\x7f", "overran the extra area"),
        (b"\x80" * 11, "size could not be read"),
    ],
    ids=["zero_size", "overrun", "unterminated_size"],
)
def test_the_cut_short_diagnostic_names_what_actually_stopped_the_walk(
    tmp_path: Path, prefix: bytes, expected: str
) -> None:
    """Four faults end the walk and they are not interchangeable.

    The stand-in used to say "more than sixteen records were malformed" whatever
    happened, so a single zero-size record told the caller about fifteen records
    that do not exist. It matters more than an ordinary miswording: this is the
    only message that explains why the member may be reported encrypted when
    nothing in its listing says so.
    """
    data = (_FIXTURES / "blake2sp.rar").read_bytes()
    path = tmp_path / "walk_stop.rar"
    path.write_bytes(_prepend_extra_bytes(data, prefix))

    with open_archive(path) as archive:
        (member,) = [m for m in archive.members() if m.is_file]

    stand_in = member.diagnostics[-1]
    assert isinstance(stand_in.context, MemberHeaderRecordContext)
    assert stand_in.context.list_truncated
    assert expected in stand_in.message, stand_in.message
    assert expected in (stand_in.context.reason or "")
    assert "more than" not in stand_in.message.lower(), (
        "the cap stopped none of these, so the message must not name it"
    )
