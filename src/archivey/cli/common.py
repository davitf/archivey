"""Shared open / track-io helpers for CLI verbs."""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TextIO

from archivey import open_archive
from archivey.cli.errors import CliError
from archivey.cli.exit_codes import EXIT_USAGE
from archivey.cli.format import format_format_label
from archivey.config import PasswordInput
from archivey.exceptions import StreamNotSeekableError

# The one internal import in the CLI, allowlisted in tests/test_cli_uses_public_api.py:
# --track-io is a debugging aid for the library, and IO measurement is not public API.
from archivey.internal.measurement import enable_measurement, io_stats
from archivey.reader import ForwardArchiveReader
from archivey.terminal import display_path


def reject_stdin_token(archive: str) -> None:
    """Fail fast when ``-`` is used: the token is reserved, not supported.

    A pipe on stdin is still readable through its path, ``/dev/stdin``, which
    :func:`is_read_once` treats as any other pipe, so the message names that path.
    Windows has no such path, so there the message says to copy the archive to a file.

    Also refuses the empty string, through :func:`reject_empty_path`. Call it on the
    string the user typed, before any ``Path()``: ``Path("")`` is ``Path(".")``.
    """
    reject_empty_path(archive, arg="archive")
    if archive == "-":
        # Grammar-level "not available yet" → usage exit (D7), matching reserved verbs.
        if sys.platform == "win32":
            hint = "copy the archive to a regular file and pass its path"
        else:
            hint = "to read an archive piped on stdin, pass /dev/stdin instead"
        raise CliError(
            f"the '-' token for stdin is reserved and not supported yet; {hint}",
            code=EXIT_USAGE,
        )


def reject_empty_path(value: str, *, arg: str) -> None:
    """Refuse an empty path argument as a usage error.

    ``Path("")`` is ``Path(".")``, so an unset shell variable (``"$ARCHIVE"``,
    ``-d "$OUT"``) would otherwise read or extract into the working directory. The
    library refuses an empty string too, but with ``ValueError``, and the CLI turns
    the path into a ``Path`` before the library sees it; ``.`` names the cwd on purpose.
    """
    if value == "":
        raise CliError(
            f"{arg} is an empty path; pass '.' to mean the current directory",
            code=EXIT_USAGE,
        )


@contextmanager
def open_for_cli(
    archive: str | Path,
    *,
    password: PasswordInput = None,
    track_io: bool = False,
    err: TextIO | None = None,
) -> Iterator[ForwardArchiveReader]:
    """Open an archive, optionally wrapping the call in measurement for ``--track-io``.

    A path that can be read only once (see :func:`is_read_once`) opens in streaming
    mode: the user cannot choose the mode, so the CLI chooses the one that can work.
    Each verb then reads the archive in one forward pass.
    """
    reject_stdin_token(str(archive))
    err = err if err is not None else sys.stderr
    streaming = is_read_once(archive)
    if track_io:
        with enable_measurement():
            with _open(archive, password=password, streaming=streaming) as reader:
                yield reader
                _report_track_io(reader, err)
    else:
        with _open(archive, password=password, streaming=streaming) as reader:
            yield reader


def _open(
    archive: str | Path, *, password: PasswordInput, streaming: bool
) -> ForwardArchiveReader:
    try:
        return open_archive(archive, password=password, streaming=streaming)
    except StreamNotSeekableError as exc:
        message = read_once_refusal(archive, exc, streaming=streaming)
        if message is None:
            raise
        raise CliError(message) from exc


def is_read_once(archive: str | Path) -> bool:
    """Whether ``archive`` is a FIFO, a character device or a socket.

    These are the paths that ``ArchiveSource.for_path`` treats as non-seekable: a
    second open reads different bytes, or waits for a writer that never comes. They
    include ``/dev/stdin`` and ``/proc/self/fd/N`` when that descriptor is a pipe. A
    block device rereads fine. A path that cannot be stat'ed is not read-once; the open
    reports its error.
    """
    try:
        mode = os.stat(archive).st_mode
    except OSError:
        return False
    return stat.S_ISFIFO(mode) or stat.S_ISCHR(mode) or stat.S_ISSOCK(mode)


def read_once_refusal(
    archive: str | Path, exc: BaseException, *, streaming: bool
) -> str | None:
    """The CLI message for a read-once path whose format needs to seek, or ``None``.

    ``None`` unless the open was in streaming mode and raised
    ``StreamNotSeekableError``: any other failure keeps its own message. The library's
    message suggests ``streaming=True`` or a ``BytesIO``, which a CLI user cannot pass;
    this one names what the user can do. The one place that makes this decision:
    ``open_for_cli`` raises the message as a ``CliError``, and ``info`` prints it as its
    ``open:`` field.

    The path is ``/``-separated here, because the caller escapes the whole message and
    an escaped native Windows path would have every separator doubled.
    """
    if not streaming or not isinstance(exc, StreamNotSeekableError):
        return None
    fmt = exc.source_format
    subject = (
        f"the {format_format_label(fmt)} format"
        if fmt is not None
        else "this archive's format"
    )
    return (
        f"{display_path(archive)}: {subject} cannot be read from a pipe or device. "
        f"It needs to seek, and a pipe or device can be read only once. Copy the "
        f"archive to a regular file first, then run archivey on that file."
    )


def _report_track_io(reader: ForwardArchiveReader, err: TextIO) -> None:
    stats = io_stats(reader)
    if stats is None:
        print("track-io: counters unavailable for this reader", file=err)
        return
    consumed_s = (
        "-"
        if stats.compressed_bytes_consumed is None
        else str(stats.compressed_bytes_consumed)
    )
    print(
        "track-io:"
        f" bytes_decompressed={stats.bytes_decompressed}"
        f" compressed_bytes_consumed={consumed_s}"
        f" source_seek_count={stats.source_seek_count}",
        file=err,
    )
