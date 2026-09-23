"""Internal password candidate resolution for encrypted archive units."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from collections.abc import Sequence as ABCSequence
from typing import TypeVar, cast

from archivey.config import PasswordInput, PasswordProvider, PasswordRequest
from archivey.exceptions import ArchiveyUsageError, EncryptionError
from archivey.internal.arg_checks import describe_value
from archivey.types import ArchiveMember

_T = TypeVar("_T")


class _PasswordCandidatesExhausted(EncryptionError):
    """Internal marker: candidate decrypts failed or no candidate was supplied.

    An ``EncryptionError`` raised by the provider itself is deliberately not wrapped in
    this marker, so a backend can customize true candidate exhaustion without rewriting a
    provider-side failure.
    """

    def __init__(
        self, message: str, *, last_error: EncryptionError | None = None
    ) -> None:
        super().__init__(message)
        self.last_error = last_error


def _to_bytes(password: str | bytes) -> bytes:
    return password.encode() if isinstance(password, str) else password


class _PasswordCandidates:
    """Per-archive password state: known-good list, remaining candidates, optional provider.

    Under ``MemberStreams.CONCURRENT`` the known-good snapshot/promotion and provider
    callback are synchronized (D10): the provider is invoked with **no** Archivey lock
    held, one call at a time per reader; a second thread waits for the first call to
    return. Reentry from the provider's own thread raises ``ArchiveyUsageError``.
    Concurrent first-touch may call the provider / attempt a candidate more than once —
    promotion still converges.
    """

    __slots__ = (
        "_candidates",
        "_known_good",
        "_provider",
        "_state_lock",
        "_provider_turn",
        "_provider_owner",
    )

    def __init__(
        self,
        *,
        candidates: ABCSequence[bytes] = (),
        provider: PasswordProvider | None = None,
    ) -> None:
        self._known_good: list[bytes] = []
        # Immutable static list: callers cannot mutate our candidate order after open.
        self._candidates: tuple[bytes, ...] = tuple(candidates)
        self._provider = provider
        self._state_lock = threading.Lock()
        # The thread whose provider call is running, or None. Keyed by thread rather
        # than counted: a count cannot tell a provider calling back into archivey on
        # its own thread (a deadlock if it waited, so refused) from a second worker
        # that needs the provider at the same moment (a correct program, so it waits).
        self._provider_turn = threading.Condition(threading.Lock())
        self._provider_owner: int | None = None

    @classmethod
    def from_input(cls, password: PasswordInput) -> _PasswordCandidates:
        if password is None:
            return cls()
        if isinstance(password, (str, bytes)):
            return cls(candidates=[_to_bytes(password)])
        if isinstance(password, ABCSequence) and not isinstance(password, (str, bytes)):
            candidates_list: list[bytes] = []
            for item in password:
                if not isinstance(item, (str, bytes)):
                    raise ArchiveyUsageError(
                        f"password= sequence items must be str or bytes, but one was "
                        f"{describe_value(item)}."
                    )
                candidates_list.append(_to_bytes(item))
            return cls(candidates=candidates_list)
        if not callable(password):
            # Anything not matched above used to be cast to a provider and stored. The
            # cast is a promise, not a check, so `password=0` survived open() and
            # surfaced at the first encrypted member as `'int' object is not callable` —
            # a raw TypeError, from a call the caller never wrote, about an argument
            # they passed several operations earlier.
            raise ArchiveyUsageError(
                f"password= takes a str, bytes, a sequence of those, a provider "
                f"callable, or None, but got {describe_value(password)}."
            )
        return cls(provider=cast(PasswordProvider, password))

    def has_passwords(self) -> bool:
        with self._state_lock:
            return bool(
                self._known_good or self._candidates or self._provider is not None
            )

    def is_ambiguous(self) -> bool:
        """Whether a weak password check needs confirmation before accepting a result.

        Duplicate static values count once. A provider is always potentially ambiguous:
        it is intentionally lazy and may return another value after a failed candidate,
        so a backend cannot soundly assume that its first answer is the only one.
        """
        with self._state_lock:
            distinct = set(self._known_good) | set(self._candidates)
            return len(distinct) > 1 or self._provider is not None

    def has_provider(self) -> bool:
        return self._provider is not None

    def ask_provider(self, member: ArchiveMember | None, attempt: int) -> bytes | None:
        """Return the provider's next answer, or ``None`` to stop.

        Invokes the provider with no Archivey lock held; calls from other threads wait
        their turn. Reentry from the provider's own thread raises
        ``ArchiveyUsageError``.
        """
        if self._provider is None:
            return None
        return self._call_provider(member, attempt)

    def _call_provider(
        self, member: ArchiveMember | None, attempt: int
    ) -> bytes | None:
        assert self._provider is not None
        me = threading.get_ident()
        with self._provider_turn:
            if self._provider_owner == me:
                raise ArchiveyUsageError(
                    "Password provider reentered a password-requiring operation on the "
                    "same archive reader. Return a password (or None) without calling "
                    "back into archivey from the provider."
                )
            # Another thread's provider call is running: wait for it rather than
            # refuse, so callbacks stay serialized (reader-concurrency).
            while self._provider_owner is not None:
                self._provider_turn.wait()
            self._provider_owner = me
        try:
            # Provider runs with no Archivey lock held (D10).
            raw = self._provider(PasswordRequest(member=member, attempt=attempt))
        finally:
            with self._provider_turn:
                self._provider_owner = None
                self._provider_turn.notify()
        if raw is None:
            return None
        if not isinstance(raw, (str, bytes)):
            # Checked here because the provider is the caller's code running inside
            # ours: without this, its return value reached the cipher and failed as
            # `TypeError: a bytes-like object is required`, naming neither the
            # provider nor the password.
            raise ArchiveyUsageError(
                f"The password provider returned {describe_value(raw)}; it must "
                f"return a str, bytes, or None."
            )
        return _to_bytes(raw)

    def record_success(self, password: bytes) -> None:
        with self._state_lock:
            if password in self._known_good:
                self._known_good.remove(password)
            self._known_good.insert(0, password)

    def iter_candidates(self) -> Iterator[bytes]:
        """Yield passwords to try for one encrypted unit (known-good, then candidates)."""
        with self._state_lock:
            snapshot = (*self._known_good, *self._candidates)
        seen: set[bytes] = set()
        for password in snapshot:
            if password not in seen:
                seen.add(password)
                yield password

    def attempt(
        self,
        member: ArchiveMember | None,
        decrypt: Callable[[bytes], _T],
        *,
        on_failure: Callable[[bytes, Exception], EncryptionError | None] | None = None,
    ) -> _T:
        """Try passwords in order; consult the provider after static candidates fail.

        ``decrypt`` must return a non-``None`` value on success: ``attempt`` uses
        ``None`` as its "wrong password, try the next candidate" sentinel, so a
        decrypt callable that returned ``None`` for a valid password would be treated
        as a failure and retried. Decrypt / key derivation runs outside password-state
        locks; only promotion and provider reentry bookkeeping take the locks.
        """
        last_error: EncryptionError | None = None
        tried: set[bytes] = set()

        def try_password(password: bytes) -> _T | None:
            nonlocal last_error
            tried.add(password)
            try:
                result = decrypt(password)
            except EncryptionError as exc:
                last_error = exc
                if on_failure is not None:
                    mapped = on_failure(password, exc)
                    if mapped is not None:
                        last_error = mapped
                return None
            self.record_success(password)
            return result

        for password in self.iter_candidates():
            result = try_password(password)
            if result is not None:
                return result

        attempt = 1
        while self._provider is not None:
            raw = self._call_provider(member, attempt)
            if raw is None:
                break
            password = raw
            # A provider that repeats a password we already tried can make no further
            # progress; stop rather than re-running an expensive decrypt (and, for 7z,
            # an expensive key derivation) on the same input forever.
            if password in tried:
                break
            result = try_password(password)
            if result is not None:
                return result
            attempt += 1

        message = (
            (
                last_error.message
                if last_error is not None
                and "wrong password" in last_error.message.lower()
                else "Password(s) rejected for this encrypted member"
            )
            if tried
            else (
                last_error.message
                if last_error is not None
                else "Password required to read this encrypted member"
            )
        )
        exhausted = _PasswordCandidatesExhausted(message, last_error=last_error)
        if last_error is not None:
            raise exhausted from last_error
        raise exhausted
