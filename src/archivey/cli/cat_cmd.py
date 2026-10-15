"""``cat`` verb — member contents, or the decompressed outer stream, to stdout."""

from __future__ import annotations

import os
import sys
from contextlib import closing
from typing import BinaryIO, TextIO

from archivey import ContainerFormat, open_stream
from archivey.cli.common import closable, open_for_cli, reject_stdin_token
from archivey.cli.errors import CliError
from archivey.cli.exit_codes import EXIT_FAIL, EXIT_OK, EXIT_USAGE
from archivey.cli.filters import MemberSelection
from archivey.cli.format import (
    escape_member_name,
    format_error_detail,
    format_format_label,
)
from archivey.cli.password import resolve_password
from archivey.config import ArchiveyConfig, PasswordInput
from archivey.exceptions import ArchiveyError, FormatDetectionError
from archivey.terminal import display_path

# Bytes copied per read: large enough that the copy costs little per call, small
# enough that one chunk held in memory does not matter.
_CHUNK = 1024 * 1024


def run_cat(
    *,
    archive: str,
    patterns: list[str],
    exclude: list[str],
    outer: bool,
    verbose: bool,
    password: str | None,
    track_io: bool,
    sink: BinaryIO,
    stdin: BinaryIO | None = None,
    config: ArchiveyConfig | None = None,
    err: TextIO | None = None,
) -> int:
    """Write member contents, or with ``outer`` the decompressed stream, to ``sink``.

    ``archive`` ``-`` reads from ``stdin``. Bytes reach ``sink`` as they are read, so a
    digest mismatch is found after the member's bytes were written: the run then exits
    ``1`` with the error on ``err``, as ``zcat`` does.
    """
    err = err if err is not None else sys.stderr
    if outer:
        if patterns or exclude:
            raise CliError(
                "--outer writes the whole decompressed stream; it takes no member "
                "patterns",
                code=EXIT_USAGE,
            )
        if track_io:
            raise CliError(
                "--track-io counts member reads; it does not apply with --outer",
                code=EXIT_USAGE,
            )
        return _cat_outer(archive, sink=sink, stdin=stdin, config=config)
    return _cat_members(
        archive,
        patterns=patterns,
        exclude=exclude,
        verbose=verbose,
        password=password,
        track_io=track_io,
        sink=sink,
        stdin=stdin,
        config=config,
        err=err,
    )


def _cat_outer(
    archive: str,
    *,
    sink: BinaryIO,
    stdin: BinaryIO | None,
    config: ArchiveyConfig | None,
) -> int:
    if archive == "-" and stdin is not None:
        source: str | BinaryIO = stdin
        shown = "stdin"
    else:
        reject_stdin_token(archive)
        source = archive
        shown = display_path(archive)
        if os.path.isdir(archive):
            raise CliError(
                f"--outer: {shown} is a directory, which has no compression layer",
                code=EXIT_USAGE,
            )
    try:
        stream = open_stream(source, config=config)
    except FormatDetectionError as exc:
        fmt = exc.source_format
        if fmt is None or fmt.container is ContainerFormat.RAW_STREAM:
            raise
        # Detection found a container (ZIP, 7z, a plain tar): the library's message
        # names open_archive, which a CLI user reaches by leaving the flag out.
        raise CliError(
            f"--outer: {shown} is a {format_format_label(fmt)} archive, which has no "
            "compression layer to remove; leave out --outer to write its members"
        ) from exc
    with stream:
        _copy(stream, sink)
    return EXIT_OK


def _cat_members(
    archive: str,
    *,
    patterns: list[str],
    exclude: list[str],
    verbose: bool,
    password: str | None,
    track_io: bool,
    sink: BinaryIO,
    stdin: BinaryIO | None,
    config: ArchiveyConfig | None,
    err: TextIO,
) -> int:
    pwd: PasswordInput = resolve_password(password)
    selection = MemberSelection(patterns, exclude)
    pass_ended = True
    with open_for_cli(
        archive,
        password=pwd,
        track_io=track_io,
        config=config,
        stdin=stdin,
        err=err,
    ) as reader:
        indexed = reader.members_report_if_available()
        if indexed is not None:
            # Judge the patterns before writing anything when the index is free, so
            # a pattern that matches nothing fails before any bytes reach stdout.
            selection.settle_from(indexed, err=err)
            if selection.settled and selection.selects_nothing:
                return EXIT_FAIL
        # ``closing`` ends the pass here, whatever leaves the loop (a member error,
        # a closed stdout), rather than leaving it suspended for ``reader.close()``.
        with closing(closable(reader.stream_members(selection.predicate))) as it:
            for member, stream in it:
                if stream is None:
                    # Directories, links and other entries have no body of their
                    # own. A pass reads forward only, so a link's target, an earlier
                    # member, cannot be read again for it.
                    if verbose:
                        print(f"skip {escape_member_name(member.name)}", file=err)
                    continue
                if verbose:
                    print(escape_member_name(member.name), file=err)
                try:
                    with stream:
                        _copy(stream, sink)
                except BrokenPipeError:
                    # stdout's reader left: main() exits 141 for it.
                    raise
                except (ArchiveyError, OSError) as exc:
                    # The concatenated output is already wrong past this point, so
                    # stop rather than go on writing the next members after it.
                    print(
                        f"archivey: {escape_member_name(member.name)}: "
                        f"{format_error_detail(exc)}",
                        file=err,
                    )
                    pass_ended = False
                    break
        if not pass_ended:
            return EXIT_FAIL
        if not selection.settled and selection.report(err=err):
            return EXIT_FAIL
    return EXIT_OK


def _copy(stream: BinaryIO, sink: BinaryIO) -> None:
    while chunk := stream.read(_CHUNK):
        sink.write(chunk)
    sink.flush()
