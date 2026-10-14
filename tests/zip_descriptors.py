"""ZIP rewrites around data descriptors, for the forward-read tests."""

from __future__ import annotations

import io
import struct
import zipfile

_DESCRIPTOR_SIGNATURE = b"PK\x07\x08"


def without_descriptor_signatures(raw: bytes) -> bytes:
    """``raw`` with the ``PK\\x07\\x08`` left out of every data descriptor, as early
    PKZIP wrote them (APPNOTE 4.3.9.3), offsets in the directory moved to match.

    Classic (non-ZIP64) archives only.
    """
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        infos = zf.infolist()
    cut: list[int] = []
    for info in infos:
        if not info.flag_bits & 0x08:
            continue
        at = info.header_offset
        name_len, extra_len = struct.unpack_from("<HH", raw, at + 26)
        end = at + 30 + name_len + extra_len + info.compress_size
        assert raw[end : end + 4] == _DESCRIPTOR_SIGNATURE, info.filename
        cut.append(end)

    def moved(offset: int) -> int:
        return offset - 4 * sum(1 for c in cut if c < offset)

    out = bytearray(raw)
    eocd = out.rindex(b"PK\x05\x06")
    (count,) = struct.unpack_from("<H", out, eocd + 10)
    (cd_offset,) = struct.unpack_from("<L", out, eocd + 16)
    at = cd_offset
    for _ in range(count):
        assert out[at : at + 4] == b"PK\x01\x02"
        (header_offset,) = struct.unpack_from("<L", out, at + 42)
        assert header_offset != 0xFFFF_FFFF, "ZIP64 offsets are not rewritten"
        struct.pack_into("<L", out, at + 42, moved(header_offset))
        name_len, extra_len, comment_len = struct.unpack_from("<HHH", out, at + 28)
        at += 46 + name_len + extra_len + comment_len
    struct.pack_into("<L", out, eocd + 16, moved(cd_offset))
    for c in reversed(cut):
        del out[c : c + 4]
    return bytes(out)
