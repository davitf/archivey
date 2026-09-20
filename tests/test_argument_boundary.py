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

import io
import zipfile
from pathlib import Path
from typing import Any, Callable

import pytest

from archivey import (
    ArchiveyConfig,
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

    for bad in ("x", -1, True, 1.5):
        rows += [
            (
                f"ListingLimits(max_members={bad!r})",
                "max_members",
                lambda b=bad: ListingLimits(max_members=b),
            ),
            (
                f"ExtractionLimits(max_extracted_bytes={bad!r})",
                "max_extracted_bytes",
                lambda b=bad: ExtractionLimits(max_extracted_bytes=b),
            ),
        ]

    return rows


def _open_member(archive: Path, member: Any) -> Any:
    with open_archive(archive) as reader:
        return reader.open(member)


def _extract_all(archive: Path, dest: Path, members: Any) -> Any:
    with open_archive(archive) as reader:
        return reader.extract_all(dest, members=members)


def test_no_raw_exception_escapes(archive: Path, tmp_path: Path) -> None:
    """Every wrong-typed public argument fails inside the error contract."""
    dest = tmp_path / "out"
    dest.mkdir()

    offenders: list[str] = []
    for label, argument, call in _cases(archive, dest):
        try:
            call()
        except (ArchiveyUsageError, ArchiveyError):
            continue
        except TypeError as exc:
            if argument not in _TYPE_ERROR_OK:
                offenders.append(f"{label}: raw {type(exc).__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001 — the point is to catch everything
            offenders.append(f"{label}: raw {type(exc).__name__}: {exc}")
        else:
            offenders.append(f"{label}: no error at all")

    assert not offenders, "raw exceptions escaped the public API:\n" + "\n".join(
        offenders
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
