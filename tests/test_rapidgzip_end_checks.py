"""The rapidgzip accelerator's end-of-data checks for gzip and zlib give the verdict the
standard library gives with the accelerator off.

rapidgzip checks neither a gzip file's length against its ISIZE trailer nor a zlib
stream's Adler-32, and reads a cut stream as a shorter whole one. archivey's checks
(``_GzipTruncationCheckStream``, ``_ZlibAdlerCheckStream``) and the takeover by the
standard library (``_StdlibOnAcceleratorError``) make up for that. Each test forces the
accelerator ``ON`` (``AUTO`` engages only from 16 MiB) and compares with ``OFF``:

- a seek, even back to the start, leaves the gzip check armed;
- ``1f 8b 08`` inside a compressed body is not taken for a further gzip member, so a cut
  or damaged one-member gzip still raises;
- a seek that meets bytes after the data (NUL padding) or damage is handed to the
  standard library, as a read is;
- a second zlib stream is trailing data, not content.
"""

from __future__ import annotations

import functools
import gzip
import io
import random
import zlib
from collections.abc import Callable
from typing import BinaryIO

import pytest

from archivey import open_archive
from archivey.config import ArchiveyConfig
from archivey.diagnostics import DiagnosticCode
from archivey.internal.config import AcceleratorMode, StreamConfig
from archivey.internal.streams import codecs
from archivey.internal.streams.codecs import (
    Codec,
    _deflate_family_uses_accelerator,
    gzip_has_additional_member,
    open_codec_stream,
)
from archivey.internal.streams.rapidgzip_child import (
    rapidgzip_child_unavailable_reason,
)
from archivey.types import ArchiveFormat
from tests.conftest import requires

_OFF = AcceleratorMode.OFF
_ON = AcceleratorMode.ON
# A gzip header with valid flags, as it can turn up by chance in a compressed body.
_FAKE_HEADER = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03"


@functools.cache
def _payload() -> bytes:
    """About 1 MB of words and noise: several DEFLATE blocks, and compressible."""
    rng = random.Random(11)
    words = [b"alpha ", b"beta ", b"gamma\n", b"delta "]
    return b"".join(
        rng.choice(words) if rng.random() < 0.8 else bytes([rng.randrange(256)])
        for _ in range(200_000)
    )


@functools.cache
def _noise() -> bytes:
    rng = random.Random(5)
    return bytes(rng.randrange(256) for _ in range(300_000))


@functools.cache
def _gzip_with_magic_in_body() -> bytes:
    """A one-member gzip whose body holds ``1f 8b 08`` and a whole header after it.

    Level 0 stores the payload verbatim, so the bytes are in the compressed file as
    they are in a large file by chance (about once per 16 MiB)."""
    payload = _noise()[:100_000] + _FAKE_HEADER + _noise()[100_000:]
    blob = gzip.compress(payload, compresslevel=0, mtime=0)
    assert _FAKE_HEADER in blob[1:]
    return blob


def _flip(blob: bytes, at: int) -> bytes:
    out = bytearray(blob)
    out[at] ^= 0x55
    return bytes(out)


def _config(mode: AcceleratorMode) -> StreamConfig:
    return StreamConfig(seekable=True, use_rapidgzip=mode)


Outcome = tuple[bytes, "type[Exception] | None"]


def _outcome(
    codec: Codec,
    blob: bytes,
    mode: AcceleratorMode,
    steps: Callable[[BinaryIO], None],
    chunk: int = -1,
) -> Outcome:
    """Run ``steps``, then read to the end in ``chunk`` reads: the bytes that last read
    got and the type of the exception, if any."""
    got = bytearray()
    try:
        with open_codec_stream(codec, io.BytesIO(blob), config=_config(mode)) as s:
            steps(s)
            while block := s.read(chunk):
                got += block
                if chunk < 0:
                    break
    except Exception as exc:  # noqa: BLE001 - compared below
        return bytes(got), type(exc)
    return bytes(got), None


def _no_seek(s: BinaryIO) -> None:
    pass


def _read_then_rewind(s: BinaryIO) -> None:
    s.read(1000)
    s.seek(0)


def _seek_then_rewind(s: BinaryIO) -> None:
    s.seek(5000)
    s.seek(0)


def _end_then_rewind(s: BinaryIO) -> None:
    s.seek(0, io.SEEK_END)
    s.seek(0)


def _skip_forward(s: BinaryIO) -> None:
    s.read(100)
    s.seek(200_000)


_SEEKS = {
    "no-seek": _no_seek,
    "read-then-rewind": _read_then_rewind,
    "seek-then-rewind": _seek_then_rewind,
    "end-then-rewind": _end_then_rewind,
    "skip-forward": _skip_forward,
}


def _assert_same(on: Outcome, off: Outcome) -> None:
    assert (len(on[0]), on[1]) == (len(off[0]), off[1])
    assert on[0] == off[0]


# --- the gzip check survives a seek ----------------------------------------------------


def _gzip_damage(kind: str) -> bytes:
    blob = gzip.compress(_payload(), mtime=0)
    if kind == "cut":
        return blob[: len(blob) * 3 // 4]
    if kind == "wrong-isize":
        return _flip(blob, len(blob) - 2)
    if kind == "intact":
        return blob
    raise AssertionError(kind)


@requires("rapidgzip")
@pytest.mark.parametrize("seek", sorted(_SEEKS))
@pytest.mark.parametrize("kind", ["cut", "wrong-isize", "intact"])
def test_a_seek_leaves_the_gzip_length_check_armed(kind: str, seek: str) -> None:
    """A seek used to turn the ISIZE check off for good: a cut file then read short
    with no error after ``read(1000); seek(0)``."""
    blob = _gzip_damage(kind)
    off = _outcome(Codec.GZIP, blob, _OFF, _SEEKS[seek])
    assert (off[1] is None) == (kind == "intact")
    _assert_same(_outcome(Codec.GZIP, blob, _ON, _SEEKS[seek]), off)


# --- a chance 1f 8b 08 is not a further member -----------------------------------------


def _magic_case(kind: str) -> bytes:
    blob = _gzip_with_magic_in_body()
    if kind == "cut":
        return blob[: len(blob) * 3 // 4]
    if kind == "wrong-isize":
        return _flip(blob, len(blob) - 2)
    if kind == "appended":
        return blob + b"JUNKJUNK"
    if kind == "intact":
        return blob
    if kind == "second-member":
        return blob + gzip.compress(b"tail " * 100, mtime=0)
    if kind == "second-member-cut":
        return blob + gzip.compress(_noise(), mtime=0)[:5000]
    raise AssertionError(kind)


@requires("rapidgzip")
@pytest.mark.parametrize("chunk", [-1, 1 << 16])
@pytest.mark.parametrize(
    "kind",
    [
        "cut",
        "wrong-isize",
        "appended",
        "intact",
        "second-member",
        "second-member-cut",
    ],
)
def test_gzip_magic_in_the_body_does_not_silence_the_length_check(
    kind: str, chunk: int
) -> None:
    blob = _magic_case(kind)
    off = _outcome(Codec.GZIP, blob, _OFF, _no_seek, chunk)
    on = _outcome(Codec.GZIP, blob, _ON, _no_seek, chunk)
    assert on[1] is off[1]
    assert (off[1] is None) == (kind in ("appended", "intact", "second-member"))
    if kind == "wrong-isize" and chunk > 0:
        # Not this test's subject: the standard library raises on the chunk that
        # holds the trailer, rapidgzip's check after the last chunk.
        assert on[0].startswith(off[0])
        return
    _assert_same(on, off)


@requires("rapidgzip")
def test_gzip_bytes_after_the_data_are_reported_with_the_accelerator(tmp_path) -> None:
    path = tmp_path / "magic.gz"
    path.write_bytes(_magic_case("appended"))
    for mode in (_OFF, _ON):
        config = ArchiveyConfig(use_rapidgzip=mode)
        with open_archive(path, config=config, seekable_members=True) as reader:
            reader.read(reader.members()[0])
            assert reader.diagnostics.counts[DiagnosticCode.ARCHIVE_TRAILING_DATA] == 1


@requires("rapidgzip")
def test_appended_length_bytes_do_not_hide_a_wrong_gzip_isize() -> None:
    """Four bytes after a wrong ISIZE are not the trailer, even when they equal the length.

    The backstop compares the decoded length with the last four bytes of the file.
    Appending the real length there makes that comparison succeed, and the accelerator
    returns the payload. The standard library still raises on the real trailer. Those
    four bytes are not a trailer: the backstop finds it by the CRC-32 of the output.
    The ``gzip_accel`` fuzz target found this shape.
    """
    payload = b"atheris seed payload\n" * 8
    blob = bytearray(gzip.compress(payload, mtime=0))
    blob[-1] ^= 0x01
    blob += len(payload).to_bytes(4, "little")
    off = _outcome(Codec.GZIP, bytes(blob), _OFF, _no_seek)
    assert off[1] is not None
    _assert_same(_outcome(Codec.GZIP, bytes(blob), _ON, _no_seek), off)


@requires("rapidgzip")
@pytest.mark.parametrize(
    ("wrong_isize", "after"),
    [
        (False, b""),
        (False, bytes(4)),
        (False, bytes(100_000)),
        (True, b""),
        (True, bytes(4)),
        (True, bytes(100_000)),
        (True, bytes(5) + (168).to_bytes(4, "little")),
        (False, b"junk"),
    ],
    ids=[
        "valid",
        "valid-padded",
        "valid-long-padding",
        "wrong-isize",
        "wrong-isize-padded",
        "wrong-isize-long-padding",
        "wrong-isize-padding-then-length",
        "valid-then-junk",
    ],
)
def test_gzip_last_isize_is_judged_as_with_the_accelerator_off(
    wrong_isize: bool, after: bytes
) -> None:
    """A wrong ISIZE on the last member raises whatever follows it; a right one reads
    with or without zero padding. The trailer is found by its CRC-32, so neither the
    padding nor the bytes after a wrong trailer decide."""
    payload = b"atheris seed payload\n" * 8
    blob = bytearray(gzip.compress(payload, mtime=0))
    if wrong_isize:
        blob[-1] ^= 0x01
    blob = bytes(blob) + after
    off = _outcome(Codec.GZIP, blob, _OFF, _no_seek)
    if wrong_isize:
        assert off[1] is not None
    _assert_same(_outcome(Codec.GZIP, blob, _ON, _no_seek), off)


@requires("rapidgzip")
@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "valid-padded",
        "last-wrong-isize",
        "last-wrong-isize-then-length",
        "cut-last",
    ],
)
def test_gzip_concatenated_members_are_judged_as_with_the_accelerator_off(
    case: str,
) -> None:
    """The output's CRC-32 is not the last member's, so it never finds a trailer in a
    concatenated file; the further-member scan decides, as before. With two small members,
    a wrong ISIZE on the last one raises, as the scan does not confirm a member whose own
    ISIZE is wrong (see the limitation test below for when it does)."""
    first, second = b"first member payload\n" * 5, b"second member payload\n" * 7
    member1 = gzip.compress(first, mtime=0)
    member2 = bytearray(gzip.compress(second, mtime=0))
    after = b""
    if case == "valid-padded":
        after = bytes(9)
    elif case.startswith("last-wrong-isize"):
        member2[-1] ^= 0x01
        if case.endswith("length"):
            after = len(second).to_bytes(4, "little")
    elif case == "cut-last":
        del member2[-6:]
    blob = member1 + bytes(member2) + after
    off = _outcome(Codec.GZIP, blob, _OFF, _no_seek)
    assert (off[1] is not None) == (case not in ("valid", "valid-padded"))
    _assert_same(_outcome(Codec.GZIP, blob, _ON, _no_seek), off)


def _require_the_accelerator_in_use() -> None:
    """Skip where ``ON`` would decode with the standard library: a comparison of the two
    modes then compares nothing."""
    if not _deflate_family_uses_accelerator(_config(_ON)):
        pytest.skip("the accelerator is not selected here")
    reason = rapidgzip_child_unavailable_reason()
    if reason is not None:
        pytest.skip(reason)


def _assert_error_or_the_known_clean_read(blob: bytes, content: bytes) -> None:
    """The standard library raises. The accelerator either raises too, or reads exactly
    ``content`` clean: the data is right, and only the verdict on a wrong length differs.

    Whether it raises depends on the rapidgzip build, not on archivey. rapidgzip 0.16.0
    has two chunk decoders: the inflate-wrapper one (ISA-L, in the Linux wheels) appends a
    member's footer without comparing its size, and its own decoder raises "Mismatching
    size" for a stream that lies wholly inside one chunk. CI saw the accelerator raise on
    the macOS wheels and on a Python 3.15 build, and read clean on the Linux wheels.
    """
    _require_the_accelerator_in_use()
    off = _outcome(Codec.GZIP, blob, _OFF, _no_seek)
    assert off[1] is not None
    on = _outcome(Codec.GZIP, blob, _ON, _no_seek)
    assert on == off or on == (content, None)


@requires("rapidgzip")
@pytest.mark.parametrize("case", ["three-members", "large-last-member"])
def test_gzip_wrong_last_isize_in_a_concatenated_file_is_a_known_limitation(
    case: str,
) -> None:
    """Known limitation (``compressed-streams``): the further-member scan stands down at
    the first further member it confirms, and a candidate counts once zlib has decoded
    64 KiB of its input, so the last member's wrong ISIZE can be missed with three or
    more members, or when the last one is large. Where the rapidgzip build does not
    compare the size itself, the accelerator reads clean; the data is right, as every
    member's CRC-32 is checked. Not worth finding the last member's start for."""
    contents = [b"first member payload\n" * 5]
    if case == "three-members":
        contents += [b"middle member payload\n" * 7, b"last member payload\n" * 3]
    else:
        contents += [random.Random(1).randbytes(300_000)]
    members = [gzip.compress(c, mtime=0) for c in contents]
    members[-1] = members[-1][:-1] + bytes([members[-1][-1] ^ 0x01])
    _assert_error_or_the_known_clean_read(b"".join(members), b"".join(contents))


@requires("rapidgzip")
def test_gzip_isize_set_to_the_crc_is_not_a_trailer_with_the_length_appended() -> None:
    """The ISIZE field holds the output's CRC-32 and the real length follows: the last
    eight bytes are then exactly CRC-32 + length. They are the member's own ISIZE field
    and the appended bytes, not a trailer, and the candidate is turned down because the
    same CRC-32 precedes it."""
    _require_the_accelerator_in_use()
    payload = b"atheris seed payload\n" * 8
    blob = bytearray(gzip.compress(payload, mtime=0))
    blob[-4:] = zlib.crc32(payload).to_bytes(4, "little")
    blob += len(payload).to_bytes(4, "little")
    off = _outcome(Codec.GZIP, bytes(blob), _OFF, _no_seek)
    assert off[1] is not None
    _assert_same(_outcome(Codec.GZIP, bytes(blob), _ON, _no_seek), off)


def test_the_gzip_accel_oracle_excuses_a_wrong_isize_only_in_a_concatenated_file() -> (
    None
):
    """The ``gzip_accel`` fuzz target excuses a clean accelerated read of a file the
    standard library rejects for its length only as far as the spec accepts it: members
    after the first, whichever is wrong, and never a single member, padded or not."""
    from tests.atheris_fuzz.targets import _gzip_ignoring_lengths

    def member(content: bytes, *, wrong_isize: bool = False) -> bytes:
        blob = bytearray(gzip.compress(content, mtime=0))
        if wrong_isize:
            blob[-1] ^= 0x01
        return bytes(blob)

    a, b, c = b"a" * 50, b"b" * 70, b"c" * 30
    wrong_first = member(a, wrong_isize=True) + member(b)
    three_wrong_last = member(a) + member(b) + member(c, wrong_isize=True)
    assert _gzip_ignoring_lengths(wrong_first) == a + b
    assert _gzip_ignoring_lengths(three_wrong_last) == a + b + c
    assert _gzip_ignoring_lengths(three_wrong_last + bytes(7)) == a + b + c
    one = member(a, wrong_isize=True)
    assert _gzip_ignoring_lengths(one) is None
    assert _gzip_ignoring_lengths(one + bytes(4)) is None
    assert _gzip_ignoring_lengths(one + len(a).to_bytes(4, "little")) is None


@requires("rapidgzip")
def test_gzip_wrong_isize_after_a_seek_that_skipped_output_is_still_caught() -> None:
    """With bytes skipped there is no CRC-32 of the output; the last four bytes of the
    file stand in for the ISIZE, which still catches a plain wrong ISIZE."""
    payload = b"atheris seed payload\n" * 8
    blob = bytearray(gzip.compress(payload, mtime=0))
    blob[-1] ^= 0x01

    def skip(s: BinaryIO) -> None:
        s.seek(len(payload) - 1)

    off = _outcome(Codec.GZIP, bytes(blob), _OFF, skip)
    assert off[1] is not None
    _assert_same(_outcome(Codec.GZIP, bytes(blob), _ON, skip), off)


@requires("rapidgzip")
@pytest.mark.parametrize(
    "blob",
    [
        pytest.param(_magic_case("second-member"), id="second-member"),
        pytest.param(gzip.compress(bytes(range(256)) * 800, mtime=0), id="one-member"),
    ],
)
def test_a_real_further_gzip_member_needs_no_second_decode(
    monkeypatch: pytest.MonkeyPatch, blob: bytes
) -> None:
    """A multi-member file's trailer is only its last member's size: the mismatch is
    expected, and the confirmed member keeps the standard library out of it. A healthy
    one-member file matches its trailer, and its decode reaches the end of the source."""
    handovers: list[object] = []
    original = codecs._StdlibOnAcceleratorError.switch_to_stdlib

    def spy(self: codecs._StdlibOnAcceleratorError, *args: object) -> None:
        handovers.append(args)
        original(self, *args)  # type: ignore[arg-type]

    monkeypatch.setattr(codecs._StdlibOnAcceleratorError, "switch_to_stdlib", spy)
    got, error = _outcome(Codec.GZIP, blob, _ON, _no_seek)
    assert error is None
    assert got == gzip.decompress(blob)
    assert handovers == []


@pytest.mark.parametrize(
    ("tail", "expected"),
    [
        pytest.param(b"", False, id="none"),
        pytest.param(_FAKE_HEADER + b"\xff" * 4000, False, id="header-then-noise"),
        pytest.param(b"\x1f\x8b\x08\xe0" + b"\0" * 20, False, id="reserved-flags"),
        pytest.param(gzip.compress(b"x", mtime=0), True, id="member"),
        pytest.param(gzip.compress(b"", mtime=0), True, id="empty-member"),
        pytest.param(gzip.compress(b"abc" * 9000)[:30], False, id="cut-member"),
    ],
)
def test_gzip_has_additional_member_confirms_the_candidate(
    tail: bytes, expected: bool
) -> None:
    blob = gzip.compress(_noise()[:50_000], compresslevel=0, mtime=0)
    assert gzip_has_additional_member(io.BytesIO(blob + tail)) is expected


def test_gzip_has_additional_member_finds_a_header_split_across_blocks() -> None:
    member = gzip.compress(b"x" * 100, mtime=0)
    head = b"\0" * ((1 << 20) - 1)  # the magic starts on the last byte of block one
    assert gzip_has_additional_member(io.BytesIO(head + member))


def test_gzip_has_additional_member_reads_a_member_header_with_every_field() -> None:
    body = zlib.compressobj(6, zlib.DEFLATED, -15)
    deflated = body.compress(b"payload") + body.flush()
    # FHCRC | FEXTRA | FNAME | FCOMMENT
    header = b"\x1f\x8b\x08\x1e" + b"\0" * 4 + b"\x00\x03"
    header += b"\x02\x00ab" + b"name\0" + b"comment\0"
    header += (zlib.crc32(header) & 0xFFFF).to_bytes(2, "little")
    trailer = zlib.crc32(b"payload").to_bytes(4, "little") + (7).to_bytes(4, "little")
    member = header + deflated + trailer
    assert gzip.decompress(member) == b"payload"
    assert gzip_has_additional_member(io.BytesIO(b"\0" + member))
    bad_crc = header[:-2] + b"\0\0" + deflated + trailer
    assert not gzip_has_additional_member(io.BytesIO(b"\0" + bad_crc))


def _costly_fake_member() -> bytes:
    """A header zlib accepts, a 4000-byte stored block, then a reserved block type: a
    candidate that decodes a whole probe piece before it fails."""
    stored = (
        b"\x00" + (4000).to_bytes(2, "little") + (0xFFFF ^ 4000).to_bytes(2, "little")
    )
    return _FAKE_HEADER + stored + b"\0" * 4000 + b"\x07"


@pytest.mark.parametrize(("fakes", "expected"), [(10, True), (400, False)])
def test_gzip_member_scan_has_a_budget(fakes: int, expected: bool) -> None:
    """Candidates that each decode a long way before failing spend a shared budget;
    once it is spent the scan answers no, and the standard library decides."""
    blob = b"\0" + _costly_fake_member() * fakes + gzip.compress(b"x", mtime=0)
    assert gzip_has_additional_member(io.BytesIO(blob)) is expected


def test_gzip_member_probe_output_stays_within_its_bound(monkeypatch) -> None:
    """One candidate decodes at most ``_MEMBER_PROBE_OUTPUT`` of output in total, also
    when no single probe read reaches the bound and the last one would pass it."""
    rng = random.Random(3)
    # About 72:1, so each 4 KiB probe read expands by about 0.3 MiB.
    raw = b"".join(bytes([rng.randrange(256)]) + b"\0" * 200 for _ in range(10_000))
    member = gzip.compress(raw, mtime=0)
    produced = 0
    real = zlib.decompressobj

    class _Counting:
        def __init__(self, wbits: int) -> None:
            self._decoder = real(wbits)

        def decompress(self, data: bytes, max_length: int = 0) -> bytes:
            nonlocal produced
            out = self._decoder.decompress(data, max_length)
            produced += len(out)
            return out

        def __getattr__(self, name: str) -> object:
            return getattr(self._decoder, name)

    monkeypatch.setattr(codecs.zlib, "decompressobj", _Counting)
    assert codecs._gzip_member_at(io.BytesIO(member), 0, 1 << 20)[0] is True
    assert produced == codecs._MEMBER_PROBE_OUTPUT


# --- a seek that meets a data error is handed over -------------------------------------


@requires("rapidgzip")
def test_a_seek_in_a_nul_padded_gzip_reads_the_data() -> None:
    """``gzip -t`` and the standard library accept NUL padding after the last member;
    rapidgzip's seek used to raise on it."""
    payload = _payload()
    blob = gzip.compress(payload, mtime=0) + b"\0" * 1024
    for mode in (_OFF, _ON):
        with open_codec_stream(Codec.GZIP, io.BytesIO(blob), config=_config(mode)) as s:
            target = len(payload) - 5000
            assert s.seek(target) == target
            assert s.read() == payload[-5000:]
            assert s.seek(0) == 0
            assert s.read() == payload


@requires("rapidgzip")
@pytest.mark.parametrize("codec", [Codec.GZIP, Codec.ZLIB])
def test_a_seek_into_damage_gives_the_verdict_of_the_accelerator_off(
    codec: Codec,
) -> None:
    compress = gzip.compress if codec is Codec.GZIP else zlib.compress
    blob = compress(_payload())
    damaged = _flip(blob, len(blob) // 2)

    def seek_far(s: BinaryIO) -> None:
        s.seek(len(_payload()) - 100)

    off = _outcome(codec, damaged, _OFF, seek_far)
    assert off[1] is not None
    on = _outcome(codec, damaged, _ON, seek_far)
    assert on[1] is off[1]


# --- a second zlib stream is trailing data ---------------------------------------------


@functools.cache
def _two_zlib_streams() -> bytes:
    return zlib.compress(_payload()) + zlib.compress(b"second " * 1000)


def _read_one_then_all(s: BinaryIO) -> None:
    s.read(1)


def _end_and_back(s: BinaryIO) -> None:
    assert s.seek(0, io.SEEK_END) == len(_payload())
    s.seek(10)


def _past_the_first_stream(s: BinaryIO) -> None:
    target = len(_payload()) + 100
    assert s.seek(target) == target
    assert s.tell() == target


@requires("rapidgzip")
@pytest.mark.parametrize(
    "steps",
    [_no_seek, _read_one_then_all, _end_and_back, _past_the_first_stream],
    ids=["read", "read-one-then-all", "end-and-back", "past-the-first-stream"],
)
def test_a_second_zlib_stream_is_not_content(steps: Callable[[BinaryIO], None]) -> None:
    blob = _two_zlib_streams()
    off = _outcome(Codec.ZLIB, blob, _OFF, steps)
    assert off[1] is None
    _assert_same(_outcome(Codec.ZLIB, blob, _ON, steps), off)


@requires("rapidgzip")
def test_a_second_zlib_stream_is_reported_as_trailing_data(tmp_path) -> None:
    path = tmp_path / "two.zz"
    path.write_bytes(_two_zlib_streams())
    for mode in (_OFF, _ON):
        config = ArchiveyConfig(use_rapidgzip=mode)
        with open_archive(
            path, format=ArchiveFormat.ZLIB, config=config, seekable_members=True
        ) as reader:
            assert reader.read(reader.members()[0]) == _payload()
            assert reader.diagnostics.counts[DiagnosticCode.ARCHIVE_TRAILING_DATA] == 1
