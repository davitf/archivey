"""Tests for the exception-translation spine wired through ``ArchiveStream``.

A raw decode error raised while reading a member stream must surface as an
``ArchiveyError`` subclass (via the backend's ``_translate_exception``) stamped with
format/archive/member context (via ``_stamp_error_context``). See ``error-handling`` and
Phase-2 task 0.1.
"""

from __future__ import annotations

import gzip
import io
import re
import subprocess
import tarfile
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import BinaryIO

import pytest

from archivey import open_archive
from archivey.cost import (
    AccessCost,
    CostReceipt,
    ListingCost,
    StreamCapability,
)
from archivey.exceptions import ArchiveyError, ArchiveyUsageError, CorruptionError
from archivey.internal.base_reader import BaseArchiveReader
from archivey.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    MemberType,
)
from tests.conftest import requires, requires_binary
from tests.corruption_util import raises_corruption_not_truncation


class _RawDecodeError(Exception):
    """Stands in for a third-party codec library's own exception type."""


class _RaisingStream(io.RawIOBase):
    """A member stream whose read raises a raw (un-translated) library exception."""

    def readable(self) -> bool:
        return True

    def read(self, n: int = -1, /) -> bytes:
        raise _RawDecodeError("boom")


class _TranslatingReader(BaseArchiveReader):
    """A reader that maps ``_RawDecodeError`` to ``CorruptionError`` and wraps its streams."""

    def _iter_members(self) -> Iterator[ArchiveMember]:
        yield ArchiveMember(type=MemberType.FILE, name="member.bin", size=1)

    def _open_member(self, member: ArchiveMember) -> BinaryIO:
        return self._wrap_member_stream(_RaisingStream(), member.name)

    def _translate_exception(self, exc: Exception) -> ArchiveyError | None:
        if isinstance(exc, _RawDecodeError):
            return CorruptionError(f"decode failed: {exc}")
        return None

    def _get_archive_info(self) -> ArchiveInfo:
        return ArchiveInfo(
            format=ArchiveFormat.ZIP,
            format_version=None,
            is_solid=False,
            member_count=None,
            comment=None,
            is_encrypted=False,
            is_multivolume=False,
            cost=CostReceipt(
                listing_cost=ListingCost.INDEXED,
                access_cost=AccessCost.DIRECT,
                stream_capability=StreamCapability.SEEKABLE,
            ),
        )

    def _close_archive(self) -> None:
        pass


def test_raw_decode_error_surfaces_as_stamped_archiveyerror() -> None:
    reader = _TranslatingReader(ArchiveFormat.ZIP, False, "archive.zip")
    with raises_corruption_not_truncation() as excinfo:
        reader.read("member.bin")

    err = excinfo.value
    assert isinstance(err.__cause__, _RawDecodeError)  # original attached as cause
    assert err.source_format is ArchiveFormat.ZIP  # format stamped
    assert err.archive_name == "archive.zip"  # archive stamped
    assert err.member_name == "member.bin"  # member stamped


def test_untranslated_exception_propagates_unchanged() -> None:
    """A backend that doesn't recognize an exception lets it propagate (no catch-all)."""

    class _Unmapped(_TranslatingReader):
        def _translate_exception(self, exc: Exception) -> ArchiveyError | None:
            return None  # recognizes nothing

    reader = _Unmapped(ArchiveFormat.ZIP, False, "archive.zip")
    with pytest.raises(_RawDecodeError):
        reader.read("member.bin")


# ---------------------------------------------------------------------------
# ArchiveStream close(): failure still marks the wrapper closed
# ---------------------------------------------------------------------------


def test_archive_stream_close_failure_still_closes_wrapper() -> None:
    from archivey.internal.streams.archive_stream import ArchiveStream

    class _FailingClose(io.BytesIO):
        def close(self) -> None:  # noqa: D102
            raise RuntimeError("simulated close failure")

    def _translate(exc: Exception) -> ArchiveyError | None:
        if isinstance(exc, RuntimeError):
            return CorruptionError(f"translated: {exc}")
        return None

    stream = ArchiveStream(lambda: _FailingClose(b"data"), translate=_translate)
    with raises_corruption_not_truncation():
        stream.close()
    # The wrapper is closed despite the inner failure: reads are refused and a retried
    # close() is a no-op instead of failing again.
    assert stream.closed
    with pytest.raises(ValueError):
        stream.read()
    stream.close()  # idempotent


# ---------------------------------------------------------------------------
# ArchiveStream: a closed underlying source surfaces as a typed error
# ---------------------------------------------------------------------------


def test_archive_stream_translates_closed_source_before_backend_translator() -> None:
    # The inner stream hitting a closed handle ("I/O operation on closed file.") is a
    # library-agnostic condition (reader or caller source closed under a live member
    # stream — see archive-reading's concurrent-open "fail loudly" clause). It must map
    # to ArchiveyUsageError *before* the per-library translator runs, so a
    # backend's generic ValueError mapping (e.g. ZIP's corrupt-offset rule) cannot
    # claim it as corruption.
    from archivey.internal.streams.archive_stream import ArchiveStream

    class _ClosedUnderneath(io.BytesIO):
        def read(self, n: int = -1, /) -> bytes:
            raise ValueError("I/O operation on closed file.")

    def _greedy_value_error_translate(exc: Exception) -> ArchiveyError | None:
        if isinstance(exc, ValueError):
            return CorruptionError(f"mislabeled: {exc}")
        return None

    stream = ArchiveStream(
        lambda: _ClosedUnderneath(b"data"),
        translate=_greedy_value_error_translate,
    )
    with pytest.raises(ArchiveyUsageError):
        stream.read()


def test_reader_boundary_translates_closed_source_before_backend_translator() -> None:
    """The reader-side boundary checks for a closed source before the translator too.

    The member open fails outside any member stream, and the translator maps every
    ``ValueError`` to corruption. The closed source must still be a usage error that
    names the member.
    """

    class _ClosedOnOpen(_TranslatingReader):
        def _open_member(self, member: ArchiveMember) -> BinaryIO:
            with self._translated_errors(member.name):
                raise ValueError("I/O operation on closed file.")

        def _translate_exception(self, exc: Exception) -> ArchiveyError | None:
            if isinstance(exc, ValueError):
                return CorruptionError(f"mislabeled: {exc}")
            return None

    reader = _ClosedOnOpen(ArchiveFormat.ZIP, False, "archive.zip")
    with pytest.raises(
        ArchiveyUsageError,
        match=r"^Cannot read member 'member\.bin': the archive source has been closed\.$",
    ):
        reader.read("member.bin")


# ---------------------------------------------------------------------------
# Every format: a caller-closed source is a usage error, also when a member opens
# ---------------------------------------------------------------------------

_RAR_FIXTURES = Path(__file__).parent / "fixtures" / "rar"
_PAYLOAD = b"hello world " * 200


def _zip(compression: int) -> Callable[[Path], bytes]:
    def build(_tmp_path: Path) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression) as zf:
            zf.writestr("f.txt", _PAYLOAD)
        return buf.getvalue()

    return build


def _tar(mode: str) -> Callable[[Path], bytes]:
    def build(_tmp_path: Path) -> bytes:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode=mode) as tf:
            info = tarfile.TarInfo("f.txt")
            info.size = len(_PAYLOAD)
            tf.addfile(info, io.BytesIO(_PAYLOAD))
        return buf.getvalue()

    return build


def _gzip(_tmp_path: Path) -> bytes:
    return gzip.compress(_PAYLOAD)


def _sevenzip(tmp_path: Path) -> bytes:
    (tmp_path / "f.txt").write_bytes(_PAYLOAD)
    out = tmp_path / "a.7z"
    subprocess.run(
        ["7z", "a", "-bd", "-bso0", str(out), "f.txt"], cwd=tmp_path, check=True
    )
    return out.read_bytes()


def _iso(_tmp_path: Path) -> bytes:
    import pycdlib

    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3)
    iso.add_fp(io.BytesIO(_PAYLOAD), len(_PAYLOAD), "/F.TXT;1")
    buf = io.BytesIO()
    iso.write_fp(buf)
    iso.close()
    return buf.getvalue()


def _solid_rar(_tmp_path: Path) -> bytes:
    return (_RAR_FIXTURES / "basic_solid__.rar").read_bytes()


def _nonsolid_rar(_tmp_path: Path) -> bytes:
    return (_RAR_FIXTURES / "basic_nonsolid__.rar").read_bytes()


_CLOSED_SOURCE_BUILDERS = [
    pytest.param(_zip(zipfile.ZIP_STORED), id="zip-stored"),
    pytest.param(_zip(zipfile.ZIP_DEFLATED), id="zip-deflated"),
    pytest.param(_tar("w"), id="tar"),
    pytest.param(_tar("w:gz"), id="tar-gz"),
    pytest.param(_gzip, id="gz"),
    pytest.param(_sevenzip, id="7z", marks=requires_binary("7z")),
    pytest.param(_iso, id="iso", marks=requires("pycdlib")),
    pytest.param(_solid_rar, id="rar-solid", marks=requires_binary("unrar")),
    pytest.param(_nonsolid_rar, id="rar-nonsolid", marks=requires_binary("unrar")),
]


@pytest.mark.parametrize("build", _CLOSED_SOURCE_BUILDERS)
@pytest.mark.parametrize("access", ["open", "stream_members"])
def test_source_closed_before_a_member_opens_is_a_usage_error(
    tmp_path: Path, build: Callable[[Path], bytes], access: str
) -> None:
    """The caller closes the ``BinaryIO`` they passed in, then opens a member.

    That is a usage error on every format (archive-reading spec: a caller-supplied
    source closed early). ZIP and ISO reported it as corruption, because the error
    came while the member opened, outside the member stream that already mapped it;
    a solid RAR let the raw ``ValueError`` out.
    """
    source = io.BytesIO(build(tmp_path))
    with open_archive(source) as reader:
        member = next(m for m in reader.members() if m.is_file)
        source.close()
        expected = (
            rf"^Cannot read member '{re.escape(member.name)}': "
            r"the archive source has been closed\.$"
        )
        with pytest.raises(ArchiveyUsageError, match=expected):
            if access == "open":
                reader.read(member)
            else:
                for _member, stream in reader.stream_members():
                    if stream is not None:
                        stream.read()


def test_source_closed_before_listing_is_a_usage_error_naming_no_member(
    tmp_path: Path,
) -> None:
    """The caller closes the source before a TAR is listed; no member is involved.

    TAR lists lazily, so listing reads the closed source. The listing runs through the
    same reader boundary as a member open, with no member name, and the message must
    not name or imply one.
    """
    source = io.BytesIO(_tar("w")(tmp_path))
    with open_archive(source) as reader:
        source.close()
        with pytest.raises(
            ArchiveyUsageError,
            match=r"^Cannot read the archive: its source has been closed\.$",
        ):
            list(reader.members())
