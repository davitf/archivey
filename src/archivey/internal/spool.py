"""Bounded copies of an archive source to temporary storage.

Some backends cannot read the caller's stream in place: ``unrar`` takes a filesystem
path, so a RAR opened from a ``BytesIO`` is copied to a temp file before ``unrar`` can
serve a member. Every such copy goes through :class:`SpoolBudget`, which enforces
:attr:`archivey.config.SpoolLimits.max_bytes` across everything one reader spools.
"""

from __future__ import annotations

import shutil
from typing import BinaryIO

from archivey.config import SpoolLimits
from archivey.exceptions import SpoolLimitExceededError
from archivey.types import ArchiveFormat

# Each read from a SharedSource view takes the source lock and seeks, so a large
# chunk keeps the number of lock acquisitions low (copyfileobj's 64 KiB default is
# about 16 times as many).
_COPY_CHUNK = 1 << 20


class SpoolBudget:
    """The spool allowance of one reader, spent as bytes are written.

    A reader holds one budget for its lifetime, so the limit weighs every byte the
    reader copies, across attempts: a copy that failed part-way still counts. Call
    :meth:`check_total` with the size of everything about to be written when it is
    known, so an oversized source is refused before the first byte is written. Then
    copy each piece with :meth:`copy`, which stops before the written total crosses
    the limit even when a size was wrong or not known. The caller removes what it
    wrote when either one raises.

    Once the budget has refused, every later call refuses at once without writing.
    Without that, a source of unknown size would cost a full ``max_bytes`` write on
    each read that retried the copy.
    """

    def __init__(
        self,
        limits: SpoolLimits,
        *,
        what: str,
        archive_name: str | None,
        source_format: ArchiveFormat,
    ) -> None:
        self._limit = limits.max_bytes
        self._what = what
        self._archive_name = archive_name
        self._source_format = source_format
        self._written = 0
        # The size named by the first refusal, repeated by every later one.
        self._refused: str | None = None

    def check_total(self, total: int | None) -> None:
        """Refuse before writing when the known ``total`` would pass the limit.

        ``total`` is what this attempt is about to write; bytes an earlier attempt
        wrote count against the limit as well.
        """
        self._check_not_refused()
        if (
            self._limit is not None
            and total is not None
            and self._written + total > self._limit
        ):
            raise self._refuse(f"{total} bytes")

    def copy(self, src: BinaryIO, out: BinaryIO) -> None:
        """Copy ``src`` to ``out`` until EOF, within what the limit has left."""
        self._check_not_refused()
        limit = self._limit
        if limit is None:
            shutil.copyfileobj(src, out, length=_COPY_CHUNK)
            return
        while True:
            remaining = limit - self._written
            # One byte past the allowance tells "exactly at the limit" apart from
            # "over it" without writing anything past the limit.
            chunk = src.read(min(_COPY_CHUNK, remaining + 1))
            if not chunk:
                return
            if len(chunk) > remaining:
                raise self._refuse(f"more than {limit} bytes")
            out.write(chunk)
            self._written += len(chunk)

    def _check_not_refused(self) -> None:
        if self._refused is not None:
            raise self._error(self._refused)

    def _refuse(self, size: str) -> SpoolLimitExceededError:
        self._refused = size
        return self._error(size)

    def _error(self, size: str) -> SpoolLimitExceededError:
        return SpoolLimitExceededError(
            f"Spool limit reached: {self._what}, and the copy would be {size}, over "
            f"SpoolLimits.max_bytes={self._limit} (ArchiveyConfig.spool_limits). "
            f"Open the archive from a file path, which is read in place, or raise "
            f"the limit (None removes it).",
            archive_name=self._archive_name,
            source_format=self._source_format,
        )
