"""ISO 9660 backend tests — Stage 4 (namespace auto-select + fidelity, cost, write/
password rejection, non-seekable rejection, corrupt handling) and the registry
degradation slice (ISO without pycdlib). Skipped when pycdlib is absent."""

from __future__ import annotations

import builtins
import contextlib
import io
import os
import struct
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import IO, Any

import pytest

from archivey import (
    ArchiveFormat,
    CompressionAlgorithm,
    CompressionMethod,
    MemberType,
    detect_format,
    format_availability,
    open_archive,
)
from archivey.cost import AccessCost, ListingCost, StreamCapability
from archivey.exceptions import (
    CorruptionError,
    StreamNotSeekableError,
    UnsupportedOperationError,
)
from archivey.internal.backends.iso_reader import IsoReader
from archivey.internal.registry import get_registry
from archivey.internal.source import ArchiveSource
from archivey.internal.streams.streamtools import DEFAULT_UNKNOWN_LENGTH_READ_STEP
from archivey.types import FormatSupport
from tests.conftest import requires
from tests.streams_util import (
    FactSizedReadRecorder,
    NonSeekableBytesIO,
    ReadSizeRecorder,
)

pytestmark = requires("pycdlib")


def _build_iso(*, rock_ridge: bool, joliet: bool) -> bytes:
    """Build a small ISO with a file, a nested file, a directory, and (RR) a symlink."""
    import pycdlib

    iso = pycdlib.PyCdlib()
    kwargs = {}
    if rock_ridge:
        kwargs["rock_ridge"] = "1.09"
    if joliet:
        kwargs["joliet"] = 3
    iso.new(interchange_level=3, **kwargs)
    iso.add_fp(
        io.BytesIO(b"hello world"),
        11,
        "/FILE.TXT;1",
        rr_name="file.txt" if rock_ridge else None,
        joliet_path="/file.txt" if joliet else None,
    )
    iso.add_fp(
        io.BytesIO(b""),
        0,
        "/EMPTY.TXT;1",
        rr_name="empty.txt" if rock_ridge else None,
        joliet_path="/empty.txt" if joliet else None,
    )
    iso.add_directory(
        "/DIR",
        rr_name="subdir" if rock_ridge else None,
        joliet_path="/subdir" if joliet else None,
    )
    iso.add_fp(
        io.BytesIO(b"nested!"),
        7,
        "/DIR/N.TXT;1",
        rr_name="n.txt" if rock_ridge else None,
        joliet_path="/subdir/n.txt" if joliet else None,
    )
    if rock_ridge:
        iso.add_symlink("/SYM.TXT;1", "sym", "file.txt")
    out = io.BytesIO()
    iso.write_fp(out)
    iso.close()
    return out.getvalue()


@pytest.fixture
def rock_ridge_iso(tmp_path: Path) -> Path:
    path = tmp_path / "rr.iso"
    path.write_bytes(_build_iso(rock_ridge=True, joliet=True))
    return path


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_iso_detected_by_extended_window() -> None:
    info = detect_format(io.BytesIO(_build_iso(rock_ridge=True, joliet=False)))
    assert info.format == ArchiveFormat.ISO
    assert info.detected_by == "magic"  # CD001 at offset 32 769


# ---------------------------------------------------------------------------
# Cost / format properties
# ---------------------------------------------------------------------------


def test_iso_cost(rock_ridge_iso: Path) -> None:
    with open_archive(rock_ridge_iso) as ar:
        assert ar.format == ArchiveFormat.ISO
        assert ar.cost.listing_cost == ListingCost.INDEXED
        assert ar.cost.access_cost == AccessCost.DIRECT
        assert ar.cost.stream_capability == StreamCapability.SEEKABLE
        assert ar.info.is_solid is False


# ---------------------------------------------------------------------------
# Namespace auto-select + metadata fidelity
# ---------------------------------------------------------------------------


def test_rock_ridge_namespace_and_fidelity(rock_ridge_iso: Path) -> None:
    with open_archive(rock_ridge_iso) as ar:
        assert ar.info.extra["iso.namespace"] == "rock_ridge"
        by_name = {m.name: m for m in ar.members()}
        f = by_name["file.txt"]  # original case + length preserved
        assert f.mode is not None and f.uid is not None and f.gid is not None
        assert f.modified is not None and f.modified.tzinfo is not None
        sym = by_name["sym"]
        assert sym.type == MemberType.SYMLINK
        assert sym.link_target == "file.txt"
        assert by_name["subdir/"].type == MemberType.DIRECTORY


def test_joliet_namespace_and_fidelity(tmp_path: Path) -> None:
    path = tmp_path / "joliet.iso"
    path.write_bytes(_build_iso(rock_ridge=False, joliet=True))
    with open_archive(path) as ar:
        assert ar.info.extra["iso.namespace"] == "joliet"
        f = ar.get("file.txt")  # Joliet preserves case
        # Joliet carries no POSIX metadata.
        assert f.mode is None and f.uid is None and f.gid is None


def test_plain_iso_namespace_and_fidelity(tmp_path: Path) -> None:
    path = tmp_path / "plain.iso"
    path.write_bytes(_build_iso(rock_ridge=False, joliet=False))
    with open_archive(path) as ar:
        assert ar.info.extra["iso.namespace"] == "iso9660"
        names = {m.name for m in ar.members()}
        # Plain ISO 9660: upper-case 8.3 names, ;version suffix stripped.
        assert "FILE.TXT" in names
        assert "DIR/" in names
        assert ar.get("FILE.TXT").mode is None  # no POSIX metadata


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def test_read_members(rock_ridge_iso: Path) -> None:
    with open_archive(rock_ridge_iso) as ar:
        assert ar.read("file.txt") == b"hello world"
        assert ar.read("subdir/n.txt") == b"nested!"
        assert ar.read("file.txt") == b"hello world"  # re-read / random access


def test_read_symlink_follows_to_target(rock_ridge_iso: Path) -> None:
    with open_archive(rock_ridge_iso) as ar:
        assert ar.read("sym") == b"hello world"


def test_read_from_seekable_stream() -> None:
    data = _build_iso(rock_ridge=True, joliet=False)
    with open_archive(io.BytesIO(data)) as ar:
        assert ar.read("file.txt") == b"hello world"


def test_read_empty_member(rock_ridge_iso: Path) -> None:
    with open_archive(rock_ridge_iso) as ar:
        assert ar.read("empty.txt") == b""


def test_seek_within_opened_member(rock_ridge_iso: Path) -> None:
    # The opened member stream is seekable (PyCdlibIO via _PyCdlibStream/DelegatingStream)
    # once SEEKABLE is declared.
    with open_archive(rock_ridge_iso, seekable_members=True) as ar:
        with ar.open("file.txt") as f:
            assert f.read(5) == b"hello"
            f.seek(0)
            assert f.read() == b"hello world"


def test_streaming_over_seekable_iso(rock_ridge_iso: Path) -> None:
    # ISO is random-access, but a streaming=True (forward-only) pass over a seekable source
    # still works and yields the members with their data.
    with open_archive(rock_ridge_iso, streaming=True) as ar:
        collected = {
            m.name: (s.read() if s is not None else None)
            for m, s in ar.stream_members()
        }
        assert collected["file.txt"] == b"hello world"
        assert collected["empty.txt"] == b""


def test_file_member_storage_attributes(rock_ridge_iso: Path) -> None:
    # ISO members are stored uncompressed and unencrypted, with no per-member checksum.
    with open_archive(rock_ridge_iso) as ar:
        m = ar.get("file.txt")
        assert m.type == MemberType.FILE
        assert m.size == len(b"hello world")
        assert m.compressed_size == m.size
        assert m.compression == (CompressionMethod(algo=CompressionAlgorithm.STORED),)
        assert m.is_encrypted is False
        assert not m.hashes


# ---------------------------------------------------------------------------
# Rejections: password, write, non-seekable
# ---------------------------------------------------------------------------


def test_password_is_accepted_and_recorded(rock_ridge_iso: Path) -> None:
    from archivey.diagnostics import DiagnosticCode

    with open_archive(rock_ridge_iso, password=b"secret") as reader:
        assert reader.diagnostics.counts[DiagnosticCode.PASSWORD_ARGUMENT_UNUSED] == 1


def test_write_rejected() -> None:
    # No ISO write backend is registered, so requesting a writer raises.
    with pytest.raises(UnsupportedOperationError):
        get_registry().writer_for_format(ArchiveFormat.ISO)


def test_non_seekable_iso_rejected() -> None:
    data = _build_iso(rock_ridge=True, joliet=False)
    with pytest.raises(StreamNotSeekableError):
        open_archive(NonSeekableBytesIO(data), format=ArchiveFormat.ISO)


# ---------------------------------------------------------------------------
# Corrupt input
# ---------------------------------------------------------------------------

# Fixed tree (same member order as corpus ``basic``); layout varies by namespace flags.
_PYCDLIB_CYCLE_ENTRIES: tuple[tuple[str, bytes, bool], ...] = (
    ("file1.txt", b"Hello, world!", False),
    ("subdir/", b"", True),
    ("empty_file.txt", b"", False),
    ("empty_subdir/", b"", True),
    ("subdir/file2.txt", b"Hello, universe!", False),
    ("implicit_subdir/file3.txt", b"Hello there!", False),
)
# ``(rock_ridge, joliet) -> (image_len, bitflip_offset, byte_before_flip, namespace)``
_PYCDLIB_CYCLE_CASES: tuple[tuple[bool, bool, int, int, int, str], ...] = (
    # Plain ISO 9660 PVD walk: ``/SUBDIR`` extent 26, +66 closes a back-edge to root 23.
    (False, False, 61440, 53314, 0x01, "iso9660"),
    # Rock Ridge PVD walk: RR padding shifts the cycle byte to +32 on the same extent.
    (True, False, 63488, 53280, 0x01, "rock_ridge"),
    # Joliet SVD walk on the RR+Joliet image (found by the mutation harness).
    (True, True, 81920, 71746, 0x01, "rock_ridge"),
)


def _build_pycdlib_cycle_fixture(*, rock_ridge: bool, joliet: bool) -> bytes:
    """ISO built from ``_PYCDLIB_CYCLE_ENTRIES`` with the requested namespaces."""
    import pycdlib

    iso = pycdlib.PyCdlib()
    kwargs: dict[str, object] = {"interchange_level": 3}
    if rock_ridge:
        kwargs["rock_ridge"] = "1.09"
    if joliet:
        kwargs["joliet"] = 3
    iso.new(**kwargs)
    made_dirs: set[str] = set()

    def _ensure_dirs(rel: str) -> None:
        parts = rel.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            joined = "/".join(parts[:i])
            if joined and joined not in made_dirs:
                made_dirs.add(joined)
                iso_path = "/" + "/".join(p.upper()[:8] for p in joined.split("/"))
                iso.add_directory(
                    iso_path,
                    rr_name=parts[i - 1] if rock_ridge else None,
                    joliet_path="/" + joined if joliet else None,
                )

    counter = 0
    for name, contents, is_dir in _PYCDLIB_CYCLE_ENTRIES:
        rel = name.rstrip("/")
        _ensure_dirs(name)
        if is_dir:
            if rel not in made_dirs:
                made_dirs.add(rel)
                iso_path = "/" + "/".join(p.upper()[:8] for p in rel.split("/"))
                iso.add_directory(
                    iso_path,
                    rr_name=rel.split("/")[-1] if rock_ridge else None,
                    joliet_path="/" + rel if joliet else None,
                )
        else:
            counter += 1
            iso_dir = "/".join(p.upper()[:8] for p in rel.split("/")[:-1])
            iso_path = ("/" + iso_dir + "/" if iso_dir else "/") + f"F{counter}.TXT;1"
            iso.add_fp(
                io.BytesIO(contents),
                len(contents),
                iso_path,
                rr_name=rel.split("/")[-1] if rock_ridge else None,
                joliet_path="/" + rel if joliet else None,
            )
    out = io.BytesIO()
    iso.write_fp(out)
    iso.close()
    return out.getvalue()


def _pycdlib_directory_cycle_image(
    *,
    rock_ridge: bool,
    joliet: bool,
    expected_len: int,
    bitflip_offset: int,
    byte_before_flip: int,
) -> bytes:
    """Flip one bit in ``/subdir``'s directory extent so pycdlib's open walk cycles."""
    data = bytearray(_build_pycdlib_cycle_fixture(rock_ridge=rock_ridge, joliet=joliet))
    assert len(data) == expected_len, (
        "fixture layout drifted — revisit cycle case table"
    )
    assert data[bitflip_offset] == byte_before_flip
    data[bitflip_offset] ^= 0x01
    return bytes(data)


def test_corrupt_iso_raises() -> None:
    # CD001 is present (so detection still picks ISO) but the volume descriptor is cut off,
    # so pycdlib cannot parse it -> CorruptionError.
    truncated = _build_iso(rock_ridge=True, joliet=False)[:32780]
    with pytest.raises(CorruptionError):
        open_archive(io.BytesIO(truncated), format=ArchiveFormat.ISO)


@pytest.mark.timeout(5)
@pytest.mark.parametrize(
    (
        "rock_ridge",
        "joliet",
        "expected_len",
        "bitflip_offset",
        "byte_before_flip",
        "namespace",
    ),
    [
        pytest.param(*case, id=case_id)
        for case, case_id in zip(
            _PYCDLIB_CYCLE_CASES,
            ("plain", "rock-ridge", "joliet"),
            strict=True,
        )
    ],
)
def test_pycdlib_directory_cycle_does_not_hang(
    rock_ridge: bool,
    joliet: bool,
    expected_len: int,
    bitflip_offset: int,
    byte_before_flip: int,
    namespace: str,
) -> None:
    """Regression: corrupt ``/subdir`` must not hang pycdlib during ``open_fp``.

    ``pycdlib._walk_directories`` (used for the PVD / Rock Ridge tree *and* each
    supplementary namespace such as Joliet) enqueues child directory extents with no visit
    tracking. One flipped bit in a directory record can add a child that points back at an
    ancestor extent; pycdlib then loops forever. ``open_fp`` walks every present namespace,
    so a Joliet-only cycle still bites RR+Joliet images even when archivey reads Rock Ridge.
    Without archivey's extent cycle guard these cases hang until pytest-timeout kills them.
    """
    image = _pycdlib_directory_cycle_image(
        rock_ridge=rock_ridge,
        joliet=joliet,
        expected_len=expected_len,
        bitflip_offset=bitflip_offset,
        byte_before_flip=byte_before_flip,
    )
    with open_archive(io.BytesIO(image), format=ArchiveFormat.ISO) as ar:
        assert ar.info.extra["iso.namespace"] == namespace
        names = {m.name for m in ar.members()}
    if namespace == "iso9660":
        assert "F1.TXT" in names
    else:
        assert "file1.txt" in names


def test_filesystem_oserror_propagates_unwrapped(tmp_path: Path) -> None:
    # A genuine OSError (missing file) is unrelated to ISO decoding and must propagate
    # unchanged, not be reclassified as CorruptionError (error-handling spec).
    missing = tmp_path / "does-not-exist.iso"
    with pytest.raises(FileNotFoundError):
        open_archive(missing, format=ArchiveFormat.ISO)


# ---------------------------------------------------------------------------
# Availability (FULL when pycdlib is present)
# ---------------------------------------------------------------------------


def test_iso_full_support_with_pycdlib() -> None:
    assert format_availability(ArchiveFormat.ISO).support == FormatSupport.FULL


def test_open_from_mid_positioned_stream(rock_ridge_iso: Path) -> None:
    # pycdlib addresses the image with absolute offsets; open_archive normalizes a
    # mid-positioned stream to a zero-origin view, so an embedded image still opens.
    junk = b"J" * 51
    stream = io.BytesIO(junk + rock_ridge_iso.read_bytes())
    stream.seek(len(junk))
    with open_archive(stream, format=ArchiveFormat.ISO) as ar:
        assert any(m.is_file for m in ar.members())


# ---------------------------------------------------------------------------
# Header-sized allocations
# ---------------------------------------------------------------------------


def _iso_with_oversized_root_directory(declared: int) -> bytes:
    """An ISO whose root directory record claims ``declared`` bytes of directory data.

    pycdlib clamps a *file*'s ``data_length`` to the image length before reading it
    but not a *directory*'s, and the root record sits at a fixed offset inside the
    primary volume descriptor, so only those eight bytes change. The both-endian
    field must be rewritten in both orders or pycdlib rejects it before reading.
    """
    blob = bytearray(_build_iso(rock_ridge=False, joliet=False))
    offset = 32768 + 156 + 10  # PVD + root directory record + data_length
    blob[offset : offset + 8] = struct.pack("<I", declared) + struct.pack(
        ">I", declared
    )
    return bytes(blob)


@pytest.mark.parametrize("length", ["fact", "hint", "unknown"])
def test_directory_data_length_does_not_drive_the_allocation(length: str) -> None:
    """A directory record's 32-bit length must not size a read of the image.

    It is read at ``open_fp`` time, before a member is listed, so a small image buys
    an allocation of up to 4 GiB — and the ``MemoryError`` it produced is not an
    ``ArchiveyError`` at all. Asking for the bytes is the observable: whether the
    allocation then succeeds depends on the machine.

    The three parameters are the three things the source can know about the image's
    length, and they take different branches of the bound. ``fact`` is a ``BytesIO``,
    whose length the boundary reads from its buffer, so the read is clamped to what is
    left; it fails against handing pycdlib an unbounded source, which passes
    4 294 967 040 straight through. ``hint`` advertises the fsspec ``size`` attribute,
    a caller's unverified claim, which must not clamp (an understating hint would
    truncate a legitimate read), so the read is stepped. ``unknown`` has neither, which
    is what an ordinary caller-supplied seekable file-like looks like; it is stepped
    too, and fails against bounding only when the length is known.
    """
    declared = 0xFFFFFF00
    data = _iso_with_oversized_root_directory(declared)
    source: FactSizedReadRecorder | ReadSizeRecorder = (
        FactSizedReadRecorder(data)
        if length == "fact"
        else ReadSizeRecorder(data, advertise_size=length == "hint")
    )

    with pytest.raises(CorruptionError):
        open_archive(source, format=ArchiveFormat.ISO)

    assert source.requested, "the source was never read"
    # As in the TAR equivalent: a raw source sits under a ``BufferedReader`` whose
    # refill size is a runtime constant (``io.DEFAULT_BUFFER_SIZE``: 8 KiB through
    # 3.13, 128 KiB from 3.14), larger than this image on a recent Python. The bound is
    # one refill or, whichever is larger, the image when its length is a fact and the
    # step when it is not; what is pinned is that no read scales with ``declared``.
    reach = len(data) if length == "fact" else DEFAULT_UNKNOWN_LENGTH_READ_STEP
    bound = max(reach, io.DEFAULT_BUFFER_SIZE)
    assert max(source.requested) <= bound, (
        f"asked the source for {max(source.requested)} bytes "
        f"from a {len(data)}-byte image"
    )


def test_a_path_source_is_read_through_the_archive_source(
    rock_ridge_iso: Path,
) -> None:
    """Bounding a path source is only possible while pycdlib reads archivey's object.

    A path handed to ``PyCdlib.open`` would open its own file and leave nothing of
    archivey's underneath it, so there would be nowhere to put the bound. This pins
    the object rather than the allocation: the allocation itself is what the bound
    prevents, and provoking it to prove that costs gigabytes.
    ``test_directory_data_length_does_not_drive_the_allocation`` covers the bound on a
    source that can record what was asked of it.
    """
    with open_archive(rock_ridge_iso) as reader:
        assert isinstance(reader, IsoReader)
        assert isinstance(reader._source, ArchiveSource)
        assert reader._source.path == rock_ridge_iso
        assert reader._iso._cdfp is reader._source
        assert [m.name for m in reader.members()]


def test_a_path_source_refuses_the_same_image(tmp_path: Path) -> None:
    """The refusal reaches the path branch, not only the stream one."""
    path = tmp_path / "bomb.iso"
    path.write_bytes(_iso_with_oversized_root_directory(0xFFFFFF00))

    with pytest.raises(CorruptionError):
        open_archive(path)


@contextlib.contextmanager
def _recording_opens(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[list[IO[bytes]]]:
    """Collect every file object opened for ``path`` while the block runs.

    A handle leak is asserted on the objects themselves rather than on
    ``/proc/self/fd``, which does not exist on the Windows and macOS legs. Anything
    still open is closed on the way out, so a failing assertion does not leak from the
    test either.
    """
    real_open = builtins.open
    opened: list[IO[bytes]] = []

    def recording_open(file, *args, **kwargs):  # type: ignore[no-untyped-def]
        fp = real_open(file, *args, **kwargs)
        if isinstance(file, (str, os.PathLike)) and Path(file) == path:
            opened.append(fp)
        return fp

    monkeypatch.setattr(builtins, "open", recording_open)
    try:
        yield opened
    finally:
        # No ``monkeypatch.undo()``: the fixture unwinds it, and undoing here would
        # also drop patches a caller set before entering this block.
        for fp in opened:
            fp.close()


def test_a_refused_path_source_does_not_hold_its_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path open that fails must close the handle it opened, not wait for the GC.

    The ``ArchiveSource`` opens the path lazily, on pycdlib's first read, so it has
    something to bound (see ``test_a_path_source_is_read_through_the_archive_source``).
    A refusal after that open leaves the source reachable from the constructor's frame,
    and the exception's traceback keeps that frame alive for as long as the caller holds
    the exception — which an inventory or fuzz loop that catches and continues does for
    the whole batch, one descriptor per refused image. ``pytest.raises`` holds it here
    the same way.

    Fails against removing ``open_archive``'s close of the source when a backend
    constructor raises: every fp recorded below is then still open at the assertion.
    """
    path = tmp_path / "bomb.iso"
    path.write_bytes(_iso_with_oversized_root_directory(0xFFFFFF00))

    with _recording_opens(path, monkeypatch) as opened:
        with pytest.raises(CorruptionError) as excinfo:
            open_archive(path, format=ArchiveFormat.ISO)
        # The traceback is what pinned the handle; assert it is still here, so this
        # test cannot pass by the exception having been collected instead.
        assert excinfo.value.__traceback__ is not None
        assert opened, "the source did not open the path"
        assert [fp for fp in opened if not fp.closed] == []


def test_a_failure_after_open_fp_is_translated_and_releases(
    rock_ridge_iso: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The release guard covers the whole constructor, not just ``open_fp``.

    The namespace auto-select runs two more pycdlib calls on the image after
    ``open_fp`` returns. They sat outside the ``try`` at first, which left two claims
    untrue of that window: a raise there leaked ``_owned_fp``, and it escaped as a bare
    ``PyCdlibException`` rather than as ``CorruptionError``. Neither is reachable
    through a crafted image — ``has_rock_ridge`` raises only on an uninitialized object
    — so the failure is injected rather than provoked. An unreachable window is still
    the shape the comment, the threat model and the PR body all describe, and the next
    call added to that block need not be as safe.

    Fails against a guard that ends at ``open_fp``: the raise arrives as
    ``PyCdlibInvalidISO`` and the recorded handle is still open.
    """
    from pycdlib.pycdlibexception import PyCdlibInvalidISO

    def boom(self) -> bool:  # type: ignore[no-untyped-def]
        raise PyCdlibInvalidISO("injected")

    monkeypatch.setattr("pycdlib.PyCdlib.has_rock_ridge", boom)

    with _recording_opens(rock_ridge_iso, monkeypatch) as opened:
        with pytest.raises(CorruptionError) as excinfo:
            open_archive(rock_ridge_iso, format=ArchiveFormat.ISO)
        assert excinfo.value.__traceback__ is not None
        assert opened, "the reader did not open the path itself"
        assert [fp for fp in opened if not fp.closed] == []


def test_a_failing_iso_close_still_releases_the_handle(
    rock_ridge_iso: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closing is two steps, and the second must not depend on the first succeeding.

    ``_release_archive_handles`` closes the ``PyCdlib`` and then the handle this reader
    opened. Without the ``finally`` a raise out of the first skips the second, which
    leaks a descriptor on the ordinary close and, on the ``__init__`` path, replaces
    the error the image produced with the close error *and* leaves the fp open — the
    outcome the guard was added to prevent.

    Injected, like ``test_a_failure_after_open_fp_is_translated_and_releases``:
    ``PyCdlib.close()`` raises only on an object it never opened, which the
    ``_iso_opened`` flag already excludes. The same reasoning applies — a helper that
    promises one release path must not give up half of it on its own first failure.

    Fails against the sequential form: the close error still propagates, but the
    recorded handle is left open.
    """

    def boom(self) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("injected close failure")

    monkeypatch.setattr("pycdlib.PyCdlib.close", boom)

    with _recording_opens(rock_ridge_iso, monkeypatch) as opened:
        reader = open_archive(rock_ridge_iso, format=ArchiveFormat.ISO)
        with pytest.raises(RuntimeError, match="injected close failure"):
            reader.close()
        assert opened, "the reader did not open the path itself"
        assert [fp for fp in opened if not fp.closed] == []


def test_a_clean_image_is_unaffected(rock_ridge_iso: Path) -> None:
    """The bound may not shorten a read a well-formed image legitimately makes."""
    with open_archive(rock_ridge_iso) as reader:
        names = [m.name for m in reader.members()]
    assert "file.txt" in names


# --- listing follows records, not names ----------------------------------------------


def _build_rr_iso(populate: Callable[[Any], None]) -> bytes:
    """A Rock Ridge image whose contents ``populate(iso)`` adds."""
    import pycdlib

    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, rock_ridge="1.09")
    populate(iso)
    out = io.BytesIO()
    iso.write_fp(out)
    iso.close()
    return out.getvalue()


def _two_rr_files(iso: Any) -> None:
    iso.add_fp(io.BytesIO(b"AAAA"), 4, "/AAA.;1", rr_name="aaa")
    iso.add_fp(io.BytesIO(b"BBBB"), 4, "/BBB.;1", rr_name="bbb")


def test_a_rock_ridge_name_holding_a_slash_costs_no_sibling() -> None:
    """A name pycdlib's own path lookup cannot find again used to abort the listing."""
    data = _build_rr_iso(_two_rr_files)
    nm = b"NM\x08\x01\x00aaa"
    assert data.count(nm) == 1
    data = data.replace(nm, b"NM\x08\x01\x00a/a")
    with open_archive(io.BytesIO(data)) as ar:
        names = {m.name for m in ar.members()}
        assert {"a/a", "bbb"} <= names
        assert ar.read("bbb") == b"BBBB"
        assert ar.read("a/a") == b"AAAA"


def test_duplicate_rock_ridge_names_all_list() -> None:
    def populate(iso: Any) -> None:
        iso.add_directory("/AAA", rr_name="dup")
        iso.add_directory("/BBB", rr_name="dup")
        iso.add_fp(io.BytesIO(b"x"), 1, "/AAA/F.;1", rr_name="f")

    with open_archive(io.BytesIO(_build_rr_iso(populate))) as ar:
        members = ar.members()
        assert [m.name for m in members].count("dup/") == 2
        assert ar.read("dup/f") == b"x"


def test_a_rock_ridge_device_node_is_other_not_file(tmp_path: Path) -> None:
    """The PX file-type bits decide the type: a character device is never a FILE."""
    data = _build_rr_iso(_two_rr_files)
    # PX mode is a both-endian 32-bit field: 0o100444 (regular, r--r--r--).
    regular = struct.pack("<I", 0o100444) + struct.pack(">I", 0o100444)
    assert data.count(regular) == 2
    device = struct.pack("<I", 0o020666) + struct.pack(">I", 0o020666)
    data = data.replace(regular, device, 1)
    with open_archive(io.BytesIO(data)) as ar:
        by_name = {m.name: m for m in ar.members()}
        assert by_name["aaa"].type is MemberType.OTHER
        assert by_name["aaa"].size is None
        assert by_name["aaa"].mode == 0o666
        assert by_name["bbb"].type is MemberType.FILE
        ar.extract_all(tmp_path / "out")
    assert not (tmp_path / "out" / "aaa").exists()
    assert (tmp_path / "out" / "bbb").read_bytes() == b"BBBB"


def test_plain_iso_versions_keep_the_newest_current(tmp_path: Path) -> None:
    """The newest ``;N`` takes the bare name (the empty-extension dot goes too); an
    older version keeps its stored identifier and ``is_current=False``, as a RAR
    history row does.
    Every version records its number in ``iso.version``."""
    import pycdlib

    iso = pycdlib.PyCdlib()
    iso.new()
    iso.add_fp(io.BytesIO(b"OLD VERSION"), 11, "/FOO.;1")
    iso.add_fp(io.BytesIO(b"NEW VERSION"), 11, "/FOO.;2")
    iso.add_fp(io.BytesIO(b"text"), 4, "/BAR.TXT;1")
    out = io.BytesIO()
    iso.write_fp(out)
    iso.close()

    with open_archive(io.BytesIO(out.getvalue())) as ar:
        rows = [(m.name, m.extra["iso.version"], m.is_current) for m in ar.members()]
        assert sorted(rows) == [
            ("BAR.TXT", 1, True),
            ("FOO", 2, True),
            ("FOO.;1", 1, False),
        ]
        assert ar.read("FOO") == b"NEW VERSION"
        assert ar.read("FOO.;1") == b"OLD VERSION"
        ar.extract_all(tmp_path / "out")
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["BAR.TXT", "FOO"]
    assert (tmp_path / "out" / "FOO").read_bytes() == b"NEW VERSION"


def test_rock_ridge_relocation_directory_is_not_listed() -> None:
    """Deep trees are parked under ``rr_moved`` and relinked; only the logical tree lists."""

    def populate(iso: Any) -> None:
        path = ""
        for depth in range(12):
            path += f"/D{depth}"
            iso.add_directory(path, rr_name=f"dir{depth}")
        iso.add_fp(io.BytesIO(b"deep"), 4, path + "/F.;1", rr_name="f.txt")

    data = _build_rr_iso(populate)
    assert b"RR_MOVED" in data
    with open_archive(io.BytesIO(data)) as ar:
        names = [m.name for m in ar.members()]
        assert not any(n.lower().startswith("rr_moved") for n in names)
        deep = "/".join(f"dir{d}" for d in range(12)) + "/f.txt"
        assert deep in names
        assert len(names) == 13
        assert ar.read(deep) == b"deep"


def test_a_rock_ridge_record_without_entries_lists_under_its_iso_name() -> None:
    """An RR image whose one record has no System Use entries keeps that member,
    names it from the ISO 9660 identifier, and says what was lost."""
    from archivey import DiagnosticCode

    data = bytearray(_build_rr_iso(_two_rr_files))
    at = data.index(b"AAA.;1")
    start = at - 33  # the identifier sits at offset 33 of its directory record
    length, ident_len = data[start], data[start + 32]
    su = start + 33 + ident_len + (1 if ident_len % 2 == 0 else 0)
    data[su : start + length] = bytes(start + length - su)

    with open_archive(io.BytesIO(bytes(data))) as ar:
        by_name = {m.name: m for m in ar.members()}
        assert set(by_name) == {"AAA", "bbb"}
        assert ar.read("AAA") == b"AAAA"
        assert [d.code for d in by_name["AAA"].diagnostics] == [
            DiagnosticCode.MEMBER_HEADER_RECORD_SKIPPED
        ]
        assert by_name["bbb"].diagnostics == ()


def test_the_record_walk_descends_each_directory_extent_once() -> None:
    """A child record pointing back at an ancestor extent is listed but not entered.

    pycdlib's parse-time guard keeps such a cycle out of a parsed tree, so the cycle
    is spliced into the parsed records here to reach the walk's own guard.
    """
    import pycdlib

    iso = pycdlib.PyCdlib()
    iso.new()
    iso.add_directory("/A")
    iso.add_directory("/A/B")
    iso.add_fp(io.BytesIO(b"x"), 1, "/A/B/F.;1")
    out = io.BytesIO()
    iso.write_fp(out)
    iso.close()

    with open_archive(io.BytesIO(out.getvalue())) as ar:
        reader: Any = ar
        a = reader._iso.get_record(iso_path="/A")
        b = reader._iso.get_record(iso_path="/A/B")
        b.children.append(a)  # B now lists its own parent A as a child
        names = [m.name for m in ar.members()]
    assert sorted(names) == ["A/", "A/B/", "A/B/A/", "A/B/F"]


def test_listing_reads_nothing_from_the_image() -> None:
    """Materialization only touches catalog records pycdlib parsed at open: the
    audit the handle-lock requirement relies on."""

    class Counting(io.BytesIO):
        calls = 0

        def read(self, size: int | None = -1, /) -> bytes:
            Counting.calls += 1
            return super().read(size)

        def seek(self, offset: int, whence: int = 0, /) -> int:
            Counting.calls += 1
            return super().seek(offset, whence)

    with open_archive(Counting(_build_rr_iso(_two_rr_files))) as ar:
        before = Counting.calls
        assert len(ar.members()) == 2
        assert Counting.calls == before


def test_rock_ridge_long_form_tf_time_is_read() -> None:
    """A TF record with LONG_FORM set carries 17-byte dates (``VolumeDescriptorDate``:
    four-digit year, ``dayofmonth``, hundredths). They used to come back as ``None``
    because only the 7-byte field names were read."""
    from datetime import datetime, timedelta, timezone

    from pycdlib.dates import DirectoryRecordDate, VolumeDescriptorDate

    from archivey.internal.backends.iso_reader import _dr_date_to_datetime

    long_form = VolumeDescriptorDate()
    # 2024-03-05 06:07:08.09, gmtoffset +8 (15-minute units: UTC+2).
    long_form.parse(b"2024030506070809" + struct.pack("=b", 8))
    assert _dr_date_to_datetime(long_form) == datetime(
        2024, 3, 5, 6, 7, 8, 90_000, tzinfo=timezone(timedelta(hours=2))
    )

    unspecified = VolumeDescriptorDate()
    unspecified.parse(b"0" * 16 + b"\x00")
    assert _dr_date_to_datetime(unspecified) is None

    short_form = DirectoryRecordDate()
    short_form.parse(struct.pack("=BBBBBBb", 124, 3, 5, 6, 7, 8, 8))
    assert _dr_date_to_datetime(short_form) == datetime(
        2024, 3, 5, 6, 7, 8, tzinfo=timezone(timedelta(hours=2))
    )


def test_rock_ridge_tf_modification_time_wins_over_record_date() -> None:
    """Through the reader: a TF modification time that differs from the directory
    record's date is the one ``modified`` reports. The record date used to win,
    because it is always present."""
    from datetime import datetime, timezone

    import pycdlib
    from pycdlib.dates import DirectoryRecordDate

    iso = pycdlib.PyCdlib()
    iso.new(rock_ridge="1.09")
    iso.add_fp(io.BytesIO(b"hi"), 2, "/A.TXT;1", rr_name="a.txt")
    tf_date = DirectoryRecordDate()
    tf_date.parse(struct.pack("=BBBBBBb", 101, 1, 2, 3, 4, 5, 0))
    record = iso.get_record(rr_path="/a.txt")
    record.rock_ridge.dr_entries.tf_record.modification_time = tf_date
    image = io.BytesIO()
    iso.write_fp(image)
    iso.close()

    with open_archive(io.BytesIO(image.getvalue())) as archive:
        (member,) = [m for m in archive.members() if m.name == "a.txt"]
    assert member.modified == datetime(2001, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
