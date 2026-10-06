"""Open an archive and extract all of it in one call, for tests that only care about
extraction.

The library has no one-shot ``extract()`` (ADR 0019): callers write
``with open_archive(source) as reader: reader.extract_all(dest)``. This helper is that
pair of lines, so a test that checks what extraction did doesn't repeat it. Open
arguments go to :func:`archivey.open_archive` and everything else to
:meth:`~archivey.ForwardArchiveReader.extract_all`, so the report's diagnostics are
extraction-only, as a caller's would be.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from archivey import ArchiveFormat, ArchiveyConfig, ExtractionReport, open_archive


def open_and_extract(
    source: Any,
    dest: str | Path,
    *,
    format: ArchiveFormat | str | None = None,
    streaming: bool = False,
    password: Any = None,
    encoding: str | None = None,
    config: ArchiveyConfig | None = None,
    **extract_kwargs: Any,
) -> ExtractionReport:
    with open_archive(
        source,
        format=format,
        streaming=streaming,
        password=password,
        encoding=encoding,
        config=config,
    ) as reader:
        return reader.extract_all(dest, **extract_kwargs)
