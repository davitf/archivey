"""No raw exception escapes the public API on a wrong-typed argument.

Every finding this file guards was found the same way: call a public entry point with
a value a caller plausibly writes, and look at what comes back. The failures were not
subtle — ``AttributeError: 'str' object has no attribute 'diagnostic_policy'`` from
``config="strict"``, ``LookupError`` from a misspelled ``encoding=``, a silently empty
extraction from ``members="notes.txt"`` — but each was reachable only by making that
one call, and nothing made the whole surface answerable at once.

So the point of this file is the **sweep**, not the individual cases:
:func:`test_no_raw_exception_escapes` walks a table of (entry point, argument, wrong
value) and asserts that what escapes is on the contract's list. A new public argument
added without a boundary check fails here as soon as someone adds its row, and the
table is short enough to be worth adding a row to.

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
from typing import Any, Callable

import pytest

from archivey import (
    ArchiveReader,
    ArchiveyConfig,
    DiagnosticPolicy,
    ExtractionLimits,
    ListingLimits,
    detect_format,
    extract,
    open_archive,
    open_stream,
)
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


def _cases(archive: Path, dest: Path) -> list[tuple[str, str, Callable[[], Any]]]:
    """(label, argument, call) for every wrong-typed public argument.

    ``argument`` is only used to decide whether a bare ``TypeError`` is allowed, so a
    row added with any other name gets the strict treatment by default.
    """
    d = iter(range(10_000))

    def out() -> Path:
        sub = dest / f"d{next(d)}"
        sub.mkdir()
        return sub

    rows: list[tuple[str, str, Callable[[], Any]]] = []

    for bad in ("x", ArchiveyConfig, 0, ()):
        rows += [
            (
                f"open_archive(config={bad!r})",
                "config",
                lambda b=bad: open_archive(archive, config=b),
            ),
            (
                f"open_stream(config={bad!r})",
                "config",
                lambda b=bad: open_stream(archive, config=b),
            ),
            (
                f"extract(config={bad!r})",
                "config",
                lambda b=bad: extract(archive, out(), config=b),
            ),
            (
                f"detect_format(config={bad!r})",
                "config",
                lambda b=bad: detect_format(archive, config=b),
            ),
        ]

    for bad in ("x", 0, ExtractionLimits):
        rows += [
            (
                f"extract(limits={bad!r})",
                "limits",
                lambda b=bad: extract(archive, out(), limits=b),
            ),
        ]

    for bad in ("not-a-codec", "rot13", b"utf-8", 0, ()):
        rows += [
            (
                f"open_archive(encoding={bad!r})",
                "encoding",
                lambda b=bad: open_archive(archive, encoding=b),
            ),
            (
                f"extract(encoding={bad!r})",
                "encoding",
                lambda b=bad: extract(archive, out(), encoding=b),
            ),
        ]

    for bad in (0, "callback", []):
        rows.append(
            (
                f"extract(on_progress={bad!r})",
                "on_progress",
                lambda b=bad: extract(archive, out(), on_progress=b),
            )
        )

    for bad in (0, None, object(), b"h.txt", 1.5):
        rows.append(
            (f"reader.open({bad!r})", "member", lambda b=bad: _open_member(archive, b))
        )

    for bad in ("h.txt", [0], [None], [1.5], 0):
        rows.append(
            (
                f"extract_all(members={bad!r})",
                "members",
                lambda b=bad: _extract_all(archive, out(), b),
            )
        )

    for bad in (0, object(), [0], [None], 1.5):
        rows.append(
            (
                f"open_archive(password={bad!r})",
                "password",
                lambda b=bad: open_archive(archive, password=b),
            )
        )

    for bad in (0, object(), b"PK\x03\x04", None, io.StringIO("x")):
        rows += [
            (f"open_archive({bad!r})", "source", lambda b=bad: open_archive(b)),
            (f"open_stream({bad!r})", "source", lambda b=bad: open_stream(b)),
            (f"detect_format({bad!r})", "source", lambda b=bad: detect_format(b)),
        ]

    for bad in (0, None, object()):
        rows.append(
            (f"extract(dest={bad!r})", "dest", lambda b=bad: extract(archive, b))
        )

    for bad in (0, "callback", []):
        rows.append(
            (
                f"extract_all(filter={bad!r})",
                "filter",
                lambda b=bad: _extract_all(archive, out(), None, filter=b),
            )
        )

    for bad in ("x", -1, True, 1.5):
        rows += [
            (
                f"ListingLimits(max_members={bad!r})",
                "max_members",
                lambda b=bad: ListingLimits(max_members=b),
            ),
            (
                f"ListingLimits(max_metadata_bytes={bad!r})",
                "max_metadata_bytes",
                lambda b=bad: ListingLimits(max_metadata_bytes=b),
            ),
            (
                f"ExtractionLimits(max_extracted_bytes={bad!r})",
                "max_extracted_bytes",
                lambda b=bad: ExtractionLimits(max_extracted_bytes=b),
            ),
            (
                f"ExtractionLimits(max_entries={bad!r})",
                "max_entries",
                lambda b=bad: ExtractionLimits(max_entries=b),
            ),
        ]

    # ``ratio_activation_threshold`` is the one limit field that is not ``| None``, so
    # None belongs in its bad set: it disables nothing, it breaks the comparison.
    for bad in ("x", -1, True, 1.5, None):
        rows.append(
            (
                f"ExtractionLimits(ratio_activation_threshold={bad!r})",
                "ratio_activation_threshold",
                lambda b=bad: ExtractionLimits(ratio_activation_threshold=b),
            )
        )

    # A NaN compares false against everything and nothing exceeds an infinity, so
    # either one silently switches the ratio guard off. Only the float fields can
    # carry them.
    for bad in ("x", -1, True, float("nan"), float("inf"), float("-inf")):
        rows.append(
            (
                f"ExtractionLimits(max_ratio={bad!r})",
                "max_ratio",
                lambda b=bad: ExtractionLimits(max_ratio=b),
            )
        )

    # ``config=`` is checked at the entry points, but the object it names has fields of
    # its own, and those are read wherever they are needed rather than at the boundary.
    for bad in ("none", 0, ExtractionLimits, None):
        rows.append(
            (
                f"ArchiveyConfig(extraction_limits={bad!r})",
                "extraction_limits",
                lambda b=bad: _with_config(archive, out(), extraction_limits=b),
            )
        )
    for bad in ("x", 0, ListingLimits, None):
        rows.append(
            (
                f"ArchiveyConfig(listing_limits={bad!r})",
                "listing_limits",
                lambda b=bad: _with_config(archive, out(), listing_limits=b),
            )
        )
    for bad in ("x", 0, DiagnosticPolicy, None):
        rows.append(
            (
                f"ArchiveyConfig(diagnostic_policy={bad!r})",
                "diagnostic_policy",
                lambda b=bad: _with_config(archive, out(), diagnostic_policy=b),
            )
        )
    for bad in ("x", -1, True, 1.5, None):
        rows.append(
            (
                f"ArchiveyConfig(max_retained_diagnostic_references={bad!r})",
                "max_retained_diagnostic_references",
                lambda b=bad: _with_config(
                    archive, out(), max_retained_diagnostic_references=b
                ),
            )
        )
    for bad in (0, "callback", []):
        rows.append(
            (
                f"ArchiveyConfig(on_diagnostic={bad!r})",
                "on_diagnostic",
                lambda b=bad: _with_config(archive, out(), on_diagnostic=b),
            )
        )
    for bad in ("not-a-codec", "rot13", b"cp437", 0, None):
        rows.append(
            (
                f"ArchiveyConfig(zip_unflagged_fallback_encoding={bad!r})",
                "zip_unflagged_fallback_encoding",
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
    for label, argument, call in _cases(archive, dest):
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
    # Enums, coerced rather than refused, guarded by ``internal/enum_args`` and its own
    # test module. Kept out of here so the two do not disagree about the answer.
    ("open_archive", "format"): "enum; internal/enum_args",
    ("open_stream", "format"): "enum; internal/enum_args",
    ("extract", "format"): "enum; internal/enum_args",
    ("extract", "policy"): "enum; internal/enum_args",
    ("extract", "overwrite"): "enum; internal/enum_args",
    ("extract", "on_error"): "enum; internal/enum_args",
    ("extract", "abort_on"): "enum; internal/enum_args",
    ("extract_all", "policy"): "enum; internal/enum_args",
    ("extract_all", "overwrite"): "enum; internal/enum_args",
    ("extract_all", "on_error"): "enum; internal/enum_args",
    ("extract_all", "abort_on"): "enum; internal/enum_args",
    ("detect_format", "budget"): "enum/preset; internal/enum_args",
    ("ArchiveyConfig", "use_rapidgzip"): "enum; internal/enum_args",
    ("ArchiveyConfig", "use_indexed_bzip2"): "enum; internal/enum_args",
    # Flags read for their truthiness. There is no wrong type to find: every value
    # means something, and ``streaming="no"`` opening in streaming mode is Python
    # behaving as written, not a leak.
    ("open_archive", "streaming"): "truthiness flag",
    ("open_archive", "seekable_members"): "truthiness flag",
    ("open_archive", "concurrent_members"): "truthiness flag",
    ("open_stream", "seekable"): "truthiness flag",
    ("detect_format", "follow_stub_volumes"): "truthiness flag",
    ("ArchiveyConfig", "strict_archive_eof"): "truthiness flag",
    # An internal type, accepted so a caller can thread one detection's diagnostics
    # into the reader that follows. A wrong one fails on its own methods, inside code
    # the caller reached for deliberately.
    ("detect_format", "collector"): "internal type, deliberate hand-off",
    # ``read`` resolves its member through ``open``, so the ``reader.open`` rows cover
    # both; a separate set would assert the same guard twice.
    ("read", "member"): "same guard as reader.open",
}


def _public_surface() -> list[tuple[str, list[str]]]:
    """(name, argument names) for every public entry point this file is about."""
    surface: list[tuple[str, list[str]]] = []
    for func in (open_archive, open_stream, extract, detect_format):
        surface.append((func.__name__, list(inspect.signature(func).parameters)))
    for method in ("open", "read", "extract_all"):
        names = list(inspect.signature(getattr(ArchiveReader, method)).parameters)
        surface.append((method, [n for n in names if n != "self"]))
    for cls in (ArchiveyConfig, ExtractionLimits, ListingLimits):
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
    swept = {argument for _label, argument, _call in _cases(archive, dest)}

    missing: list[str] = []
    for name, arguments in _public_surface():
        for argument in arguments:
            if argument in swept or (name, argument) in _NOT_SWEPT:
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
    and nothing notices.
    """
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
    for label, _argument, call in _cases(archive, dest):
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
