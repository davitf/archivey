"""The PPMd decoder (pyppmd), in process or in a child process."""

from __future__ import annotations

import os
from typing import BinaryIO, NoReturn, Protocol

from archivey.config import DecoderLimits
from archivey.exceptions import ResourceLimitError, TruncatedError
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.streams.codecs.ppmd_child import (
    PpmdChildAllocationError,
    PpmdChildDecoder,
    PpmdChildStartError,
    child_decoding_available,
)
from archivey.internal.streams.decompressor_stream import (
    BaseDecoder,
    DecodeOut,
    DecompressorStream,
    SeekPoint,
)

# Per-call output request for PPMd8 decodes without a declared size; 64 KiB matches
# the stream layer's read chunk. NOTE: a bound is NOT what makes decoding safe — on
# pyppmd 1.3.x any request exceeding the stream's true remaining output by ≳64 KiB
# corrupts the heap even without -1 (measured: +64/+4096 over → 0/20 crashes,
# +65536 over → 13/20). PPMd8 is safe here because its end mark stops the native
# worker on valid data before any over-decode; PPMd7 has no end mark, which is why
# PpmdDecoder refuses to decode it without ``unpack_size`` at all.
_PPMD_UNSIZED_DECODE_CHUNK = 65536

# Per-call ceiling for extra-NUL recovery and post-NUL empty drains. A single
# ``decode(b"\0", large_remaining)`` on truncated mid-stream input is the
# exit-after-green / mid-suite abort on pyppmd 1.3.x; chunking at 64 avoids that
# call shape. When the container pack is known-complete, empty ``decode(b"", 64)``
# drains may still finish a large tail (including past premature ``eof``); when the
# pack is known-incomplete, we refuse those post-eof drains (near-EOF MemoryError
# otherwise). See ``dev-docs/investigations/ppmd-exit-after-green-exploration.md``.
_PPMD_EXTRA_NUL_MAX_OUTPUT = 64

# Bounded number of single-symbol NUL decodes used to quiesce a parked native
# worker before the decoder is freed (see ``PpmdDecoder._quiesce_worker``). A
# parked worker only needs the range coder's few-byte tail lookahead satisfied to
# reach its 1-symbol budget and exit; 8 is a safe cushion over the observed need.
_PPMD_QUIESCE_MAX_CALLS = 8

# pyppmd parses ``decode``'s ``length`` as a C ``int``; anything larger raises a bare
# ``OverflowError``. A request is capped here and the rest is asked for on the next
# call. The oversized request comes from ``feed(chunk, -1)`` — what ``readall()`` /
# ``read(-1)`` sends — whose limit is the whole remaining ``unpack_size``, and from
# one large ``read(n)``; either reaches it on a member over 2 GiB, or on a header
# that overstates ``unpack_size`` past that.
_PPMD_MAX_REQUEST = (1 << 31) - 1

# How PPMd avoids pyppmd's crash on corrupt input: pyppmd segfaults when asked to
# decode after a corrupt stream has ended early, and a caller feeding it in pieces
# cannot tell that state from a valid stream waiting for input (see ``ppmd_child``).
# Handed the whole member at once, any short return is the end, and nothing further
# is asked of it. ``PpmdDecoder`` therefore holds compressed input until it has the
# whole member, or compressed EOF, or ``DecoderLimits.max_ppmd_in_process_input``;
# past that the member goes to a child process, where a crash costs the member only.

_DEFAULT_IN_PROCESS_MAX_INPUT = DecoderLimits().max_ppmd_in_process_input

# Output asked of pyppmd per call once a held member is handed over at compressed EOF
# (a truncated member, or a password check's capped input): the stream drains the rest
# through further calls rather than taking it all from ``flush``.
_PPMD_FLUSH_DRAIN_CHUNK = 65536


class _PpmdNativeDecoder(Protocol):
    """The ``pyppmd.Ppmd7Decoder`` / ``Ppmd8Decoder`` methods this adapter calls.

    ``length`` is the library's keyword. ``needs_input`` is read via ``getattr``
    as defensive coverage; the pinned floor (``pyppmd>=1.3.1``) exposes it on
    both decoders, and no known build lacks it.
    """

    def decode(self, data: bytearray | bytes | memoryview, length: int) -> bytes: ...

    @property
    def eof(self) -> bool: ...


class PpmdDecoder(BaseDecoder):
    """Decode a PPMd stream via ``pyppmd``.

    Variant 7 (``Ppmd7Decoder``) is the 7z var.H coder. Variant 8 (``Ppmd8Decoder``)
    is ZIP method 98 / WinZip ZIPX PPMd, which also carries a restore-method parameter.

    ``unpack_size`` (the 7z folder / ZIP member size) is passed through as
    ``max_length`` on every ``decode`` call, matching py7zr's
    ``PpmdDecompressor.decompress(..., max_length)``. This is load-bearing on pyppmd
    1.3.x: the native worker thread decodes as many symbols as the request allows, and
    running it materially past the true end of stream corrupts the heap (Linux
    ``malloc`` abort/SIGSEGV, Windows ``STATUS_HEAP_CORRUPTION``) — measured for
    ``-1`` and for sized requests ≳64 KiB beyond the real payload alike. Requesting
    exactly the remaining output is the safe contract; see
    ``dev-docs/known-issues.md`` and ``dev-docs/investigations/pyppmd-upstream-report.md``.

    Because PPMd7 has no end mark, there is no safe request size without knowing the
    payload length — so ``unpack_size`` is **required** for variant 7 (the 7z header
    always provides it). PPMd8 carries an end mark that stops the native worker on
    valid data, so variant 8 may be unsized; it is then decoded via bounded
    :data:`_PPMD_UNSIZED_DECODE_CHUNK` requests in a drain loop, never ``-1``.

    ``pack_size`` is the container-declared compressed length (7z pack stream / ZIP
    compressed size, or the sized view length). When set, empty post-``eof`` drains
    that chase ``unpack_size`` are allowed only after ``fed_compressed >= pack_size``;
    a short pack delivery stops instead (avoids near-EOF ``MemoryError`` on
    truncated members). It is **required for PPMd7** (see ``__init__``): without it a
    premature native ``eof`` cannot be told from truncation, and the only safe choice
    left is to refuse the drain, which silently truncates valid members on chunked
    reads. ``PpmdCodec`` fills it from the sized source (``compressed_input_size``) or,
    for chained 7z folders whose PPMd input is unsized (AES), from the plumbed coder
    input size. PPMd8 may leave it unknown; recovery is then conservative — at most one
    capped NUL and **no** chunked empty drains. At compressed EOF, at most one
    documented extra NUL is injected with a per-call budget of
    :data:`_PPMD_EXTRA_NUL_MAX_OUTPUT`; unsized PPMd8 gets **no** post-eof drain at all
    (its end mark terminates valid decodes; a drain would only fabricate trailing bytes).

    **Invariant:** ``pack_size`` must measure the same byte stream that
    ``feed()`` accumulates into ``_fed_compressed`` (the PPMd coder's compressed
    input — after ZIP method-98 header stripping / as the 7z pack ``SlicingStream``
    length). If a wrapper sets ``compressed_input_size`` to a larger enclosing
    member while feeding only a subset, the gate would wrongly treat a complete
    pack as short and suppress legitimate post-eof drains.
    """

    def __init__(
        self,
        *,
        order: int,
        mem_size: int,
        variant: int = 7,
        restore_method: int = 0,
        unpack_size: int | None = None,
        pack_size: int | None = None,
        in_process_max_input: int | None = _DEFAULT_IN_PROCESS_MAX_INPUT,
    ) -> None:
        if variant != 8 and unpack_size is None:
            raise ValueError(
                "PPMd7 (7z var.H) requires unpack_size: the format has no end mark, "
                "and decoding without the exact output bound runs pyppmd past the "
                "end of stream (native heap corruption on 1.3.x — see "
                "dev-docs/known-issues.md)"
            )
        if variant != 8 and pack_size is None:
            # Without pack_size, a premature native ``eof`` (pyppmd flips it early on a
            # small ``max_length`` over compressible data) is indistinguishable from
            # truncation: draining toward unpack_size to finish the tail can MemoryError
            # on 1.3.x, so the decoder must refuse it — which silently truncates a valid
            # member on chunked reads. PPMd7 is 7z-only and 7z always knows the pack
            # length (sized pack slice, or the preceding coder's output for AES folders),
            # so require it rather than choose between truncation and a crash.
            raise ValueError(
                "PPMd7 (7z var.H) requires pack_size: it has no end mark, so completing "
                "a member past a premature native eof needs the declared compressed "
                "length to tell full delivery from truncation (see "
                "dev-docs/known-issues.md)"
            )
        self._order = order
        self._mem_size = mem_size
        self._variant = variant
        self._restore_method = restore_method
        self._unpack_size = unpack_size
        self._pack_size = pack_size
        self._produced = 0
        self._fed_compressed = 0
        self._nul_injected = False
        self._compressed_eof = False
        # Set once the payload is provably spent (see ``_note_decoded``); from then on
        # nothing on the decode path calls the native decoder. Teardown still does:
        # ``_quiesce_worker`` sends its bounded NUL in exactly this state.
        self._exhausted = False
        # ``DecoderLimits.max_ppmd_in_process_input``: past it, a child process, or
        # ``ResourceLimitError`` where none can start. ``None``: always in-process.
        self._in_process_max_input = in_process_max_input
        # Compressed input held back from pyppmd until the whole member is here, or
        # compressed EOF, or ``in_process_max_input`` is passed (then it goes to a
        # child process). ``None`` once handed over: from then on ``_decomp`` exists
        # and the rest of this class works as a plain chunked decoder.
        self._held: bytearray | None = bytearray()
        # Set by ``flush`` when it hands the held input over: the stream keeps pulling
        # output through ``feed(b"")`` until this decoder has none left
        # (``drains_after_flush``), then calls ``flush`` again.
        self._draining = False
        # Set when a member past ``in_process_max_input`` was refused: every later
        # ``feed`` and ``flush`` raises ``ResourceLimitError`` again, since there is
        # no decoder to go on with.
        self._refusal: str | None = None
        self._decomp: _PpmdNativeDecoder | None = None
        # ``_check_end_mark`` has run; ``_end_probe_parked``: its call left the
        # worker waiting for input, so ``_quiesce_worker`` still has to run.
        self._end_checked = False
        self._end_probe_parked = False

    def _open_native(self, *, in_child: bool) -> None:
        """Create the decoder ``_decomp``, in this process or in a child one.

        ``mem_size`` is bounded one layer up, by ``check_decoder_memory`` in
        ``codecs/ppmd_codec.py``, against ``DecoderLimits.max_decoder_memory`` — not here,
        because these constructor calls are the allocation and there is no catching
        it once it has been made (pyppmd 1.3.1 aborts the process rather than raising
        when it is refused). Two paths reach this class without passing that guard: a
        direct ``PpmdDecompressorStream`` in the tests, and ``recreate()`` rebuilding
        from a ``mem_size`` the guard already passed. Anything new that constructs a
        decoder from an archive-declared number calls the guard first.
        """
        if self._decomp is not None:  # a test double installed before first use
            return
        if in_child:
            self._decomp = PpmdChildDecoder(
                variant=self._variant,
                order=self._order,
                mem_size=self._mem_size,
                restore_method=self._restore_method,
            )
            return
        import pyppmd

        if self._variant == 8:
            self._decomp = pyppmd.Ppmd8Decoder(
                self._order, self._mem_size, self._restore_method
            )
        else:
            self._decomp = pyppmd.Ppmd7Decoder(self._order, self._mem_size)

    def _release_held(self, *, in_child: bool) -> bytes:
        """Hand the held input over: open ``_decomp`` and return the bytes to feed it."""
        held = self._held
        assert held is not None
        self._held = None
        self._open_native(in_child=in_child)
        return bytes(held)

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> PpmdDecoder:
        del point, inner
        return PpmdDecoder(
            order=self._order,
            mem_size=self._mem_size,
            variant=self._variant,
            restore_method=self._restore_method,
            unpack_size=self._unpack_size,
            pack_size=self._pack_size,
            in_process_max_input=self._in_process_max_input,
        )

    @property
    def _native(self) -> _PpmdNativeDecoder:
        """The decoder, once the held input has been handed over (never before)."""
        assert self._decomp is not None
        return self._decomp

    def _max_length(self) -> int:
        if self._unpack_size is None:
            return -1
        return max(0, self._unpack_size - self._produced)

    def _pack_complete(self) -> bool | None:
        """True if declared pack fully fed, False if known short, None if unknown."""
        if self._pack_size is None:
            return None
        return self._fed_compressed >= self._pack_size

    def _note_decoded(self, out: bytes, requested: int) -> bytes:
        """Record whether a sized native call proved the payload spent; return ``out``.

        On pyppmd 1.3.1 a ``decode`` returns short of ``requested`` only when its
        worker blocked on empty input or decoded the model's end. Short **and** at
        ``eof`` **and** with every compressed byte already fed, no more input is
        coming and the payload has nothing left: another decode-path call could only
        resume a worker parked on empty input, which raises a spurious
        ``MemoryError`` (it reads past the input buffer) rather than returning. So
        ``feed`` and ``flush`` make no further call; ``_quiesce_worker`` still sends
        its bounded NUL at teardown, which is what keeps the parked worker from
        writing into freed memory when the decoder is freed. A valid member never gets
        here, because its ``unpack_size`` matches the payload and every request is
        met in full — premature ``eof`` (the ``Code == 0`` proxy) arrives on a *full*
        return, which is why ``eof`` alone cannot be the stop. Reached when the
        container overstates ``unpack_size``; ``flush`` then reports
        ``TruncatedError``.

        ``_decode_unsized`` (PPMd8 without ``unpack_size``) is deliberately not
        wrapped: its end mark stops the worker on valid data, ``_decode`` refuses to
        call it again once ``eof`` is set, and ``flush`` runs no post-eof drain without
        a size, so it never reaches the parked-worker resume this guards against.
        """
        if (
            len(out) < requested
            and self._native.eof
            and (self._compressed_eof or self._pack_complete() is True)
        ):
            self._exhausted = True
        return out

    def _decode_unsized(self, data: bytes) -> bytes:
        # Unsized PPMd8 only (PPMd7 without a size is rejected in __init__). Never
        # hand pyppmd max_length=-1; request one bounded chunk per call. Valid PPMd8
        # stops at its end mark before any over-decode; the bound avoids the -1
        # allocation path and caps the damage on corrupt data. One chunk, not a
        # drain loop: a held member arrives whole, and draining it here would build
        # its entire output in one call. The stream asks again while ``needs_input``
        # is False.
        return self._native.decode(data, _PPMD_UNSIZED_DECODE_CHUNK)

    def _nul_budget(self, max_length: int) -> int:
        budget = _PPMD_EXTRA_NUL_MAX_OUTPUT
        if max_length >= 0:
            budget = min(max_length, budget)
        return budget

    def _inject_nul_once(self, max_length: int) -> bytes:
        """Documented single extra NUL (pyppmd / py7zr); never loop fabricated input."""
        if self._exhausted or self._nul_injected or self._native.eof:
            return b""
        if not getattr(self._native, "needs_input", False):
            return b""
        self._nul_injected = True
        budget = self._nul_budget(max_length)
        return self._note_decoded(self._native.decode(b"\0", budget), budget)

    def _drain_empty_chunked(self, max_length: int) -> bytes:
        """Pull remaining output in ``_PPMD_EXTRA_NUL_MAX_OUTPUT`` empty decodes.

        Only for a **known-complete** pack (see :meth:`flush`): premature native
        ``eof`` after a small ``max_length`` can still leave legitimate symbols
        reachable via ``decode(b"", …)`` — so this intentionally continues past
        native ``eof`` (breaking on eof would defeat premature-eof recovery).
        Stops on ``needs_input``, a short return at ``eof`` (the payload is spent;
        see :meth:`_note_decoded`), quiet empty, or budget exhaustion. Does **not**
        run when ``pack_size`` is unknown or short. Corrupt-but-declared-complete
        packs can still fill toward ``unpack_size`` here; container CRC is the
        backstop. Worst-case iteration count is ``remaining + 2`` at 64 bytes
        per call (perf cliff on huge members if this path is hit).
        """
        if max_length == 0:
            return b""
        parts: list[bytes] = []
        remaining = max_length
        quiet = 0
        # Bound iterations: worst case one byte per call up to remaining.
        for _ in range(remaining + 2):
            if remaining <= 0:
                break
            if getattr(self._native, "needs_input", False):
                break
            budget = min(_PPMD_EXTRA_NUL_MAX_OUTPUT, remaining)
            chunk = self._note_decoded(self._native.decode(b"", budget), budget)
            parts.append(chunk)
            remaining -= len(chunk)
            if self._exhausted:
                break
            if not chunk:
                quiet += 1
                if quiet >= 2:
                    break
                continue
            quiet = 0
        return b"".join(parts)

    def _decode(self, data: bytes, max_length: int) -> bytes:
        if max_length == 0 or self._exhausted:
            return b""
        # Decoding after native EOF is trailing garbage at best (and the crashy
        # runaway path on pyppmd 1.3.x when unbounded) — drop the input instead.
        if self._native.eof and max_length < 0:
            return b""
        # Empty + needs_input after compressed EOF: at most one documented NUL.
        # Before compressed EOF, empty+needs_input means "read more pack bytes" —
        # do not fabricate input.
        if (
            not data
            and getattr(self._native, "needs_input", False)
            and not self._native.eof
        ):
            if not self._compressed_eof:
                return b""
            return self._inject_nul_once(max_length)
        if max_length < 0:
            return self._decode_unsized(data)
        max_length = min(max_length, _PPMD_MAX_REQUEST)
        return self._note_decoded(self._native.decode(data, max_length), max_length)

    def _refuse(self, reason: str, cause: BaseException | None = None) -> NoReturn:
        """Refuse this member for good; see ``_refusal``."""
        self._refusal = reason
        self._held = None
        raise ResourceLimitError(reason) from cause

    def _check_refusal(self) -> None:
        if self._refusal is not None:
            raise ResourceLimitError(self._refusal)

    def _release_to_child(self, limit: int) -> bytes:
        reason = (
            f"Decoder limit reached: max_ppmd_in_process_input={limit}. This PPMd "
            "member is larger, and no child process can be started to decode it "
            "(pyppmd can crash the process on corrupt input past this size). Raise "
            "DecoderLimits.max_ppmd_in_process_input to decode it in-process."
        )
        if not child_decoding_available():
            self._refuse(reason)
        try:
            return self._release_held(in_child=True)
        except PpmdChildStartError as exc:
            self._refuse(f"{reason} ({exc})", exc)
        except PpmdChildAllocationError as exc:
            # The child started, so the advice above does not apply: in-process, the
            # same allocation would abort this process.
            self._refuse(f"Decoder limit reached: {exc}", exc)

    def feed(self, chunk: bytes, max_length: int = -1) -> DecodeOut:
        self._check_refusal()
        if chunk:
            self._fed_compressed += len(chunk)
        if self._held is not None:
            self._held += chunk
            limit = self._in_process_max_input
            # The limit is checked first: a member past it goes to a child even when
            # its whole pack arrived in this one feed. How much one feed carries is up
            # to the caller's read size, which must not decide where a member decodes.
            if limit is not None and len(self._held) > limit:
                chunk = self._release_to_child(limit)
            elif self._pack_complete() is True:
                chunk = self._release_held(in_child=False)
            else:
                return DecodeOut(b"")
        # Honour both the container unpack_size cap and the stream-layer read budget.
        unpack_cap = self._max_length()
        if max_length >= 0 and unpack_cap >= 0:
            limit = min(max_length, unpack_cap)
        elif max_length >= 0:
            limit = max_length
        else:
            limit = unpack_cap
        if self._compressed_eof:
            # After compressed EOF (``flush`` has handed the held input over): the same
            # bounded request as its first call, never the whole declared remainder,
            # whether or not the drain is still running.
            limit = (
                _PPMD_FLUSH_DRAIN_CHUNK
                if limit < 0
                else min(limit, _PPMD_FLUSH_DRAIN_CHUNK)
            )
        # Empty drains past native eof are only safe when every compressed byte the
        # decoder will ever get has been handed over: the declared pack was fully
        # delivered, or ``flush`` handed over everything up to compressed EOF (and
        # then a short return ends the drain, below). Otherwise refuse (near-EOF
        # MemoryError / garbage fill). Callers must pass pack_size (or sized-view
        # compressed_input_size) for correct premature-eof completion.
        if (
            not chunk
            and not (self._draining or self._pack_complete() is True)
            and self._native.eof
        ):
            return DecodeOut(b"")
        out = self._decode(chunk, limit)
        self._produced += len(out)
        if self._draining and len(out) < limit:
            # All input is in, so a short return is the end: ask pyppmd nothing more
            # on the drain; the stream's next ``flush`` settles the member.
            self._draining = False
        return DecodeOut(out)

    def flush(self) -> DecodeOut:
        self._check_refusal()
        self._compressed_eof = True
        if self._held is not None:
            # Compressed EOF with input still held: a member shorter than its pack
            # (truncated, or a password check's capped read), or one with no declared
            # pack size. Everything that will ever arrive is here, so hand it over
            # whole, in this process, and take the first chunk of output; the stream
            # drains the rest through ``feed(b"")`` and then calls ``flush`` again.
            data = self._release_held(in_child=False)
            limit = self._max_length()
            limit = (
                _PPMD_FLUSH_DRAIN_CHUNK
                if limit < 0
                else min(limit, _PPMD_FLUSH_DRAIN_CHUNK)
            )
            out = self._decode(data, limit) if limit else b""
            self._produced += len(out)
            # Only a full return leaves output to drain: short, the member has ended
            # and pyppmd is asked nothing more (see ``feed``).
            if len(out) == limit and not self.finished and not self._exhausted:
                self._draining = True
                return DecodeOut(out)
            return DecodeOut(out + self._finish_input().data)
        return self._finish_input()

    @property
    def drains_after_flush(self) -> bool:
        """True while ``flush`` has handed input over and output may still follow."""
        return self._draining and not self.finished and not self._exhausted

    def _finish_input(self) -> DecodeOut:
        # Compressed EOF: optionally one documented extra NUL, then (only when the
        # pack is known-complete) chunked empty drains. Never inject fabricated
        # NULs in a loop. Unknown pack_size is treated like incomplete for drains:
        # single capped NUL only — do not chase unpack_size.
        self._draining = False
        max_length = self._max_length()
        if max_length == 0:
            self._check_end_mark()
            return DecodeOut(b"")
        out = b""
        if not self._native.eof and getattr(self._native, "needs_input", False):
            more = self._inject_nul_once(max_length)
            out += more
            self._produced += len(more)
            max_length = self._max_length()
        # Empty drains past a premature native ``eof`` only run when the pack is
        # known-complete AND a container ``unpack_size`` bounds them (``max_length >= 0``).
        # Without that bound the drain is pure fabrication: an unsized PPMd8 stream ends
        # at its end mark, so any post-eof pull is trailing garbage (measured: +N bytes
        # on compressible payloads). Corrupt-but-declared-complete sized packs can still
        # fill toward ``unpack_size`` here — container CRC is the backstop.
        if max_length > 0 and self._pack_complete() is True and not self._exhausted:
            if not getattr(self._native, "needs_input", False):
                drained = self._drain_empty_chunked(max_length)
                out += drained
                self._produced += len(drained)
        if not self.finished:
            self._pending_error = TruncatedError("File is truncated")
        return DecodeOut(out)

    def _check_end_mark(self) -> None:
        """At the declared size, look for a PPMd8 end mark and input left after it.

        Called once, at compressed EOF, when the output has reached ``unpack_size``.
        7-Zip writes a ZIP PPMd8 member with an end mark and reports a member with
        input after it as "Data Error". pyppmd decodes the end mark only when asked
        for more output than the data holds, and only then does ``unused_data`` show
        the input after it (pyppmd 1.3.1, measured on a ``7z a -tzip -mm=PPMd``
        member). So one more symbol is asked for. At ``eof`` with an empty return,
        the end mark is there, and any ``unused_data`` is input after the end
        (:attr:`input_after_end`). Anything else means no end mark at the size: a
        stream written without one, which pyppmd cannot tell from input past the
        size, so it reads clean, as a marker-less LZMA1 stream does. The extra symbol
        is dropped.

        Asked only of a worker that stopped on its output budget with input left
        (``needs_input`` False): with all input consumed, nothing can follow the
        stream, and resuming a worker parked on empty input is the crash path
        ``_note_decoded`` describes. One symbol is far inside the over-decode
        pyppmd survives (``_PPMD_UNSIZED_DECODE_CHUNK``). PPMd7 (7z) has no end mark
        and is not asked.
        """
        if (
            self._end_checked
            or self._variant != 8
            or self._unpack_size is None
            or self._exhausted
            or self._decomp is None
        ):
            return
        self._end_checked = True
        native = self._native
        if native.eof or getattr(native, "needs_input", True):
            return
        extra = native.decode(b"", 1)
        if extra or not native.eof:
            # No end mark here. A worker that returned nothing is waiting for input.
            self._end_probe_parked = not extra and not native.eof
            return
        if isinstance(native, PpmdChildDecoder):
            unused = native.unused_size
        else:
            unused = len(getattr(native, "unused_data", b"") or b"")
        if unused:
            self._input_after_end = True

    @property
    def finished(self) -> bool:
        # Prefer the container size when known: it detects truncation (short output
        # at compressed EOF) and ends the member exactly at its boundary — PPMd7 has
        # no end mark, so native eof alone cannot do either.
        if self._unpack_size is not None:
            return self._produced >= self._unpack_size
        return self._decomp is not None and bool(self._decomp.eof)

    @property
    def needs_input(self) -> bool:
        if self._decomp is None:
            return True
        return bool(getattr(self._decomp, "needs_input", True))

    def _quiesce_worker(self) -> None:
        """Drive a parked native decode worker to exit before the decoder is freed.

        On pyppmd 1.3.x the decode worker thread is left blocked in the reader
        whenever a ``decode`` requested more output than the fed input could
        produce (a truncated or abandoned member). ``Ppmd7T_Free`` — run from the
        pyppmd decoder's ``dealloc`` — then wakes that worker with no new input,
        and it free-runs into the output block already released by the previous
        ``decode`` call: a use-after-free that intermittently aborts the process
        at GC (the "exit-after-green" residual in ``known-issues.md``).

        Feeding one bounded single-symbol NUL makes the worker consume its tail
        lookahead and exit on its own 1-symbol budget, so ``Ppmd7T_Free`` sees a
        finished worker and becomes a no-op. Measured: valgrind 8154 → 0 invalid
        writes on a truncated-pack teardown. Best-effort and idempotent; safe to
        call more than once. See ``dev-docs/investigations/ppmd-native-investigation-results.md``
        (§D root cause, §I mitigation).
        """
        decomp = getattr(self, "_decomp", None)
        if decomp is None:
            return
        try:
            # A fully-decoded member exited its worker on budget / the end mark;
            # only an incomplete (truncated / abandoned) decode can leave one
            # parked. Skip the happy path so valid closes cost nothing. The end-mark
            # probe (``_check_end_mark``) can park one after the member finished.
            if self.finished and not self._end_probe_parked:
                return
            for _ in range(_PPMD_QUIESCE_MAX_CALLS):
                # ``not needs_input`` is the "worker not parked" signal — the last
                # call exited on budget, so Free is already a no-op. The ``eof``
                # short-circuit is deliberately kept ahead of it: at native eof the
                # worker is finished AND feeding a NUL here would be a decode-after-eof
                # (the unbounded form of which is itself a crash path on 1.3.x), so
                # never issue one — skip instead. The exception is a spent payload
                # (``_exhausted``): its last call returned short at ``eof``, which
                # pyppmd reports with ``needs_input`` False even when the worker is
                # parked on empty input, so neither flag can be trusted and the NUL
                # is sent anyway. Measured on an overstated ``unpack_size``: without
                # it valgrind shows the Free-time invalid write and a later decoder
                # in the same process can start from corrupted state.
                if not self._exhausted and (
                    decomp.eof or not getattr(decomp, "needs_input", False)
                ):
                    return
                # One-symbol budget: the worker resumes, consumes the NUL, and
                # exits on budget (returns the byte), or blocks needing one more
                # tail byte — the next iteration feeds it. A non-empty return means
                # the worker hit its budget and exited, so it will not be resumed.
                if decomp.decode(b"\0", 1):
                    return
        except Exception:  # noqa: BLE001 - teardown hygiene; never raise from close()/__del__
            pass

    def close(self) -> None:
        self._quiesce_worker()
        decomp = getattr(self, "_decomp", None)
        if isinstance(decomp, PpmdChildDecoder):
            decomp.close()

    def __del__(self) -> None:
        # GC safety net: quiesce even when the owning stream's close() did not run
        # (decoder used directly, or stream leaked). Runs before self._decomp is
        # dropped, so the native worker is finished before Ppmd7T_Free executes.
        self.close()


def PpmdDecompressorStream(
    path: str | os.PathLike[str] | BinaryIO,
    *,
    order: int,
    mem_size: int,
    variant: int = 7,
    restore_method: int = 0,
    unpack_size: int | None = None,
    pack_size: int | None = None,
    in_process_max_input: int | None = _DEFAULT_IN_PROCESS_MAX_INPUT,
    refuse_input_after_end: bool = False,
    collector: DiagnosticCollector | None = None,
) -> DecompressorStream:
    """Decode a PPMd stream (forward-only).

    ``variant=7`` is 7z PPMd var.H; ``variant=8`` is ZIP method 98 (PPMd8).
    ``unpack_size`` and ``pack_size`` are required for PPMd7 (no end mark — see
    :class:`PpmdDecoder`). ``pack_size`` is the container-declared compressed length
    (or sized view); post-eof empty drains are gated on full pack delivery.
    For PPMd8, ``unpack_size`` / ``pack_size`` are recommended when the container
    declares them; unsized PPMd8 relies on the end mark (no post-eof drain).
    """
    return DecompressorStream(
        path,
        make_decoder=lambda _p, _i: PpmdDecoder(
            order=order,
            mem_size=mem_size,
            variant=variant,
            restore_method=restore_method,
            unpack_size=unpack_size,
            pack_size=pack_size,
            in_process_max_input=in_process_max_input,
        ),
        collector=collector,
        codec_name="ppmd",
        refuse_input_after_end=refuse_input_after_end,
    )
