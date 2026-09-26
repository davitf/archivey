"""Run ``unar`` (The Unarchiver's XADMaster command-line tool) for member bytes on stdout.

Format-agnostic. The caller names the archive by path and the entries by ``unar``'s own
entry indexes, in the order ``lsar`` lists them. This module does not decide which
archives ``unar`` reads correctly; a backend refuses those before it calls here
(see :mod:`archivey.internal.backends.rar_unar` for RAR).

The argv is fixed except for the archive path and the optional index list:

``unar -o - -q -nr -k skip [-i] -- <absolute archive path> [index …]``

- ``-o -`` writes every selected entry to stdout, concatenated in archive order with no
  framing, and creates no file.
- ``-nr`` stops ``unar`` from opening an archive found inside the archive and emitting
  *its* contents instead (a ``.tar.gz`` member would otherwise come out as tar members).
- ``-k skip`` drops Mac OS resource forks, which would otherwise reach stdout as extra
  bytes after the data fork.
- ``-i`` makes every trailing argument an entry index; without it, every entry is
  selected. Indexes are decimal numbers built
  here, so a hostile entry name never reaches the argv; there is no include mask to
  match siblings, and no glob to backtrack on.
- ``--`` ends option parsing and the archive path is absolute, so a path that starts
  with ``-`` cannot read as an option.

Passwords are not supported. ``unar`` accepts one only as ``-p <password>`` on its
command line, where any local user can read it from the process list, and it answers a
wrong password with exit 0 and no output. A caller that needs a password uses a
different program.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO, cast

from archivey.exceptions import CorruptionError, PackageNotInstalledError, ReadError
from archivey.internal.external.cli import Banner, CliToolFinder, terminate_process
from archivey.internal.streams.streamtools import DelegatingStream

# Inclusive floor. Measured: Debian/Ubuntu ``unar`` 1.10.1 and MacPaw XADMaster v1.10.8
# built from source (banner v1.10.7). Homebrew's formula builds the same v1.10.8.
UNAR_VERSION_FLOOR: tuple[int, int] = (1, 10)

# ``unar -h`` opens with ``unar v1.10.1, a tool for extracting the contents of archive
# files.`` (Debian). Homebrew's build adds its build date: ``unar v1.10.7 (Oct 10
# 2023), a tool for extracting …``. The digit runs are bounded so ``int()`` cannot hit
# CPython's digit limit.
_UNAR_BANNER_RE = re.compile(
    r"^unar v(\d{1,4})\.(\d{1,4})(?:\.(\d{1,4}))?(?: \([^)\n]{0,64}\))?, "
    r"a tool for extracting",
    re.MULTILINE,
)

UNAR_INSTALL_HINT = (
    "Install unar 1.10 or later (Homebrew: brew install unar; Debian and Ubuntu: "
    "apt install unar)."
)


def parse_unar_banner(text: str) -> Banner:
    """Classify the output of ``unar -h``."""
    match = _UNAR_BANNER_RE.search(text)
    if match is None:
        return Banner(identified=False, version=None)
    parts = tuple(int(group) for group in match.groups() if group is not None)
    return Banner(identified=True, version=parts)


_finder = CliToolFinder(
    display_name="unar",
    names=("unar",),
    probe_args=("-h",),
    parse_banner=parse_unar_banner,
    version_floor=UNAR_VERSION_FLOOR,
    install_hint=UNAR_INSTALL_HINT,
)


def find_unar(*, purpose: str) -> str:
    """Absolute path of ``unar`` 1.10+ on ``PATH``, or ``PackageNotInstalledError``."""
    return _finder.find(purpose=purpose)


def clear_unar_cache() -> None:
    """Forget the identification answers. For tests that swap ``unar`` on ``PATH``."""
    _finder.clear_cache()


def unar_argv(
    unar: str, archive_path: str | Path, indexes: Sequence[int] | None
) -> list[str]:
    """The complete argv for one ``unar`` stdout run. See the module docstring.

    ``indexes=None`` selects every entry. An empty sequence is refused: ``unar -i`` with
    no index also selects every entry, which is never what an empty selection means.
    """
    cmd = [unar, "-o", "-", "-q", "-nr", "-k", "skip"]
    selected: list[str] = []
    if indexes is not None:
        if not indexes:
            raise ValueError(
                "unar_argv needs at least one entry index, or None for all"
            )
        if any(index < 0 for index in indexes):
            raise ValueError("unar entry indexes are non-negative")
        cmd.append("-i")
        selected = [str(index) for index in indexes]
    return [*cmd, "--", str(Path(archive_path).absolute()), *selected]


def open_unar_stdout(
    archive_path: str | Path, indexes: Sequence[int] | None, *, purpose: str
) -> tuple[subprocess.Popen[bytes], BinaryIO]:
    """Spawn ``unar`` for the given entries (``None``: all) and return ``(proc, stdout)``.

    The caller owns the process: wrap ``stdout`` in :class:`UnarOutputStream` at once,
    or call :func:`~archivey.internal.external.cli.terminate_process` on failure.

    Reading ``stdout`` has no time bound, for the reason ``open_unrar_p`` gives: a solid
    archive legitimately produces no bytes while earlier entries decode.
    """
    unar = find_unar(purpose=purpose)
    cmd = unar_argv(unar, archive_path, indexes)
    try:
        proc = subprocess.Popen(
            cmd,
            # ``unar`` asks for a missing password on a terminal. With no stdin it
            # reports the missing password and exits non-zero instead.
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=1024 * 1024,
        )
    except OSError as exc:
        raise PackageNotInstalledError(
            f"unar could not be started {purpose}. {UNAR_INSTALL_HINT}"
        ) from exc
    if proc.stdout is None:
        terminate_process(proc)
        raise ReadError("unar produced no stdout pipe")
    return proc, cast(BinaryIO, proc.stdout)


class UnarOutputStream(DelegatingStream):
    """``unar`` stdout that owns the process: close stops and reaps it.

    The exit status is checked once the pipe reaches end of file: on that read, or on
    close if the child had not exited by then. ``unar`` exits 0 on success and 1 or 2
    on a failure it noticed (bad data, missing password). A negative status is a
    signal, and before end of file it is a crash, which ``unar`` 1.10.1 does on some
    archives. A close before end of file checks nothing: archivey closed the pipe on a
    child that was still writing, so whatever status follows is archivey's doing.

    ``unar`` also returns exit 0 with missing or short output on some archives, so exit
    status is not the integrity check. The caller verifies the declared size and any
    stored digest of every entry it reads; that is what catches short or wrong bytes.
    ``has_verifiable_digest`` says the caller does that for every byte of this pipe, and
    then a failure status is not reported: the digest check has already decided.
    """

    readinto_passthrough = False
    _SUBCLASS_CLOSES_INNER = True

    def __init__(
        self,
        stdout: BinaryIO,
        proc: subprocess.Popen[bytes],
        *,
        has_verifiable_digest: bool,
    ) -> None:
        # Everything close() reads is assigned before DelegatingStream.__init__,
        # which can raise, so a half-built instance still reaps the child.
        self._proc = proc
        self._has_verifiable_digest = has_verifiable_digest
        self._saw_eof = False
        self._exit_checked = False
        super().__init__(stdout)

    def read(self, n: int = -1, /) -> bytes:
        data = super().read(n)
        if not data and n != 0:
            self._saw_eof = True
            self._check_exit(wait_timeout=1.0)
        return data

    def _raise_for_returncode(self, rc: int) -> None:
        if rc == 0 or self._has_verifiable_digest:
            return
        if rc > 0:
            raise CorruptionError(f"unar reported a failure (exit {rc}) reading data")
        raise ReadError(f"unar stopped on signal {-rc} while reading data")

    def _check_exit(self, *, wait_timeout: float | None) -> None:
        if self._exit_checked or not self._saw_eof:
            return
        if self._proc.poll() is None:
            if wait_timeout is None:
                return
            try:
                self._proc.wait(timeout=wait_timeout)
            except subprocess.TimeoutExpired:
                return
        self._exit_checked = True
        self._raise_for_returncode(self._proc.returncode)

    def close(self) -> None:
        if self.closed:
            return
        close_error: BaseException | None = None
        try:
            self._inner.close()
        except BaseException as exc:  # noqa: BLE001 - close must reap unar even on KeyboardInterrupt
            close_error = exc
        if self._proc.poll() is None:
            terminate_process(self._proc)
        else:
            try:
                self._proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                terminate_process(self._proc)
        super().close()
        try:
            self._check_exit(wait_timeout=None)
        except BaseException as mapped:  # noqa: BLE001 - chain onto inner.close(), do not replace it
            if close_error is not None:
                raise close_error from mapped
            raise
        if close_error is not None:
            raise close_error
