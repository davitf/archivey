"""Minimal writer for traditional (ZipCrypto / PKWARE) encrypted ZIP members.

The stdlib :mod:`zipfile` can *read* ZipCrypto but cannot *write* encryption, and the
`7z` CLI is not always present, so tests that need to exercise the ZipCrypto read path
(notably the multi-password disambiguation, where a wrong candidate password can pass
the cipher's single verification byte) build their archives here instead.

``build_zipcrypto_zip`` writes one member in STORED, DEFLATE, BZIP2 or LZMA.
``build_stored_zipcrypto_zip`` writes several STORED members, each with its own
password. Both are test scaffolding, not a general-purpose ZIP writer.

The cipher (APPNOTE.txt §6.1): a 96-bit key state seeded from the password, a 12-byte
random encryption header whose final byte is a verification value (the high byte of the
CRC-32 when no data descriptor is used), then the file bytes, all run through the same
keystream. The verification byte is only *one* byte, so ~1/256 of wrong passwords pass
it — which is exactly the hazard :func:`find_check_byte_collision` reproduces.
"""

from __future__ import annotations

import bz2
import lzma
import struct
import zipfile
import zlib
from collections.abc import Sequence


def _make_crc_table() -> list[int]:
    table = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ 0xEDB88320 if (c & 1) else (c >> 1)
        table.append(c)
    return table


_CRC_TABLE = _make_crc_table()


def _crc32_update(crc: int, byte: int) -> int:
    return ((crc >> 8) & 0xFFFFFF) ^ _CRC_TABLE[(crc ^ byte) & 0xFF]


class _Keys:
    """The 96-bit ZipCrypto key state."""

    def __init__(self, password: bytes) -> None:
        self.k0, self.k1, self.k2 = 0x12345678, 0x23456789, 0x34567890
        for b in password:
            self.update(b)

    def update(self, byte: int) -> None:
        self.k0 = _crc32_update(self.k0, byte) & 0xFFFFFFFF
        self.k1 = (self.k1 + (self.k0 & 0xFF)) & 0xFFFFFFFF
        self.k1 = (self.k1 * 134775813 + 1) & 0xFFFFFFFF
        self.k2 = _crc32_update(self.k2, (self.k1 >> 24) & 0xFF) & 0xFFFFFFFF

    def keystream_byte(self) -> int:
        temp = (self.k2 | 2) & 0xFFFF
        return ((temp * (temp ^ 1)) >> 8) & 0xFF


def _encrypt(password: bytes, check_byte: int, payload: bytes) -> bytes:
    keys = _Keys(password)
    out = bytearray()
    # 12-byte encryption header: 11 fixed bytes + the verification byte. Real writers
    # randomize the first 11; fixed here keeps fixtures byte-for-byte reproducible.
    header = bytes(range(11)) + bytes([check_byte])
    for plain in (*header, *payload):
        out.append(plain ^ keys.keystream_byte())
        keys.update(plain)
    return bytes(out)


def _member_bytes(
    name: bytes,
    data: bytes,
    enc: bytes,
    *,
    compression: int,
    version_needed: int,
    made_by: int,
    flags: int,
    extra: bytes,
    offset: int,
    external_attr: int,
) -> tuple[bytes, bytes]:
    """Local record and central entry for one ZipCrypto member.

    Shared so the single-member and multi-member writers pack the same headers
    for the same STORED entry.
    """
    crc = zlib.crc32(data) & 0xFFFFFFFF
    local = (
        struct.pack(
            "<IHHHHHIIIHH",
            0x04034B50,
            version_needed,
            flags,
            compression,
            0,
            0,
            crc,
            len(enc),
            len(data),
            len(name),
            len(extra),
        )
        + name
        + extra
        + enc
    )
    central = (
        struct.pack(
            "<IHHHHHHIIIHHHHHII",
            0x02014B50,
            made_by,
            version_needed,
            flags,
            compression,
            0,
            0,
            crc,
            len(enc),
            len(data),
            len(name),
            len(extra),
            0,
            0,
            0,
            external_attr,
            offset,
        )
        + name
        + extra
    )
    return local, central


def build_zipcrypto_zip(
    password: bytes,
    name: bytes,
    data: bytes,
    *,
    compression: int = zipfile.ZIP_DEFLATED,
    unix_mode: int | None = None,
    extra_flags: int = 0,
    extra: bytes = b"",
    compressed: bytes | None = None,
) -> bytes:
    """A single-entry ZIP whose one member is ZipCrypto-encrypted with ``password``.

    Supports the four compression methods decoded by stdlib ``zipfile``. No data
    descriptor is used, so the verification byte is the high byte of the payload CRC-32.
    ``unix_mode`` marks the entry as made on Unix with that mode (``0o120777`` for a
    symlink, whose data is then its target). ``extra_flags`` is OR-ed into the
    general-purpose flags and ``extra`` is written as the extra field of both headers.
    ``compressed`` supplies the member body already compressed with ``compression``,
    for a method this builder cannot compress itself (its ZIP header included, if the
    method has one).
    """
    made_by = 20 if unix_mode is None else (3 << 8) | 20
    external_attr = 0 if unix_mode is None else unix_mode << 16
    crc = zlib.crc32(data) & 0xFFFFFFFF
    flags = 0x1 | extra_flags  # bit 0: encrypted; no data descriptor
    if compressed is not None:
        stored = compressed
        version_needed = 63
    elif compression == zipfile.ZIP_STORED:
        stored = data
        version_needed = 20
    elif compression == zipfile.ZIP_DEFLATED:
        body = zlib.compressobj(9, zlib.DEFLATED, -15)
        stored = body.compress(data) + body.flush()
        version_needed = 20
    elif compression == zipfile.ZIP_BZIP2:
        stored = bz2.compress(data)
        version_needed = 46
    elif compression == zipfile.ZIP_LZMA:
        body = zipfile.LZMACompressor()
        stored = body.compress(data) + body.flush()
        version_needed = 63
        flags |= 0x2  # ZIP's LZMA end-of-stream marker flag
    else:
        raise ValueError(f"unsupported test compression method: {compression}")
    enc = _encrypt(password, (crc >> 24) & 0xFF, stored)
    # The encryption header is part of the compressed size, inside ``enc``.
    local, central = _member_bytes(
        name,
        data,
        enc,
        compression=compression,
        version_needed=version_needed,
        made_by=made_by,
        flags=flags,
        extra=extra,
        offset=0,
        external_attr=external_attr,
    )
    eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, len(central), len(local), 0)
    return local + central + eocd


def build_stored_zipcrypto_zip(
    members: Sequence[tuple[bytes, bytes, bytes]],
) -> bytes:
    """A STORED ZipCrypto ZIP with one member per ``(password, name, data)``.

    Same headers as :func:`build_zipcrypto_zip` for a single STORED member: no data
    descriptor, so the verification byte is the high byte of the CRC-32, and one
    central directory covers every member.
    """
    if not members:
        raise ValueError("need at least one member")
    locals_ = bytearray()
    centrals = bytearray()
    for password, name, data in members:
        enc = _encrypt(password, (zlib.crc32(data) >> 24) & 0xFF, data)
        local, central = _member_bytes(
            name,
            data,
            enc,
            compression=zipfile.ZIP_STORED,
            version_needed=20,
            made_by=20,
            flags=0x1,
            extra=b"",
            offset=len(locals_),
            external_attr=0,
        )
        locals_ += local
        centrals += central
    eocd = struct.pack(
        "<IHHHHIIH",
        0x06054B50,
        0,
        0,
        len(members),
        len(members),
        len(centrals),
        len(locals_),
        0,
    )
    return bytes(locals_ + centrals + eocd)


def zip_with_truncated_zipcrypto_header(
    password: bytes,
    name: bytes,
    data: bytes,
) -> bytes:
    """A ZipCrypto ZIP whose 12-byte encryption header cannot be read.

    stdlib ``ZipExtFile._init_decrypter`` does ``self._decrypter(header)[11]`` after
    ``read(12)``. This fixture inflates the local extra-field length so that skip
    lands at EOF, then adds a dummy central-directory entry with a huge
    ``header_offset`` so zipfile's overlap guard does not fire first. Opening the
    encrypted member then raises a bare ``IndexError`` — the Atheris nightly
    2026-09-01 finding (run 33505689273).
    """
    blob = bytearray(
        build_zipcrypto_zip(password, name, data, compression=zipfile.ZIP_STORED)
    )
    # Local extra-field length (LFH offset 28). 0xFFFF skips past the 12-byte header
    # and the rest of the file, whether zipfile uses read() (3.11) or seek() (3.12+).
    struct.pack_into("<H", blob, 28, 0xFFFF)
    eocd_at = blob.rfind(b"PK\x05\x06")
    if eocd_at < 0:
        raise ValueError("EOCD not found")
    dummy_name = b"pad.bin"
    dummy_cdh = (
        struct.pack(
            "<IHHHHHHIIIHHHHHII",
            0x02014B50,
            20,
            20,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            len(dummy_name),
            0,
            0,
            0,
            0,
            0,
            10_000_000,
        )
        + dummy_name
    )
    n_this, n_total, cd_size = struct.unpack_from("<HHI", blob, eocd_at + 8)
    struct.pack_into(
        "<HHI", blob, eocd_at + 8, n_this + 1, n_total + 1, cd_size + len(dummy_cdh)
    )
    return bytes(blob[:eocd_at] + dummy_cdh + blob[eocd_at:])


def corrupt_zipcrypto_payload(blob: bytes) -> bytes:
    """Flip encrypted member data after its intact 12-byte ZipCrypto header."""
    name_len, extra_len = struct.unpack_from("<HH", blob, 26)
    payload_start = 30 + name_len + extra_len + 12
    if payload_start >= len(blob):
        raise ValueError("ZIP member has no encrypted payload to corrupt")
    corrupt = bytearray(blob)
    corrupt[payload_start] ^= 0x80
    return bytes(corrupt)


def find_check_byte_collision(
    blob: bytes, name: str, right_password: bytes, *, search: int = 20000
) -> bytes:
    """A *wrong* password whose ZipCrypto verification byte matches ``blob``'s member.

    Such a password passes :meth:`zipfile.ZipFile.open` (the 1-byte check) but fails the
    CRC when the member is actually read — the false-accept the disambiguation guards
    against. Deterministic for a fixed ``blob`` (fixed search order). Raises if none is
    found within ``search`` attempts (astronomically unlikely: ~1/256 hit rate).
    """
    return find_check_byte_collisions(
        blob, name, right_password, count=1, search=search
    )[0]


def find_check_byte_collisions(
    blob: bytes,
    name: str,
    right_password: bytes,
    *,
    count: int,
    search: int = 20000,
) -> list[bytes]:
    """Return ``count`` distinct wrong passwords passing the one-byte open check."""
    import io

    collisions: list[bytes] = []
    for i in range(search):
        wrong = f"collide-{i}".encode()
        if wrong == right_password:
            continue
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            info = zf.getinfo(name)
            try:
                handle = zf.open(info, pwd=wrong)  # only the 1-byte check runs here
            except RuntimeError:
                continue  # verification byte mismatched: correctly rejected
            try:
                with handle:
                    handle.read()
            except (zipfile.BadZipFile, zlib.error, lzma.LZMAError):
                # Passed the 1-byte check but failed the real check: a corrupt
                # decompressor stream (compressed member) or a CRC mismatch (stored).
                collisions.append(wrong)
            except OSError as exc:
                # bz2 uniquely reports invalid compressed bytes as this message-specific
                # OSError. Never hide an unrelated source/filesystem OSError in a test.
                if str(exc) != "Invalid data stream":
                    raise
                collisions.append(wrong)
            if len(collisions) == count:
                return collisions
    raise AssertionError(
        f"found only {len(collisions)} of {count} verification-byte collisions "
        f"in {search} attempts (unlucky)"
    )
