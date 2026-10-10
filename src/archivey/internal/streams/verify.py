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
  from 0 to the end after any seeks is verified in full. Any other seek off the
  sequential frontier forfeits the **checksum** only (incremental hashing needs
  linear consumption). Length / truncation / over-run stay on and key
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
from archivey.exceptions import ArchiveyError, CorruptionError, TruncatedError
from archivey.internal.diagnostics_collector import (
    DiagnosticCollector,
    resolve_collector,
)
from archivey.internal.hashing.blake2sp import Blake2sp
from archivey.internal.logs import integrity as logger
from archivey.internal.streams.decompressor_stream import _COMPRESSED_READ_SIZE_MAX
from archivey.internal.streams.resume import ask_resume_offset
from archivey.internal.streams.streamtools import (
    ReadOnlyIOStream,
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

    An ``ArchiveyError``, an ``OSError`` or a ``MemoryError`` propagates. Any other
    error from the decoder at this point counts as "nothing more": an accelerator can
    raise an opaque error at the end of its input instead of returning ``b""``, and
    every declared byte has already been delivered. What is given up is only the
    over-run verdict. On a sequential read the digests, checked after this probe, still
    judge the content; after a seek the checksum was forfeited already.
    """
    try:
        return inner.read(1)
    except (ArchiveyError, OSError, MemoryError):
        raise
    except Exception:  # noqa: BLE001 - an opaque decoder error past the end is "no more data"
        return b""


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
        self._furthest_read_pos = 0
        # Decode-error abandon: skip all end-of-stream checks on later reads.
        self._abandoned = False
        # Seek off the frontier forfeits checksum only (ADR 0014); length stays on. A
        # seek back to 0 re-arms it (``_rearm``).
        self._digests_enabled = True

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
    def pos(self) -> int:
        return self._pos

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
        """Advance logical position and furthest-read position after a returning read."""
        if not data:
            return
        self._pos += len(data)
        if self._pos > self._furthest_read_pos:
            self._furthest_read_pos = self._pos
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
        ``[_furthest_read_pos, expected_size)`` gap first — the checksum is already
        forfeited by the seek, so this only advances the length frontier — then apply
        the same length and over-run checks, and on success restore the inner to the
        caller's position. Bounded by the declared size (a decompression-bomb cap).
        """
        self._verified = True
        expected_size = self._expected_size
        resume: int | None = None
        if (
            expected_size is not None
            and self._furthest_read_pos < expected_size <= self._pos
        ):
            # Only a seek off the frontier puts ``_pos`` past it, and ``note_seek``
            # forfeits the digests for that seek; the gap bytes never reach the
            # hashers, so the digest check below must not run on this path.
            assert not self._digests_enabled
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

    def note_seek(self, result: int) -> None:
        """Update the frontier after a successful inner seek.

        A seek to position 0 re-arms every check (``_rearm``): the hashers start
        again and the furthest-read and verified state is cleared, so a read from 0
        to the end is verified as a first read is, digests included. Any other seek
        off the sequential frontier forfeits the **checksum** (incremental hashing
        assumes linear consumption). Length / truncation / over-run checks stay
        enabled and key off bytes actually read (``_furthest_read_pos``); a seek that
        jumps to/past the declared size has the skipped gap read back and a byte
        probed past the size at conclusion (``_conclude``), so ``seek(declared_size)``
        cannot silence truncation (short) or over-run (long) (ADR 0014).
        """
        if result == 0:
            self._rearm()
            return
        if result != self._pos:
            self._digests_enabled = False
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
        result = self._inner.seek(offset, whence)
        self._verifier.note_seek(result)
        return result

    def tell(self) -> int:
        return self._inner.tell()

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
