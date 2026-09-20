"""The public API's enum arguments accept string spellings, and refuse bad ones loudly.

Two things are under test, and the second is the reason the first exists.

**The contract.** Every ``Enum`` parameter on a public entry point takes the member, its
``value`` or its name, in any case, with ``-`` and ``_`` interchangeable; anything else
raises :class:`ArchiveyUsageError` at the call, naming the spellings that would have
worked.

**The bugs it closes.** The consuming code tests these with ``is``, so before coercion an
unrecognised value was not refused — it silently took the other branch.
``extract(overwrite="skip")`` fell through to REPLACE and deleted the file the caller
asked to keep; ``extract(on_error="stop")`` behaved as CONTINUE and swallowed a
corruption error. Both have a red-green test here.

The collision guards matter more than they look: coercion is only safe while no two
members share a normalized spelling. They fail on the day a new value introduces an
ambiguity, rather than leaving it to resolve silently to the wrong member.
"""

from __future__ import annotations

import os
import struct
import zipfile
from enum import Enum
from pathlib import Path

import pytest

from archivey import (
    AbortOn,
    ExtractionPolicy,
    ExtractionStatus,
    OnError,
    OverwritePolicy,
    extract,
)
from archivey.config import AcceleratorMode, ArchiveyConfig
from archivey.detection_cost import DetectionBudgetPreset
from archivey.exceptions import ArchiveyError, ArchiveyUsageError
from archivey.internal.enum_args import (
    coerce_enum,
    coerce_enum_collection,
    normalize_spelling,
)

# Every enum reachable from a public argument.
PUBLIC_ENUMS: tuple[type[Enum], ...] = (
    ExtractionPolicy,
    OverwritePolicy,
    OnError,
    AbortOn,
    AcceleratorMode,
    DetectionBudgetPreset,
)


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    path = tmp_path / "a.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("hello.txt", "original")
    return path


# --- the coercion contract ---------------------------------------------------------


@pytest.mark.parametrize("enum_cls", PUBLIC_ENUMS, ids=lambda c: c.__name__)
def test_no_two_members_share_a_normalized_spelling(enum_cls: type[Enum]) -> None:
    """The guard that makes case- and dash-insensitivity safe to offer.

    If this fails, a new member's value or name collides with another's under
    ``normalize_spelling``, and one of the two has become unreachable by that spelling.
    Fix the value, do not relax the normalization — callers are already using it.
    """
    seen: dict[str, str] = {}
    for member in enum_cls:
        spellings = {normalize_spelling(member.name)}
        if isinstance(member.value, str):
            spellings.add(normalize_spelling(member.value))
        for spelling in spellings:
            clash = seen.get(spelling)
            assert clash is None or clash == member.name, (
                f"{enum_cls.__name__}: {spelling!r} spells both {clash} and "
                f"{member.name}"
            )
            seen[spelling] = member.name


@pytest.mark.parametrize("enum_cls", PUBLIC_ENUMS, ids=lambda c: c.__name__)
def test_every_member_is_reachable_by_every_documented_spelling(
    enum_cls: type[Enum],
) -> None:
    for member in enum_cls:
        spellings = [member.name, member.name.lower(), member.name.replace("_", "-")]
        if isinstance(member.value, str):
            spellings += [
                member.value,
                member.value.upper(),
                member.value.replace("_", "-"),
                f"  {member.value}  ",
            ]
        for spelling in spellings:
            assert coerce_enum(spelling, enum_cls, call="t()", param="p=") is member, (
                f"{enum_cls.__name__}: {spelling!r} did not resolve to {member.name}"
            )


@pytest.mark.parametrize("enum_cls", PUBLIC_ENUMS, ids=lambda c: c.__name__)
def test_an_unknown_spelling_names_the_ones_that_work(enum_cls: type[Enum]) -> None:
    with pytest.raises(ArchiveyUsageError) as exc_info:
        coerce_enum("definitely-not-a-member", enum_cls, call="t()", param="p=")

    message = str(exc_info.value)
    assert enum_cls.__name__ in message
    for member in enum_cls:
        expected = str(member.value) if isinstance(member.value, str) else member.name
        assert repr(expected) in message


def test_a_member_of_the_wrong_enum_is_a_type_error_not_a_spelling_error() -> None:
    """``AbortOn`` mixes in ``str``, so it would otherwise land in the string branch."""
    with pytest.raises(ArchiveyUsageError) as exc_info:
        coerce_enum(AbortOn.BLOCKED_MEMBER, OverwritePolicy, call="t()", param="p=")

    message = str(exc_info.value)
    assert "AbortOn.BLOCKED_MEMBER" in message
    assert "OverwritePolicy" in message


def test_a_bare_string_is_not_a_collection_of_one() -> None:
    """``abort_on="blocked_member"`` is a typo, not eleven single-character members."""
    with pytest.raises(ArchiveyUsageError) as exc_info:
        coerce_enum_collection("blocked_member", AbortOn, call="t()", param="abort_on=")

    assert "['blocked_member']" in str(exc_info.value)


def test_usage_errors_escape_archiveyerror_handlers() -> None:
    """ADR 0012: a caller mistake must not be swallowed by the untrusted-input handler."""
    with pytest.raises(ArchiveyUsageError):
        try:
            coerce_enum("nope", OverwritePolicy, call="t()", param="p=")
        except ArchiveyError:  # pragma: no cover - the point is that it does not fire
            pytest.fail("ArchiveyUsageError was swallowed by `except ArchiveyError`")


# --- the bugs it closes ------------------------------------------------------------


def test_overwrite_as_a_string_skips_instead_of_replacing(
    archive: Path, tmp_path: Path
) -> None:
    """Red-green for the data loss: ``"skip"`` used to fall through to REPLACE.

    The branch chain is ``is ERROR`` / ``is SKIP`` / else replace, so a string matched
    neither and landed on the one branch that unlinks the existing entry — destroying
    the local file the caller had asked to keep.
    """
    dest = tmp_path / "out"
    extract(archive, dest)
    (dest / "hello.txt").write_text("LOCAL EDIT")

    report = extract(archive, dest, overwrite="skip")

    assert (dest / "hello.txt").read_text() == "LOCAL EDIT"
    assert [r.status for r in report.results] == [ExtractionStatus.NOT_OVERWRITTEN]


def test_overwrite_as_a_string_agrees_with_the_member(
    archive: Path, tmp_path: Path
) -> None:
    results = {}
    for label, value in (("str", "replace"), ("enum", OverwritePolicy.REPLACE)):
        dest = tmp_path / label
        extract(archive, dest)
        (dest / "hello.txt").write_text("LOCAL EDIT")
        report = extract(archive, dest, overwrite=value)
        results[label] = (
            (dest / "hello.txt").read_text(),
            [r.status for r in report.results],
        )
    assert results["str"] == results["enum"]


def test_on_error_as_a_string_stops_instead_of_continuing(tmp_path: Path) -> None:
    """Red-green: every site is ``is OnError.STOP``, so a string took the CONTINUE path.

    A STORED member with its data bytes flipped fails its CRC on read and nothing else,
    which is the per-member failure ``on_error`` governs.
    """
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("ok1.txt", "fine")
        zf.writestr("broken.txt", "y" * 2000)
        zf.writestr("ok2.txt", "also fine")

    raw = bytearray(path.read_bytes())
    offset = raw.find(b"PK\x03\x04")
    while offset != -1:
        name_len, extra_len = struct.unpack_from("<HH", raw, offset + 26)
        name = bytes(raw[offset + 30 : offset + 30 + name_len])
        data = offset + 30 + name_len + extra_len
        if name == b"broken.txt":
            for i in range(data + 100, data + 200):
                raw[i] ^= 0xFF
            break
        offset = raw.find(b"PK\x03\x04", offset + 1)
    path.write_bytes(bytes(raw))

    with pytest.raises(ArchiveyError):
        extract(path, tmp_path / "stop_str", on_error="stop")

    # The opposite value still reports rather than raising, so the test above is not
    # just asserting that any extraction of this archive fails.
    report = extract(path, tmp_path / "continue", on_error="continue")
    assert ExtractionStatus.FAILED in [r.status for r in report.results]


@pytest.mark.parametrize(
    ("param", "value"),
    [
        ("policy", "nonsense"),
        ("overwrite", "nonsense"),
        ("on_error", "nonsense"),
    ],
)
def test_extract_refuses_a_bad_spelling_with_a_usage_error(
    archive: Path, tmp_path: Path, param: str, value: str
) -> None:
    """Not a ``KeyError`` from a transform table, and not silence."""
    with pytest.raises(ArchiveyUsageError) as exc_info:
        extract(archive, tmp_path / "out", **{param: value})

    assert param in str(exc_info.value)


def test_extract_refuses_before_it_writes_anything(
    archive: Path, tmp_path: Path
) -> None:
    dest = tmp_path / "out"
    with pytest.raises(ArchiveyUsageError):
        extract(archive, dest, overwrite="nonsense")

    assert not dest.exists() or not os.listdir(dest)


def test_abort_on_accepts_the_spelling_the_cli_advertises(
    archive: Path, tmp_path: Path
) -> None:
    """``--abort-on blocked-member`` from ``--help``, pasted into a script."""
    extract(archive, tmp_path / "out", abort_on=["blocked-member"])


# --- the same contract on the other entry points -----------------------------------


def test_archivey_config_stores_the_member_not_the_string() -> None:
    """The field is read with ``is``, so a surviving string would read as AUTO."""
    config = ArchiveyConfig(use_rapidgzip="on", use_indexed_bzip2="off")
    assert config.use_rapidgzip is AcceleratorMode.ON
    assert config.use_indexed_bzip2 is AcceleratorMode.OFF


def test_archivey_config_refuses_a_bad_accelerator_spelling() -> None:
    with pytest.raises(ArchiveyUsageError) as exc_info:
        ArchiveyConfig(use_rapidgzip="sometimes")

    assert "use_rapidgzip" in str(exc_info.value)


def test_detect_format_takes_a_budget_preset_by_name(archive: Path) -> None:
    from archivey.internal.detection import detect_format

    assert detect_format(archive, budget="fast").format is not None


def test_detect_format_refuses_an_unknown_budget_without_an_attribute_error(
    archive: Path,
) -> None:
    """It used to return the string unchanged and die on ``budget.max_tail_bytes``."""
    from archivey.internal.detection import detect_format

    with pytest.raises(ArchiveyUsageError) as exc_info:
        detect_format(archive, budget="turbo")

    assert "'fast'" in str(exc_info.value)
