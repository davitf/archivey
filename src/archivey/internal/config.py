"""Internal views of :class:`ArchiveyConfig` and the limits it carries.

:class:`StreamConfig` is the stream layer's view of the config. The decoder-memory
checks and :class:`KeyDerivationBudget` enforce :class:`~archivey.config.DecoderLimits`
for the backends.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from archivey.config import (
    DEFAULT_ARCHIVEY_CONFIG,
    AcceleratorMode,
    ArchiveyConfig,
    DecoderLimits,
)
from archivey.exceptions import ResourceLimitError

# Only the names other modules import from here. ``ArchiveyConfig`` and
# ``DEFAULT_ARCHIVEY_CONFIG`` are imported for use below, not re-exported —
# callers take those from ``archivey.config``.
__all__ = [
    "AcceleratorMode",
    "DEFAULT_STREAM_CONFIG",
    "DecoderLimits",
    "KeyDerivationBudget",
    "StreamConfig",
    "check_decoder_memory",
    "exceeds_decoder_memory",
    "stream_config_from_archivey",
]


@dataclass(frozen=True)
class StreamConfig:
    """Options that influence how compressed streams are opened.

    ``seekable`` is declared seek demand (``MemberStreams.SEEKABLE``): accelerator
    ``AUTO`` resolution and index construction key off it. ``streaming`` remains the
    archive access mode for backends that still need to know forward-only vs random.
    ``compressed_input_size`` is the known compressed byte length of the source (path
    size, slice length, …), used by ``use_rapidgzip`` AUTO's minimum-size gate; ``None``
    means unknown (AUTO keeps pre-threshold behaviour when truncation is also
    verifiable). ``expected_decompressed_size`` is a container-declared uncompressed
    length (ZIP central-dir size, …) used both to gate rapidgzip AUTO and to wrap the
    accelerator in a length-verifying stream. ``gzip_isize_backstop`` is set when a
    seekable gzip source has a readable ISIZE trailer — enough for AUTO to select
    rapidgzip with the ISIZE truncation check, but *not* a hard ``VerifyingStream``
    bound (ISIZE is mod 2**32 and multi-member trailers only cover the last member).
    ``decoder_limits`` is the caller's :class:`~archivey.config.DecoderLimits`,
    carried down so a codec can refuse an archive-declared allocation before making
    it; it defaults to the public default rather than to "unlimited", because a
    :class:`StreamConfig` built directly (detection, tests) is still decoding a file
    someone else wrote. ``on_gzip_sole_member_end`` is called when the stdlib gzip
    decoder reaches a clean end of input that held exactly one member and nothing
    after it, so the source's last 8 bytes are that member's trailer (see
    :class:`~archivey.internal.streams.decompress.GzipDecoder`). The rapidgzip path
    does not call it: it hides member boundaries.
    """

    streaming: bool = False
    seekable: bool = False
    use_rapidgzip: AcceleratorMode = AcceleratorMode.AUTO
    use_indexed_bzip2: AcceleratorMode = AcceleratorMode.AUTO
    compressed_input_size: int | None = None
    expected_decompressed_size: int | None = None
    gzip_isize_backstop: bool = False
    decoder_limits: DecoderLimits = DecoderLimits()
    on_gzip_sole_member_end: Callable[[], None] | None = field(
        default=None, compare=False
    )


def stream_config_from_archivey(
    config: ArchiveyConfig,
    *,
    streaming: bool,
    seekable: bool = False,
) -> StreamConfig:
    """Derive the codec-layer view from the public config and declared seek demand."""
    return StreamConfig(
        streaming=streaming,
        seekable=seekable,
        use_rapidgzip=config.use_rapidgzip,
        use_indexed_bzip2=config.use_indexed_bzip2,
        decoder_limits=config.decoder_limits,
    )


DEFAULT_STREAM_CONFIG = stream_config_from_archivey(
    DEFAULT_ARCHIVEY_CONFIG, streaming=False, seekable=False
)


def exceeds_decoder_memory(declared: int, limits: DecoderLimits) -> bool:
    """Whether an archive-declared allocation is over ``max_decoder_memory``.

    The one statement of the boundary: :func:`check_decoder_memory` raises on it, and
    a caller that has to decide without raising yet (the ``.lzma`` codec) branches on
    it, so the two cannot drift apart.
    """
    cap = limits.max_decoder_memory
    return cap is not None and declared > cap


def check_decoder_memory(declared: int, *, limits: DecoderLimits, what: str) -> None:
    """Refuse an archive-declared decoder allocation above ``max_decoder_memory``.

    ``declared`` is the number the *archive* asked for, read out of a header field —
    not a measurement of anything, and not bounded by the file's own size. ``what``
    names the field for the message, so a caller who raised the cap on purpose can
    tell which archive is asking and for how much.

    Callers run this before the decoder object is constructed, because the
    allocation it guards is made inside a C extension, and what a refused native
    allocation does differs by extension. pyppmd 1.3.1's failure path is unsound —
    measured, ``Ppmd7Decoder(6, 0xFFFFFFFF)`` under a 2 GiB ``RLIMIT_AS`` aborts on
    ``double free or corruption`` with SIGABRT, leaving nothing to catch. liblzma
    does return ``MemoryError`` when the reservation itself fails, but a reservation
    that succeeds is the worse case there: the dictionary is touched as output is
    written, so a 151 KB stream declaring 4 GiB holds 1.1 GiB resident after
    producing 1 GiB of zeros, where the same stream declaring 1 MiB holds 59 MB.

    Lives here rather than in ``codecs.py`` so the xz and lzip decoders, which
    ``codecs.py`` imports, can call it without an import cycle.
    """
    if exceeds_decoder_memory(declared, limits):
        raise ResourceLimitError(
            f"Decoder limit reached: max_decoder_memory={limits.max_decoder_memory} "
            f"({what} declares {declared} bytes). The archive chose this number; "
            f"raise DecoderLimits.max_decoder_memory if the archive is trusted."
        )


class KeyDerivationBudget:
    """The rounds of archive-declared key derivation one open archive may still run.

    One per reader, built from its :class:`~archivey.config.DecoderLimits` and shared
    by every key cache the reader holds. A cache calls :meth:`spend` on a miss,
    immediately before the derivation, so a hit costs nothing and the refusal lands
    before the work starts, which is the only place it helps: the derivation runs in
    ``hashlib`` with the GIL released and cannot be interrupted.

    ``spend`` is locked so two threads opening members at once cannot both pass the
    check on the same remaining budget. The derivation itself runs outside the lock.
    """

    __slots__ = ("_cap", "_lock", "_spent")

    def __init__(self, limits: DecoderLimits = DecoderLimits()) -> None:
        self._cap = limits.max_key_derivation_rounds
        self._spent = 0
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"<KeyDerivationBudget: {self._spent} of {self._cap} rounds spent>"

    def spend(self, rounds: int, *, what: str) -> None:
        """Charge ``rounds`` to the budget, or raise before the derivation runs.

        ``what`` names the scheme for the message, for example
        ``"RAR5 key derivation"``.
        """
        with self._lock:
            total = self._spent + rounds
            if self._cap is not None and total > self._cap:
                raise ResourceLimitError(
                    f"Decoder limit reached: max_key_derivation_rounds={self._cap} "
                    f"({what} at {rounds} rounds would bring this archive's total "
                    f"to {total}). The archive chose the cost and the number of "
                    f"keys; raise DecoderLimits.max_key_derivation_rounds if the "
                    f"archive is trusted."
                )
            self._spent = total
