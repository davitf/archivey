"""``test`` / ``t`` verb — full-read integrity check."""

from __future__ import annotations

import sys
from typing import TextIO

from archivey import ArchiveReader, ExtractionProgress
from archivey.cli.common import open_for_cli, reject_salvage
from archivey.cli.exit_codes import EXIT_FAIL, EXIT_OK
from archivey.cli.filters import MemberSelection
from archivey.cli.format import escape_member_name, format_error_detail
from archivey.cli.password import resolve_password
from archivey.cli.progress import ProgressCallback, make_progress_callback
from archivey.config import PasswordInput
from archivey.diagnostics import DiagnosticCode
from archivey.exceptions import (
    ArchiveyError,
    ArchiveyUsageError,
    LinkTargetNotFoundError,
    ReadError,
)
from archivey.types import ArchiveMember, MemberType

# Codes saying a digest went unchecked: the bytes were read but nothing confirmed them,
# so the run is not a clean verification (X6). Archive-level digests count too, such as
# a gzip trailer past the trailing-data scan bound.
_UNVERIFIED_CODES = (
    DiagnosticCode.DIGEST_UNVERIFIABLE,
    DiagnosticCode.ENCRYPTED_MEMBER_UNVERIFIED,
)


def run_test(
    *,
    archive: str,
    patterns: list[str],
    exclude: list[str],
    verbose: bool,
    salvage: bool,
    password: str | None,
    track_io: bool,
    hide_progress: bool = False,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    del out  # test writes summaries to stderr only
    reject_salvage(salvage)
    err = err if err is not None else sys.stderr
    pwd: PasswordInput = resolve_password(password)
    selection = MemberSelection(patterns, exclude)
    pred = selection.predicate

    ok = 0
    failed = 0
    members_total: int | None = None
    with open_for_cli(archive, password=pwd, track_io=track_io, err=err) as reader:
        indexed = reader.members_report_if_available()
        if indexed is not None and indexed.error is not None:
            # A free list that ends in damage holds only the members before it, so
            # it cannot say a pattern matches nothing. The run's own pass judges the
            # patterns instead, and reaches the damage itself.
            indexed = None
        total_bytes: int | None = None
        if indexed is not None:
            # A free index settles the patterns before the run. Without one, the run's
            # own pass offers each member to them (see the end of the pass).
            selected = [m for m in indexed if selection(m)]
            if selection.report(err=err):
                return EXIT_FAIL
            file_members = [m for m in selected if m.is_file]
            members_total = len(file_members)
            sizes = [m.size for m in file_members if m.size is not None]
            if len(sizes) == len(file_members):
                total_bytes = sum(sizes)

        on_progress: ProgressCallback | None = make_progress_callback(
            hide_progress=hide_progress, stream=err
        )
        bytes_done = 0
        files_done = 0
        pending_links: list[ArchiveMember] = []
        pass_ended_early = False
        try:
            # Manual iteration so open-time failures (wrong password, corrupt header)
            # count as FAIL and still reach the summary (F4). Once the generator raises,
            # further next() yields StopIteration — remaining members are lost (library
            # limitation for solid / poisoned streams); report them as "not tested" (P8).
            it = iter(reader.stream_members(pred))
            while True:
                try:
                    member, stream = next(it)
                except StopIteration:
                    break
                except (ArchiveyError, OSError) as exc:
                    failed += 1
                    pass_ended_early = True
                    print(f"FAIL: {format_error_detail(exc)}", file=err)
                    continue

                if stream is None and _link_needs_verification(member):
                    # Verified after the pass: the reader refuses an open() while
                    # stream_members() is running.
                    pending_links.append(member)
                    continue
                if stream is None:
                    # Directories / links / non-file: no body to verify — omit from counts
                    # so "N OK" matches unzip -t style (files only).
                    if verbose:
                        print(f"skip {escape_member_name(member.name)}", file=err)
                    continue
                member_written = 0
                try:
                    with stream:
                        while True:
                            chunk = stream.read(1024 * 1024)
                            if not chunk:
                                break
                            n = len(chunk)
                            member_written += n
                            bytes_done += n
                            if on_progress is not None:
                                on_progress(
                                    ExtractionProgress(
                                        member=member,
                                        bytes_written=bytes_done,
                                        total_bytes_estimated=total_bytes,
                                        members_done=files_done,
                                        members_total=members_total,
                                        member_bytes_written=member_written,
                                        members_extracted=0,
                                        members_blocked=0,
                                    )
                                )
                    ok += 1
                    files_done += 1
                    if on_progress is not None:
                        on_progress(
                            ExtractionProgress(
                                member=member,
                                bytes_written=bytes_done,
                                total_bytes_estimated=total_bytes,
                                members_done=files_done,
                                members_total=members_total,
                                member_bytes_written=member_written,
                                members_extracted=0,
                                members_blocked=0,
                            )
                        )
                    if verbose:
                        print(f"OK   {escape_member_name(member.name)}", file=err)
                except (ArchiveyError, OSError) as exc:
                    failed += 1
                    print(
                        f"FAIL {escape_member_name(member.name)}: "
                        f"{format_error_detail(exc)}",
                        file=err,
                    )
        finally:
            if on_progress is not None:
                on_progress.close()

        # Decided only now: a 7z or RAR4 link's target is member data the pass reads
        # at its end, in either reader mode, so which links need this is known only
        # once the pass is over. A target the pass read was checked by that read, as
        # listing checks a ZIP link's, and the link is skipped like a ZIP link. Only a
        # link the pass could not read a target for is opened again here.
        unverified: list[ArchiveMember] = []
        for link in pending_links:
            if _link_needs_verification(link):
                unverified.append(link)
            elif verbose:
                print(f"skip {escape_member_name(link.name)}", file=err)
        if members_total is not None:
            members_total += len(unverified)
        for link in unverified:
            try:
                _verify_link(reader, link)
            except (ArchiveyError, OSError) as exc:
                failed += 1
                print(
                    f"FAIL {escape_member_name(link.name)}: {format_error_detail(exc)}",
                    file=err,
                )
            else:
                ok += 1
                if verbose:
                    print(f"OK   {escape_member_name(link.name)}", file=err)

        # Without an index, the pass that just ran offered every member to the
        # patterns. Only the generator raising ends that pass early and leaves later
        # members unseen; a failure inside one member's read does not. So the
        # patterns are judged after any pass that reached its end.
        if indexed is None and not pass_ended_early and selection.report(err=err):
            return EXIT_FAIL

        # Read before the reader closes; each such diagnostic was already logged with
        # its reason, so the summary only counts them.
        counts = reader.diagnostics.counts
        not_verified = sum(counts.get(code, 0) for code in _UNVERIFIED_CODES)

    print(
        _test_summary(
            ok=ok,
            failed=failed,
            members_total=members_total,
            not_verified=not_verified,
        ),
        file=err,
    )
    # An untested remainder or an unchecked digest is an incomplete verification.
    not_tested = _not_tested(ok=ok, failed=failed, members_total=members_total)
    return EXIT_FAIL if failed or not_tested or not_verified else EXIT_OK


def _link_needs_verification(member: ArchiveMember) -> bool:
    """Whether ``member`` is a symlink whose stored target has not been read cleanly.

    A ZIP, 7z or RAR4 symlink keeps its target in the member's data. Listing reads and
    checks that data, so a link listed with a target has passed its check. A link
    listed without one either records no target at all (``_link_target_absent``: not
    a fault, and ``extract`` reports it as ``LINK_TARGET_UNAVAILABLE``) or has a
    target that the read could not produce: damaged, encrypted, or out of reach.
    ``extract`` fails the second kind, so ``test`` does too.
    """
    return (
        member.type is MemberType.SYMLINK
        and member.link_target is None
        and not member._link_target_absent
    )


def _verify_link(reader: ArchiveReader, member: ArchiveMember) -> None:
    """Read ``member``'s stored target again, raising what the read raises.

    ``open()`` reads a link's target before it follows the link, and that read raises
    the fault that listing only reported. Once the target is read, the rest is about
    where the link points, not about this member's data: a target outside the archive,
    a directory or a link cycle is not a fault, and a target inside it is verified as
    a member of its own. Those three are the only errors ignored; any other error is
    raised, such as a target member that cannot be opened or a usage error.
    """
    try:
        reader.open(member).close()
    except (ReadError, ArchiveyUsageError) as exc:
        # ``open()`` sets ``link_target`` once it has read the stored target, so a
        # target still ``None`` means the error came from this member's own data.
        if member.link_target is None or not _is_link_destination_error(exc):
            raise


def _is_link_destination_error(exc: ReadError | ArchiveyUsageError) -> bool:
    """Whether ``exc`` is one of the errors link following raises about where a link
    points (``_open_with_link_follow`` in ``base_reader``), not about any data.

    The cycle and the directory have no exception type of their own, so they are told
    apart by the message that function writes.
    """
    if isinstance(exc, LinkTargetNotFoundError):
        return True
    if type(exc) is ReadError:
        return exc.raw_message.startswith("Link cycle detected at ")
    # A link to a directory, an anti-item or an OTHER member: ``open()`` refuses to
    # return bytes for it, as a usage error, after following the link.
    return isinstance(exc, ArchiveyUsageError) and str(exc).endswith("(not a file)")


def _not_tested(*, ok: int, failed: int, members_total: int | None) -> int:
    """Untested remainder of an indexed selection; ``0`` when unknown or none (P8).

    The summary line and the exit code both read this, so they cannot disagree.
    """
    if members_total is None:
        return 0
    return max(members_total - ok - failed, 0)


def _test_summary(
    *, ok: int, failed: int, members_total: int | None, not_verified: int = 0
) -> str:
    """Format the quiet test summary, with the untested remainder (P8) and unchecked
    digests (X6) when there are any.
    """
    summary = f"{ok} OK, {failed} failed"
    not_tested = _not_tested(ok=ok, failed=failed, members_total=members_total)
    if not_tested:
        summary += f", {not_tested} not tested"
    if not_verified:
        summary += f", {not_verified} not verified"
    return summary
