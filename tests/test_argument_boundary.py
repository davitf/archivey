"""No raw exception escapes the public API on a wrong-typed argument.

Every finding this file guards was found the same way: call a public entry point with
a value a caller plausibly writes, and look at what comes back. The failures were not
subtle — ``AttributeError: 'str' object has no attribute 'diagnostic_policy'`` from
``config="strict"``, ``LookupError`` from a misspelled ``encoding=``, a silently empty
extraction from ``members="notes.txt"`` — but each was reachable only by making that
one call, and nothing made the whole surface answerable at once.

So the point of this file is the **sweep**, not the individual cases, and it takes two
tests to be one. :func:`test_no_raw_exception_escapes` walks a table of (entry point,
argument, wrong value) and asserts that what escapes is on the contract's list. That
half can only ever check the rows it was handed, so
:func:`test_every_public_argument_is_swept` reads the public signatures back and fails
when an argument has neither a row nor a written exemption — which is what makes an
argument added with no check a failure here rather than a silence.

**What the contract permits**, and therefore what this test allows through
(``error-handling`` and ``archive-reading``):

* :class:`~archivey.ArchiveyUsageError` — a detected caller bug (ADR 0012), outside
  ``ArchiveyError`` so ``except ArchiveyError`` cannot swallow it. The usual answer.
* :class:`~archivey.exceptions.ArchiveyError` — an archive/environment failure.
* ``TypeError`` for a wrong-typed **source** or ``dest``, and for ``len()``/``in``.
  ``open_archive(0)`` raising ``TypeError: unsupported source type`` is deliberate:
  it is raised at the boundary with a message that names the problem, and a
  wrong-typed positional raising ``TypeError`` is what a Python caller expects.
* ``KeyError`` for an unknown member name (``archive-reading`` specifies it), plus
  ``io.UnsupportedOperation`` for an unsupported ``seek`` and ``ValueError`` for I/O
  on a closed stream — neither of which this file exercises.

Anything else is a finding. In particular an ``AttributeError`` is **never** allowed:
every one of them here named a private attribute of ours in the message, which is the
"no internal leakage" rule in ``CONTRIBUTING.md``.
"""

from __future__ import annotations

import dataclasses
import inspect
import io
import zipfile
from pathlib import Path
from typing import Any, Callable, NamedTuple

import pytest

from archivey import (
    ArchiveReader,
    ArchiveyConfig,
    DecoderLimits,
    DiagnosticPolicy,
    ExtractionLimits,
    ListingLimits,
    detect_format,
    extract,
    open_archive,
    open_stream,
)
from archivey.detection_cost import DetectionBudgetPreset, default_detection_budget
from archivey.exceptions import ArchiveyError, ArchiveyUsageError

# TypeError is permitted only for the arguments named here; see the module docstring.
_TYPE_ERROR_OK = frozenset({"source", "dest"})


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    path = tmp_path / "a.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("h.txt", "hi")
        zf.writestr("b.txt", "bye")
    return path


class _Case(NamedTuple):
    """One (entry point, argument, wrong value) probe.

    ``entry`` is the name as :func:`_public_surface` and :data:`_NOT_SWEPT` spell it,
    and it is what the inventory matches on. Keying by argument name alone was not
    enough: a ``limits`` row on ``extract`` made ``extract_all(limits=…)`` look swept,
    so removing that check would not have failed the inventory.

    ``argument`` also decides whether a bare ``TypeError`` is allowed, so a row added
    with an unfamiliar name gets the strict treatment by default.
    """

    entry: str
    argument: str
    label: str
    call: Callable[[], Any]


def _case(
    entry: str,
    argument: str,
    bad: Any,
    call: Callable[[], Any],
    *,
    label: str | None = None,
) -> _Case:
    return _Case(entry, argument, label or f"{entry}({argument}={bad!r})", call)


def _cases(archive: Path, dest: Path) -> list[_Case]:
    """Every wrong-typed public argument, as a probe the sweep can run."""
    d = iter(range(10_000))

    def out() -> Path:
        # Do not create the directory. extract / extract_all used to check
        # members= only after dest existed, and a helper that mkdir'd first
        # made any "refusal must not touch the disk" assertion dead on arrival.
        return dest / f"d{next(d)}"

    rows: list[_Case] = []

    for bad in ("x", ArchiveyConfig, 0, ()):
        rows += [
            _case(
                "open_archive",
                "config",
                bad,
                lambda b=bad: open_archive(archive, config=b),
            ),
            _case(
                "open_stream",
                "config",
                bad,
                lambda b=bad: open_stream(archive, config=b),
            ),
            _case(
                "extract",
                "config",
                bad,
                lambda b=bad: extract(archive, out(), config=b),
            ),
            _case(
                "detect_format",
                "config",
                bad,
                lambda b=bad: detect_format(archive, config=b),
            ),
        ]

    for bad in ("x", 0, ExtractionLimits):
        rows += [
            _case(
                "extract",
                "limits",
                bad,
                lambda b=bad: extract(archive, out(), limits=b),
            ),
            _case(
                "extract_all",
                "limits",
                bad,
                lambda b=bad: _extract_all(archive, out(), None, limits=b),
            ),
        ]

    for bad in ("not-a-codec", "rot13", b"utf-8", 0, ()):
        rows += [
            _case(
                "open_archive",
                "encoding",
                bad,
                lambda b=bad: open_archive(archive, encoding=b),
            ),
            _case(
                "extract",
                "encoding",
                bad,
                lambda b=bad: extract(archive, out(), encoding=b),
            ),
        ]

    # Object shape of budget=, not preset spellings. ``"balanced"`` is a real
    # DetectionBudgetPreset value, coerced by ``enum_args`` and asserted in
    # ``tests/test_enum_arguments.py``; the values below are none of the three types.
    for bad in (0, object(), "x"):
        rows.append(
            _case(
                "detect_format",
                "budget",
                bad,
                lambda b=bad: detect_format(archive, budget=b),
            )
        )

    for bad in (0, "callback", []):
        rows += [
            _case(
                "extract",
                "on_progress",
                bad,
                lambda b=bad: extract(archive, out(), on_progress=b),
            ),
            _case(
                "extract_all",
                "on_progress",
                bad,
                lambda b=bad: _extract_all(archive, out(), None, on_progress=b),
            ),
            _case(
                "extract_all",
                "filter",
                bad,
                lambda b=bad: _extract_all(archive, out(), None, filter=b),
            ),
        ]

    for bad in (0, None, object(), b"h.txt", 1.5):
        rows += [
            _case(
                "open",
                "member",
                bad,
                lambda b=bad: _open_member(archive, b),
                label=f"reader.open({bad!r})",
            ),
            _case(
                "read",
                "member",
                bad,
                lambda b=bad: _read_member(archive, b),
                label=f"reader.read({bad!r})",
            ),
        ]

    for bad in ("h.txt", [0], [None], [1.5], 0):
        rows += [
            _case(
                "extract_all",
                "members",
                bad,
                lambda b=bad: _extract_all(archive, out(), b),
            ),
            _case(
                "stream_members",
                "members",
                bad,
                lambda b=bad: _stream_members(archive, b),
            ),
        ]

    for bad in (0, object(), [0], [None], 1.5):
        rows += [
            _case(
                "open_archive",
                "password",
                bad,
                lambda b=bad: open_archive(archive, password=b),
            ),
            _case(
                "extract",
                "password",
                bad,
                lambda b=bad: extract(archive, out(), password=b),
            ),
        ]

    for bad in (0, object(), b"PK\x03\x04", None, io.StringIO("x")):
        rows += [
            _case(
                "open_archive",
                "source",
                bad,
                lambda b=bad: open_archive(b),
                label=f"open_archive({bad!r})",
            ),
            _case(
                "open_stream",
                "source",
                bad,
                lambda b=bad: open_stream(b),
                label=f"open_stream({bad!r})",
            ),
            _case(
                "detect_format",
                "source",
                bad,
                lambda b=bad: detect_format(b),
                label=f"detect_format({bad!r})",
            ),
            _case(
                "extract",
                "source",
                bad,
                lambda b=bad: extract(b, out()),
                label=f"extract({bad!r}, dest)",
            ),
        ]

    for bad in (0, None, object()):
        rows.append(_case("extract", "dest", bad, lambda b=bad: extract(archive, b)))
        rows.append(
            _case(
                "extract_all", "dest", bad, lambda b=bad: _extract_all(archive, b, None)
            )
        )

    for bad in ("x", -1, True, 1.5):
        rows += [
            _case(
                "ListingLimits",
                "max_members",
                bad,
                lambda b=bad: ListingLimits(max_members=b),
            ),
            _case(
                "ListingLimits",
                "max_metadata_bytes",
                bad,
                lambda b=bad: ListingLimits(max_metadata_bytes=b),
            ),
            _case(
                "ExtractionLimits",
                "max_extracted_bytes",
                bad,
                lambda b=bad: ExtractionLimits(max_extracted_bytes=b),
            ),
            _case(
                "ExtractionLimits",
                "max_entries",
                bad,
                lambda b=bad: ExtractionLimits(max_entries=b),
            ),
            _case(
                "DecoderLimits",
                "max_decoder_memory",
                bad,
                lambda b=bad: DecoderLimits(max_decoder_memory=b),
            ),
            _case(
                "DecoderLimits",
                "max_key_derivation_rounds",
                bad,
                lambda b=bad: DecoderLimits(max_key_derivation_rounds=b),
            ),
        ]

    # ``ratio_activation_threshold`` is the one limit field that is not ``| None``, so
    # None belongs in its bad set: it disables nothing, it breaks the comparison.
    for bad in ("x", -1, True, 1.5, None):
        rows.append(
            _case(
                "ExtractionLimits",
                "ratio_activation_threshold",
                bad,
                lambda b=bad: ExtractionLimits(ratio_activation_threshold=b),
            )
        )

    # A NaN compares false against everything and nothing exceeds an infinity, so
    # either one silently switches the ratio guard off. Only the float fields can
    # carry them.
    for bad in ("x", -1, True, float("nan"), float("inf"), float("-inf")):
        rows.append(
            _case(
                "ExtractionLimits",
                "max_ratio",
                bad,
                lambda b=bad: ExtractionLimits(max_ratio=b),
            )
        )

    # ``config=`` is checked at the entry points, but the object it names has fields of
    # its own, and those are read wherever they are needed rather than at the boundary.
    for bad in ("none", 0, ExtractionLimits, None):
        rows.append(
            _case(
                "ArchiveyConfig",
                "extraction_limits",
                bad,
                lambda b=bad: _with_config(archive, out(), extraction_limits=b),
            )
        )
    for bad in ("x", 0, ListingLimits, None):
        rows.append(
            _case(
                "ArchiveyConfig",
                "listing_limits",
                bad,
                lambda b=bad: _with_config(archive, out(), listing_limits=b),
            )
        )
    for bad in ("x", 0, DecoderLimits, None):
        rows.append(
            _case(
                "ArchiveyConfig",
                "decoder_limits",
                bad,
                lambda b=bad: _with_config(archive, out(), decoder_limits=b),
            )
        )
    for bad in ("x", 0, DiagnosticPolicy, None):
        rows.append(
            _case(
                "ArchiveyConfig",
                "diagnostic_policy",
                bad,
                lambda b=bad: _with_config(archive, out(), diagnostic_policy=b),
            )
        )
    for bad in ("x", -1, True, 1.5, None):
        rows.append(
            _case(
                "ArchiveyConfig",
                "max_retained_diagnostic_references",
                bad,
                lambda b=bad: _with_config(
                    archive, out(), max_retained_diagnostic_references=b
                ),
            )
        )
    for bad in (0, "callback", []):
        rows.append(
            _case(
                "ArchiveyConfig",
                "on_diagnostic",
                bad,
                lambda b=bad: _with_config(archive, out(), on_diagnostic=b),
            )
        )
    for bad in ("not-a-codec", "rot13", b"cp437", 0, None):
        rows.append(
            _case(
                "ArchiveyConfig",
                "zip_unflagged_fallback_encoding",
                bad,
                lambda b=bad: _with_config(
                    archive, out(), zip_unflagged_fallback_encoding=b
                ),
            )
        )

    return rows


def _open_member(archive: Path, member: Any) -> Any:
    with open_archive(archive) as reader:
        return reader.open(member)


def _extract_all(archive: Path, dest: Path, members: Any, **kwargs: Any) -> Any:
    with open_archive(archive) as reader:
        return reader.extract_all(dest, members=members, **kwargs)


def _read_member(archive: Path, member: Any) -> Any:
    with open_archive(archive) as reader:
        return reader.read(member)


def _stream_members(archive: Path, members: Any) -> Any:
    with open_archive(archive) as reader:
        # Do not wrap in list(): a check left inside the generator would still
        # raise on first next() and look like a call-time refusal.
        return reader.stream_members(members=members)


def _with_config(archive: Path, dest: Path, **field: Any) -> Any:
    """Build a config with one bad field and put it through a full extraction.

    Construction is where the refusal should happen, but the call is what proves it:
    every one of these fields used to survive construction and fail somewhere inside
    the extraction instead, naming a private attribute.
    """
    return extract(archive, dest, config=ArchiveyConfig(**field))


def test_no_raw_exception_escapes(archive: Path, tmp_path: Path) -> None:
    """Every wrong-typed public argument fails inside the error contract."""
    dest = tmp_path / "out"
    dest.mkdir()

    offenders: list[str] = []
    for _entry, argument, label, call in _cases(archive, dest):
        lenient = argument in _TYPE_ERROR_OK
        try:
            call()
        except ArchiveyUsageError:
            continue
        except ArchiveyError as exc:
            # Only the source rows may answer this way, and only because a wrong-typed
            # source can also be a real one that fails to open (``b"PK\x03\x04"`` is a
            # truncated ZIP, not a type error). Everywhere else an ``ArchiveyError``
            # means the wrong argument was taken for archive data.
            if not lenient:
                offenders.append(
                    f"{label}: {type(exc).__name__} (want ArchiveyUsageError): {exc}"
                )
        except TypeError as exc:
            if not lenient:
                offenders.append(f"{label}: raw {type(exc).__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001 — the point is to catch everything
            offenders.append(f"{label}: raw {type(exc).__name__}: {exc}")
        else:
            offenders.append(f"{label}: no error at all")

    assert not offenders, "raw exceptions escaped the public API:\n" + "\n".join(
        offenders
    )


# Arguments the sweep deliberately does not cover, and why. Anything not listed here
# and not exercised by a row in :func:`_cases` fails
# :func:`test_every_public_argument_is_swept` — which is the half of this file that can
# notice an argument nobody thought about.
_NOT_SWEPT: dict[tuple[str, str], str] = {
    # ``format=`` is owned by ``internal/format_args``, which **coerces** it: a
    # recognised spelling becomes the ``ArchiveFormat``, and anything else raises
    # ``ArchiveyUsageError`` at the entry point, before a row here would ever see it.
    # Kept out of this table so the two do not drift into disagreeing about the answer;
    # ``tests/test_format_arguments.py`` asserts the behaviour. (``ArchiveFormat`` is a
    # plain class, not an enum — it is grouped with the enums by nobody but its
    # argument name.)
    ("open_archive", "format"): "coerced by format_args; test_format_arguments.py",
    ("open_stream", "format"): "coerced by format_args; test_format_arguments.py",
    ("extract", "format"): "coerced by format_args; test_format_arguments.py",
    # The enum-typed arguments, owned by ``internal/enum_args``, which **coerces**
    # them: a recognised spelling becomes the member, so a refusal here would
    # contradict it. ``tests/test_enum_arguments.py`` asserts what they do; this
    # module must not assert the opposite.
    #
    # These are guarded, and the coercion runs at the entry point, so a bad value
    # raises ``ArchiveyUsageError`` before reaching the code this table is about.
    # But read the exemption for what it is: it records that another module *owns*
    # the argument, never that the argument is safe today. Neither test above can
    # tell those apart, so a wrong exemption here is live rather than stale — the
    # blind spot left once ``test_not_swept_entries_are_all_live`` has done its half.
    # Measured on this tree rather than assumed: ``policy="looose"``,
    # ``overwrite=0``, ``on_error="halt"``, ``abort_on="blocked"`` and ``abort_on=0``
    # all answer ``ArchiveyUsageError``. Anything that turns out not to be covered
    # belongs in _cases, not here.
    ("extract", "policy"): "coerced by enum_args; test_enum_arguments.py",
    ("extract", "overwrite"): "coerced by enum_args; test_enum_arguments.py",
    ("extract", "on_error"): "coerced by enum_args; test_enum_arguments.py",
    # Collection[AbortOn], not an enum, so the container shape is a second way to get
    # it wrong. ``coerce_enum_collection`` refuses both: a bare string, which would
    # otherwise iterate into characters and silently disable every abort, and a
    # non-iterable, which would otherwise be a raw TypeError.
    ("extract", "abort_on"): (
        "Collection[AbortOn]; container-shape refusal is coerce_enum_collection "
        "in enum_args"
    ),
    ("extract_all", "policy"): "coerced by enum_args; test_enum_arguments.py",
    ("extract_all", "overwrite"): "coerced by enum_args; test_enum_arguments.py",
    ("extract_all", "on_error"): "coerced by enum_args; test_enum_arguments.py",
    ("extract_all", "abort_on"): (
        "Collection[AbortOn]; container-shape refusal is coerce_enum_collection "
        "in enum_args"
    ),
    ("ArchiveyConfig", "use_rapidgzip"): "coerced by enum_args, in __post_init__",
    (
        "ArchiveyConfig",
        "use_indexed_bzip2",
    ): "coerced by enum_args, in __post_init__",
    # Flags read for their truthiness. There is no wrong type to find: every value
    # means something, and ``streaming="no"`` opening in streaming mode is Python
    # behaving as written, not a leak.
    ("open_archive", "streaming"): "truthiness flag",
    ("open_archive", "seekable_members"): "truthiness flag",
    ("open_archive", "concurrent_members"): "truthiness flag",
    ("open_stream", "seekable"): "truthiness flag",
    ("detect_format", "follow_stub_volumes"): "truthiness flag",
    (
        "ArchiveyConfig",
        "rar_allow_glob_member_concatenation",
    ): "truthiness flag",
    ("ArchiveyConfig", "read_link_targets"): "truthiness flag",
    # An internal type, accepted so a caller can thread one detection's diagnostics
    # into the reader that follows. A wrong one fails on its own methods, inside code
    # the caller reached for deliberately.
    ("detect_format", "collector"): "internal type, deliberate hand-off",
    # ``get`` is mapping-shaped on purpose: like ``dict.get`` it answers with the
    # default rather than raising, so ``reader.get(0)`` returning ``None`` is the
    # contract, not an escape. ``reader.open("absent.txt")`` is where a lookup raises.
    ("get", "name"): "mapping-shaped; returns the default rather than raising",
    ("get", "default"): "any object is a valid default",
}


def _public_surface() -> list[tuple[str, list[str]]]:
    """(name, argument names) for every public entry point this file is about."""
    surface: list[tuple[str, list[str]]] = []
    for func in (open_archive, open_stream, extract, detect_format):
        surface.append((func.__name__, list(inspect.signature(func).parameters)))
    for method in ("open", "read", "extract_all", "stream_members", "get"):
        names = list(inspect.signature(getattr(ArchiveReader, method)).parameters)
        surface.append((method, [n for n in names if n != "self"]))
    for cls in (ArchiveyConfig, DecoderLimits, ExtractionLimits, ListingLimits):
        surface.append((cls.__name__, [f.name for f in dataclasses.fields(cls)]))
    return surface


def test_every_public_argument_is_swept(archive: Path, tmp_path: Path) -> None:
    """A public argument added without a row fails here.

    This is the half of the file that can fail for something *absent*.
    :func:`test_no_raw_exception_escapes` only ever checks the rows it was given, so on
    its own it goes quiet exactly when a new argument arrives unguarded — the failure
    mode of every "we swept it once" claim in this repo. Reading the signatures back
    means the table has to keep up with the API or say in :data:`_NOT_SWEPT` why not.
    """
    dest = tmp_path / "covered"
    dest.mkdir()
    swept = {(case.entry, case.argument) for case in _cases(archive, dest)}

    missing: list[str] = []
    for name, arguments in _public_surface():
        for argument in arguments:
            if (name, argument) in swept or (name, argument) in _NOT_SWEPT:
                continue
            missing.append(f"{name}({argument}=…)")

    assert not missing, (
        "public arguments with no row in _cases() and no entry in _NOT_SWEPT:\n"
        + "\n".join(missing)
    )


def test_not_swept_entries_are_all_live(archive: Path, tmp_path: Path) -> None:
    """:data:`_NOT_SWEPT` does not outlive the arguments it excuses.

    An exemption for an argument that no longer exists is how an exclusion list turns
    into a place to hide one: the name stays, a real argument is added with it later,
    and nothing notices. An exemption for an argument that *is* swept is the same rot
    from the other end — it reads as a decision not to check something this file does
    check, so a later reader trusts the wrong half.
    """
    dest = tmp_path / "live"
    dest.mkdir()
    swept = {(case.entry, case.argument) for case in _cases(archive, dest)}
    live = {
        (name, argument)
        for name, arguments in _public_surface()
        for argument in arguments
    }
    stale = sorted(
        f"{name}({argument}=…)"
        for name, argument in _NOT_SWEPT
        if (name, argument) not in live
    )
    assert not stale, "_NOT_SWEPT names arguments that no longer exist:\n" + "\n".join(
        stale
    )

    contradicted = sorted(
        f"{name}({argument}=…)"
        for name, argument in _NOT_SWEPT
        if (name, argument) in swept
    )
    assert not contradicted, (
        "_NOT_SWEPT excuses arguments that _cases() does sweep:\n"
        + "\n".join(contradicted)
    )


def test_no_usage_error_message_names_a_private_attribute(
    archive: Path, tmp_path: Path
) -> None:
    """A refusal explains the argument; it never leaks an internal field name.

    Every case in the sweep failed this way before the boundary checks existed
    (``'str' object has no attribute 'max_extracted_bytes'``), and the message is
    what makes the difference between a refusal and a crash.
    """
    dest = tmp_path / "out2"
    dest.mkdir()

    leaks: list[str] = []
    for _entry, _argument, label, call in _cases(archive, dest):
        try:
            call()
        except Exception as exc:  # noqa: BLE001 — inspecting whatever comes back
            message = str(exc)
            if "has no attribute" in message or "_archive_id" in message:
                leaks.append(f"{label}: {message}")

    assert not leaks, "usage errors named a private attribute:\n" + "\n".join(leaks)


def test_valid_arguments_still_work(archive: Path, tmp_path: Path) -> None:
    """The guards refuse only what they are meant to refuse."""
    dest = tmp_path / "ok"
    dest.mkdir()

    assert detect_format(archive).format.container.name == "ZIP"
    assert (
        detect_format(
            archive, budget=DetectionBudgetPreset.BALANCED
        ).format.container.name
        == "ZIP"
    )
    assert (
        detect_format(archive, budget=default_detection_budget()).format.container.name
        == "ZIP"
    )
    assert extract(archive, dest / "a", config=ArchiveyConfig()).results
    assert extract(archive, dest / "b", limits=ExtractionLimits.UNLIMITED).results
    assert extract(archive, dest / "c", encoding="UTF8").results  # an alias, not a name
    assert extract(archive, dest / "d", on_progress=lambda _p: None).results

    with open_archive(archive) as reader:
        members = list(reader)
        assert reader.open("h.txt").read() == b"hi"
        assert reader.open(members[0]) is not None
        report = reader.extract_all(dest / "e", members=["h.txt"])
        assert [r.member.name for r in report.results] == ["h.txt"]
        report = reader.extract_all(dest / "f", members=members[:1])
        assert len(report.results) == 1

    assert ListingLimits(max_members=None) == ListingLimits(max_members=None)
    assert ExtractionLimits(max_ratio=2.5).max_ratio == 2.5


def test_unknown_member_name_still_raises_keyerror(archive: Path) -> None:
    """``KeyError`` for an absent name is specified, and the member guard keeps it.

    The guard added for a wrong-typed member sits on the other branch; a string that
    names nothing must still behave like a mapping lookup (``archive-reading``).
    """
    with open_archive(archive) as reader:
        with pytest.raises(KeyError):
            reader.open("nope.txt")


def test_extract_all_wrong_typed_members_does_not_create_dest(
    archive: Path, tmp_path: Path
) -> None:
    """A members= refusal must not have already created dest (K3)."""
    dest = tmp_path / "should_not_exist"
    with open_archive(archive) as reader:
        with pytest.raises(ArchiveyUsageError):
            reader.extract_all(dest, members=0)
    assert not dest.exists()


def test_stream_members_wrong_typed_members_raises_at_the_call(archive: Path) -> None:
    """stream_members(members=0) must refuse before returning a generator (K3)."""
    with open_archive(archive) as reader:
        with pytest.raises(ArchiveyUsageError):
            reader.stream_members(members=0)


def test_members_bytes_message_does_not_advise_wrapping(
    archive: Path, tmp_path: Path
) -> None:
    """Pass [b'notes.txt'] is itself a usage error; do not suggest it (K4)."""
    dest = tmp_path / "b"
    with open_archive(archive) as reader:
        with pytest.raises(ArchiveyUsageError) as caught:
            reader.extract_all(dest, members=b"notes.txt")
    assert "Pass [" not in str(caught.value)
