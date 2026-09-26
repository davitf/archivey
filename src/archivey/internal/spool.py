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
from archivey.exceptions import ResourceLimitError
from archivey.types import ArchiveFormat

# Each read from a SharedSource view takes the source lock and seeks, so a large
# chunk keeps the number of lock acquisitions low (copyfileobj's 64 KiB default is
# about 16 times as many).
_COPY_CHUNK = 1 << 20


class SpoolBudget:
    """The spool allowance of one reader, spent as bytes are written.

    Call :meth:`check_total` with the size of everything about to be written when it
    is known, so an oversized source is refused before the first byte is written. Then
    copy each piece with :meth:`copy`, which stops before the written total crosses the
    limit even when a size was wrong or not known. The caller removes what it wrote
    when either one raises.
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

    def check_total(self, total: int | None) -> None:
        """Refuse before writing when the known ``total`` is over the limit."""
        if self._limit is not None and total is not None and total > self._limit:
            raise self._error(f"{total} bytes")

    def copy(self, src: BinaryIO, out: BinaryIO) -> None:
        """Copy ``src`` to ``out`` until EOF, within what the limit has left."""
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
                raise self._error(f"more than {limit} bytes")
            out.write(chunk)
            self._written += len(chunk)

    def _error(self, size: str) -> ResourceLimitError:
        return ResourceLimitError(
            f"Spool limit reached: {self._what}, and the copy would be {size}, over "
            f"SpoolLimits.max_bytes={self._limit} (ArchiveyConfig.spool_limits). "
            f"Open the archive from a file path, which is read in place, or raise "
            f"the limit (None removes it).",
            archive_name=self._archive_name,
            source_format=self._source_format,
        )
