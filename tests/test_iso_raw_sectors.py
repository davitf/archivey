"""Raw CD sector images (a ``.bin`` from a ``.bin``/``.cue`` pair) are refused by name.

Reading one means stripping every 2352-byte sector to its 2048-byte payload, which is not
implemented. What ships is the recognition: such a file used to fail detection with "no
magic-byte match", which sends a user looking for a corrupt file when the answer is to
convert it. None of this needs pycdlib — the refusal comes before the reader.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from archivey import (
    ArchiveFormat,
    UnsupportedFeatureError,
    detect_format,
    open_archive,
)

_SYNC = b"\x00" + b"\xff" * 10 + b"\x00"


def _sector(index: int, *, mode: int, form2: bool = False, size: int = 2352) -> bytes:
    """One raw sector: sync, BCD MSF address, mode byte, payload, zeroed EDC/ECC."""
    lba = index + 150  # the two-second pregap every MSF address counts from
    minute, rest = divmod(lba, 75 * 60)
    second, frame = divmod(rest, 75)
    msf = bytes(int(str(v), 16) for v in (minute, second, frame))
    header = _SYNC + msf + bytes([mode])
    if mode == 2:
        submode = 0x20 if form2 else 0x08
        body = bytes([0, 0, submode, 0]) * 2 + b"D" * (2324 if form2 else 2048)
    else:
        body = b"D" * 2048
    sector = header + body
    sector += b"\x00" * (2352 - len(sector))
    return sector + b"\x00" * (size - 2352)


def _image(
    *, mode: int, form2: bool = False, size: int = 2352, count: int = 4
) -> bytes:
    return b"".join(_sector(i, mode=mode, form2=form2, size=size) for i in range(count))


_CASES = [
    pytest.param(_image(mode=1), "Mode 1, 2352-byte sectors", id="mode1"),
    pytest.param(_image(mode=2), "Mode 2 Form 1, 2352-byte sectors", id="mode2-form1"),
    pytest.param(
        _image(mode=2, form2=True), "Mode 2 Form 2, 2352-byte sectors", id="mode2-form2"
    ),
    pytest.param(
        _image(mode=1, size=2448), "Mode 1, 2448-byte sectors", id="mode1-subchannel"
    ),
    pytest.param(
        _image(mode=7), "unknown sector mode 7, 2352-byte sectors", id="mode7"
    ),
    pytest.param(_image(mode=1, count=1), "(Mode 1)", id="single-sector"),
]


@pytest.mark.parametrize(("data", "layout"), _CASES)
def test_raw_sector_image_is_refused_by_name(
    data: bytes, layout: str, tmp_path: Path
) -> None:
    path = tmp_path / "disc.bin"
    path.write_bytes(data)
    with pytest.raises(UnsupportedFeatureError, match="raw CD sector image") as ei:
        open_archive(path)
    assert layout in str(ei.value)
    assert ei.value.archive_name is not None
    with pytest.raises(UnsupportedFeatureError, match="raw CD sector image"):
        open_archive(io.BytesIO(data))


def test_form2_says_there_is_no_filesystem(tmp_path: Path) -> None:
    path = tmp_path / "video.bin"
    path.write_bytes(_image(mode=2, form2=True))
    with pytest.raises(UnsupportedFeatureError, match="not an ISO 9660 filesystem"):
        open_archive(path)


def test_convertible_layouts_say_how_to_convert(tmp_path: Path) -> None:
    path = tmp_path / "disc.bin"
    path.write_bytes(_image(mode=1))
    with pytest.raises(UnsupportedFeatureError, match="convert it to a plain .iso"):
        open_archive(path)


def test_raw_sector_image_is_detected_as_iso(tmp_path: Path) -> None:
    # Detection claims it as ISO on the sync pattern, which is what lets the backend
    # refuse it by name instead of the file failing detection.
    path = tmp_path / "disc.bin"
    path.write_bytes(_image(mode=1))
    info = detect_format(path)
    assert info.format == ArchiveFormat.ISO
    assert info.detected_by == "magic"


def test_explicit_iso_format_refuses_too() -> None:
    data = _image(mode=1)
    with pytest.raises(UnsupportedFeatureError, match="raw CD sector image"):
        open_archive(io.BytesIO(data), format=ArchiveFormat.ISO)


def test_the_probe_restores_the_stream_position() -> None:
    from archivey.internal.backends.iso_reader import _describe_raw_sector_image

    stream = io.BytesIO(b"\x00" * 4096)
    assert _describe_raw_sector_image(stream) is None
    assert stream.tell() == 0
