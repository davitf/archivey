"""The optional packages the codecs use, found once and read at call time.

Codec modules read these as ``deps.zstd`` and so on, never ``from deps import zstd``, so a
test that patches a name here reaches every codec.
"""

from __future__ import annotations

import importlib
import importlib.util
from types import ModuleType

from archivey.internal import logs


# Optional packages: resolved once via importlib (rather than static imports) because
# several of these have no type stubs and are absent in the core-only environment. Absence
# becomes a clear PackageNotInstalledError when the corresponding codec is opened.
def _optional(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except (
        ImportError
    ):  # pragma: no cover - the absent path runs in the core-only CI leg
        return None


def _optional_zstd() -> ModuleType | None:
    """Stdlib ``compression.zstd`` (3.14+) or ``backports.zstd`` (older Pythons)."""
    for name in ("compression.zstd", "backports.zstd"):
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    return None


class LazyOptional:
    """An optional package that is found without importing it, and imported on first use.

    On a free-threaded CPython, importing an extension module that has not declared
    free-thread support re-enables the GIL for the whole process. pyppmd, inflate64,
    brotli and rapidgzip are such modules (measured 2026-10-08 on 3.13t, 3.14t and
    3.15t). Importing them when this module loads would re-enable the GIL for every
    program that imports archivey with them installed, even one that never opens a
    stream they decode. So :meth:`available` only looks the package up, and the import
    waits for :meth:`load`, which a codec calls when it opens a stream that needs it.
    zstd and lz4 keep the GIL disabled and stay eager imports.

    There is no lock: two threads in :meth:`load` at once both get the same module
    (the import system serialises the import), and every field write stores the value
    the other thread would store, so a lost update changes nothing. A new field that is
    not repeat-safe in that way needs a lock.
    """

    def __init__(self, name: str, *, present: bool | None = None) -> None:
        self.name = name
        # ``present=`` overrides the lookup, for tests: ``False`` stands for an absent
        # package, ``True`` for an installed one.
        self._present = present
        self._module: ModuleType | None = None
        self._import_failed = False

    def available(self) -> bool:
        """Whether the package is installed. Does not import it."""
        if self._present is None:
            try:
                self._present = importlib.util.find_spec(self.name) is not None
            except (ImportError, ValueError):
                self._present = False
        return self._present

    def load(self) -> ModuleType | None:
        """The imported package, or ``None`` if it is absent or fails to import.

        A package that is found but fails to import (a broken wheel, a missing shared
        library) is logged once and then treated as absent, as it was when these were
        imported with this module. Any exception counts: the import runs inside a codec
        open, where an untranslated error from another package would be a surprise.
        """
        if self._module is None and not self._import_failed and self.available():
            try:
                self._module = importlib.import_module(self.name)
            except Exception as exc:  # noqa: BLE001 - any import failure means absent
                self._import_failed = True
                logs.streams.warning(
                    "%r is installed but failed to import (%r); archivey reads as "
                    "if it were absent.",
                    self.name,
                    exc,
                )
        return self._module

    def loaded(self) -> ModuleType | None:
        """The package if it has been imported already, else ``None``. Never imports.

        For exception translation: an exception from a package that was never
        imported cannot be one of that package's own types.
        """
        return self._module


zstd = _optional_zstd()
lz4_frame = _optional("lz4.frame")
lz4_block = _optional("lz4.block")
brotli = LazyOptional("brotli")
pyppmd = LazyOptional("pyppmd")
inflate64 = LazyOptional("inflate64")
# rapidgzip runs in a child process for every codec it decodes (gzip, zlib, raw deflate
# and bzip2), so this process only needs to know it is installed. bzip2 uses rapidgzip's
# bundled IndexedBzip2File, never the separate indexed_bzip2 package: the two loaded into
# one process corrupt the heap on macOS (ADR 0008).
rapidgzip = LazyOptional("rapidgzip")
