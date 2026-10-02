"""How a data pass treats members whose bytes are a copy of an earlier member's."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from archivey.types import ArchiveMember


@dataclass(frozen=True)
class FileCopyPass:
    """How a data pass treats a member whose bytes are a copy of an earlier member's.

    Only RAR has such members (a RAR5 file copy, ``extra["is_file_copy"]``); every
    other backend ignores this. ``streams=False`` yields ``None`` for each copy, and the
    pass keeps nothing for them (``stream_members(file_copy_streams=False)``).
    ``keep_source``, when set, is asked about each source the pass would keep, when the
    source's first byte is decoded; ``False`` leaves it unkept, and a copy that is then
    read decodes it again. Extraction answers ``False`` for a source it is writing to
    disk, because it copies that source's copies from the written file.
    """

    streams: bool = True
    keep_source: Callable[[ArchiveMember], bool] | None = None


DEFAULT_FILE_COPY_PASS = FileCopyPass()
