"""Archivey exception hierarchy.

Two roots (intentional):

- :class:`ArchiveyError` — archive / environment / format problems. Safe to catch
  broadly when wrapping untrusted input.
- :class:`ArchiveyUsageError` — caller API misuse. Deliberately **outside** the
  ``ArchiveyError`` tree so ``except ArchiveyError`` does not hide bugs in calling
  code.

Under ``ArchiveyError``, names track the failing phase: open → read → extract,
plus feature/package/resource limits.

Both roots **escape their message** at construction: the text call sites build
interpolates attacker-controlled member names and the paths derived from them, and
an exception message reaches a terminal by routes no single consumer configures —
including a traceback the interpreter prints on its own. See
:class:`ArchiveyError` for what stays raw and why.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from archivey.escaping import escape_control_chars

if TYPE_CHECKING:
    from archivey.diagnostics import Diagnostic
    from archivey.types import ArchiveFormat


class ArchiveyError(Exception):
    """Root of all Archivey exceptions.

    **The message is escaped.** Call sites build messages by interpolating
    archive-derived text — a member name, or a destination path built from one — and
    those are attacker-controlled. An exception message reaches a terminal by more
    routes than any one consumer controls: ``print(e)``, ``logging.exception``, a
    third-party error reporter, and above all an uncaught exception whose traceback
    the interpreter prints itself, whose final line is ``str(e)``. Escaping in the
    handler that displays it protects only the routes someone remembered to
    configure; escaping here protects all of them, with no configuration.

    So ``message`` is stored escaped, and that escaped form is what
    :meth:`__str__`, ``args[0]`` and ``repr()`` all render. ``raw_message`` keeps the
    text as the call site wrote it, for the one job the escaped form cannot do: being
    embedded in *another* message that will escape it in turn.

    ``archive_name``, ``member_name``, ``link_target`` and ``source_format`` stay
    **raw** too, for callers that need the real value to act on rather than to print.
    :meth:`__str__` renders the names through ``!r``, which escapes them for display in
    turn — so a name available as an attribute should **not** also be interpolated into
    the message, or it prints twice. Prefer prose plus attributes:
    ``SymlinkEscapeError("Symlink target escapes destination", member_name=name,
    link_target=target)``.

    ``format_unconfirmed`` is a boolean (default ``False``): ``True`` when the format
    claim rested only on a content probe at ``GUESS`` confidence, so a decode failure
    should not be read as "this known format truncated."

    **Escape exactly once, at the outermost message.** Everything a message
    interpolates should therefore be raw when it goes in — which is what the two
    helpers are for, and why neither of them is a matter of taste:

    - a member name, link target or path → :func:`~archivey.escaping.quoted`, not
      ``!r``. ``!r`` escapes first, and this escapes the backslashes it introduced.
    - a caught exception that might be one of ours → :func:`raw_message_of`, not
      ``{exc}`` or ``{exc!r}``.

    Note this is the opposite of the rule for ``logger.*`` calls, whose records the CLI
    does **not** escape: there, ``%r`` is what makes an interpolated name inert and must
    stay.
    """

    def __init__(
        self,
        message: str,
        *,
        source_format: "ArchiveFormat | None" = None,
        archive_name: str | None = None,
        member_name: str | None = None,
        link_target: str | None = None,
        format_unconfirmed: bool = False,
    ) -> None:
        self.raw_message = message
        message = escape_control_chars(message)
        super().__init__(message)
        self.message = message
        self.source_format = source_format
        self.archive_name = archive_name
        self.member_name = member_name
        self.link_target = link_target
        self.format_unconfirmed = format_unconfirmed

    def __str__(self) -> str:
        parts = [self.message]
        if self.archive_name:
            parts.append(f"archive={self.archive_name!r}")
        if self.member_name:
            parts.append(f"member={self.member_name!r}")
        if self.link_target:
            parts.append(f"target={self.link_target!r}")
        if self.source_format:
            # Human label (ZIP / TAR_GZ / SEVEN_Z), not ArchiveFormat.ZIP repr.
            parts.append(f"format={self.source_format.display_name}")
        if self.format_unconfirmed:
            parts.append("format_unconfirmed=True")
        if len(parts) == 1:
            return self.message
        return f"{self.message} ({', '.join(parts[1:])})"


class OpenError(ArchiveyError):
    """Cannot open or parse archive header."""


class FormatDetectionError(OpenError):
    """Could not detect archive format."""


class UnsupportedFormatError(OpenError):
    """Format detected but no backend available."""


class StreamNotSeekableError(OpenError):
    """Source is non-seekable but this format/backend needs seek."""


class ReadError(ArchiveyError):
    """Error reading a member."""


class CorruptionError(ReadError):
    """CRC mismatch or bad data block."""


class TruncatedError(ReadError):
    """Unexpected EOF."""


class EncryptionError(ReadError):
    """Password required or wrong password."""


class LinkTargetNotFoundError(ReadError):
    """A symlink/hardlink target is absent from the archive."""


class WriteError(ArchiveyError):
    """Error writing an archive."""


class ExtractionError(ArchiveyError):
    """Error extracting a member to disk."""


class FilterRejectionError(ExtractionError):
    """Safety filter blocked the member."""


class PathTraversalError(FilterRejectionError):
    """Path traversal attempt (../ or absolute path)."""


class SymlinkEscapeError(FilterRejectionError):
    """Symlink resolves outside destination."""


class SpecialFileError(FilterRejectionError):
    """Device node, FIFO, socket — always rejected."""


class UnportableNameError(FilterRejectionError):
    """A member name is unsafe on the destination OS under the active policy.

    Covers the cross-platform name hazards (see ``safe-extraction``): Windows-reserved
    device names, a trailing dot/space, or ``:`` in a path segment, rejected under
    ``STRICT`` on every platform (``STANDARD`` rejects the reserved/``:`` subset).
    """


class DeceptiveNameError(FilterRejectionError):
    """A member name is built to display as something other than what it is.

    Raised for Unicode bidi **override/isolate** characters (U+202A–202E, U+2066–2069),
    which reorder the surrounding text so that ``evil‮gnp.exe`` reads as a ``.png``
    in every listing a person will see. Distinct from its siblings because a caller
    triaging a batch acts differently on each: nothing here escapes the destination
    (``PathTraversalError``) and nothing is unwritable on this platform
    (``UnportableNameError``) — the name is simply a lie.

    The three *directional marks* (U+061C, U+200E, U+200F) are **not** covered: they
    reorder nothing and appear in legitimate Arabic and Hebrew filenames. They are
    reported at listing time as ``MEMBER_NAME_BIDI_CONTROL`` and extract normally.
    """


class NameCollisionError(ExtractionError):
    """A member resolved to a destination another member of this run already claimed.

    Raised only when the caller opted in with ``AbortOn.NAME_COLLISION``; without that
    opt-in a collision is not an error at all — ``OverwritePolicy`` resolves it and both
    members' ``ExtractionResult`` records the outcome. It is raised for *every*
    non-``TRUSTED`` collision regardless of how the policy would have resolved it, since
    the trigger is the collision itself, not its resolution.
    """


class NameRewrittenError(ExtractionError):
    """A member name was rewritten to its portable spelling.

    Raised only when the caller opted in with ``AbortOn.NAME_SANITIZED`` — a narrow
    escape hatch for callers who require the on-disk name to match the archive's byte
    for byte. Without the opt-in the rewrite is a success, recorded as
    ``ExtractionResult.presented_name``.
    """


class ResourceLimitError(ArchiveyError):
    """A configured listing or extraction resource limit was exceeded.

    Covers :class:`~archivey.config.ListingLimits` materialization caps and
    :class:`~archivey.config.ExtractionLimits` bomb guards. Sibling of
    :class:`ExtractionError` (not a subclass): limit trips are not filter/path failures.
    """


class UnsupportedFeatureError(ArchiveyError):
    """Recognized but unhandled feature/variant/codec."""


class PackageNotInstalledError(ArchiveyError):
    """A required optional package or external tool is absent."""


class UnsupportedOperationError(ArchiveyError):
    """Operation not valid for this archive, format, backend, or access mode.

    Describes what an archive or mode cannot provide — not a bug in calling code.
    Caller misuse (wrong-reader identity, post-close use, undeclared concurrent
    streams) raises :class:`ArchiveyUsageError` instead.
    """


class ArchiveyUsageError(Exception):
    """Caller misuse of the Archivey API — deliberately not an :class:`ArchiveyError`.

    ``except ArchiveyError`` wraps archive/environment problems; usage errors indicate
    a bug in calling code and must not be swallowed by those handlers.

    The message is escaped on the same terms as :class:`ArchiveyError`'s. A usage
    error's text is mostly archivey's own, so the escaping is usually a no-op — but
    "mostly" is not a property worth carving an exception into, and a usage error is
    free to name the member that provoked it.
    """

    def __init__(self, message: str) -> None:
        self.raw_message = message
        message = escape_control_chars(message)
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


class ConcurrentAccessError(ArchiveyUsageError):
    """A second overlapping member stream was opened without ``concurrent_members=True``.

    The message includes the ``open_archive()`` call site so the error points at where
    the capability should have been declared.
    """


class DiagnosticRaisedError(ArchiveyError):
    """A diagnostic was escalated to an error via :class:`~archivey.diagnostics.DiagnosticPolicy`.

    Always-stop: extraction MUST NOT catch this as a per-member failure under
    ``OnError.CONTINUE``. Carries the escalated :class:`~archivey.diagnostics.Diagnostic`.
    """

    def __init__(
        self,
        message: str,
        *,
        diagnostic: "Diagnostic",
        source_format: "ArchiveFormat | None" = None,
        archive_name: str | None = None,
        member_name: str | None = None,
        link_target: str | None = None,
    ) -> None:
        super().__init__(
            message,
            source_format=source_format,
            archive_name=archive_name,
            member_name=member_name,
            link_target=link_target,
        )
        self.diagnostic = diagnostic


def raw_message_of(exc: BaseException) -> str:
    """The text of ``exc`` as its call site wrote it, for embedding in a new message.

    Interpolating a caught exception into a message that will itself be escaped needs
    the *unescaped* text, or the outer escape doubles the backslashes the inner one
    wrote. Archivey's exceptions keep that text on ``raw_message``; everything else was
    never escaped and is already raw.

    Needed only where the caught type is broad enough to include one of ours — a
    ``except Exception`` around a call that may raise an ``ArchiveyError``. A handler
    catching only third-party types (``lzma.LZMAError``, ``zipfile.BadZipFile``) can
    interpolate the exception directly.
    """
    if isinstance(exc, (ArchiveyError, ArchiveyUsageError)):
        return exc.raw_message
    return str(exc)
