"""What ``open_archive`` tells a caller who hands it a pipe.

The refusal is one typed error (``StreamNotSeekableError``) for every format, but the
*next step* it names depends on the backend rather than on the mode that was asked for.
TAR and the single-file compressors read forward-only, so ``streaming=True`` really is
the fix; ZIP, ISO, 7z and RAR need seek in either mode, so for those the only fix is a
seekable source. Suggesting ``streaming=True`` there sent callers into a second refusal
that explained the retry could never have worked, which is what these tests pin shut.

Every case names ``format=`` explicitly, so the refusal is reached before a byte is
read and no fixture archive is needed.
"""

from __future__ import annotations

import pytest

from archivey import (
    ArchiveFormat,
    FormatSupport,
    StreamNotSeekableError,
    format_availability,
    open_archive,
)
from tests.streams_util import NonSeekableBytesIO

# Trailing index or offset-addressed: no forward-only pass exists for these.
_NEEDS_SEEK = (
    ArchiveFormat.ZIP,
    ArchiveFormat.ISO,
    ArchiveFormat.SEVEN_Z,
    ArchiveFormat.RAR,
)

# Read front to back, so a pipe is fine once the caller asks for streaming. The
# single-file compressors share one backend and one SUPPORTS_STREAMING_NON_SEEKABLE, so
# sampling only GZ would miss a per-codec regression; the ones whose package is absent
# skip through _skip_unless_registered rather than being left out of the list.
_READS_FORWARD = (
    ArchiveFormat.TAR,
    ArchiveFormat.TAR_GZ,
    ArchiveFormat.GZ,
    ArchiveFormat.BZ2,
    ArchiveFormat.XZ,
    ArchiveFormat.ZST,
    ArchiveFormat.LZ4,
    ArchiveFormat.LZIP,
    ArchiveFormat.ZLIB,
    ArchiveFormat.BROTLI,
)


def _skip_unless_registered(fmt: ArchiveFormat) -> None:
    # ISO needs pycdlib; without it the open fails as UnsupportedFormatError long
    # before the seekability check this module is about.
    availability = format_availability(fmt)
    if availability.support is FormatSupport.NONE:
        pytest.skip(f"{fmt.display_name} has no usable backend here")


@pytest.mark.parametrize("fmt", _NEEDS_SEEK, ids=lambda f: f.display_name)
@pytest.mark.parametrize("streaming", [False, True], ids=["random", "streaming"])
def test_seek_only_format_never_proposes_streaming(
    fmt: ArchiveFormat, streaming: bool
) -> None:
    _skip_unless_registered(fmt)
    with pytest.raises(StreamNotSeekableError) as excinfo:
        open_archive(NonSeekableBytesIO(b""), format=fmt, streaming=streaming)

    message = str(excinfo.value)
    assert "streaming=True" not in message, (
        f"{fmt.display_name} refuses streaming=True as well, so proposing it is a "
        f"dead end: {message}"
    )
    assert "BytesIO" in message  # the step that does work
    assert excinfo.value.source_format is fmt


@pytest.mark.parametrize("fmt", _NEEDS_SEEK, ids=lambda f: f.display_name)
def test_seek_only_format_refuses_both_modes_alike(fmt: ArchiveFormat) -> None:
    """Same source, either mode, same answer — the mode was never the problem."""
    _skip_unless_registered(fmt)
    messages = set()
    for streaming in (False, True):
        with pytest.raises(StreamNotSeekableError) as excinfo:
            open_archive(NonSeekableBytesIO(b""), format=fmt, streaming=streaming)
        messages.add(str(excinfo.value))
    assert len(messages) == 1, messages


@pytest.mark.parametrize("fmt", _READS_FORWARD, ids=lambda f: f.display_name)
def test_forward_only_format_still_proposes_streaming(fmt: ArchiveFormat) -> None:
    _skip_unless_registered(fmt)
    with pytest.raises(StreamNotSeekableError) as excinfo:
        open_archive(NonSeekableBytesIO(b""), format=fmt, streaming=False)

    assert "streaming=True" in str(excinfo.value)
    assert excinfo.value.source_format is fmt
