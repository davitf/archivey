"""A gzip member after the first keeps the standard library's header errors.

``compressed-streams`` lets an accelerator differ on a wrong ISIZE of a member
other than the last. It does not let one differ on a header CRC or a reserved
flag bit, on the first member or a later one (``format-single-file-compressors``).
The handbook used to say the later member read clean under rapidgzip. These
tests fail if that comes back.
"""

from __future__ import annotations

import gzip
import random
import struct
import zlib
from collections.abc import Callable

import pytest

from archivey import ArchiveFormat
from archivey.exceptions import CorruptionError, UnsupportedFeatureError
from tests.conftest import requires
from tests.test_audit2_tar_streams import _ACCEL_ON, _read_single


def _bad_fhcrc(member: bytes) -> bytes:
    header = bytearray(member[:10])
    header[3] |= 0x02
    crc16 = (zlib.crc32(bytes(header)) & 0xFFFF) ^ 0x0001
    return bytes(header) + struct.pack("<H", crc16) + member[10:]


def _reserved_flag(member: bytes) -> bytes:
    return member[:3] + bytes([0x20]) + member[4:]


def _two_members(damage: Callable[[bytes], bytes]) -> bytes:
    payload = random.Random(4).randbytes(200_000)
    first = gzip.compress(b"A" * 10_000, mtime=0)
    second = gzip.compress(payload, mtime=0)
    return first + damage(second)


@requires("rapidgzip")
@pytest.mark.parametrize(
    ("damage", "error"),
    [
        (_bad_fhcrc, CorruptionError),
        (_reserved_flag, UnsupportedFeatureError),
    ],
)
def test_later_gzip_member_header_raises_with_the_accelerator(
    damage: Callable[[bytes], bytes], error: type[BaseException]
) -> None:
    """The standard-library path and ``use_rapidgzip=ON`` raise the same class.

    A header CRC mismatch is corruption. A reserved flag bit is unsupported, on
    a later member as on the first.
    """
    data = _two_members(damage)
    with pytest.raises(error):
        _read_single(data, ArchiveFormat.GZ)
    with pytest.raises(error):
        _read_single(data, ArchiveFormat.GZ, config=_ACCEL_ON, seekable_members=True)
