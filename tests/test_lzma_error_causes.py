"""liblzma errors map to archivey's by cause, not all to ``CorruptionError``.

CPython raises every liblzma failure as ``lzma.LZMAError`` and tells them apart only
by message text. The end-to-end case builds the one options error a real file can
carry, an xz block header with a valid CRC naming a filter liblzma does not know, so a
change in CPython's wording fails here rather than silently turning into corruption.
"""

from __future__ import annotations

import lzma
import struct
import zlib
from pathlib import Path

import pytest

from archivey import open_archive
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    ResourceLimitError,
    UnsupportedFeatureError,
)
from archivey.internal.streams.codecs import LzmaAloneCodec
from archivey.internal.streams.xz import lzma_error_to_archivey

_PAYLOAD = b"hello " * 200


def _xz_with_unknown_filter() -> bytes:
    """xz whose first block header names filter id 0x7F, with the header CRC repaired."""
    data = bytearray(lzma.compress(_PAYLOAD, format=lzma.FORMAT_XZ))
    header_start = 12  # after the 12-byte stream header
    header_size = (data[header_start] + 1) * 4
    assert data[header_start + 2] == 0x21  # the LZMA2 filter id liblzma wrote
    data[header_start + 2] = 0x7F
    crc_at = header_start + header_size - 4
    data[crc_at : crc_at + 4] = struct.pack(
        "<I", zlib.crc32(bytes(data[header_start:crc_at]))
    )
    return bytes(data)


def test_liblzma_still_words_an_unknown_filter_as_an_options_error() -> None:
    with pytest.raises(lzma.LZMAError, match="^Invalid or unsupported options$"):
        lzma.LZMADecompressor(format=lzma.FORMAT_XZ).decompress(
            _xz_with_unknown_filter()
        )


def test_xz_with_an_unknown_filter_is_unsupported_not_corrupt(tmp_path: Path) -> None:
    archive = tmp_path / "a.txt.xz"
    archive.write_bytes(_xz_with_unknown_filter())
    with open_archive(archive) as reader:
        (entry,) = reader.members()
        with pytest.raises(UnsupportedFeatureError), reader.open(entry) as stream:
            stream.read()


def test_corrupt_xz_data_is_still_corruption(tmp_path: Path) -> None:
    data = bytearray(lzma.compress(_PAYLOAD, format=lzma.FORMAT_XZ))
    data[30] ^= 0xFF  # inside the first block's compressed data
    archive = tmp_path / "a.txt.xz"
    archive.write_bytes(bytes(data))
    with open_archive(archive) as reader:
        (entry,) = reader.members()
        with pytest.raises(CorruptionError), reader.open(entry) as stream:
            stream.read()


def test_liblzma_still_words_a_memlimit_refusal_the_same_way() -> None:
    with pytest.raises(lzma.LZMAError, match="^Memory usage limit"):
        lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=1024).decompress(
            lzma.compress(_PAYLOAD, format=lzma.FORMAT_XZ)
        )


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Invalid or unsupported options", UnsupportedFeatureError),
        ("Unsupported integrity check", UnsupportedFeatureError),
        ("Memory usage limit exceeded", ResourceLimitError),
        ("Corrupt input data", CorruptionError),
        ("Input format not supported by decoder", CorruptionError),
        ("Unrecognized error from liblzma: 42", CorruptionError),
    ],
)
def test_each_liblzma_cause_maps_to_its_own_error(
    message: str, expected: type[ArchiveyError]
) -> None:
    translated = lzma_error_to_archivey(lzma.LZMAError(message), "ctx")
    assert type(translated) is expected
    assert message in str(translated)


def test_the_lzma_codecs_translate_by_cause() -> None:
    """The raw LZMA and .lzma paths share the codec taxonomy, not xz's own mapping."""
    codec = LzmaAloneCodec()
    options = codec.translate(lzma.LZMAError("Invalid or unsupported options"))
    assert isinstance(options, UnsupportedFeatureError)
    corrupt = codec.translate(lzma.LZMAError("Corrupt input data"))
    assert isinstance(corrupt, CorruptionError)
