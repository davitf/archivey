"""Internal hashing helpers (not part of the public API)."""

from archivey.internal.hashing.combine import crc32_combine


def crc32_digest(value: int) -> bytes:
    """Encode a CRC-32 as four big-endian bytes for :attr:`~archivey.ArchiveMember.hashes`."""
    return (value & 0xFFFFFFFF).to_bytes(4, "big")


__all__ = ["crc32_combine", "crc32_digest"]
