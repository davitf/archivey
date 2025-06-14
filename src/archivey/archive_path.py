"""Pathlib-compatible access to archives."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, Optional

from archivey.core import open_archive
from archivey.exceptions import ArchiveMemberNotFoundError
from archivey.formats import detect_archive_format
from archivey.types import ArchiveFormat


class ArchivePath(Path):
    """`Path` subclass that treats archive files as directories."""

    __slots__ = ("_archive_path", "_member_path", "_reader")
    _flavour = type(Path())._flavour

    def __new__(cls, *args):
        self = cls._from_parts(args)
        self._archive_path: Optional[Path] = None
        self._member_path: Optional[str] = None
        self._reader = None
        return self

    @classmethod
    def _from_parsed_parts(cls, drv, root, parts):
        self = super()._from_parsed_parts(drv, root, parts)
        self._archive_path = None
        self._member_path = None
        self._reader = None
        return self

    # ------------------------------------------------------------------
    # Internal helpers
    def _detect_archive(self) -> None:
        if self._archive_path is not None:
            return

        p = Path(super().__str__())
        parts = p.parts
        for i in range(1, len(parts) + 1):
            prefix = Path(*parts[:i])
            if prefix.is_file():
                fmt = detect_archive_format(str(prefix))
                if fmt != ArchiveFormat.UNKNOWN and fmt != ArchiveFormat.FOLDER:
                    self._archive_path = prefix
                    rest = parts[i:]
                    self._member_path = "/".join(rest)
                    return
        self._archive_path = None
        self._member_path = None

    def _get_reader(self):
        self._detect_archive()
        if self._archive_path is None:
            return None
        if self._reader is None:
            self._reader = open_archive(self._archive_path)
        return self._reader

    # ------------------------------------------------------------------
    # Basic stat helpers
    def exists(self) -> bool:  # type: ignore[override]
        reader = self._get_reader()
        if reader is None:
            return super().exists()

        if not self._member_path:
            return True

        try:
            reader.get_member(self._member_path)
            return True
        except ArchiveMemberNotFoundError:
            prefix = self._member_path.rstrip("/") + "/"
            return any(m.filename.startswith(prefix) for m in reader.get_members())

    def is_dir(self) -> bool:  # type: ignore[override]
        reader = self._get_reader()
        if reader is None:
            return super().is_dir()

        if not self._member_path:
            return True
        try:
            m = reader.get_member(self._member_path)
            return m.is_dir
        except ArchiveMemberNotFoundError:
            prefix = self._member_path.rstrip("/") + "/"
            return any(m.filename.startswith(prefix) for m in reader.get_members())

    def is_file(self) -> bool:  # type: ignore[override]
        reader = self._get_reader()
        if reader is None:
            return super().is_file()

        if not self._member_path:
            return False
        try:
            m = reader.get_member(self._member_path)
            return m.is_file
        except ArchiveMemberNotFoundError:
            return False

    # ------------------------------------------------------------------
    # Directory iteration
    def iterdir(self) -> Iterator["ArchivePath"]:  # type: ignore[override]
        reader = self._get_reader()
        if reader is None:
            for p in super().iterdir():
                yield ArchivePath(str(p))
            return

        prefix = self._member_path.rstrip("/") if self._member_path else ""
        members = reader.get_members()

        if prefix:
            try:
                m = reader.get_member(prefix)
                if not m.is_dir:
                    raise NotADirectoryError(str(self))
            except ArchiveMemberNotFoundError:
                if not any(m.filename.startswith(prefix + "/") for m in members):
                    raise FileNotFoundError(str(self))
        seen = set()
        base = prefix + "/" if prefix else ""
        for m in members:
            if not m.filename.startswith(base):
                continue
            rest = m.filename[len(base) :]
            if not rest:
                continue
            name = rest.split("/", 1)[0]
            child_member = base + name if base else name
            if child_member in seen:
                continue
            seen.add(child_member)
            yield ArchivePath(str(self._archive_path / child_member))

    # ------------------------------------------------------------------
    # Opening files
    def open(self, mode: str = "r", *args, **kwargs):  # type: ignore[override]
        if any(flag in mode for flag in "wax+"):
            raise ValueError("ArchivePath is read-only")

        reader = self._get_reader()
        if reader is None:
            return super().open(mode, *args, **kwargs)

        if "b" not in mode:
            import io

            binary = reader.open(self._member_path)
            encoding = kwargs.get("encoding", "utf-8")
            newline = kwargs.get("newline")
            return io.TextIOWrapper(binary, encoding=encoding, newline=newline)
        return reader.open(self._member_path)

    # Convenience wrappers
    def read_bytes(self) -> bytes:  # type: ignore[override]
        with self.open("rb") as fh:
            return fh.read()

    def read_text(self, encoding: str | None = None) -> str:  # type: ignore[override]
        with self.open("r", encoding=encoding) as fh:
            return fh.read()
