"""Public configuration types for archivey."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, ClassVar

from archivey.diagnostics import DiagnosticPolicy, OnDiagnostic
from archivey.exceptions import ArchiveyUsageError
from archivey.internal.arg_checks import (
    check_callable,
    check_encoding,
    check_instance,
    describe_value,
)
from archivey.internal.enum_args import coerce_enum

if TYPE_CHECKING:
    from archivey.types import ArchiveMember


class AcceleratorMode(Enum):
    """Tri-state control for an optional random-access accelerator backend.

    - ``ON``  — always use the accelerator (raise ``PackageNotInstalledError`` if its
      package is absent: the caller asked for it explicitly).
    - ``OFF`` — never use it; the stream stays sequential-only.
    - ``AUTO`` — use it only when seekability was declared
      (``seekable_members=True`` on ``open_archive``, ``seekable=True`` on
      ``open_stream``, or internal seek demand). Without declared seek demand, AUTO
      leaves the cheaper sequential backend in place (no index/accelerator work). When
      AUTO would enable the accelerator but its package is absent, fall back to
      sequential silently (it is an enhancement, not a requirement). For the
      ``rapidgzip`` DEFLATE-family path, AUTO also requires the known compressed
      input size to reach :data:`RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE` (see
      :meth:`enabled_for`) **and** a verifiable decompressed size
      (``StreamConfig.expected_decompressed_size``, or gzip ISIZE) so truncation
      cannot be silently short-read.
    """

    AUTO = "auto"
    ON = "on"
    OFF = "off"

    def enabled_for(
        self,
        *,
        seekable: bool,
        available: bool,
        input_size: int | None = None,
        min_size: int | None = None,
    ) -> bool:
        """Resolve the tri-state to "use the accelerator?".

        ``ON`` always returns ``True`` (the caller checks availability and raises
        ``PackageNotInstalledError`` if the package is missing — the user asked for it
        explicitly; ``min_size`` is ignored). ``AUTO`` enables it only when
        seekability is declared and the package is available, so a missing package
        falls back silently. When ``min_size`` is set and ``input_size`` is known and
        strictly below that threshold, ``AUTO`` also falls back (tiny members do not
        repay per-stream accelerator setup). Unknown ``input_size`` keeps the
        pre-threshold AUTO behaviour.
        """
        if self is AcceleratorMode.OFF:
            return False
        if self is AcceleratorMode.ON:
            return True
        # AUTO: only pay for seek machinery when the caller asked for seekable streams.
        if not (available and seekable):
            return False
        if min_size is not None and input_size is not None and input_size < min_size:
            return False
        return True


# Minimum known compressed input size (bytes) before ``use_rapidgzip`` AUTO selects
# rapidgzip for a DEFLATE-family stream (gzip / zlib / raw deflate). Below this,
# stdlib backends stay cheaper: rapidgzip's per-stream index/thread setup dominates
# for tiny members (many-small ZIP/gzip case). Benchmarked in
# ``scripts/bench_rapidgzip_auto_threshold.py``; see the rapidgzip-deflate-zlib
# acceleration design note. ``ON`` ignores this; unknown size keeps pre-threshold AUTO.
RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE: int = 1 * 1024 * 1024


# How many decompressed bytes a backward seek must re-decode before
# STREAM_REWIND_REDECOMPRESSES reports it: target offset minus the nearest preceding
# seek point, measured at seek time.
#
# ABSOLUTE, NOT RELATIVE, and the counterexample is why. A relative rule ("you re-decoded
# more than the distance you jumped") sounds like it captures disproportionate work, but
# on a 1 GB single-block .xz, seeking from the end back to 900 MB re-decodes 900 MB while
# jumping only ~100 MB — a 0.11x ratio, under any sane relative threshold. Relative goes
# quietest exactly where the absolute cost is highest. The caller cares about wall time,
# which tracks bytes re-decoded.
#
# Same number as RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE above. They measure different
# quantities (compressed input size vs decompressed re-decode distance) but encode the
# same judgement: below about a megabyte the work is not worth a caller's attention.
REWIND_REDECODE_WARN_BYTES: int = 1 * 1024 * 1024


def _check_limit(
    value: object,
    *,
    cls: str,
    field_name: str,
    allow_float: bool = False,
    allow_none: bool = True,
) -> None:
    """Validate one numeric limit field at construction.

    The guards these fields drive are all comparisons, so a wrong-typed one is not
    found until something is actually being counted — ``ListingLimits(max_members="x")``
    built fine and then failed mid-listing as ``TypeError: '>' not supported between
    instances of 'int' and 'str'``, naming neither the field nor the class. A limit is
    a promise about a future operation; checking it where the caller wrote it is the
    only place the message can still name what they wrote.

    ``bool`` is refused explicitly: it is an ``int`` subclass, so ``max_members=True``
    would otherwise pass and cap the listing at one member. The type test is spelled
    out per branch rather than parameterised, because a parameterised ``isinstance``
    narrows nothing and leaves the comparison below unprovable.

    Two further shapes are refused for the same reason the wrong type is, namely that
    they switch a guard off silently rather than loudly:

    * ``allow_none=False`` for a field that is not ``| None``. ``None`` reads as
      "disable this guard" on every other field, but ``ratio_activation_threshold``
      is read unconditionally, so a ``None`` there is a ``TypeError`` during the
      extraction rather than a disabled guard.
    * a NaN or an infinity on a float field. Every comparison against a NaN is false
      and nothing ever exceeds an infinity, so ``max_ratio=float("nan")`` constructs,
      extracts, and enforces nothing. ``None`` is the way to say that on purpose.
    """
    if value is None:
        if allow_none:
            return
        raise ArchiveyUsageError(
            f"{cls}.{field_name} is not optional and takes "
            f"{'a number' if allow_float else 'an int'}, but got None."
        )
    if isinstance(value, bool):
        number: int | float | None = None
    elif isinstance(value, int):
        number = value
    elif allow_float and isinstance(value, float):
        number = value
    else:
        number = None

    if number is None:
        raise ArchiveyUsageError(
            f"{cls}.{field_name} takes {'a number' if allow_float else 'an int'}"
            f"{' or None' if allow_none else ''}, but got {describe_value(value)}."
        )
    if isinstance(number, float) and not math.isfinite(number):
        raise ArchiveyUsageError(
            f"{cls}.{field_name} takes a finite number, but got {value!r}. A NaN "
            f"compares false against everything and an infinity is never exceeded, so "
            f"either one would leave this guard switched off without saying so; pass "
            f"None if that is what you want."
        )
    if number < 0:
        raise ArchiveyUsageError(
            f"{cls}.{field_name} cannot be negative, but got {value!r}."
            + (" Pass None to disable this guard." if allow_none else "")
        )


@dataclass(frozen=True)
class ExtractionLimits:
    """Decompression-bomb limits for :func:`archivey.extract` / :meth:`extract_all`.

    ``None`` on a guard field disables that guard. :attr:`UNLIMITED` sets the three
    guard fields to ``None``; :attr:`ratio_activation_threshold` is a parameter of the
    ratio guard rather than a guard of its own, and is moot once ``max_ratio`` is
    ``None``.
    """

    max_extracted_bytes: int | None = 2 * 2**30
    max_ratio: float | None = 1000.0
    ratio_activation_threshold: int = 5 * 2**20
    max_entries: int | None = 1_048_576

    UNLIMITED: ClassVar[ExtractionLimits]

    def __post_init__(self) -> None:
        cls = "ExtractionLimits"
        _check_limit(
            self.max_extracted_bytes, cls=cls, field_name="max_extracted_bytes"
        )
        _check_limit(self.max_ratio, cls=cls, field_name="max_ratio", allow_float=True)
        # Not ``| None``: the ratio guard reads it unconditionally, so a None here
        # does not disable anything, it fails the comparison mid-extraction.
        _check_limit(
            self.ratio_activation_threshold,
            cls=cls,
            field_name="ratio_activation_threshold",
            allow_none=False,
        )
        _check_limit(self.max_entries, cls=cls, field_name="max_entries")


ExtractionLimits.UNLIMITED = ExtractionLimits(
    max_extracted_bytes=None,
    max_ratio=None,
    max_entries=None,
)


@dataclass(frozen=True)
class ListingLimits:
    """Caps for materializing a member list (``members`` / ``scan_members`` / extract prep).

    Applied from the reader's open :attr:`ArchiveyConfig.listing_limits` for its lifetime.
    ``None`` on a field disables that guard. :attr:`UNLIMITED` disables both.
    ``stream_members`` / ``streaming=True`` / forward-only iteration do not
    enforce these caps. 7z and RAR apply ``max_members`` at parse, so
    ``open_archive`` raises and neither is an escape hatch.
    """

    max_members: int | None = 1_048_576
    max_metadata_bytes: int | None = 64 * 2**20  # 64 MiB

    UNLIMITED: ClassVar[ListingLimits]

    def __post_init__(self) -> None:
        cls = "ListingLimits"
        _check_limit(self.max_members, cls=cls, field_name="max_members")
        _check_limit(self.max_metadata_bytes, cls=cls, field_name="max_metadata_bytes")


ListingLimits.UNLIMITED = ListingLimits(
    max_members=None,
    max_metadata_bytes=None,
)


@dataclass(frozen=True)
class ArchiveyConfig:
    """Library tuning knobs passed as ``config=`` to :func:`open_archive` / :func:`extract`.

    Per-call operationals (``format``, ``streaming``, ``password``, extraction's
    ``members``/``filter``/``policy``/…) stay keyword arguments — not fields here.
    """

    # Tri-state for the [seekable] rapidgzip accelerator (gzip / zlib / raw deflate).
    # Under AUTO, also requires known compressed input ≥ RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE
    # and a verifiable decompressed size (so truncation cannot be silently swallowed).
    use_rapidgzip: AcceleratorMode = AcceleratorMode.AUTO
    # Tri-state for rapidgzip's bundled bzip2 random-access backend.
    use_indexed_bzip2: AcceleratorMode = AcceleratorMode.AUTO
    # When True, a missing TAR (etc.) end-of-archive marker becomes TruncatedError
    # instead of ARCHIVE_EOF_MARKER_MISSING, AND every byte from the trailer to EOF must
    # be zero — a non-zero byte raises CorruptionError (trailing junk, or a second
    # archive concatenated on). Zero padding still passes; `tar` writes 10 KiB records.
    #
    # COST: that second check reads to EOF, so this flag is O(tail length), not O(512
    # bytes). On a non-seekable source it is a real scan, and on a compressed tar the
    # tail is decompressed to inspect it. That cost is why the check is gated on the
    # flag rather than emitted as an unconditional advisory.
    strict_archive_eof: bool = False
    # Legacy encoding for a ZIP member name stored without the UTF-8 flag whose bytes are
    # also not valid UTF-8 (the sniff prefers UTF-8 first). Default cp437 per APPNOTE; set a
    # local codepage (e.g. "cp1252", "shift_jis") for a known-legacy corpus. An explicit
    # ``encoding=`` on ``open_archive`` overrides this and disables the sniff entirely.
    zip_unflagged_fallback_encoding: str = "cp437"
    # Escape hatch for RAR members whose *stored name* contains ``*`` or ``?``.
    # ``unrar`` is addressed by an include mask, so such a name can also match other
    # members. Names like this are almost always constructed, so the read is refused
    # by default. On a nonsolid archive the extra decode is also unbounded and
    # unadvertised (``ExtractionLimits`` do not cover ``open()`` / ``read()``); on a
    # solid archive those bytes are already inside ``AccessCost.SOLID``. Set True to
    # read it anyway. A glob name that matches no other member is unaffected either way.
    rar_allow_glob_member_concatenation: bool = False
    extraction_limits: ExtractionLimits = ExtractionLimits()
    listing_limits: ListingLimits = ListingLimits()
    diagnostic_policy: DiagnosticPolicy = field(default_factory=DiagnosticPolicy)
    max_retained_diagnostic_references: int = 256
    on_diagnostic: OnDiagnostic | None = None

    def __post_init__(self) -> None:
        """Check the fields at construction, and convert the two that hold enums.

        A config field is read wherever it is needed, which is never where it was
        written: ``ArchiveyConfig(extraction_limits="none")`` builds fine and then
        fails part-way through an extraction as ``AttributeError: 'str' object has no
        attribute 'max_extracted_bytes'`` — a private attribute name, and no mention of
        the argument the caller actually got wrong. Checking ``config=`` at the entry
        points does not reach this: the object passed there *is* an ``ArchiveyConfig``,
        and the wrong type is one field in.

        ``strict_archive_eof`` is deliberately not checked. It is a flag read for its
        truthiness, so there is no wrong type to find — every value means something.

        The two accelerator fields are **converted** rather than only checked, because
        their consumers test them with ``is`` (:meth:`AcceleratorMode.enabled_for`): a
        string that survived construction would not be refused on use, it would read as
        "neither ON nor OFF" and silently take the AUTO path. Converting here means the
        field always holds a member, and a bad spelling names itself at the call site
        that wrote it rather than during some later stream open.

        They stay annotated ``AcceleratorMode`` rather than ``AcceleratorMode | str``
        because that is what they hold once constructed, and it keeps every consumer
        honest. A string is still accepted at construction — a type checker flags it,
        which is the right answer for a typed caller who has the enum imported anyway,
        and an untyped script gets the conversion.

        That conversion goes through ``object.__setattr__`` because the dataclass is
        frozen and it *rewrites* the field rather than only inspecting it. The checks
        above reject without writing, so they need no such thing.
        """
        check_instance(
            self.extraction_limits,
            ExtractionLimits,
            call="ArchiveyConfig(extraction_limits=…)",
            allow_none=False,
        )
        check_instance(
            self.listing_limits,
            ListingLimits,
            call="ArchiveyConfig(listing_limits=…)",
            allow_none=False,
        )
        check_instance(
            self.diagnostic_policy,
            DiagnosticPolicy,
            call="ArchiveyConfig(diagnostic_policy=…)",
            allow_none=False,
        )
        check_callable(self.on_diagnostic, call="ArchiveyConfig(on_diagnostic=…)")
        check_encoding(
            self.zip_unflagged_fallback_encoding,
            call="ArchiveyConfig(zip_unflagged_fallback_encoding=…)",
            allow_none=False,
        )
        _check_limit(
            self.max_retained_diagnostic_references,
            cls="ArchiveyConfig",
            field_name="max_retained_diagnostic_references",
            allow_none=False,
        )
        # These two are the one enum coerced at a public boundary with no ``Literal``
        # alias beside it, and that is deliberate: the annotation is read by every
        # consumer of the attribute, not only by the constructor's callers, and after
        # construction the field always holds a member. ``tests/test_enum_arguments.py``
        # records the exemption so the gap is not "fixed" back into a union.
        for field_name in ("use_rapidgzip", "use_indexed_bzip2"):
            object.__setattr__(
                self,
                field_name,
                coerce_enum(
                    getattr(self, field_name),
                    AcceleratorMode,
                    call="ArchiveyConfig()",
                    param=f"{field_name}=",
                ),
            )


DEFAULT_ARCHIVEY_CONFIG = ArchiveyConfig()


@dataclass(frozen=True)
class PasswordRequest:
    """Context passed to a :data:`PasswordProvider` when a password is needed."""

    member: ArchiveMember | None
    """The member being decrypted, or ``None`` for archive-level (header) decryption."""

    attempt: int
    """1 on the first ask for this unit; increments after a wrong-password retry."""


PasswordProvider = Callable[[PasswordRequest], str | bytes | None]
"""Callable consulted when static password candidates fail for an encrypted unit."""

PasswordInput = str | bytes | Sequence[str | bytes] | PasswordProvider | None
"""Accepted ``password=`` shapes: one value, an ordered candidate list, a provider, or None."""
