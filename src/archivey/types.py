"""Core data types for the Archivey public API."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone, tzinfo
from enum import Enum, Flag, auto
from typing import (
    TYPE_CHECKING,
    ClassVar,
    Final,
    Literal,
    Mapping,
    NamedTuple,
    overload,
)

from archivey.internal.enum_args import coerce_enum

if TYPE_CHECKING:
    from archivey.cost import CostReceipt
    from archivey.diagnostics import Diagnostic


class MemberStreams(Flag):
    """The member-stream capabilities a reader was opened with.

    Callers declare these as booleans —
    ``open_archive(..., seekable_members=True, concurrent_members=True)`` — so there is
    no need to construct a ``MemberStreams`` value to open an archive. This flag set is
    the internal representation those booleans map to at the entry point, and what every
    backend receives. It is **not** carried on :class:`~archivey.CostReceipt` or in
    diagnostics. Concrete readers expose the value they were opened with as
    ``reader.member_streams``; that property is not on the
    :class:`~archivey.ArchiveReader` ABC, so it is reachable at runtime but not part of
    the typed public contract.

    Default (no bits set — ``MemberStreams(0)``) is the cheap contract:

    - at most one live member stream at a time
    - streams are forward-only (``seek()`` raises)

    ``CONCURRENT``
        Multiple overlapping ``open()`` calls are allowed. First-touch member
        materialization is coordinated (one build; waiters share the snapshot);
        ``close()`` drains in-flight worker calls. Callers still synchronize any
        *shared* stream objects they hand around. Reader-wide passes
        (``__iter__`` / ``stream_members`` / ``extract_all``) remain single-owner.
        Does **not** remove solid open-order cost — see :class:`~archivey.AccessCost`.

    ``SEEKABLE``
        Member streams from random ``open()`` support ``seek()``. Without this
        flag, seek raises. A backward seek may re-decompress from the start
        (loud-slow-rewind) when there is no index or accelerator. This is a
        guarantee, not a request mask: a backend that can list a file member
        must also seek it when the flag is set. ``stream_members()`` yields
        stay a single-pass decode; this flag does not require those handles
        to seek.
    """

    CONCURRENT = auto()
    SEEKABLE = auto()


class ContainerFormat(str, Enum):
    ZIP = "zip"
    TAR = "tar"
    RAR = "rar"
    SEVEN_Z = "7z"
    ISO = "iso"
    DIRECTORY = "directory"
    RAW_STREAM = "raw_stream"
    UNKNOWN = "unknown"


class StreamFormat(str, Enum):
    UNCOMPRESSED = "uncompressed"
    GZIP = "gz"
    BZIP2 = "bz2"
    XZ = "xz"
    ZSTD = "zst"
    LZ4 = "lz4"
    LZIP = "lz"
    LZMA_ALONE = "lzma"  # legacy LZMA Alone file format (not raw FORMAT_RAW)
    ZLIB = "zz"
    BROTLI = "br"
    UNIX_COMPRESS = "Z"


@dataclass(frozen=True)
class ArchiveFormat:
    """A ``(container, stream)`` pair identifying how an archive is packaged.

    Prefer the named class attributes (``ArchiveFormat.ZIP``, ``ArchiveFormat.TAR_GZ``,
    …) over constructing pairs by hand. Those names are assigned immediately below
    the class body; the ``ClassVar`` declarations exist so type checkers see them
    without per-use suppressions. ``_FORMAT_NAMES`` is built from the same
    assignments so ``repr`` / ``display_name`` stay in sync automatically.

    A pair built by hand from strings — ``ArchiveFormat("raw_stream", "gz")``, say,
    from a format round-tripped through a config file — is converted to the enum
    members at construction, with the same spellings the other enum arguments take,
    and an unknown spelling raises :class:`~archivey.ArchiveyUsageError` there.
    """

    container: ContainerFormat
    stream: StreamFormat

    def __post_init__(self) -> None:
        # Converted, not only checked: both enums mix in ``str``, so a string pair
        # compares and hashes equal to the named format, while the code that decides
        # behaviour tests ``container is ContainerFormat.RAW_STREAM`` and would take
        # the other branch for it. Holding members makes the two agree. A member
        # of another enum is refused as a container, but left as it is as a
        # stream: the codec registry is keyed on these pairs, and a codec
        # registered from outside (``tests/test_codec_descriptor.py`` does) brings
        # its own stream enum.
        if not isinstance(self.container, ContainerFormat):
            object.__setattr__(
                self,
                "container",
                coerce_enum(
                    self.container,
                    ContainerFormat,
                    call="ArchiveFormat()",
                    param="container=",
                ),
            )
        if not isinstance(self.stream, Enum):
            object.__setattr__(
                self,
                "stream",
                coerce_enum(
                    self.stream, StreamFormat, call="ArchiveFormat()", param="stream="
                ),
            )

    ZIP: ClassVar[ArchiveFormat]
    TAR: ClassVar[ArchiveFormat]
    TAR_GZ: ClassVar[ArchiveFormat]
    TAR_BZ2: ClassVar[ArchiveFormat]
    TAR_XZ: ClassVar[ArchiveFormat]
    TAR_ZST: ClassVar[ArchiveFormat]
    TAR_LZ4: ClassVar[ArchiveFormat]
    GZ: ClassVar[ArchiveFormat]
    BZ2: ClassVar[ArchiveFormat]
    XZ: ClassVar[ArchiveFormat]
    ZST: ClassVar[ArchiveFormat]
    LZ4: ClassVar[ArchiveFormat]
    LZIP: ClassVar[ArchiveFormat]
    LZMA_ALONE: ClassVar[ArchiveFormat]
    ZLIB: ClassVar[ArchiveFormat]
    BROTLI: ClassVar[ArchiveFormat]
    Z: ClassVar[ArchiveFormat]
    SEVEN_Z: ClassVar[ArchiveFormat]
    RAR: ClassVar[ArchiveFormat]
    ISO: ClassVar[ArchiveFormat]
    DIRECTORY: ClassVar[ArchiveFormat]
    UNKNOWN: ClassVar[ArchiveFormat]

    def file_extension(self) -> str:
        """The on-disk file extension for this format, without a leading dot.

        Used for extension-based naming and detection — e.g. choosing the output
        filename when converting between formats, or matching by extension in the
        detector. Examples: ``ZIP`` -> ``"zip"``, ``TAR_GZ`` -> ``"tar.gz"``,
        ``GZ`` -> ``"gz"``. Formats with no on-disk file representation
        (``DIRECTORY``, ``UNKNOWN``) return ``""``.
        """
        if self.container in (ContainerFormat.DIRECTORY, ContainerFormat.UNKNOWN):
            return ""
        if self.container == ContainerFormat.RAW_STREAM:
            # A bare single-file compressed stream (no container): the extension is
            # just the codec's own — GZ -> "gz", not "raw_stream.gz".
            return self.stream.value
        if self.stream == StreamFormat.UNCOMPRESSED:
            return self.container.value
        return f"{self.container.value}.{self.stream.value}"

    @property
    def display_name(self) -> str:
        """Human-readable name for this format, e.g. ``"ZIP"``, ``"TAR_GZ"``.

        Uses the predefined named-instance attribute name (``ZIP``, ``TAR_GZ``, …);
        falls back to ``repr()`` for an ad-hoc combination not in the named set.
        ``_FORMAT_NAMES`` is populated just after the class definition — safe at
        runtime because this property is never called before the module is fully loaded.
        """
        name = _FORMAT_NAMES.get(self)
        return name if name is not None else repr(self)

    def __repr__(self) -> str:
        name = _FORMAT_NAMES.get(self)
        if name is not None:
            return f"ArchiveFormat.{name}"
        return f"ArchiveFormat({self.container!r}, {self.stream!r})"


# Predefined named instances, assigned as class attributes.
ArchiveFormat.ZIP = ArchiveFormat(ContainerFormat.ZIP, StreamFormat.UNCOMPRESSED)
ArchiveFormat.TAR = ArchiveFormat(ContainerFormat.TAR, StreamFormat.UNCOMPRESSED)
ArchiveFormat.TAR_GZ = ArchiveFormat(ContainerFormat.TAR, StreamFormat.GZIP)
ArchiveFormat.TAR_BZ2 = ArchiveFormat(ContainerFormat.TAR, StreamFormat.BZIP2)
ArchiveFormat.TAR_XZ = ArchiveFormat(ContainerFormat.TAR, StreamFormat.XZ)
ArchiveFormat.TAR_ZST = ArchiveFormat(ContainerFormat.TAR, StreamFormat.ZSTD)
ArchiveFormat.TAR_LZ4 = ArchiveFormat(ContainerFormat.TAR, StreamFormat.LZ4)
ArchiveFormat.GZ = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.GZIP)
ArchiveFormat.BZ2 = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.BZIP2)
ArchiveFormat.XZ = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.XZ)
ArchiveFormat.ZST = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.ZSTD)
ArchiveFormat.LZ4 = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.LZ4)
ArchiveFormat.LZIP = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.LZIP)
ArchiveFormat.LZMA_ALONE = ArchiveFormat(
    ContainerFormat.RAW_STREAM, StreamFormat.LZMA_ALONE
)
ArchiveFormat.ZLIB = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.ZLIB)
ArchiveFormat.BROTLI = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.BROTLI)
ArchiveFormat.Z = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.UNIX_COMPRESS)
ArchiveFormat.SEVEN_Z = ArchiveFormat(
    ContainerFormat.SEVEN_Z, StreamFormat.UNCOMPRESSED
)
ArchiveFormat.RAR = ArchiveFormat(ContainerFormat.RAR, StreamFormat.UNCOMPRESSED)
ArchiveFormat.ISO = ArchiveFormat(ContainerFormat.ISO, StreamFormat.UNCOMPRESSED)
ArchiveFormat.DIRECTORY = ArchiveFormat(
    ContainerFormat.DIRECTORY, StreamFormat.UNCOMPRESSED
)
ArchiveFormat.UNKNOWN = ArchiveFormat(
    ContainerFormat.UNKNOWN, StreamFormat.UNCOMPRESSED
)

# Reverse map (instance -> attribute name) for __repr__, derived by introspecting the
# class attributes above so the names live in exactly one place.
_FORMAT_NAMES: dict[ArchiveFormat, str] = {
    value: name
    for name, value in vars(ArchiveFormat).items()
    if isinstance(value, ArchiveFormat)
}


@dataclass(frozen=True)
class MissingComponent:
    """A package, extra, or external tool required for a format (or a codec inside it).

    Appears on :class:`~archivey.FormatAvailability` when something is absent, and as
    the ``requirement`` on internal codec/backend descriptors. Defined in this leaf
    module (not the registry) so codec descriptors can reference it without a
    registry ↔ codecs import cycle.
    """

    name: str  # e.g. "pycdlib", "brotli", "unrar"
    install_hint: str  # e.g. "pip install archivey[recommended]"
    unlocks: tuple[
        str, ...
    ] = ()  # member-codecs/capabilities it enables, e.g. ("ppmd",)

    def message(self, purpose: str, *, note: str = "") -> str:
        """Text for the ``PackageNotInstalledError`` raised when this component is absent.

        Every raise site builds its message here so the advice a caller gets when a
        *read* fails cannot drift from the ``install_hint`` that listing and
        :class:`~archivey.FormatAvailability` report. The two used to be independent
        strings, and the extras consolidation updated the hints while every ``raise``
        kept advertising extras that no longer exist.

        ``note`` appends a sentence for cases where the bare hint would mislead (e.g.
        a backport that is pointless on a Python version shipping the module).
        """
        text = f"The {self.name!r} package is required for {purpose} ({self.install_hint})."
        return f"{text} {note}" if note else text


class MagicSignature(NamedTuple):
    """Exact magic-byte match declared by a backend/codec descriptor (not end-user API).

    Detection accepts a match on the byte comparison alone. Formats too unspecific
    for an exact magic (zlib's 2-byte CMF/FLG header) or with no signature (Brotli)
    use a content probe instead — see codec ``content_probe`` and ``format-detection``.
    """

    offset: int
    magic: bytes
    format: "ArchiveFormat"


class MemberType(Enum):
    """Kind of archive entry.

    ``ANTI`` is a deletion/tombstone (solid 7z incremental updates), not a payload
    file — ``is_file`` is false and extraction skips it. ``OTHER`` covers device
    nodes, FIFOs, sockets, etc., and is always rejected by safe extraction.
    """

    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    HARDLINK = "hardlink"
    OTHER = "other"
    ANTI = "anti"


class HashAlgorithm(str, Enum):
    """Digest algorithms that may appear as keys in :attr:`ArchiveMember.hashes`."""

    CRC32 = "crc32"
    BLAKE2SP = "blake2sp"
    ADLER32 = "adler32"


def crc32_digest(value: int) -> bytes:
    """Encode a CRC-32 as four big-endian bytes for :attr:`ArchiveMember.hashes`."""
    return (value & 0xFFFFFFFF).to_bytes(4, "big")


class CompressionAlgorithm(Enum):
    """A compression/filter codec. Extensible: codecs Archivey does not recognize
    map to ``UNKNOWN`` rather than raising, so callers should treat the set as
    open-ended. ``ContainerFormat.RAR`` and ``CompressionAlgorithm.RAR`` are
    homonyms (container vs codec), not a new name like ``RAR_COMPRESSION``.
    """

    STORED = "stored"
    DEFLATE = "deflate"
    DEFLATE64 = "deflate64"
    BZIP2 = "bzip2"
    LZMA = "lzma"
    LZMA2 = "lzma2"
    ZSTD = "zstd"
    LZ4 = "lz4"
    BROTLI = "brotli"
    PPMD = "ppmd"
    BCJ = "bcj"  # x86 executable filter
    BCJ2 = "bcj2"
    DELTA = "delta"
    RAR = "rar"
    UNKNOWN = "unknown"  # unrecognized codec ID


@dataclass(frozen=True)
class CompressionMethod:
    """One codec in a member's filter chain.

    Members store ``tuple[CompressionMethod, ...]``. Order matches the compress /
    pack direction: pre-filters first, packing codec last (closest to the stored
    bytes). Example: 7z ``(BCJ2, LZMA2)`` — decompress by applying LZMA2, then BCJ2.
    """

    algo: CompressionAlgorithm
    # RAR's M1-M5 method-byte offset (0 = stored, 5 = best). RAR is the only
    # backend that fills this in; ZIP, 7z and TAR leave it None.
    level: int | None = None
    properties: bytes | None = None  # raw codec properties blob, if any


class CreateSystem(Enum):
    """OS that created the archive entry (mirrors ZIP create_system values)."""

    FAT = 0
    AMIGA = 1
    OPENVMS = 2
    UNIX = 3
    VM_CMS = 4
    ATARI_ST = 5
    OS2_HPFS = 6
    MACINTOSH = 7
    Z_SYSTEM = 8
    CPM = 9
    WINDOWS_NTFS = 10
    MVS = 11
    VSE = 12
    ACORN_RISC = 13
    VFAT = 14
    ALTERNATE_MVS = 15
    BEOS = 16
    TANDEM = 17
    OS_400 = 18
    OS_X_DARWIN = 19
    UNKNOWN = 255


# Key in ArchiveMember.extra marking a member as a Windows NTFS junction. The formats
# ZIP, 7z and RAR can all describe a junction, so this key is deliberately NOT
# namespaced under a single format like "zip.". Which readers actually set it is a
# narrower list: see ArchiveMember.is_junction.
# Final keeps an overloaded ``__getitem__`` subscript with this constant a literal
# key; without it a checker that widens the assignment to ``str`` falls through
# to the ``str → object`` fallback.
EXTRA_IS_JUNCTION: Final = "is_junction"

# Key in ArchiveMember.extra marking a member that the archive recorded as a Windows
# reparse point — a Windows symlink or a junction, as opposed to a POSIX symlink. Set
# whenever the archive says so: the FILE_ATTRIBUTE_REPARSE_POINT bit for ZIP and 7z,
# the redirect type for RAR5, and the live entry for a directory scan on Windows. Not
# namespaced, for the same reason as EXTRA_IS_JUNCTION.
#
# Every junction is a reparse point, but not every reparse point is a junction, and the
# two keys answer different questions from different places: this one comes off metadata
# the archive always carries, while EXTRA_IS_JUNCTION needs the reparse *tag*, which
# lives in the member's data and which 7-Zip does not store for a directory reparse
# point. So a junction written by 7-Zip carries this key and not that one.
EXTRA_IS_REPARSE_POINT: Final = "is_reparse_point"

# Key in ArchiveMember.extra: True when this RAR member's ``created`` is Unix
# ``st_ctime`` (inode-change), False when the writer OS stores a birth time
# (Win32, and RAR3 FAT/OS2/Mac/BeOS). Derived from ``host_os``; omitted when
# ``created`` is None or ``host_os`` is unknown. A later OpenSpec change will
# promote this to a cross-format ``created_meaning`` field — do not infer that
# meaning from ``create_system`` (7z hardcodes UNIX while reading a FILETIME
# birth time; ZIP splits by extra source, not OS).
EXTRA_RAR_CREATED_IS_CTIME: Final = "rar.created_is_ctime"

# RAR3 FILE-header ``UNP_VER`` byte as stored (unvalidated); RAR5 reports 50
# because RAR5 records no per-file unpack version. Lives here, not on
# CompressionMethod.level, which carries the method-byte offset instead.
EXTRA_RAR_EXTRACT_VERSION: Final = "rar.extract_version"


class MemberExtra(dict[str, object]):
    """Per-member format-specific metadata on :class:`~archivey.ArchiveMember`.

    A ``dict[str, object]`` whose known keys return their declared types from a
    subscript (``extra["zip.compress_type"]`` is an ``int``). Unknown keys
    (third-party or future) stay legal and read as ``object``. The ``EXTRA_*``
    constants on this module still name the keys they cover.

    Writes are not type-checked: a wrong-type assignment to a known key falls
    through to the ``str → object`` fallback, same as an unknown key. ``.get()``
    returns ``object`` for every key. Assign a ``MemberExtra({...})`` (or mutate
    the existing bag); a bare dict is not assignable to the field.

    Known keys:

    * ``is_junction`` (``bool``) — ZIP, 7z, RAR, directory. A Windows NTFS
      junction; ZIP and 7z set it only when the writer stored the junction's
      reparse data, and 7-Zip does not. Implies ``is_reparse_point``.
    * ``is_reparse_point`` (``bool``) — ZIP, 7z, RAR, directory. The weaker,
      metadata-only sibling of ``is_junction``: a Windows symlink or junction
      rather than a POSIX one.
    * ``rar.created_is_ctime`` (``bool``)
    * ``rar.extract_version`` (``int``)
    * ``rar.file_version`` (``int``)
    * ``rar.tweaked_crc32`` (``int``)
    * ``rar.tweaked_blake2sp`` (``bytes``)
    * ``zip.compress_type`` (``int``)
    * ``zip.aes_vendor_version`` (``int``)
    * ``zip.aes_strength`` (``int``)
    * ``zip.aes_actual_method`` (``int``)
    * ``tar.type`` (``bytes``)
    * ``tar.pax_headers`` (``dict[str, str]``)
    * ``tar.devmajor`` (``int``)
    * ``tar.devminor`` (``int``)
    * ``gzip.original_filename`` (``str``)
    * ``iso.version`` (``int``) — plain ISO 9660 only: the ``;N`` file version
      stripped from the name. Versions of one name share it; the highest is
      listed last and is the current one.
    """

    __slots__ = ()

    # Overloaded ``__getitem__``, not a PEP 728 TypedDict: mypy rejects
    # ``extra_items=`` and then treats the TypedDict as having no keys, so every
    # read and write in a user's file errors. A ``total=False`` TypedDict also
    # makes every subscript read an error under pyright. This shape was measured
    # clean on pyright 1.1.414, mypy 1.19.1, pyrefly 1.1.1 and ty 0.0.60 with no
    # suppressions. Writes are not overloaded: the ``str → object`` fallback
    # unknown keys need also accepts a wrong-type write to a known key.
    # ``.get()`` stays ``object`` because the four checkers disagree on
    # ``dict.get``'s own signature.

    @overload
    def __getitem__(self, key: Literal["is_junction"], /) -> bool: ...
    @overload
    def __getitem__(self, key: Literal["is_reparse_point"], /) -> bool: ...
    @overload
    def __getitem__(self, key: Literal["rar.created_is_ctime"], /) -> bool: ...
    @overload
    def __getitem__(self, key: Literal["rar.extract_version"], /) -> int: ...
    @overload
    def __getitem__(self, key: Literal["rar.file_version"], /) -> int: ...
    @overload
    def __getitem__(self, key: Literal["rar.tweaked_crc32"], /) -> int: ...
    @overload
    def __getitem__(self, key: Literal["rar.tweaked_blake2sp"], /) -> bytes: ...
    @overload
    def __getitem__(self, key: Literal["zip.compress_type"], /) -> int: ...
    @overload
    def __getitem__(self, key: Literal["zip.aes_vendor_version"], /) -> int: ...
    @overload
    def __getitem__(self, key: Literal["zip.aes_strength"], /) -> int: ...
    @overload
    def __getitem__(self, key: Literal["zip.aes_actual_method"], /) -> int: ...
    @overload
    def __getitem__(self, key: Literal["tar.type"], /) -> bytes: ...
    @overload
    def __getitem__(self, key: Literal["tar.pax_headers"], /) -> dict[str, str]: ...
    @overload
    def __getitem__(self, key: Literal["tar.devmajor"], /) -> int: ...
    @overload
    def __getitem__(self, key: Literal["tar.devminor"], /) -> int: ...
    @overload
    def __getitem__(self, key: Literal["gzip.original_filename"], /) -> str: ...
    @overload
    def __getitem__(self, key: Literal["iso.version"], /) -> int: ...
    @overload
    def __getitem__(self, key: str, /) -> object: ...
    def __getitem__(self, key: str, /) -> object:
        return super().__getitem__(key)


class ArchiveInfoExtra(dict[str, object]):
    """Archive-level format-specific metadata on :class:`~archivey.ArchiveInfo`.

    Same shape as :class:`~archivey.MemberExtra` over a separate key set — do not merge
    the two bags.

    Known keys:

    * ``iso.namespace`` (``str``)
    * ``zip.volume_count`` (``int``)
    * ``rar.volume_count`` (``int``)
    * ``7z.volume_count`` (``int``)
    """

    __slots__ = ()

    @overload
    def __getitem__(self, key: Literal["iso.namespace"], /) -> str: ...
    @overload
    def __getitem__(self, key: Literal["zip.volume_count"], /) -> int: ...
    @overload
    def __getitem__(self, key: Literal["rar.volume_count"], /) -> int: ...
    @overload
    def __getitem__(self, key: Literal["7z.volume_count"], /) -> int: ...
    @overload
    def __getitem__(self, key: str, /) -> object: ...
    def __getitem__(self, key: str, /) -> object:
        return super().__getitem__(key)


@dataclass(slots=True)
class ArchiveMember:
    """One archive entry.

    Mutable on purpose: backends fill late-bound fields in place after the member
    is first constructed (``link_target_member``, digests, attached diagnostics).
    Callers must treat instances as read-only — use :meth:`replace` for edits.
    """

    type: MemberType
    """What kind of entry this is (file, directory, symlink, …)."""

    name: str
    """Normalized member path, ``/``-separated, decoded for display and lookup."""

    raw_name: bytes | None = None
    """The member name exactly as stored in the archive, undecoded, or ``None`` when
    the format stores no name or the bytes cannot be recovered from the decoded one."""

    size: int | None = None
    """Uncompressed size in bytes, or ``None`` if unknown (e.g. a streaming entry)."""

    compressed_size: int | None = None
    """Compressed size in bytes, or ``None`` if unknown."""

    modified: datetime | None = None
    """Last-modified time, if recorded."""

    accessed: datetime | None = None
    """Last-access time, if recorded."""

    created: datetime | None = None
    """The format's creation-time slot, if recorded (rare; most formats store only mtime).

    Meaning follows the writer, not a cross-format guarantee. A Unix RARLAB
    archive stores inode-change time (``st_ctime``) here; those members set
    ``extra["rar.created_is_ctime"]`` to ``True``. A Win32 RAR stores birth
    time and sets the same key to ``False``. Directory listing uses
    ``st_birthtime`` only and never ``st_ctime``.
    """

    mode: int | None = None
    """Unix permission bits, or ``None`` if the format/entry carries no mode."""

    uid: int | None = None
    """Owner user id, if recorded."""

    gid: int | None = None
    """Owner group id, if recorded."""

    uname: str | None = None
    """Owner user name, if recorded."""

    gname: str | None = None
    """Owner group name, if recorded."""

    link_target: str | None = None
    """For a symlink/hardlink, the raw target path string as stored."""

    # compare=False: identity is path/type/metadata, not the resolved peer object
    # (resolution is late-bound and would make equality order-dependent).
    link_target_member: "ArchiveMember | None" = field(default=None, compare=False)
    """For a link, the resolved target member within this archive, if found."""

    compression: tuple[CompressionMethod, ...] = field(default_factory=tuple)
    """Codec chain in compress order — pre-filters first, packing codec last."""

    is_encrypted: bool = False
    """Whether this member's data is encrypted.

    ``True`` also when the backend could not rule encryption out. A RAR5 member
    whose header stopped part-way through its optional records is reported this
    way, because answering "not encrypted" from a header nobody finished reading
    would be a wrong answer rather than a missing one; the member then carries a
    ``MEMBER_HEADER_RECORD_SKIPPED`` diagnostic saying the header was cut short.
    A member with no such diagnostic is a definite answer.
    """

    is_current: bool = True
    """Last-entry-wins: ``True`` for the live final state of this path.

    Duplicate names keep earlier rows with ``is_current=False`` (history /
    superseded). :meth:`~archivey.ArchiveReader.get` returns the current one.
    """

    is_sparse: bool = False
    """Whether this member is stored as a sparse file."""

    comment: str | None = None
    """Per-member comment, if the format records one."""

    create_system: CreateSystem | None = None
    """The OS that created the entry (drives mode/attribute interpretation)."""

    windows_attrs: int | None = None
    """Raw Windows file-attribute bitmask, if recorded."""

    # compare=False: digests may be filled after first construction; equality is
    # about the entry identity, not verification state (see archive-data-model).
    hashes: Mapping[HashAlgorithm, bytes] = field(default_factory=dict, compare=False)
    """Stored content digests keyed by :class:`HashAlgorithm` (values always ``bytes``).

    CRC-32 is four big-endian bytes (:func:`crc32_digest`). Excluded from equality.
    """

    # compare=False: format-specific bags must not affect logical identity.
    extra: MemberExtra = field(default_factory=MemberExtra, compare=False)
    """Format-specific extra fields (e.g. ``extra["is_junction"]``). Excluded from equality.

    Known keys and their value types are :class:`~archivey.MemberExtra`. Unknown keys
    (third-party or future) stay legal and read as ``object``. The ``EXTRA_*``
    constants on this module remain the names for the keys they cover.
    """

    # Private internal fields (not part of the public contract)
    _member_id: int | None = field(default=None, repr=False, compare=False)
    _archive_id: str | None = field(default=None, repr=False, compare=False)
    _raw: object = field(default=None, repr=False, compare=False)
    """Opaque backend handle carried on the member (e.g. the stdlib ``ZipInfo`` /
    ``TarInfo``), so a backend can open the member's data straight from the member without
    a separate name/id lookup table. Not part of the public contract. Typed ``object``:
    each backend narrows it with an ``isinstance`` check on its own handle type before
    use."""
    _diagnostics: tuple["Diagnostic", ...] = field(
        default=(), repr=False, compare=False
    )
    """Library-retained diagnostic attachments (bounded by the collector budget)."""
    _link_target_resolved: bool = field(default=False, repr=False, compare=False)
    """Set once a backend has looked for this link's target, found or not.

    ``link_target is None`` alone cannot say whether the target is missing or merely
    not looked for yet, so without this the lookup repeats on every access — re-reading
    the member's data and re-emitting its diagnostic. Not part of the public contract."""
    _link_target_absent: bool = field(default=False, repr=False, compare=False)
    """Set when the archive itself records no target for this link.

    A lookup that came back empty has two causes that look identical from here. The
    archive may carry no target at all — a writer that stored none, a reparse buffer
    naming nothing — or it may carry one this reader could not reach, because the bytes
    are compressed, split across volumes or encrypted. Only the first is the archive's
    omission, and only the first is an extraction outcome rather than a failure, so the
    backend that knows which it is says so here. Not part of the public contract."""

    # Mutable members are intentionally unhashable. ``@dataclass`` (``eq=True``, not
    # frozen) sets ``__hash__ = None``, which is what makes ``isinstance(m, Hashable)``
    # False. Do not add a ``__hash__`` method that raises: it makes the class claim to
    # be hashable while every hash fails.

    @property
    def diagnostics(self) -> tuple["Diagnostic", ...]:
        """Read-only tuple of diagnostics attached to this member (may be empty)."""
        return self._diagnostics

    @property
    def member_id(self) -> int:
        if self._member_id is None:
            raise AttributeError("member_id not set; member not yet registered")
        return self._member_id

    @property
    def archive_id(self) -> str:
        if self._archive_id is None:
            raise AttributeError("archive_id not set; member not yet registered")
        return self._archive_id

    def modified_utc(self, tz_for_naive: tzinfo | None = None) -> datetime | None:
        """The modification time as a timezone-aware UTC ``datetime``, or ``None``.

        ``modified`` itself is faithful to what the archive stores: **naive** when the
        format records local wall-clock time (ZIP's DOS field, RAR4), **aware** when it
        records UTC or an offset — so naive and aware values from one archive cannot be
        compared or sorted directly. This helper makes that usable: an aware value is
        converted to UTC; a naive one first gets ``tz_for_naive`` attached (the caller's
        explicit assumption about where the archive was created), defaulting to the
        local timezone when not given. Whether the stored value was wall-clock remains
        visible on the field itself: ``member.modified.tzinfo is None``.
        """
        dt = self.modified
        if dt is None:
            return None
        if dt.tzinfo is None:
            if tz_for_naive is not None:
                dt = dt.replace(tzinfo=tz_for_naive)
            else:
                dt = dt.astimezone()  # naive -> assume local timezone
        return dt.astimezone(timezone.utc)

    @property
    def is_file(self) -> bool:
        return self.type == MemberType.FILE

    @property
    def is_dir(self) -> bool:
        return self.type == MemberType.DIRECTORY

    @property
    def is_link(self) -> bool:
        return self.type in (MemberType.SYMLINK, MemberType.HARDLINK)

    @property
    def is_other(self) -> bool:
        return self.type == MemberType.OTHER

    @property
    def is_anti(self) -> bool:
        return self.type == MemberType.ANTI

    @property
    def is_junction(self) -> bool:
        """The archive recorded this symlink as a Windows NTFS junction.

        ``False`` means no junction was detected, not that the entry is not one:

        - RAR names the link kind in a header field, so a RAR junction is reported
          while listing.
        - A directory source asks the filesystem (``os.DirEntry.is_junction``), so a
          scan on Windows reports junctions under Python 3.12 and later; under 3.11
          a junction is listed as a symlink and reads ``False``.
        - ZIP and 7z keep the junction's reparse *tag* in the member's data, so they
          report one only when the writer stored that reparse buffer. 7-Zip stores
          none for a directory reparse point, and a junction is always one, so a
          junction in an archive 7-Zip wrote reads ``False`` here.
        - TAR and ISO have no junction concept and always read ``False``.

        :attr:`is_reparse_point` comes from metadata the archive always carries, and
        is the check to use when a Windows link of either kind matters.
        """
        return self.type == MemberType.SYMLINK and bool(
            self.extra.get(EXTRA_IS_JUNCTION)
        )

    @property
    def is_reparse_point(self) -> bool:
        """The archive recorded this entry as a Windows symlink or junction.

        Deliberately not gated on :attr:`type` the way :attr:`is_junction` is: a member
        the archive flags as a reparse point whose data turns out not to be a link
        buffer is presented as an ordinary file or directory, and the flag still records
        what the archive said about it.
        """
        return bool(self.extra.get(EXTRA_IS_REPARSE_POINT))

    def replace(self, **kwargs: object) -> "ArchiveMember":
        """Return a copy with the given fields changed; never mutates self.

        ``object`` is not a check: neither the keyword names nor the value types are
        verified statically, so a misspelled field or a wrongly typed value is still
        a ``TypeError`` from ``dataclasses.replace`` at runtime. Typing the names
        would need a per-field ``TypedDict``, which is deliberately deferred.
        """
        return replace(self, **kwargs)


@dataclass(frozen=True)
class ArchiveInfo:
    """Archive-level metadata, available immediately after ``open_archive()`` without
    a full member scan."""

    format: ArchiveFormat
    """The detected ``(container, stream)`` format of the archive."""

    format_version: str | None
    """Format version string, e.g. ``"4.5"`` for ZIP or ``"5"`` for RAR5; ``None`` if unknown."""

    is_solid: bool
    """Whether decompressing one member may require decompressing earlier ones."""

    member_count: int | None
    """Number of members, or ``None`` when a count would require scanning the whole archive."""

    comment: str | None
    """Archive-level comment, if the format records one."""

    is_encrypted: bool
    """Header-level encryption (7z, RAR5) — not per-member encryption (see ``ArchiveMember.is_encrypted``)."""

    is_multivolume: bool
    """Whether the archive spans multiple volumes."""

    cost: "CostReceipt"
    """Listing/access cost receipt for the archive (see the ``access-mode-and-cost`` capability)."""

    extra: ArchiveInfoExtra = field(default_factory=ArchiveInfoExtra, compare=False)
    """Format-specific archive-level metadata, keyed by namespaced strings (mirrors
    ``ArchiveMember.extra``). For example the ISO backend records the auto-selected
    namespace as ``extra["iso.namespace"]``. Excluded from ``__eq__``. Known keys
    and their value types are :class:`~archivey.ArchiveInfoExtra`; unknown keys stay legal
    and read as ``object``."""
