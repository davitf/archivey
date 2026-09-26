"""Find and identify an external decompressor on ``PATH``, once per binary.

A program on ``PATH`` with the right name is not proof that it is the right program, so
each candidate runs once with an identification argv, and its banner decides. The
answer is cached by absolute path and stat identity, so later reads do not spawn the
probe again, and a binary replaced on disk is probed afresh.

This is the policy :func:`archivey.internal.backends.rar_unrar.find_rarlab_unrar`
applies to RARLAB ``unrar``, written once for any program. That finder shares
:func:`stat_identity` with this module, whose :func:`terminate_process` the ``unrar``
read paths also use, but it still runs its own loop and cache; moving it onto
:class:`CliToolFinder` is recorded in ``dev-docs/IDEAS.md``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from archivey.exceptions import PackageNotInstalledError
from archivey.terminal import display_path

# Seconds an identification probe may run. A binary that has not printed its banner by
# then is remembered as unusable (see ``CliToolFinder``).
PROBE_TIMEOUT_SECONDS: float = 10


@dataclass(frozen=True, slots=True)
class Banner:
    """What one identification banner says: the right program or not, and its version."""

    identified: bool
    version: tuple[int, ...] | None


@dataclass(frozen=True, slots=True)
class _Probe:
    banner: Banner
    identity: tuple[int, int, int, int]
    timed_out: bool


def stat_identity(path: str) -> tuple[int, int, int, int]:
    """``(st_dev, st_ino, st_mtime_ns, st_size)``: the identity a replaced binary cannot keep."""
    st = os.stat(path)
    return (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)


class CliToolFinder:
    """Locate one program on ``PATH`` and accept it only if its banner identifies it.

    ``names`` are tried in order; the first candidate that identifies and meets
    ``version_floor`` wins. ``parse_banner`` receives stdout and stderr of
    ``<candidate> <probe_args>`` decoded as UTF-8 with replacement.

    Cache rules (the same as the ``unrar`` finder):

    - A durable answer is stored per absolute path with the binary's stat identity, so a
      lookalike costs one process per process lifetime, not one per member read.
    - A probe that runs out of time is stored as unusable. Re-probing would cost the
      full timeout on every read; the refusal names the binary and the timeout, so the
      cause stays visible. A replaced binary changes the stat identity and is probed
      again.
    - A ``which`` miss and a probe that could not *start* the binary (``OSError``) are
      not stored, so a program installed later is found without a restart.
    """

    def __init__(
        self,
        *,
        display_name: str,
        names: Sequence[str],
        probe_args: Sequence[str],
        parse_banner: Callable[[str], Banner],
        version_floor: tuple[int, ...],
        install_hint: str,
    ) -> None:
        self._display_name = display_name
        self._names = tuple(names)
        self._probe_args = tuple(probe_args)
        self._parse_banner = parse_banner
        self._version_floor = version_floor
        self._install_hint = install_hint
        self._cache: dict[str, _Probe] = {}
        self._lock = threading.Lock()

    def clear_cache(self) -> None:
        """Forget every probe answer. For tests that swap binaries on ``PATH``."""
        with self._lock:
            self._cache.clear()

    def _floor_text(self) -> str:
        return ".".join(str(part) for part in self._version_floor)

    def _probe(self, path: str) -> Banner:
        completed = subprocess.run(
            [path, *self._probe_args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
        )
        text = ((completed.stdout or b"") + (completed.stderr or b"")).decode(
            "utf-8", errors="replace"
        )
        return self._parse_banner(text)

    def _meets_floor(self, banner: Banner) -> bool:
        return (
            banner.identified
            and banner.version is not None
            and banner.version >= self._version_floor
        )

    def find(self, *, purpose: str) -> str:
        """Return the absolute path of a usable binary, or raise ``PackageNotInstalledError``.

        ``purpose`` completes "<program> is required …" in the refusal, so the caller
        can say which read needed the program.
        """
        # Sample PATH once, so a concurrent ``os.environ`` rewrite cannot mix two values.
        path_env = os.environ.get("PATH", "")
        too_old: list[tuple[str, tuple[int, ...] | None]] = []
        timed_out: list[str] = []
        cause: BaseException | None = None
        for name in self._names:
            candidate = shutil.which(name, path=path_env)
            if candidate is None:
                continue
            candidate = os.path.abspath(candidate)
            try:
                identity = stat_identity(candidate)
            except OSError as exc:
                cause = exc
                continue
            with self._lock:
                cached = self._cache.get(candidate)
            if cached is None or cached.identity != identity:
                probe_timed_out = False
                try:
                    banner = self._probe(candidate)
                except subprocess.TimeoutExpired as exc:
                    cause = exc
                    probe_timed_out = True
                    banner = Banner(identified=False, version=None)
                except (OSError, subprocess.SubprocessError) as exc:
                    cause = exc
                    continue
                cached = _Probe(banner, identity, probe_timed_out)
                with self._lock:
                    self._cache[candidate] = cached
            if self._meets_floor(cached.banner):
                return candidate
            if cached.banner.identified:
                too_old.append((candidate, cached.banner.version))
            elif cached.timed_out:
                timed_out.append(candidate)
        raise PackageNotInstalledError(
            self._refusal(purpose, too_old, timed_out)
        ) from cause

    def _refusal(
        self,
        purpose: str,
        too_old: list[tuple[str, tuple[int, ...] | None]],
        timed_out: list[str],
    ) -> str:
        name = self._display_name
        if too_old:
            found = "; ".join(
                f"{display_path(path)} reports version "
                + ".".join(str(part) for part in version)
                if version is not None
                else f"the version of {display_path(path)} could not be parsed"
                for path, version in too_old
            )
            return (
                f"{name} {self._floor_text()} or later is required {purpose}, but "
                f"{found}. {self._install_hint}"
            )
        if timed_out:
            shown = " and ".join(display_path(path) for path in timed_out)
            return (
                f"{name} is required {purpose}. Found {shown} on PATH, but it did not "
                f"answer the identification probe within {PROBE_TIMEOUT_SECONDS:g} "
                "seconds. archivey does not probe an unchanged binary again in this "
                f"process. {self._install_hint}"
            )
        return (
            f"{name} is required {purpose}, but it was not found on PATH (or the "
            f"program found under that name is not {name}). {self._install_hint}"
        )


def terminate_process(proc: subprocess.Popen[bytes] | None) -> None:
    """Stop a child process if it is still running, and reap it."""
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
