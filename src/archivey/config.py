"""Public configuration types for archivey."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, ClassVar

from archivey.cli_helpers import coerce_enum
from archivey.diagnostics import DiagnosticPolicy, OnDiagnostic
from archivey.exceptions import ArchiveyUsageError
from archivey.internal.arg_checks import (
    check_callable,
    check_encoding,
    check_instance,
    describe_value,
)

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
    """Most bytes one extraction may write in total, across every member. 2 GiB.

    Bytes copied rather than decoded (the cross-device hardlink fallback) count too.
    Crossing it stops the whole extraction, even under ``on_error="continue"``.
    """

    max_ratio: float | None = 1000.0
    """Largest decompressed-to-compressed ratio allowed. ``1000.0``.

    Checked per member (a member over it fails on its own, and ``on_error="continue"``
    moves on) and across the archive (which stops the extraction). The per-member check
    needs the member's compressed size; where the format or access mode does not give
    one, only the archive-wide check applies. Neither check starts before
    :attr:`ratio_activation_threshold` bytes of output.
    """

    ratio_activation_threshold: int = 5 * 2**20
    """Output bytes before :attr:`max_ratio` is checked. 5 MiB.

    Small members compress extremely well without being bombs, so the ratio is only
    judged once a member (or the archive, for the archive-wide check) has produced this
    much. It cannot be ``None``; set :attr:`max_ratio` to ``None`` to turn the ratio
    guard off.
    """

    max_entries: int | None = 1_048_576
    """Most entries one extraction may create: files, directories and links.

    Crossing it stops the whole extraction, even under ``on_error="continue"``.
    """

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
    """Most members a listing may hold."""

    max_metadata_bytes: int | None = 64 * 2**20
    """Most bytes of text a listing may retain across its members. 64 MiB.

    Counts member names (and raw names), comments, link targets, owner and group names
    and the string or bytes values in ``extra``, plus the archive comment. Non-ASCII text counts four bytes per character,
    so it is an upper bound rather than an exact size.
    """

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
class DecoderLimits:
    """Caps on what a decoder may allocate or compute because the *archive* said to.

    Several codecs size their working memory from a number in the archive's own
    header rather than from anything the caller chose: 7z PPMd var.H carries a
    32-bit window size, ZIP method 98 an 8-bit megabyte count, LZMA a 32-bit
    dictionary size. Those numbers are attacker-chosen, they are read before a
    single byte of member data is, and the allocation that follows is not
    proportional to the archive's size — a 153-byte 7z can ask for 4 GiB.

    **What is capped today:** both PPMd paths, and the LZMA dictionary size
    wherever an archive declares one — 7z LZMA and LZMA2, ZIP method 14, each xz
    block, ``.lzma`` and each lzip member. The two hazards differ. A refused
    allocation inside pyppmd takes the process down. liblzma does raise
    ``MemoryError`` when it cannot reserve the dictionary, but a reservation
    that succeeds is its real cost: the dictionary fills as output is written,
    so a 151 KB stream declaring 4 GiB held 1.1 GiB resident after producing
    1 GiB, where the same stream declaring 1 MiB held 59 MB. The dictionary
    bounds how much of the output the decoder keeps, and the archive picks it.

    The same shape holds for key derivation, which costs time rather than memory:
    RAR5 and 7z headers say how many hashing rounds turn a password into a key,
    and :attr:`max_key_derivation_rounds` caps their total over one open archive.

    This is not an :class:`ExtractionLimits` field, and the difference is not
    cosmetic. The bomb guards there measure *output*: they count bytes as an
    extraction produces them and stop when the total or the ratio says the
    archive is lying about its size. A decoder's working memory is neither
    output nor proportional to it, it is claimed up front, and it is claimed on
    ``open()`` and ``read()`` as much as on ``extract()`` — paths
    ``ExtractionLimits`` does not cover at all.

    Applied from the reader's open :attr:`ArchiveyConfig.decoder_limits` for its
    lifetime, as :class:`ListingLimits` is: the codec-layer view is built once
    when the reader is.

    ``None`` on a field disables that guard. :attr:`UNLIMITED` disables every
    one. Exceeding a guard raises
    :class:`~archivey.exceptions.ResourceLimitError` *before* the allocation,
    which is the only place it can be raised: the process has no recourse once
    the request is in the allocator's hands.

    **Detection is not capped.** Formats without magic (``.lzma``, and the
    compressed tar inside ``.tar.xz`` and its siblings) are recognised by
    decoding a small sample, and that sample is decoded with no decoder limit,
    because a capped probe would report a different format for a caller who
    passed :attr:`UNLIMITED`. The sample bounds how much of the dictionary is
    filled, not how much liblzma reserves: under a memory cap, a file declaring
    4 GiB can raise ``MemoryError`` from ``open_archive`` before this limit is
    consulted. The open that follows detection is capped as described here. Under a memory cap (a container
    limit, ``RLIMIT_AS``, a small machine) a refused native allocation does not
    surface as ``MemoryError`` — pyppmd 1.3.1 dies on ``double free or
    corruption`` and takes the interpreter with it, so no ``try``/``except``
    around the decode can contain it.

    (The ``Attributes:`` block below is the older form; new fields in this module get
    an attribute docstring after the assignment, as :class:`ArchiveyConfig` has.)

    Attributes:
        max_decoder_memory: Largest archive-declared working set a single
            decoder may allocate. The default is 2 GiB.

            That number is a policy choice, not a limit of the format, so here
            is what it was chosen against. Measured on 7-Zip 23.01, a writer
            declares whatever ``-m0=PPMd:mem=…`` asked for, reduced for a small
            member to 16× its size rounded up to a power of two (floor 64 KiB).
            Its presets never ask for much: plain ``-m0=PPMd`` declares 16 MiB
            and ``-mx9`` 256 MiB, whatever the input. Reaching 2 GiB therefore
            takes an explicit ``mem=2g``, which cannot go above it at any member
            size; only ``mem=3g`` and ``mem=4g`` do. The field itself is
            32 bits, so a header may declare just under 4 GiB — and a 153-byte
            archive may declare it, which is the case the cap is really for,
            since asking costs an attacker nothing and it is read before any
            member data is.

            2 GiB is the last round value below that 32-bit ceiling, matching
            :attr:`ExtractionLimits.max_extracted_bytes`. It admits every
            archive 7-Zip's own presets write, by a factor of eight, and admits
            a deliberate ``mem=2g`` as well; it refuses the top of the field.
            Reading archives written with ``mem=3g`` or above means raising it
            or passing :attr:`UNLIMITED`.

            The LZMA dictionary sits further under it. xz Utils refuses to
            write a dictionary over 1.5 GiB, and its ``-9`` preset writes
            64 MiB whatever the input. 7-Zip 23.01 shrinks the dictionary it
            declares to about the member's size (4 KiB for a 25-byte member,
            7 MiB for 6.7 MB, 96 MiB for 100 MB, all asked for with
            ``-md=1536m``), so declaring more than 2 GiB takes a member over
            2 GiB *and* an explicit ``-md`` above 2g. lzip's field cannot say
            more than 512 MiB. The same number serves both codecs because it
            answers the same question, and xz is checked through liblzma's own
            memory limit, which also counts about 64 KiB of the decoder's
            overhead; a 128 KiB allowance keeps a dictionary exactly at the
            cap readable there as on every other path.

            What the default is *not* is a promise about the machine. The cap
            bounds what an archive may ask for; whether an allocation succeeds
            is a property of the host, and the two are independent. A process
            with less headroom than the cap — a container under a memory limit,
            a small VM — gets nothing from the default: a declaration below
            2 GiB passes the guard, and the allocation that follows is the one
            that fails, which is the unsurvivable case described above. Such a
            process should set the cap under its own headroom, anchored on the
            limit it runs with rather than on anything 7-Zip writes. Code that
            opens files it did not choose — an upload endpoint, a mail scanner —
            wants the same move for a different reason: 256 MiB still takes
            everything the PPMd and LZMA presets produce.
        max_key_derivation_rounds: Total rounds of password-to-key derivation one
            open archive may run. The default is ``2**27``.

            RAR5 and 7z let the archive choose how expensive a key is to derive:
            RAR5's ``kdf_count`` asks for ``2**kdf_count`` PBKDF2-HMAC-SHA256
            rounds, 7z's ``NumCyclesPower`` for ``2**cycles`` SHA-256 rounds, each
            up to ``2**24`` (a few seconds). Each derivation is capped already;
            this caps the sum. RAR5 salts each encryption record and 7z each
            folder, so an archive can make every member cost a fresh derivation,
            and a candidate password list multiplies that again. The work runs
            inside ``hashlib`` with the GIL released and cannot be interrupted,
            so what the caller sees is a process that stops responding.

            Rounds are counted as the archive declares them, per derivation that
            actually runs. A key the reader already derived for the same
            password and salt comes from its cache and costs nothing, and real
            writers reuse one salt across an archive (rar 7.00 writes one per
            archiving run, 7-Zip 23.01 writes none), so an ordinary archive
            spends one or two derivations whatever its size. Every candidate
            password that is tried counts, right or wrong. The RAR3 scheme,
            whose cost is fixed at ``2**18`` SHA-1 rounds, counts at that
            number; ZIP AES (a fixed 1000 rounds) is not counted.

            ``2**27`` is eight derivations at the ``2**24`` maximum, about half
            a minute of hashing; 256 at 7-Zip's ``2**19``; 4096 at rar's
            ``2**15``. Exceeding it raises
            :class:`~archivey.exceptions.ResourceLimitError` before the
            derivation that would cross it starts. Code that opens archives it
            did not choose may want ``2**24``, one maximum-cost derivation.
    """

    max_decoder_memory: int | None = 2 * 2**30
    max_key_derivation_rounds: int | None = 2**27

    UNLIMITED: ClassVar[DecoderLimits]

    def __post_init__(self) -> None:
        cls = "DecoderLimits"
        _check_limit(self.max_decoder_memory, cls=cls, field_name="max_decoder_memory")
        _check_limit(
            self.max_key_derivation_rounds,
            cls=cls,
            field_name="max_key_derivation_rounds",
        )


DecoderLimits.UNLIMITED = DecoderLimits(
    max_decoder_memory=None,
    max_key_derivation_rounds=None,
)


@dataclass(frozen=True)
class ArchiveyConfig:
    """Library tuning knobs passed as ``config=`` to :func:`open_archive` / :func:`extract`.

    Per-call operationals (``format``, ``streaming``, ``password``, extraction's
    ``members``/``filter``/``policy``/…) stay keyword arguments — not fields here.
    """

    use_rapidgzip: AcceleratorMode = AcceleratorMode.AUTO
    """Whether to use the ``rapidgzip`` accelerator for gzip, zlib and raw deflate.

    It gives seekable member streams over those codecs. Under ``AUTO`` it is used only
    when the compressed input is known to be at least
    ``RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE`` bytes and the decompressed size can be
    verified, so a truncated stream cannot be swallowed silently.
    """

    use_indexed_bzip2: AcceleratorMode = AcceleratorMode.AUTO
    """Whether to use rapidgzip's bundled bzip2 backend for random access into bzip2."""

    zip_unflagged_fallback_encoding: str = "cp437"
    """Encoding for a ZIP member name that is neither flagged nor valid UTF-8.

    A name stored without the UTF-8 flag is tried as UTF-8 first; this encoding is used
    when those bytes are not valid UTF-8. The default is cp437, as the ZIP specification
    (APPNOTE) says. Set a local code page (for example ``"cp1252"`` or ``"shift_jis"``)
    for archives known to come from one. An explicit ``encoding=`` on
    :func:`~archivey.open_archive` overrides this and turns off the UTF-8 attempt.
    """

    rar_allow_glob_member_concatenation: bool = False
    """Read a RAR member whose stored name contains ``*`` or ``?`` even when that name
    also matches other members.

    ``unrar`` selects members by an include mask, so such a name can match other
    members as well. Names like this are almost always constructed, so the read is
    refused by default. On a nonsolid archive the extra decode is also unbounded and not
    reported (:class:`ExtractionLimits` does not cover ``open()`` or ``read()``); on a
    solid archive those bytes are already inside ``AccessCost.SOLID``. Set ``True`` to
    read it anyway. A glob name that matches no other member is unaffected either way.
    """

    read_link_targets: bool = True
    """Whether the reader reads a symlink's target when the format stores it as member
    data (ZIP, 7z, RAR3/4) rather than in the header.

    ``True``: listing and a finished ``stream_members()`` pass read every such target,
    selected or not, so the report matches random access. On ZIP and 7z that can
    decompress data nobody selected and consult the password provider. ``False``: the
    reader reads none of them on its own; ``extract_all`` reads the targets of links its
    selector and filter accept, and ``open()`` reads the target of a link it follows.
    Fixed for the reader's lifetime.
    """

    extraction_limits: ExtractionLimits = ExtractionLimits()
    """Decompression-bomb guards for extraction. See :class:`ExtractionLimits`."""

    listing_limits: ListingLimits = ListingLimits()
    """Caps on the size of a member listing. See :class:`ListingLimits`."""

    decoder_limits: DecoderLimits = DecoderLimits()
    """Caps on what the archive may make a decoder allocate or compute.

    See :class:`DecoderLimits`.
    """

    diagnostic_policy: DiagnosticPolicy = field(default_factory=DiagnosticPolicy)
    """Whether each diagnostic code is ignored, collected or raised.

    See :class:`~archivey.DiagnosticPolicy`.
    """

    max_retained_diagnostic_references: int = 256
    """How many references to diagnostics the library keeps per collector.

    Each diagnostic retained in the summary takes a slot, and each one attached to a
    member takes another. Counts stay exact past the cap. A random-access member walk
    also keeps, until it ends, a record that a walk started over after a failure
    replays: the codes of the diagnostics emitted for the members it has built, and the
    full diagnostics only for the member being typed. That record takes no slot.
    """

    on_diagnostic: OnDiagnostic | None = None
    """A callback called with each diagnostic the policy collects or raises, as it is
    emitted, or ``None``."""

    def __post_init__(self) -> None:
        """Check the fields at construction, and convert the two that hold enums.

        A config field is read wherever it is needed, which is never where it was
        written: ``ArchiveyConfig(extraction_limits="none")`` builds fine and then
        fails part-way through an extraction as ``AttributeError: 'str' object has no
        attribute 'max_extracted_bytes'`` — a private attribute name, and no mention of
        the argument the caller actually got wrong. Checking ``config=`` at the entry
        points does not reach this: the object passed there *is* an ``ArchiveyConfig``,
        and the wrong type is one field in.

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
            self.decoder_limits,
            DecoderLimits,
            call="ArchiveyConfig(decoder_limits=…)",
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
        # These two are coerced at a public boundary with no ``Literal`` alias beside
        # them (as are ``ArchiveFormat``'s two fields), and that is deliberate: the
        # annotation is read by every consumer of the attribute, not only by the
        # constructor's callers, and after construction the field always holds a
        # member. ``tests/test_enum_arguments.py``
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
    """1 on the first ask for this unit; increments on every later ask for it.

    Each ask follows a failure: either the previous answer failed to decrypt the unit,
    or it was a password that had already failed for this unit (a known-good password
    from an earlier unit, a listed candidate) and was skipped without a second try.
    Asking stops when the provider returns ``None`` or gives an answer it already gave
    for this unit.
    """


PasswordProvider = Callable[[PasswordRequest], str | bytes | None]
"""Callable consulted when static password candidates fail for an encrypted unit."""

PasswordInput = str | bytes | Sequence[str | bytes] | PasswordProvider | None
"""Accepted ``password=`` shapes: one value, an ordered candidate list, a provider, or None."""
