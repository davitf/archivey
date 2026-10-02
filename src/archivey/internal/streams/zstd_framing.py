"""zstd frame framing for detection (RFC 8878 §3.1).

A **skippable frame** — a 4-byte magic in ``0x184D2A50 .. 0x184D2A5F`` followed by a
little-endian ``uint32`` payload size and that many bytes — carries no compressed data
and may legally precede the first regular frame (the *Seekable zstd* seek table is one
producer of them). Because the frame declares its own size, the next frame's offset is
exact arithmetic with nothing decoded.

Detection therefore walks a leading run of skippable frames and looks for the regular
frame behind it. Two properties keep that honest:

- **A regular frame is still required.** Registering the 16 skippable magics as ordinary
  magic-table entries would be less code and wrong: a source of nothing but skippable
  frames carries no payload, so reporting ``ZST`` would open it as one fabricated empty
  member. Identification stays tied to a frame that holds data.
- **The walk never extends the read.** It is arithmetic over the already-peeked prefix; a
  skippable frame whose declared size runs past those bytes ends the walk with no answer.
  A 4 GiB skippable frame is legal and is not worth a peek extension to see past.
"""

from __future__ import annotations

FRAME_MAGIC = b"\x28\xb5\x2f\xfd"

_SKIPPABLE_MAGIC_MIN = 0x184D2A50
_SKIPPABLE_MAGIC_MAX = 0x184D2A5F
# 4-byte magic + 4-byte little-endian payload size.
_SKIPPABLE_HEADER_SIZE = 8


def skippable_prefix_end(prefix: bytes) -> int | None:
    """Offset just past the leading run of skippable frames in ``prefix``.

    ``0`` when the prefix starts with something other than a skippable frame (including
    a regular frame, or too few bytes to tell). ``None`` when the walk cannot finish
    inside ``prefix`` — a declared payload size that runs past the peeked bytes — which
    is a declined answer, not an offset to keep walking from.
    """
    offset = 0
    while True:
        header = prefix[offset : offset + _SKIPPABLE_HEADER_SIZE]
        if len(header) < _SKIPPABLE_HEADER_SIZE:
            return offset
        magic = int.from_bytes(header[:4], "little")
        if not _SKIPPABLE_MAGIC_MIN <= magic <= _SKIPPABLE_MAGIC_MAX:
            return offset
        offset += _SKIPPABLE_HEADER_SIZE + int.from_bytes(header[4:], "little")
        if offset > len(prefix):
            return None


def regular_frame_behind_skippable_frames(prefix: bytes) -> bool:
    """Whether a regular zstd frame follows a leading run of skippable frames.

    ``False`` for a regular frame at offset 0 — that is an ordinary magic match, which
    the magic table already answers — and for skippable frames with nothing behind them.
    """
    offset = skippable_prefix_end(prefix)
    if offset is None or offset == 0:
        return False
    return prefix[offset : offset + len(FRAME_MAGIC)] == FRAME_MAGIC


# Magic, frame header descriptor, window descriptor, 4-byte dictionary id, 8-byte
# frame content size: the longest a frame header can be (RFC 8878 §3.1.1.1).
MAX_FRAME_HEADER_SIZE = 18


def frame_window_size(header: bytes) -> int | None:
    """The window a regular zstd frame at offset 0 of ``header`` declares.

    RFC 8878 §3.1.1.1: the window descriptor gives it, or, for a single-segment frame,
    the frame content size does. ``None`` when ``header`` does not start with a regular
    frame whose header it holds in full, or when the descriptor's reserved bit is set.
    """
    if header[: len(FRAME_MAGIC)] != FRAME_MAGIC or len(header) < 5:
        return None
    descriptor = header[4]
    if descriptor & 0x08:
        return None
    single_segment = bool(descriptor & 0x20)
    if not single_segment:
        if len(header) < 6:
            return None
        exponent, mantissa = header[5] >> 3, header[5] & 0x07
        base = 1 << (10 + exponent)
        return base + (base >> 3) * mantissa
    offset = 5 + (0, 1, 2, 4)[descriptor & 0x03]
    size_bytes = (1, 2, 4, 8)[descriptor >> 6]
    field = header[offset : offset + size_bytes]
    if len(field) < size_bytes:
        return None
    content_size = int.from_bytes(field, "little")
    return content_size + 256 if size_bytes == 2 else content_size
