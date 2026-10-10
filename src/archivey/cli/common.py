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
from archivey.config import PasswordInput
from archivey.exceptions import StreamNotSeekableError

# The one internal import in the CLI, allowlisted in tests/test_cli_uses_public_api.py:
# --track-io is a debugging aid for the library, and IO measurement is not public API.
from archivey.internal.measurement import enable_measurement, io_stats
from archivey.reader import ForwardArchiveReader


def reject_stdin_token(archive: str) -> None:
    """Fail fast when ``-`` is used (stdin archives reserved, not supported)."""
    if archive == "-":
        # Grammar-level "not available yet" → usage exit (D7), matching reserved verbs.
        raise CliError(
            "stdin archives are not supported yet (the '-' token is reserved)",
            code=EXIT_USAGE,
        )


def reject_salvage(salvage: bool) -> None:
    if salvage:
        raise CliError("--salvage is not implemented yet", code=EXIT_USAGE)


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
        if not streaming:
            raise
        raise CliError(read_once_refusal(archive, exc)) from exc


def is_read_once(archive: str | Path) -> bool:
    """Whether ``archive`` is a FIFO, a character device or a socket.

    These are the paths that ``ArchiveSource.for_path`` treats as non-seekable: a
    second open reads different bytes, or waits for a writer that never comes. A block
    device rereads fine. A path that cannot be stat'ed is not read-once; the open
    reports its error.
    """
    try:
        mode = os.stat(archive).st_mode
    except OSError:
        return False
    return stat.S_ISFIFO(mode) or stat.S_ISCHR(mode) or stat.S_ISSOCK(mode)


def read_once_refusal(archive: str | Path, exc: StreamNotSeekableError) -> str:
    """The CLI message for a read-once path whose format needs to seek.

    The library's own message suggests ``streaming=True`` or a ``BytesIO``, which a CLI
    user cannot pass; this one names what the user can do.
    """
    fmt = exc.source_format.display_name if exc.source_format else "this format"
    return (
        f"{archive}: {fmt} cannot be read from a pipe or device. {fmt} needs to seek, "
        f"and a pipe or device can be read only once. Copy the archive to a regular "
        f"file first, then run archivey on that file."
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
