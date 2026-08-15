"""Escaping of attacker-controlled text, and the escape-exactly-once rule around it.

Assertions here are on *behaviour* — inertness, round-trip distinctions, absence of
doubling — rather than on golden strings, because the rendering of an individual escape
is delegated to ``repr`` and is not a documented CPython guarantee.
"""

from __future__ import annotations

import ast
import os
import pathlib
import re

import pytest

from archivey.diagnostics import (
    Diagnostic,
    DiagnosticCode,
    DiagnosticSeverity,
    MemberNameControlsContext,
)
from archivey.escaping import escape_control_chars, quoted
from archivey.exceptions import ArchiveyError, ExtractionError, raw_message_of

# Characters that must never survive into a terminal unescaped.
_DANGEROUS = ("\x1b", "\r", "\n", "\x00", "\x9b")


@pytest.mark.parametrize(
    "text",
    [
        "\x1b[2K\r",  # the line-erase spoof
        "\x00",
        "\x7f",
        "\x9b",  # C1 CSI — a single-character ESC[
        "\xa0",
        "\u202e",  # RLO
        "\u200b",
        "\ud800",  # lone high surrogate
        "\U0001d173",  # astral non-printable
        "\udc80",  # surrogateescape of byte 0x80
    ],
)
def test_dangerous_characters_do_not_survive(text: str) -> None:
    out = escape_control_chars(text)
    assert not any(ch in out for ch in _DANGEROUS)
    assert out.isprintable()


@pytest.mark.parametrize("text", ["café", "日本語.txt", "dir/file.txt", "", "a b"])
def test_printable_text_is_unchanged(text: str) -> None:
    """Ordinary names — including non-ASCII ones — must stay readable.

    This is what rules out ``ascii()`` and the ``unicode_escape`` codec, which mangle
    every non-ASCII character.
    """
    assert escape_control_chars(text) == text


def test_astral_code_points_render_as_a_readable_escape() -> None:
    """A 5-hex-digit ``\\uXXXXX`` would read back as a 4-digit escape plus a stray digit."""
    out = escape_control_chars("x\U0001d173y")
    assert out.startswith("x") and out.endswith("y")
    body = out[1:-1]
    assert body.startswith("\\U")
    # Recoverable: the escape names the code point it replaced.
    assert int(body[2:], 16) == 0x1D173


def test_literal_backslash_is_distinguishable_from_a_real_control_char() -> None:
    """The reason backslash is escaped at all."""
    assert escape_control_chars("a\\nb") != escape_control_chars("a\nb")


def test_surrogateescaped_bytes_show_the_underlying_octet() -> None:
    """A name decoded with ``surrogateescape`` never contained U+DC80; it contained 0x80."""
    assert escape_control_chars("\udc80") == "\\x80"
    # …and a backslash before one is not mistaken for part of the escape.
    assert escape_control_chars("literal\\\udc80text") == "literal\\\\\\x80text"


@pytest.mark.parametrize("cp", [0xDC00, 0xDC7F, 0xDD00, 0xDFFF])
def test_lone_surrogates_outside_the_escape_range_are_not_reinterpreted(
    cp: int,
) -> None:
    """``surrogateescape`` only produces U+DC80–U+DCFF.

    A surrogate arriving by any other route is not a byte, and reversing it into one
    would report a character the name never contained.
    """
    out = escape_control_chars(chr(cp))
    assert out != f"\\x{cp & 0xFF:02x}"
    assert out.isprintable()


# --- escape exactly once, at the outermost message ---------------------------------


def test_message_escapes_archive_derived_text() -> None:
    exc = ExtractionError("Destination exists: /out/ev\x1b[2Kil\rSPOOF.txt")
    assert "\x1b" not in exc.message
    assert "\x1b" not in str(exc)
    assert "\x1b" not in repr(exc)
    assert "\\x1b[2K" in exc.message


def test_structured_fields_stay_raw() -> None:
    name = "ev\x1b[2Kil.txt"
    exc = ExtractionError("boom", member_name=name)
    assert exc.member_name == name  # data, for acting on
    assert "\x1b" not in str(exc)  # display, rendered through !r


def test_quoted_does_not_escape_so_the_message_escapes_once() -> None:
    """``{name!r}`` would escape first, and the message escape would double it."""
    name = "ev\x1b[2Kil.txt"
    via_quoted = ExtractionError(f"Blocked: {quoted(name)}").message
    via_repr = ExtractionError(f"Blocked: {name!r}").message
    assert "\\x1b" in via_quoted and "\\\\x1b" not in via_quoted
    assert "\\\\x1b" in via_repr  # the doubling quoted() exists to avoid


def test_raw_message_of_avoids_doubling_when_wrapping() -> None:
    inner = ExtractionError("bad: /out/ev\x1b[2Kil.txt")
    wrapped = ExtractionError(f"context: {raw_message_of(inner)}").message
    naive = ExtractionError(f"context: {inner.message}").message
    assert "\\x1b" in wrapped and "\\\\x1b" not in wrapped
    assert "\\\\x1b" in naive


def test_raw_message_of_passes_foreign_exceptions_through() -> None:
    """A third-party exception was never escaped, so its text is already raw."""
    assert raw_message_of(ValueError("plain")) == "plain"


def test_diagnostic_message_is_escaped_and_context_is_raw() -> None:
    name = "ev\x1b[2Kil.txt"
    d = Diagnostic(
        occurrence_id="1",
        code=DiagnosticCode.MEMBER_NAME_BIDI_CONTROL,
        severity=DiagnosticSeverity.WARNING,
        message=f"Member name has controls: {quoted(name)}",
        context=MemberNameControlsContext(member_name=name),
    )
    assert "\x1b" not in d.message
    assert "\\\\x1b" not in d.message  # escaped once, not twice
    assert d.context.member_name == name
    assert d.to_dict()["message"] == d.message


# --- the rule holds for future call sites too --------------------------------------

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "archivey"
_ESCAPING_CALL = re.compile(r"Error$|^emit$|Issue$")
_FOREIGN = {"FileNotFoundError", "KeyError", "TypeError", "ValueError", "OSError"}
_ARCHIVE_WORDS = {
    "name",
    "filename",
    "path",
    "member",
    "entry",
    "target",
    "directory",
    "dest",
    "link",
}


# Modules whose "names" are archivey's own vocabulary, not the archive's.
# ``reader_state`` is the concurrency state machine: its ``name`` is an
# ``OperationToken.name`` — an API operation such as ``"members"`` or ``"open"`` — so
# ``!r`` there is correct and ``quoted()`` would be actively misleading.
_NON_ARCHIVE_MODULES = {"internal/reader_state.py"}


def _is_archive_derived(expr: str, module: str) -> bool:
    """Does this interpolated expression carry archive-controlled text?

    Splits on non-letters rather than matching with ``\\b``: an underscore is a *word*
    character, so ``\\bmember\\b`` does not match inside ``member_name`` — which is how
    five sites and two whole files slipped past the first version of this check.

    The exemptions are deliberately narrow and named. This is a spelling heuristic: it
    cannot tell an operation name from a member name, so anything it lets through has
    to be justified here rather than by loosening the word list.
    """
    if module in _NON_ARCHIVE_MODULES:
        return False
    if expr.endswith(".value"):
        return False  # an enum member's value, e.g. member.type.value
    return any(tok.lower() in _ARCHIVE_WORDS for tok in re.split(r"[^A-Za-z]+", expr))


def test_no_message_site_interpolates_an_archive_derived_name_with_repr() -> None:
    """``!r`` inside a self-escaping message double-escapes; ``quoted()`` is the form.

    A static check rather than a runtime one: the failure it guards against is cosmetic
    per-site and would otherwise only be noticed for a hostile archive.
    """
    offenders: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fname = (
                getattr(node.func, "id", None) or getattr(node.func, "attr", None) or ""
            )
            if fname in _FOREIGN or not _ESCAPING_CALL.search(fname):
                continue
            targets = list(node.args[:1]) + [
                k.value for k in node.keywords if k.arg == "message"
            ]
            for target in targets:
                if not isinstance(target, ast.JoinedStr):
                    continue
                for value in target.values:
                    if (
                        isinstance(value, ast.FormattedValue)
                        and value.conversion == ord("r")
                        and _is_archive_derived(
                            ast.unparse(value.value), str(path.relative_to(_SRC))
                        )
                        and not re.fullmatch(
                            r"exc|e|err|error", ast.unparse(value.value)
                        )
                    ):
                        offenders.append(
                            f"{path.relative_to(_SRC)}:{node.lineno} "
                            f"{{{ast.unparse(value.value)}!r}} in {fname}(...)"
                        )
    assert not offenders, (
        "use quoted() instead of !r for archive-derived text in a self-escaping "
        "message:\n  " + "\n  ".join(offenders)
    )


def test_library_log_sites_still_escape_interpolated_names() -> None:
    """The mirror-image rule: the CLI does *not* escape log records.

    ``%r`` is what makes a name interpolated into a log record inert, so these call
    sites must keep it — the opposite of the message rule above.
    """
    bad: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        if "cli" in path.parts:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr in {"debug", "info", "warning", "error", "exception"}
            ):
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            fmt = str(node.args[0].value)
            for spec, arg in zip(re.findall(r"%[sr]", fmt), node.args[1:]):
                if spec == "%s" and _is_archive_derived(
                    ast.unparse(arg), str(path.relative_to(_SRC))
                ):
                    bad.append(f"{path.relative_to(_SRC)}:{node.lineno} {fmt!r}")
    assert not bad, (
        "interpolate names into log records with %r, not %s:\n  " + "\n  ".join(bad)
    )


def test_uncaught_exception_message_is_inert() -> None:
    """No display site is involved on this path — the interpreter renders it."""
    exc = ArchiveyError("boom: /out/ev\x1b[2Kil\rSPOOF.txt")
    rendered = f"{type(exc).__name__}: {exc}"
    assert not any(ch in rendered for ch in _DANGEROUS)


def test_paths_in_messages_do_not_double_their_separators() -> None:
    """A native path would have every separator doubled by the backslash escape.

    Rendering ``/``-separated first leaves the escape nothing to double. Building the
    path with ``/`` rather than a literal makes this meaningful on both platforms: it
    is ``out\\sub\\a.txt`` on Windows and ``out/sub/a.txt`` here, and the assertion is
    the same either way.
    """
    from pathlib import Path

    from archivey.escaping import display_path

    nested = Path("out") / "sub" / "a.txt"
    assert display_path(nested) == "out/sub/a.txt"
    assert "\\" not in ExtractionError(f"Destination: {display_path(nested)}").message


def test_display_path_uses_the_native_flavour_so_names_keep_their_backslashes() -> None:
    """A backslash is a legal filename character on POSIX, and must stay escaped.

    ``display_path`` converts *separators*, which means using the running platform's
    path flavour. Rewriting every backslash unconditionally would corrupt a legitimate
    POSIX member named ``a\\b`` into a two-segment path — turning a display fix into a
    correctness bug, and hiding exactly the character the escaping exists to show.
    """
    from archivey.escaping import display_path

    if os.sep == "/":
        rendered = display_path("a\\b")
        assert rendered == "a\\b"
        assert "\\\\" in ExtractionError(f"Blocked: {rendered}").message


def test_open_site_location_is_posix_rendered() -> None:
    """The breadcrumb exists to be pasted back; doubled separators defeat that."""
    from archivey.internal.open_site import OpenSite

    assert OpenSite(filename="/src/app.py", lineno=12).location == "/src/app.py:12"


def test_no_message_site_rewraps_an_already_escaped_message() -> None:
    """``SomeError(exc.message)`` re-escapes text that was escaped once already.

    Not an f-string, so the interpolation guards above never see it — this pattern is
    how two sites (`zip_reader`, `sevenzip_reader`) kept doubling after the first sweep.
    ``raw_message_of(exc)`` is the form.
    """
    offenders: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            fname = (
                getattr(node.func, "id", None) or getattr(node.func, "attr", None) or ""
            )
            if fname in _FOREIGN or not _ESCAPING_CALL.search(fname):
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Attribute) and arg.attr == "message":
                offenders.append(
                    f"{path.relative_to(_SRC)}:{node.lineno} "
                    f"{fname}({ast.unparse(arg)})"
                )
    assert not offenders, (
        "use raw_message_of(exc) — passing an escaped .message into a new message "
        "escapes it twice:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("plain.txt", "'plain.txt'"),
        ("it's.txt", '"it\'s.txt"'),  # switch, rather than escape
        ('say "hi".txt', "'say \"hi\".txt'"),
        ("", "''"),
    ],
)
def test_quoted_chooses_a_delimiter_that_does_not_appear_inside(
    name: str, expected: str
) -> None:
    """Escaping the quote would write a backslash for the message escape to double."""
    assert quoted(name) == expected


def test_quoted_never_introduces_a_backslash() -> None:
    """The escape-once property: quoted() must add delimiters and nothing else.

    Checked against a name containing both quote characters — the ambiguous fallback —
    because that is where an implementation would be tempted to escape.
    """
    both = "a'b\"c"
    assert "\\" not in quoted(both)
    assert "\\\\" not in ExtractionError(f"Blocked: {quoted(both)}").message


def test_link_target_is_a_structured_field_not_message_text() -> None:
    """Six two-name messages became prose; the target renders once, escaped, in __str__."""
    from archivey.exceptions import SymlinkEscapeError

    exc = SymlinkEscapeError(
        "Symlink target escapes destination",
        member_name="ev\x1b[2Kil",
        link_target="../etc/pa\x1b[2Ksswd",
    )
    assert exc.link_target == "../etc/pa\x1b[2Ksswd"  # raw, for acting on
    rendered = str(exc)
    assert "\x1b" not in rendered
    assert "target=" in rendered and "member=" in rendered
    assert "\\\\x1b" not in rendered  # escaped once


def test_names_are_not_rendered_twice() -> None:
    """The message no longer repeats what member= already shows."""
    from archivey.exceptions import PathTraversalError

    rendered = str(PathTraversalError("Null byte in member name", member_name="a.txt"))
    assert rendered.count("a.txt") == 1
