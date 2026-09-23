"""A stream whose constructor refused its input must still close cleanly.

``IOBase.__del__`` calls ``close()`` on every instance not marked closed, including
one whose ``__init__`` raised. So ``close()`` must work on whatever ``__init__`` got
through before the raise. When it does not, the refusal dies a second time in the
finalizer, as an ``AttributeError`` reported at an arbitrary later moment.

A GIL build without ``-X dev`` drops that second error silently, so these tests do not
wait for the collector. They take the half-built object out of the ``__init__`` frame
in the traceback and do to it what the finalizer does.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import BinaryIO, TypeVar

import pytest

from archivey.diagnostics import DiagnosticPolicy
from archivey.exceptions import (
    ArchiveyError,
    ArchiveyUsageError,
    DiagnosticRaisedError,
    OpenError,
    StreamNotSeekableError,
)
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.streams.archive_stream import ArchiveStream
from archivey.internal.streams.decompressor_stream import (
    DecompressorStream,
    SeekPoint,
)
from archivey.internal.volumes import ConcatenatedFile
from tests.streams_util import ShortReadNonSeekable

T = TypeVar("T")


def _refused(cls: type[T], exc_info: pytest.ExceptionInfo[BaseException]) -> T:
    """The ``cls`` instance whose ``__init__`` raised ``exc_info``."""
    tb = exc_info.tb
    while tb is not None:
        frame = tb.tb_frame
        candidate = frame.f_locals.get("self")
        if frame.f_code.co_name == "__init__" and isinstance(candidate, cls):
            return candidate
        tb = tb.tb_next
    raise AssertionError(f"no {cls.__name__}.__init__ frame in the traceback")


def _assert_closes_cleanly(obj: io.IOBase) -> None:
    # IOBase.__del__: close() only when not already closed, and it must not raise.
    if not obj.closed:
        obj.close()
    assert obj.closed


@pytest.mark.parametrize(
    ("sources", "error"),
    [
        pytest.param(lambda _tmp: [], ArchiveyUsageError, id="no-volumes"),
        pytest.param(lambda tmp: [tmp / "gone.bin"], OpenError, id="missing-path"),
        pytest.param(lambda tmp: [tmp], OpenError, id="directory"),
        pytest.param(
            lambda _tmp: [ShortReadNonSeekable(b"abc", 1)],
            StreamNotSeekableError,
            id="non-seekable-stream",
        ),
    ],
)
def test_concatenated_file_refusal_closes_cleanly(
    tmp_path: Path, sources: object, error: type[ArchiveyError]
) -> None:
    with pytest.raises(error) as caught:
        ConcatenatedFile(sources(tmp_path))  # type: ignore[operator]
    _assert_closes_cleanly(_refused(ConcatenatedFile, caught))


class _DecoderRefused(Exception):
    pass


def _refusing_decoder(_point: SeekPoint, _inner: BinaryIO) -> object:
    raise _DecoderRefused("declared parameters refused")


def test_decompressor_stream_refused_decoder_closes_the_file_it_opened(
    tmp_path: Path,
) -> None:
    path = tmp_path / "member.bin"
    path.write_bytes(b"payload")
    with pytest.raises(_DecoderRefused) as caught:
        DecompressorStream(path, make_decoder=_refusing_decoder)  # type: ignore[arg-type]
    stream = _refused(DecompressorStream, caught)
    # Released by the constructor itself, not left for the collector.
    assert stream.closed
    assert stream._inner.closed
    _assert_closes_cleanly(stream)


@pytest.mark.parametrize("owns_inner", [True, False])
def test_decompressor_stream_refused_decoder_respects_ownership(
    owns_inner: bool,
) -> None:
    inner = io.BytesIO(b"payload")
    with pytest.raises(_DecoderRefused) as caught:
        DecompressorStream(
            inner,
            make_decoder=_refusing_decoder,  # type: ignore[arg-type]
            owns_inner=owns_inner,
        )
    _assert_closes_cleanly(_refused(DecompressorStream, caught))
    assert inner.closed is owns_inner


def test_decompressor_stream_missing_path_closes_cleanly(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError) as caught:
        DecompressorStream(tmp_path / "gone.bin", make_decoder=_refusing_decoder)  # type: ignore[arg-type]
    _assert_closes_cleanly(_refused(DecompressorStream, caught))


def test_archive_stream_refused_by_verifier_diagnostic_closes_cleanly() -> None:
    """Under the strict policy an unverifiable digest raises while the verifier is built."""
    opened: list[io.BytesIO] = []

    def open_fn() -> BinaryIO:
        opened.append(io.BytesIO(b"payload"))
        return opened[-1]

    collector = DiagnosticCollector(policy=DiagnosticPolicy.strict())
    with pytest.raises(DiagnosticRaisedError) as caught:
        ArchiveStream(
            open_fn,
            translate=lambda _e: None,
            collector=collector,
            expected_hashes={"no-such-digest": b"\x00"},  # type: ignore[dict-item]
        )
    _assert_closes_cleanly(_refused(ArchiveStream, caught))
    assert opened == []  # refused before the member was opened
