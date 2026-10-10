"""Decompressed-output digest (and length) verification.

Container digests (``ArchiveMember.hashes``) and optional declared decompressed
length are checked on a clean sequential read to EOF.

Two delivery shapes (same rules, different wrappers):

- :class:`MemberVerifier` — the logic object. **Fused into**
  :class:`~archivey.internal.streams.archive_stream.ArchiveStream` on the member
  hot path (one fewer Python layer; nested codec ``ArchiveStream``s can collapse).
- :class:`VerifyingStream` — standalone ``BinaryIO`` wrapper around an inner +
  verifier. Kept for codec length backstops and tests; prefer fusion for members.

Per ADR 0014 / ``compressed-streams``:

- Public ``read(n)`` (``n ≥ 1``) is **full-count**: ``n`` bytes or a terminal boundary,
  from one ``inner.read(n)`` — the guarantee is the inner's (fill-or-EOF), and a short
  non-empty return is terminal, never "ask again" (ADR 0014).
- Verification runs on a read that **reaches the end** (declared size, or decoder
  EOS). A partial read is never verified. ``read(0)`` is a no-op (not EOF).
- A seek to position 0 re-arms every check (fresh hashers, no frontier), so a read
  from 0 to the end after any seeks is verified in full. The hashers cover the bytes
  from 0 to the furthest position a read reached (the frontier), so a backward seek
  keeps the **checksum**: later reads hash only what lies past the frontier. A forward
  seek past the frontier keeps it too when the inner would decode the skipped bytes
  anyway (its ``nearest_resume_offset`` for the target is at or before the frontier):
  the next read reads those bytes through the hashers first (``MemberVerifier.seek``),
  so the seek itself stays lazy. A seek to or past the declared size keeps it as well: concluding hashes the gap. A
  read that starts past the frontier, after a seek that jumped there by an index, an
  accelerator or random access, forfeits the checksum only. Length / truncation /
  over-run stay on and key
  off bytes **actually read** (``_furthest_read_pos``). If a seek jumps to/past the
  declared size without reading the intervening bytes, concluding reads the skipped
  gap and probes one byte past the declared size (``_conclude``) rather than
  returning ``b""`` blind, so a past-EOF ``seek(declared_size)`` still catches
  truncation (short) *and* over-run (long). A member already concluded
  (``_verified``) returns with no extra I/O.
- **Size-declared corruption** (digest mismatch / over-run at the declared size):
  the reaching read raises and **withholds** that chunk.
- **Size-unknown corruption**: deliver data bytes; raise on the EOS-observing
  (typically empty) read — no mandatory lookahead withhold.
- **Truncation-shaped**: first read past available returns a short prefix; the
  next empty read raises ``TruncatedError``.
- On ``read(-1)`` / ``readall``, the complete-stream call includes the EOF verdict
  and raises (so ``read(); close()`` cannot silently accept bad content).
- ``close()`` / ``finish_on_close`` MUST NOT introduce a first content
  ``TruncatedError`` / ``CorruptionError`` (teardown errors may still propagate).
- Missing/unknown digest algorithms emit ``DIGEST_UNVERIFIABLE`` and are skipped.
"""

from __future__ import annotations

import hashlib
import zlib
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, BinaryIO, Protocol

from archivey.diagnostics import DiagnosticCode, DigestContext
from archivey.exceptions import CorruptionError, TruncatedError
from archivey.internal.diagnostics_collector import (
    DiagnosticCollector,
    resolve_collector,
)
from archivey.internal.hashing.blake2sp import Blake2sp
from archivey.internal.logs import integrity as logger
from archivey.internal.streams.decompressor_stream import _COMPRESSED_READ_SIZE_MAX
from archivey.internal.streams.resume import (
    ask_resume_offset,
    ask_seek_resume_offset,
)
from archivey.internal.streams.streamtools import (
    ReadOnlyIOStream,
    is_closed_file_error,
    is_seekable,
)
from archivey.types import HashAlgorithm

if TYPE_CHECKING:
    from archivey.types import ArchiveMember

# Keys are ``HashAlgorithm`` (``member.hashes``). Values are digest ``bytes``
# (CRC-32 as four big-endian bytes). Mapping's key parameter is invariant, so
# this matches every typed caller rather than ``HashAlgorithm | str``.
# Name strings still work at runtime (``_algo_key`` / hashlib lookup) but sit
# outside the typed contract; ``algorithms_available`` is also how a future
# enum member gets a hasher for free.
_ExpectedHashes = Mapping[HashAlgorithm, bytes]
_DigestTransforms = Mapping[HashAlgorithm, Callable[[bytes], bytes]]

# Bounded drain step for sized ``read(-1)``. Must not use ``inner.read(-1)`` on the
# sized branch: ``expected_size`` is a decompression-bomb cap. The step itself is not
# the bound (the drain stops at ``expected_size`` whatever the step), so it is sized
# for speed: a member up to one step arrives as one piece, which ``b"".join`` returns
# without copying, and it is the decoder's largest compressed feed, so one step is one
# inflate call.
_SIZED_DRAIN_CHUNK = _COMPRESSED_READ_SIZE_MAX


def _algo_key(algorithm: HashAlgorithm | str) -> str:
    """Return the lowercase algorithm name used to choose a hasher.

    A ``HashAlgorithm`` stringifies to its value because it is a ``StrEnum``,
    which ``tests/test_str_enums.py`` pins. A bare string is folded the same
    way, so ``"CRC32"`` and the enum member share one lookup key. A plain
    ``Enum`` base would stringify as ``HashAlgorithm.CRC32``. That name matches
    no hasher, so ``MemberVerifier`` reports ``DIGEST_UNVERIFIABLE`` and does
    not compare the stored digest.
    """
    return str(algorithm).lower()


class _IncrementalHasher(Protocol):
    """The subset of the ``hashlib`` hash interface this stage uses."""

    @property
    def digest_size(self) -> int: ...
    def update(self, data: bytes, /) -> None: ...
    def digest(self) -> bytes: ...


class _Crc32Hasher:
    """A ``hashlib``-shaped wrapper over ``zlib.crc32`` so all algorithms share an interface."""

    digest_size = 4

    def __init__(self) -> None:
        self._value = 0

    def update(self, data: bytes, /) -> None:
        self._value = zlib.crc32(data, self._value)

    def digest(self) -> bytes:
        return (self._value & 0xFFFFFFFF).to_bytes(self.digest_size, "big")


def _make_hasher(
    algorithm: HashAlgorithm | str,
) -> Callable[[], _IncrementalHasher] | None:
    """Return a zero-arg factory for an incremental hasher, or ``None`` if unavailable."""
    name = _algo_key(algorithm)
    if name == "crc32":
        return _Crc32Hasher
    if name == "blake2sp":
        # RAR5 BLAKE2sp — not in hashlib; zero-dep tree hash on blake2s (see hashing/).
        return Blake2sp
    if name in hashlib.algorithms_available:
        return lambda: hashlib.new(name)
    return None


def _probe_past_declared(inner: BinaryIO) -> bytes:
    """Read one byte past a member's declared size; ``b""`` means the member ends there.

    A decoder error propagates: the body goes on past the declared size and does not
    decode, which is damage the read must not hand over as clean. The one error that
    counts as "nothing more" is a closed source. Closing an archive waits for a read in
    its source, not for the whole member read, so ``close()`` can land between the read
    that delivered the last declared byte and this probe. A stored ZIP member's bounded
    view then refuses the probe with the closed-file ``ValueError``, though the probe
    would read no source byte (``test_archive_closed_before_the_overrun_probe``). Every
    declared byte has been delivered, so the over-run verdict is what is given up. On a
    read the digests, checked after this probe, still judge the content, unless a
    read skipped bytes past the frontier and forfeited them; then nothing does.
    """
    try:
        return inner.read(1)
    except ValueError as exc:
        if is_closed_file_error(exc):
            return b""
        raise


class MemberVerifier:
    """Incremental digest/length checker over bytes read from an inner stream.

    Not a ``BinaryIO`` itself — :class:`VerifyingStream` and
    :class:`~archivey.internal.streams.archive_stream.ArchiveStream` own one and
    call :meth:`read` / :meth:`note_seek` / :meth:`finish_on_close` against their
    inner handle.
    """

    def __init__(
        self,
        expected: _ExpectedHashes,
        *,
        expected_size: int | None = None,
        collector: DiagnosticCollector | None = None,
        member: ArchiveMember | None = None,
        archive_name: str | None = None,
        digest_transforms: _DigestTransforms | None = None,
    ) -> None:
        self._expected_size = expected_size
        self._expected: dict[str, bytes] = {}
        self._hashers: dict[str, _IncrementalHasher] = {}
        self._digest_transforms: dict[str, Callable[[bytes], bytes]] = {}
        if digest_transforms:
            for key, transform in digest_transforms.items():
                self._digest_transforms[_algo_key(key)] = transform
        # Kept so a rewind to the start can begin each digest again (``note_seek``).
        self._hasher_factories: dict[str, Callable[[], _IncrementalHasher]] = {}
        for algorithm, value in expected.items():
            key = _algo_key(algorithm)
            factory = _make_hasher(key)
            if factory is None:
                message = (
                    f"Cannot verify digest {key!r} (unknown algorithm or backend "
                    f"not installed); skipping integrity check for it."
                )
                resolve_collector(collector).emit(
                    code=DiagnosticCode.DIGEST_UNVERIFIABLE,
                    message=message,
                    context=DigestContext(
                        archive_name=archive_name,
                        member_name=member.name if member is not None else "",
                        member_id=member._member_id if member is not None else None,
                        algorithm=key,
                        reason="unknown_algorithm_or_backend",
                    ),
                    member=member,
                    attach_to_member=member is not None,
                    logger=logger,
                )
                continue
            self._hasher_factories[key] = factory
            self._hashers[key] = factory()
            self._expected[key] = value
        self._verified = False
        self._pos = 0  # logical position (updated by read and seek)
        # Furthest position an actual read has reached — length / truncation / over-run
        # key off this, not seek-updated ``_pos``, so ``seek(declared_size)`` on a short
        # body cannot fabricate a clean end (ADR 0014). Reaching the declared size here
        # also marks the member length-verified, so a later seek past the end is cheap.
        # It is also the hashing frontier: while the digests are on, the hashers have
        # seen exactly the bytes ``[0, _furthest_read_pos)``.
        self._furthest_read_pos = 0
        # Decode-error abandon: skip all end-of-stream checks on later reads.
        self._abandoned = False
        # A seek that jumps past the frontier without decoding the skipped bytes
        # forfeits the checksum only (ADR 0014); length stays on. A seek back to 0
        # re-arms it (``_rearm``).
        self._digests_enabled = True
        # A forward seek that read through to the end of a size-unknown member saw it
        # end here: a position past the frontier then skips no byte (``seek``).
        self._ended_at_frontier = False
        # A forward seek whose skipped bytes the next read will hash first: the target
        # (``_pos`` already holds it) and where the inner was left. ``None`` when no
        # read-through is due.
        self._read_through_due: tuple[int, int] | None = None

    def _rearm(self) -> None:
        """Put every check back to its state before the first read.

        Runs on a seek to position 0 (``note_seek``): the hashers start again and the
        frontier, verified, abandoned and forfeited state is cleared, so a read from
        the start to the end after a rewind is verified as a first one is.
        """
        for key, factory in self._hasher_factories.items():
            self._hashers[key] = factory()
        self._verified = False
        self._pos = 0
        self._furthest_read_pos = 0
        self._abandoned = False
        self._digests_enabled = True
        self._ended_at_frontier = False
        self._read_through_due = None

    @property
    def enabled(self) -> bool:
        """True when end-of-stream checks may still run (not abandoned by a decode error)."""
        return not self._abandoned

    @property
    def expected_algorithms(self) -> frozenset[str]:
        """The digests this verifier will actually check.

        Narrower than the ``expected`` it was built from: an algorithm with no
        hasher available is dropped at construction (with ``DIGEST_UNVERIFIABLE``).
        A caller that reads a stream *in order to* establish something from its
        digest — rather than checking one alongside a read it wanted anyway — must
        consult this, since a verifier left with nothing to check reports no fault.
        """
        return frozenset(self._expected)

    @property
    def digests_enabled(self) -> bool:
        return self._digests_enabled and not self._abandoned

    @property
    def digest_intact(self) -> bool | None:
        """Whether the stored digest has run, or will run on a read on to the end.

        ``None`` when this verifier checks no digest (a length-only backstop). False
        once a read skipped bytes past the frontier, until a seek to 0 re-arms it, and
        while the position sits past the frontier short of the declared size (the next
        read would skip them; a seek back to the frontier or behind it, or to the
        declared size, whose conclusion reads the gap, keeps the digest).
        """
        if not self._expected:
            return None
        if not self.digests_enabled:
            return False
        if (
            self._verified
            or self._pos <= self._furthest_read_pos
            or self._read_through_due is not None
        ):
            return True
        if self._expected_size is None:
            return self._ended_at_frontier
        return self._pos >= self._expected_size

    @property
    def pos(self) -> int:
        return self._pos

    def tell(self, inner: BinaryIO) -> int:
        """The caller's position: the seek target while a read-through is due."""
        if self._read_through_due is not None:
            return self._pos
        return inner.tell()

    def _verify_digests(self) -> None:
        """Check every computable digest; raise on the first mismatch."""
        for algorithm, expected in self._expected.items():
            computed = self._hashers[algorithm].digest()
            transform = self._digest_transforms.get(algorithm)
            if transform is not None:
                computed = transform(computed)
            if computed != expected:
                raise CorruptionError(
                    f"Digest mismatch for {algorithm!r}: stored value does not match the "
                    f"decompressed content."
                )

    def _record_read(self, data: bytes) -> None:
        """Advance logical position and furthest-read position after a returning read.

        Only the part of ``data`` past the frontier reaches the hashers: a read after a
        backward seek returns bytes they have already seen. A read that starts past
        the frontier forfeits the digests.
        """
        if not data:
            return
        start = self._pos
        end = start + len(data)
        self._pos = end
        frontier = self._furthest_read_pos
        if end <= frontier:
            return
        self._furthest_read_pos = end
        if start > frontier:
            # A seek jumped past the frontier and this read skips the bytes between:
            # the hashers can never see them (ADR 0014). Length checks stay on.
            self._digests_enabled = False
        elif start < frontier:
            data = data[frontier - start :]
        self._update_digests(data)

    def _conclude(self, inner: BinaryIO) -> None:
        """Run end-of-stream checks once (read path only — never called from close).

        Short bodies raise :class:`~archivey.exceptions.TruncatedError` and digest
        mismatches raise :class:`~archivey.exceptions.CorruptionError`. A short body
        that also carries a hash raises ``TruncatedError`` (best-effort: shortfall vs
        digest mismatch are not always separable). Length / over-run use
        :attr:`_furthest_read_pos` (bytes actually read), not seek-updated ``_pos``.
        Digests run only while :attr:`digests_enabled`.

        When a seek jumped the logical position to/past the declared size without
        reading the intervening bytes, a complete member is indistinguishable from a
        short or long one. Rather than return ``b""`` and hide the fault, read the
        ``[_furthest_read_pos, expected_size)`` gap first, through the hashers while
        the digests are on (the gap starts at the frontier, so they stay contiguous),
        then apply the same length and over-run checks, and on success restore the
        inner to the caller's position. Bounded by the declared size (a
        decompression-bomb cap).

        With no declared size, a position past the frontier means a seek skipped
        bytes the hashers never saw (unless the stream was seen to end at the
        frontier), so the digests are not checked.
        """
        self._verified = True
        expected_size = self._expected_size
        resume: int | None = None
        if (
            expected_size is None
            and self._pos > self._furthest_read_pos
            and not self._ended_at_frontier
        ):
            self._digests_enabled = False
        if (
            expected_size is not None
            and self._furthest_read_pos < expected_size <= self._pos
        ):
            resume = inner.tell()
            inner.seek(self._furthest_read_pos)
            try:
                while self._furthest_read_pos < expected_size:
                    want = min(
                        _SIZED_DRAIN_CHUNK, expected_size - self._furthest_read_pos
                    )
                    piece = inner.read(want)
                    if not piece:
                        break
                    self._furthest_read_pos += len(piece)
                    self._update_digests(piece)
            except BaseException:
                # A decoder truncation/corruption error propagating from the gap read is
                # the member's own honest verdict; the stream is faulted — abandon and let
                # it surface (skip the pointless best-effort position restore).
                self._abandon()
                raise
        delivered = self._furthest_read_pos
        if expected_size is not None and delivered >= expected_size:
            # Delivered the declared size; the underlying must have nothing more. Any
            # trailing byte is corruption independent of the checksum, so a seek that
            # reached the size must not silence it. This probe also drains post-payload
            # authenticators (e.g. WinZip AES HMAC).
            if _probe_past_declared(inner):
                raise CorruptionError(
                    "Decompressed content exceeds its declared size of "
                    f"{expected_size} bytes."
                )
        elif expected_size is not None:
            # Short of declared size by actual reads — TruncatedError even when a hash
            # is present (best-effort verdict). Seek alone cannot satisfy this check.
            # After a gap read the inner sits at that EOF (no restore — we are raising).
            raise TruncatedError(
                f"Decompressed content ended after {delivered} of "
                f"{expected_size} expected bytes."
            )
        if resume is not None:
            inner.seek(resume)
        if self._digests_enabled:
            self._verify_digests()

    def _update_digests(self, data: bytes) -> None:
        if self._digests_enabled and data:
            for hasher in self._hashers.values():
                hasher.update(data)

    def _abandon(self) -> None:
        self._abandoned = True
        self._digests_enabled = False

    def _read_sized_all(self, inner: BinaryIO) -> bytes:
        """Drain to genuine EOF in bounded steps, capped by the declared size.

        ``expected_size`` is a decompression-bomb bound: do **not** delegate to
        ``inner.read(-1)``, which would pull an over-long adversarial payload into
        RAM. A single ``inner.read(remaining)`` is also insufficient — ``BinaryIO``
        may short-read without EOF — so this loop re-asks until empty. Unlike a
        bounded ``read(n)``, continuing past a short is *wanted* here: this call
        drains to EOF, so a decoder's deferred truncation belongs in it.

        On digest / over-run fault the verdict raises here and returns no bytes
        (withhold), matching ADR 0014's size-declared reaching-read rule.
        """
        assert self._expected_size is not None
        chunks: list[bytes] = []
        try:
            while self._pos < self._expected_size:
                remaining = self._expected_size - self._pos
                want = min(_SIZED_DRAIN_CHUNK, remaining)
                try:
                    piece = inner.read(want)
                except BaseException:
                    # The decoder's own error is the verdict, as on the bounded path
                    # in read(): the translator above this verifier classifies it.
                    # Relabelling every raw error as TruncatedError here made a
                    # corrupt deflate body read as truncated through read() and as
                    # corrupt through read(n).
                    self._abandon()
                    raise
                if not piece:
                    break
                self._record_read(piece)
                chunks.append(piece)
            # EOF verdict in this complete-stream call — raise withholds the body.
            if not self._abandoned and not self._verified:
                self._conclude(inner)
        except BaseException:
            # The raise withholds these bytes, and its traceback keeps this frame
            # alive for as long as the caller keeps the error: let the body go —
            # the list and the last piece read, which can be the whole member.
            chunks.clear()
            piece = b""
            raise
        return b"".join(chunks)

    def read(self, inner: BinaryIO, n: int | None = -1) -> bytes:
        """Read from ``inner``, update digests/bounds, and verify on clean EOF.

        Bounded ``read(n)`` is full-count by way of one ``inner.read`` — the inner is
        fill-or-EOF, and a short non-empty return is a terminal boundary to forward
        rather than retry (ADR 0014). A size-declared reaching read that fails
        digest / over-run raises and returns no bytes for that call.
        """
        # ``None`` reads to EOF, as on any ``io`` stream.
        if n is None:
            n = -1
        # read(0) is a no-op — never treat it as EOF (stdlib file / BytesIO contract).
        if n == 0:
            return b""
        if self._read_through_due is not None:
            self._settle_read_through(inner)
        if n < 0:
            # Complete-stream read: include the EOF verdict in this call.
            if (
                not self._abandoned
                and self._expected_size is not None
                and not self._verified
            ):
                return self._read_sized_all(inner)
            try:
                data = inner.read(-1)
            except Exception:  # noqa: BLE001 - abandon verify; re-raise decoder error
                self._abandon()
                raise
            if data:
                self._record_read(data)
            if not self._abandoned and not self._verified:
                try:
                    self._conclude(inner)
                except BaseException:
                    del data  # withheld: the kept traceback must not pin it
                    raise
            return data

        # Bounded full-count read.
        if self._abandoned or self._verified:
            try:
                return inner.read(n)
            except Exception:  # noqa: BLE001 - abandon verify; re-raise decoder error
                self._abandon()
                raise

        want = n
        reaches_declared = False
        if self._expected_size is not None:
            remaining = self._expected_size - self._pos
            if remaining <= 0:
                # Declared size 0 read from the start, or a seek at/past the declared
                # size (a sequential read reaching the size verifies inline): conclude
                # here, reading any seek-skipped gap rather than trusting the seek.
                self._conclude(inner)
                return b""
            want = min(n, remaining)
            reaches_declared = want == remaining

        try:
            data = inner.read(want)
        except Exception:  # noqa: BLE001 - abandon verify; re-raise decoder error
            self._abandon()
            raise

        if data:
            self._record_read(data)
            if (
                reaches_declared
                and self._expected_size is not None
                and self._furthest_read_pos >= self._expected_size
            ):
                # Size-declared verifying event: withhold this chunk on fault.
                try:
                    self._conclude(inner)
                except BaseException:
                    del data  # withheld: the kept traceback must not pin it
                    raise
            return data

        # Empty: size-unknown digest / truncation-shaped terminal read.
        if not self._verified:
            self._conclude(inner)
        return data

    def seek(self, inner: BinaryIO, offset: int, whence: int = 0) -> int:
        """Seek, keeping the checksum when reaching the target decodes the gap anyway.

        A forward seek past the frontier on a decompressing inner decodes every byte
        between its resume point and the target. When that resume point is at or
        before the frontier (``nearest_resume_offset``), the skipped bytes cost the
        same whether the inner discards them or this verifier reads them. The seek
        then leaves the inner where it is and records the target; the next read reads
        the gap through the hashers first (``_settle_read_through``). The seek stays
        lazy: a later seek replaces the target, and a member closed with no read
        decodes nothing. The hashers see the first pass over each byte only: bytes
        read again after a seek back are not checked again.

        Every other seek is passed on (``note_seek``). One to or behind the frontier
        keeps the checksum. So does one to or past the declared size: the read that
        concludes reads the gap through the hashers (``_conclude``), so the seek need
        not, and a ``seek(0, SEEK_END)`` that a caller undoes stays as cheap as the
        inner makes it. A seek past the frontier that the inner can jump (a seek
        index, an accelerator, a stored or decrypt-only member) or cannot price
        forfeits the checksum once a read skips the gap (``_record_read``).
        """
        target = self._seek_target(offset, whence)
        due = self._read_through_due
        expected_size = self._expected_size
        if (
            target is not None
            and target > self._furthest_read_pos
            # Parked past the frontier (a jump, or the end already found there):
            # reaching the frontier again would mean a rewind, not a decode forward.
            and (self._pos <= self._furthest_read_pos or due is not None)
            and not self._ended_at_frontier
            and (expected_size is None or target < expected_size)
            and self._expected
            and self.digests_enabled
            and not self._verified
        ):
            resume = ask_seek_resume_offset(inner, target)
            if resume is not None and resume <= self._furthest_read_pos:
                left_at = due[1] if due is not None else self._pos
                self._read_through_due = (target, left_at)
                self._pos = target
                return target
        if due is not None and target is not None:
            # The inner is still where the deferred seek left it, not at ``_pos``: a
            # relative ``whence`` must not apply to it.
            offset, whence = target, 0
        result = inner.seek(offset, whence)
        self.note_seek(result)
        return result

    def _seek_target(self, offset: int, whence: int) -> int | None:
        """The position a seek asks for, or ``None`` when it needs the unknown size."""
        if whence == 0:
            return offset
        if whence == 1:
            return self._pos + offset
        if self._expected_size is not None:
            return self._expected_size + offset
        return None

    def _settle_read_through(self, inner: BinaryIO) -> None:
        """Read and hash from the frontier up to a deferred seek's target.

        The target is short of the declared size (``seek`` sends the rest to
        ``_conclude``), so this read never concludes. A decoder error abandons the
        checks and propagates from the read that settles, as it would had the inner's
        own lazy seek decoded the same bytes there. With no declared size, reaching the
        end first is recorded, so the position past it skips no byte. The inner ends
        with a seek to the target, which also raises anything an index build held for
        the next seek (``ask_seek_resume_offset``).
        """
        due = self._read_through_due
        assert due is not None
        target, left_at = due
        self._read_through_due = None
        self._pos = left_at
        try:
            if self._pos < self._furthest_read_pos:
                # Behind the frontier after a backward seek: let the inner reach the
                # frontier its own cheapest way, then read on from there.
                self._pos = inner.seek(self._furthest_read_pos)
            while self._pos < target:
                piece = inner.read(min(_SIZED_DRAIN_CHUNK, target - self._pos))
                if not piece:
                    self._ended_at_frontier = True
                    break
                self._record_read(piece)
        except BaseException:
            self._abandon()
            raise
        self._pos = inner.seek(target)

    def note_seek(self, result: int) -> None:
        """Update the position after a successful (or raised but moved) inner seek.

        A seek to position 0 re-arms every check (``_rearm``): the hashers start
        again and the furthest-read and verified state is cleared, so a read from 0
        to the end is verified as a first read is, digests included. Any other seek
        only moves the position: the hashers cover ``[0, frontier)``, a read from at
        or behind the frontier hashes only what lies past it, and a read that starts
        past it forfeits the **checksum** (``_record_read``). Length / truncation /
        over-run checks stay enabled and key off bytes actually read
        (``_furthest_read_pos``); a seek that jumps to/past the declared size has the
        skipped gap read back and a byte probed past the size at conclusion
        (``_conclude``), so ``seek(declared_size)`` cannot silence truncation (short)
        or over-run (long) (ADR 0014).
        """
        self._read_through_due = None
        if result == 0:
            self._rearm()
            return
        self._pos = result

    def finish_on_close(self, inner: BinaryIO) -> None:
        """Close ``inner`` — teardown only, never a content-fault surface.

        Every content verdict (digest mismatch, hash-less short, over-length) fires
        from a completing read. ``close`` therefore does **not** read, probe, or
        drain the inner to force a late verdict — a partial read before clean EOF is
        a deliberate abandon with no verdict. A teardown error raised by
        ``inner.close()`` itself (a subprocess exit code, ``OSError``) still
        propagates. WinZip AES HMAC is a content verdict and fires from the
        completing read, not from ``close`` (ADR 0014).
        """
        inner.close()


def note_raised_seek(verifier: MemberVerifier | None, inner: BinaryIO) -> int | None:
    """Tell ``verifier`` where a seek that raised left ``inner``; return that position.

    A seek can raise after it moved: a decompressor stream raises an escalated
    report once its own seek has finished (see ``DecompressorStream``), so a caller
    that catches it reads on from the new position. The verifier must know, or it
    keeps hashing as if the read were still linear and checks length against a
    frontier the stream has left. Both wrappers that drive a verifier
    (:class:`VerifyingStream` and ``ArchiveStream``) call this from their seek.
    Returns ``None`` when ``inner`` cannot say where it is; the seek's own error is
    the one that propagates.
    """
    try:
        after = inner.tell()
    except Exception:  # noqa: BLE001 - the seek's own error propagates instead
        return None
    if verifier is not None:
        verifier.note_seek(after)
    return after


def ask_digest_intact(stream: object) -> bool | None:
    """Ask ``stream`` whether the digest its verifier checks can still run.

    Duck-typed on a ``_digest_intact()`` method, as :func:`ask_resume_offset` is on
    ``nearest_resume_offset``: :class:`VerifyingStream` and an ``ArchiveStream`` with
    a fused verifier answer (see :attr:`MemberVerifier.digest_intact`). Private, so
    the public ``ArchiveStream`` gains no published method. ``None`` means the stream
    cannot say: it has no verifier, or one that checks no digest.
    """
    ask = getattr(stream, "_digest_intact", None)
    if ask is None:
        return None
    answer = ask()
    return answer if isinstance(answer, bool) else None


def build_member_verifier(
    expected: _ExpectedHashes | None,
    *,
    expected_size: int | None = None,
    collector: DiagnosticCollector | None = None,
    member: ArchiveMember | None = None,
    archive_name: str | None = None,
    digest_transforms: _DigestTransforms | None = None,
) -> MemberVerifier | None:
    """Return a :class:`MemberVerifier` when there is something to check, else ``None``."""
    hashes = expected if expected is not None else {}
    if not hashes and expected_size is None:
        return None
    return MemberVerifier(
        hashes,
        expected_size=expected_size,
        collector=collector,
        member=member,
        archive_name=archive_name,
        digest_transforms=digest_transforms,
    )


class VerifyingStream(ReadOnlyIOStream):
    """Wrap ``inner`` and verify digests/length via a :class:`MemberVerifier`.

    Kept for codec length backstops and tests. Member backends fuse the same
    verifier into :class:`~archivey.internal.streams.archive_stream.ArchiveStream`.
    """

    def __init__(
        self,
        inner: BinaryIO,
        expected: _ExpectedHashes,
        *,
        expected_size: int | None = None,
        collector: DiagnosticCollector | None = None,
        member: ArchiveMember | None = None,
        archive_name: str | None = None,
        digest_transforms: _DigestTransforms | None = None,
    ) -> None:
        super().__init__()
        self._inner = inner
        # This wrapper is used explicitly (codec length backstops, tests), so it always
        # owns a verifier even when there is nothing to check — unlike the fused path,
        # which uses ``build_member_verifier`` to skip the wrapper entirely in that case.
        try:
            self._verifier = MemberVerifier(
                expected,
                expected_size=expected_size,
                collector=collector,
                member=member,
                archive_name=archive_name,
                digest_transforms=digest_transforms,
            )
        except BaseException:
            # The build can raise (a digest diagnostic under a RAISE policy), and
            # ``IOBase.__del__`` still calls ``close()``, which needs the verifier.
            # Close the inner this wrapper owns and mark it closed, so the finalizer
            # has nothing left to do.
            try:
                inner.close()
            except Exception:  # noqa: BLE001 - the refusal below is the error to report
                pass
            finally:
                super().close()
            raise

    # Compat for tests / diagnostics that inspect frontier state.
    @property
    def _verify_enabled(self) -> bool:
        return self._verifier.enabled

    @property
    def _pos(self) -> int:
        return self._verifier.pos

    @property
    def _expected_size(self) -> int | None:
        return self._verifier._expected_size

    def read(self, n: int | None = -1, /) -> bytes:
        return self._verifier.read(self._inner, n)

    def seekable(self) -> bool:
        return is_seekable(self._inner)

    def seek(self, offset: int, whence: int = 0, /) -> int:
        try:
            return self._verifier.seek(self._inner, offset, whence)
        except Exception:
            note_raised_seek(self._verifier, self._inner)
            raise

    def _digest_intact(self) -> bool | None:
        return self._verifier.digest_intact

    def tell(self) -> int:
        return self._verifier.tell(self._inner)

    def nearest_resume_offset(self, target: int) -> int | None:
        # Length backstop around a codec stream; preserve the inner's offset space.
        return ask_resume_offset(self._inner, target)

    def close(self) -> None:
        if self.closed:
            return
        try:
            self._verifier.finish_on_close(self._inner)
        finally:
            # finish_on_close closes the inner; always mark the wrapper closed even
            # when a teardown error from inner.close() propagates.
            if not self.closed:
                super().close()
