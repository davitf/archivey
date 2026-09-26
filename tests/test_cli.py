"""CLI behavior-matrix tests (argv → exit / stdout / stderr)."""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest

from archivey.cli.exit_codes import EXIT_FAIL, EXIT_OK, EXIT_USAGE
from archivey.cli.main import _inject_default_list, main
from archivey.exceptions import ArchiveyError


def _zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


@pytest.fixture
def sample_zip(tmp_path: Path) -> Path:
    return _zip(
        tmp_path / "sample.zip",
        {
            "a.txt": b"hello",
            "b/c.py": b"print(1)\n",
            "b/d_test.py": b"x",
        },
    )


def test_inject_default_list() -> None:
    assert _inject_default_list(["a.zip"]) == ["list", "a.zip"]
    assert _inject_default_list(["--track-io", "a.zip"]) == [
        "--track-io",
        "list",
        "a.zip",
    ]
    assert _inject_default_list(["x", "a.zip"]) == ["x", "a.zip"]
    assert _inject_default_list(["create", "a.zip"]) == ["create", "a.zip"]
    # Bare "-" is a positional (reserved stdin), not an option.
    assert _inject_default_list(["-"]) == ["list", "-"]


def test_global_flags_before_verb(
    sample_zip: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Shared-parents argparse pitfall: pre-verb globals must survive subparser defaults.
    assert main(["--track-io", str(sample_zip)]) == EXIT_OK
    err = capsys.readouterr().err
    assert "track-io:" in err

    assert main(["-v", "test", str(sample_zip)]) == EXIT_OK
    err = capsys.readouterr().err
    assert "OK   a.txt" in err or "a.txt" in err
    assert "OK," in err or "failed" in err


def test_password_before_verb_reaches_dispatch(
    sample_zip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from archivey.cli import main as main_mod

    seen: dict[str, object] = {}

    def _capture_test(**kwargs: object) -> int:
        seen.update(kwargs)
        return EXIT_OK

    monkeypatch.setattr(main_mod, "run_test", _capture_test)
    assert main(["--password", "secret", "test", str(sample_zip)]) == EXIT_OK
    assert seen.get("password") == "secret"


def test_abbrev_password_rejected(sample_zip: Path) -> None:
    # allow_abbrev=False: --pass must not become --password with a mangled value.
    assert main(["--pass", "secret", str(sample_zip)]) == EXIT_USAGE


def test_abbrev_overwrite_rejected_post_verb(sample_zip: Path, tmp_path: Path) -> None:
    # Subparsers also need allow_abbrev=False (R2) — --over must not become --overwrite.
    dest = tmp_path / "out"
    assert (
        main(["x", str(sample_zip), "-d", str(dest), "--over", "error"]) == EXIT_USAGE
    )


def test_bare_invocation_is_usage() -> None:
    assert main([]) == EXIT_USAGE


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == EXIT_OK
    assert capsys.readouterr().out.strip().startswith("archivey ")


def test_version_verbose_lists_formats(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version", "-v"]) == EXIT_OK
    out = capsys.readouterr().out
    assert out.startswith("archivey ")
    assert "formats:" in out
    assert "zip:" in out
    assert main(["-v", "--version"]) == EXIT_OK
    assert "formats:" in capsys.readouterr().out


def test_default_list_dispatch(
    sample_zip: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(sample_zip)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "a.txt" in out
    assert "b/c.py" in out


def test_list_alias(sample_zip: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["l", str(sample_zip)]) == EXIT_OK
    assert "a.txt" in capsys.readouterr().out


def test_list_incomplete_members_report_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI list prints recovered members then exits 1 when members_report.error is set."""
    from contextlib import contextmanager

    from archivey.cli import list_cmd
    from archivey.diagnostics import DiagnosticSummary, MemberListReport
    from archivey.exceptions import TruncatedError
    from archivey.types import ArchiveMember, MemberType

    member = ArchiveMember(type=MemberType.FILE, name="recovered.txt", size=4)
    report = MemberListReport(
        members=(member,),
        error=TruncatedError("truncated for test"),
        diagnostics=DiagnosticSummary.empty(),
    )

    class _FakeReader:
        def members_report(self) -> MemberListReport:
            return report

    @contextmanager
    def fake_open(*_args: object, **_kwargs: object):
        yield _FakeReader()

    monkeypatch.setattr(list_cmd, "open_for_cli", fake_open)
    archive = tmp_path / "dummy.tar"
    archive.write_bytes(b"x")
    assert (
        list_cmd.run_list(
            archive=str(archive),
            patterns=[],
            exclude=[],
            digests=False,
            verbose=False,
            salvage=False,
            password=None,
            track_io=False,
        )
        == EXIT_FAIL
    )
    captured = capsys.readouterr()
    assert "recovered.txt" in captured.out
    assert "truncated for test" in captured.err


def test_verb_named_file_known_verb_wins(tmp_path: Path) -> None:
    # A file named "x" collides with the extract alias; a bare verb word is a verb, so
    # this dispatches extract. Escape hatches: `archivey list x`, or a path-qualified
    # token such as `archivey ./x` (next test).
    named = tmp_path / "x"
    _zip(named, {"f.txt": b"data"})
    # extract into dest — should not fall through to list
    dest = tmp_path / "out"
    code = main(["x", str(named), "-d", str(dest)])
    assert code == EXIT_OK
    assert (dest / "f.txt").read_bytes() == b"data"
    # escape hatch lists the verb-named archive
    code = main(["list", str(named)])
    assert code == EXIT_OK


@pytest.mark.parametrize("shape", ["./x", "sub/x", "absolute"])
def test_path_qualified_verb_word_is_listed_not_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    shape: str,
) -> None:
    """A path-qualified token whose basename is a verb word is a path, never a verb.

    A regression here would run ``extract`` and write into the working directory.
    """
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "sub" / "x" if shape == "sub/x" else tmp_path / "x"
    target.parent.mkdir(exist_ok=True)
    _zip(target, {"f.txt": b"data"})
    token = str(target) if shape == "absolute" else shape
    before = sorted(p.name for p in tmp_path.iterdir())
    assert main([token]) == EXIT_OK
    assert "f.txt" in capsys.readouterr().out
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_dash_prefixed_verb_rejected(sample_zip: Path) -> None:
    assert main(["-x", str(sample_zip)]) == EXIT_USAGE


def test_stdin_token_reserved() -> None:
    assert main(["list", "-"]) == EXIT_USAGE
    assert main(["-"]) == EXIT_USAGE


def test_reserved_verbs(sample_zip: Path) -> None:
    assert main(["create", str(sample_zip)]) == EXIT_USAGE
    assert main(["hash", str(sample_zip)]) == EXIT_USAGE
    assert main(["convert", str(sample_zip)]) == EXIT_USAGE
    assert main(["cat", str(sample_zip)]) == EXIT_USAGE


def test_salvage_reserved(sample_zip: Path) -> None:
    assert main(["list", str(sample_zip), "--salvage"]) == EXIT_USAGE
    assert main(["--salvage", "list", str(sample_zip)]) == EXIT_USAGE
    assert (
        main(
            [
                "extract",
                str(sample_zip),
                "--salvage",
                "-d",
                str(sample_zip.parent / "o"),
            ]
        )
        == EXIT_USAGE
    )


def test_include_flag_rejected(sample_zip: Path) -> None:
    assert main(["list", str(sample_zip), "--include", "x"]) == EXIT_USAGE


def test_exclude_filter(sample_zip: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["list", str(sample_zip), "*.py", "--exclude", "*_test.py"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "b/c.py" in out
    assert "d_test.py" not in out
    assert "a.txt" not in out


def test_test_quiet_summary(
    sample_zip: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["test", str(sample_zip)]) == EXIT_OK
    captured = capsys.readouterr()
    assert "OK," in captured.err
    assert "failed" in captured.err
    # Quiet: no per-member OK lines on stderr by default
    assert "OK   a.txt" not in captured.err


def test_test_verbose_per_member(
    sample_zip: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["test", "-v", str(sample_zip)]) == EXIT_OK
    err = capsys.readouterr().err
    assert "OK   a.txt" in err or "OK   " in err


def test_extract_policy_and_dest(sample_zip: Path, tmp_path: Path) -> None:
    dest = tmp_path / "out"
    assert (
        main(
            [
                "extract",
                str(sample_zip),
                "-d",
                str(dest),
                "--policy",
                "strict",
                "--overwrite",
                "rename",
            ]
        )
        == EXIT_OK
    )
    assert (dest / "a.txt").read_bytes() == b"hello"


def test_extract_strict_blocks_device_name_and_continues(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Windows-reserved device names are rejected under STRICT; default CONTINUE
    # still extracts the remaining members (Q1). Trailing-dot names are stripped,
    # not rejected — see #123.
    from archivey.cli.exit_codes import EXIT_POLICY

    bad = _zip(tmp_path / "bad.zip", {"NUL": b"x", "ok.txt": b"y"})
    dest = tmp_path / "out"
    code = main(["extract", str(bad), "-d", str(dest), "--policy", "strict"])
    assert code == EXIT_POLICY
    err = capsys.readouterr().err
    assert "NUL" in err
    assert "blocked:" in err
    assert "extraction stopped" not in err.lower()
    assert (dest / "ok.txt").read_bytes() == b"y"


def test_extract_strict_stop_on_error_continues_on_device_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # --stop-on-error = library STOP = failures only; policy blocks continue.
    from archivey.cli.exit_codes import EXIT_POLICY

    bad = _zip(tmp_path / "bad.zip", {"NUL": b"x", "ok.txt": b"y"})
    dest = tmp_path / "out"
    code = main(
        [
            "extract",
            str(bad),
            "-d",
            str(dest),
            "--policy",
            "strict",
            "--stop-on-error",
        ]
    )
    assert code == EXIT_POLICY
    err = capsys.readouterr().err
    assert "NUL" in err
    assert "blocked:" in err
    assert "extraction stopped" not in err.lower()
    assert (dest / "ok.txt").read_bytes() == b"y"


def test_extract_zip_root_slash_under_default_strict(tmp_path: Path) -> None:
    # ZIP root entry "/" normalizes to "."; STRICT must not abort on that spelling.
    archive = tmp_path / "rooted.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(zipfile.ZipInfo("/"), b"")
        zf.writestr("a.txt", b"hello")
    dest = tmp_path / "out"
    assert main(["extract", str(archive), "-d", str(dest)]) == EXIT_OK
    assert (dest / "a.txt").read_bytes() == b"hello"


def test_extract_smart_dest_multi_toplevel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    z = _zip(tmp_path / "multi.zip", {"a.txt": b"a", "b.txt": b"b"})
    assert main(["extract", str(z)]) == EXIT_OK
    assert (tmp_path / "multi" / "a.txt").exists()
    assert not (tmp_path / "a.txt").exists()


def test_extract_smart_dest_single_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    z = _zip(tmp_path / "one.zip", {"root/a.txt": b"a", "root/b.txt": b"b"})
    assert main(["extract", str(z)]) == EXIT_OK
    assert (tmp_path / "root" / "a.txt").exists()
    assert not (tmp_path / "one").exists()


def test_extract_dest_dot_splatter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    z = _zip(tmp_path / "splat.zip", {"a.txt": b"a", "b.txt": b"b"})
    assert main(["extract", str(z), "-d", "."]) == EXIT_OK
    assert (tmp_path / "a.txt").exists()
    assert (tmp_path / "b.txt").exists()


def test_info_and_detect(sample_zip: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["info", str(sample_zip)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "format:      zip" in out or "format:" in out and "zip" in out
    assert "ArchiveFormat.ZIP" not in out
    assert "SEVEN_Z" not in out
    assert "access:      random (indexed)" in out
    assert main(["detect", str(sample_zip)]) == EXIT_OK


def test_info_detects_once(
    sample_zip: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``info`` prints the detection the open ran instead of detecting again."""
    from typing import Any

    from archivey import FormatInfo
    from archivey.internal import detection

    calls = 0
    real = detection._detect_format_body

    def counting(*args: Any, **kwargs: Any) -> FormatInfo:
        nonlocal calls
        calls += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(detection, "_detect_format_body", counting)
    assert main(["info", str(sample_zip)]) == EXIT_OK
    assert calls == 1
    out = capsys.readouterr().out
    assert "confidence:  certain" in out
    assert "detected_by: magic" in out


def test_info_prints_identity_once_when_the_open_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A recognised format that fails to open still gets its identity lines, once."""
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"PK\x03\x04" + b"\x00" * 40)
    assert main(["info", str(bad)]) != EXIT_OK
    captured = capsys.readouterr()
    assert captured.out.count("path:") == 1
    assert "format:" in captured.out
    assert "open:" in captured.err


def test_info_on_a_directory_reports_the_directory_format(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`info <dir>` works: detect_format answers DIRECTORY, as open_archive reads it."""
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.txt").write_bytes(b"hello")
    assert main(["info", str(tree)]) == EXIT_OK
    captured = capsys.readouterr()
    assert "directory" in captured.out
    assert "cannot open" not in captured.err
    assert "password" not in captured.err


def test_default_run_on_tar_prints_no_password_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The CLI's own provider is not a password the user supplied."""
    import tarfile

    path = tmp_path / "a.tar"
    with tarfile.open(path, mode="w") as tar:
        info = tarfile.TarInfo("a.txt")
        info.size = 5
        tar.addfile(info, io.BytesIO(b"hello"))
    assert main(["list", str(path)]) == EXIT_OK
    assert "password" not in capsys.readouterr().err


def test_info_verbose_prints_cost_axes(
    sample_zip: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["info", "-v", str(sample_zip)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "access:      random (indexed)" in out
    assert "listing:     indexed" in out
    assert "access_cost: direct" in out
    assert "stream:      seekable" in out


def test_info_access_solid_for_tar_gz(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import gzip
    import io
    import tarfile

    tar_path = tmp_path / "a.tar"
    with tarfile.open(tar_path, "w") as tf:
        info = tarfile.TarInfo("hello.txt")
        info.size = 5
        tf.addfile(info, io.BytesIO(b"hello"))
    gz_path = tmp_path / "a.tar.gz"
    gz_path.write_bytes(gzip.compress(tar_path.read_bytes()))

    assert main(["info", str(gz_path)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "access:      solid (" in out
    assert "listing requires decompression" in out


def test_info_track_io_is_explicit_na(
    sample_zip: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["info", str(sample_zip), "--track-io"]) == EXIT_OK
    err = capsys.readouterr().err
    assert "track-io: n/a" in err


def test_extract_reports_renames(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    z = _zip(tmp_path / "c.zip", {"a.txt": b"new"})
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "a.txt").write_bytes(b"old")
    assert (
        main(
            [
                "extract",
                str(z),
                "-d",
                str(dest),
                "--overwrite",
                "rename",
            ]
        )
        == EXIT_OK
    )
    err = capsys.readouterr().err
    assert "renamed:" in err
    assert "extracted," in err and "renamed," in err
    assert (dest / "a.txt").read_bytes() == b"old"
    # Library rename spelling: "a (1).txt"
    assert any(p.name.startswith("a (") for p in dest.iterdir())


def test_extract_verbose_lists_members(
    sample_zip: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dest = tmp_path / "out"
    assert main(["-v", "extract", str(sample_zip), "-d", str(dest)]) == EXIT_OK
    err = capsys.readouterr().err
    assert "extracted: a.txt" in err
    assert "→" in err


def test_list_missing_archive(tmp_path: Path) -> None:
    assert main(["list", str(tmp_path / "missing.zip")]) == EXIT_FAIL


def test_cli_list_unencrypted_format_without_password(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Regression: the CLI used to always pass a PasswordProvider (TTY getpass
    # fallback). Formats with SUPPORTS_PASSWORD=False (TAR, ISO, …) rejected that as
    # "does not support passwords" even when the user never passed --password.
    import tarfile

    src = tmp_path / "plain.tar"
    with tarfile.open(src, "w") as t:
        info = tarfile.TarInfo("hello.txt")
        info.size = 5
        t.addfile(info, io.BytesIO(b"hello"))

    assert main([str(src)]) == EXIT_OK
    assert "hello.txt" in capsys.readouterr().out


def test_c_is_not_integrity_alias(sample_zip: Path) -> None:
    # Integrity check is `test`/`t`; letter `c` is reserved for future `create`.
    from archivey.cli.main import _VERBS

    assert "c" not in _VERBS
    assert main(["t", str(sample_zip)]) == EXIT_OK
    assert main(["create", str(sample_zip)]) == EXIT_USAGE


def test_no_tqdm_progress_still_extracts(
    sample_zip: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Patch where extract_cmd looks up the helper (import-by-name binding).
    import archivey.cli.extract_cmd as extract_mod

    monkeypatch.setattr(extract_mod, "make_progress_callback", lambda **_: None)
    dest = tmp_path / "out"
    assert main(["extract", str(sample_zip), "-d", str(dest)]) == EXIT_OK
    assert (dest / "a.txt").exists()


def test_progress_callback_requires_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    from archivey.cli import progress as progress_mod

    class _NonTTY(io.StringIO):
        def isatty(self) -> bool:  # noqa: A003 - match TextIO API
            return False

    monkeypatch.setattr(progress_mod.sys, "__stderr__", _NonTTY())
    assert (
        progress_mod.make_progress_callback(hide_progress=False, stream=_NonTTY())
        is None
    )


def test_progress_callback_on_tty_updates_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types

    from archivey.cli import progress as progress_mod
    from archivey.types import ArchiveMember, ExtractionProgress, MemberType

    class _TTY(io.StringIO):
        def isatty(self) -> bool:
            return True

    created: list[object] = []

    class _FakeBar:
        def __init__(self, **kwargs: object) -> None:
            created.append(kwargs)
            self.n = 0
            self.total = kwargs.get("total")
            self.desc = kwargs.get("desc")
            self.closed = False

        def set_description(self, desc: str, refresh: bool = True) -> None:
            self.desc = desc

        def update(self, n: int) -> None:
            self.n += n

        def refresh(self) -> None:
            pass

        def close(self) -> None:
            self.closed = True

    def _fake_tqdm(**kwargs: object) -> _FakeBar:
        return _FakeBar(**kwargs)

    # Inject a fake tqdm so this runs in core-only (no real tqdm installed).
    fake_mod = types.ModuleType("tqdm")
    fake_mod.tqdm = _fake_tqdm  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tqdm", fake_mod)

    cb = progress_mod.make_progress_callback(hide_progress=False, stream=_TTY())
    assert cb is not None

    member = ArchiveMember(type=MemberType.FILE, name="big.bin", size=100)
    cb(
        ExtractionProgress(
            member=member,
            bytes_written=40,
            total_bytes_estimated=100,
            members_done=0,
            members_total=1,
            member_bytes_written=40,
            members_extracted=0,
            members_blocked=0,
        )
    )
    cb(
        ExtractionProgress(
            member=member,
            bytes_written=100,
            total_bytes_estimated=100,
            members_done=1,
            members_total=1,
            member_bytes_written=100,
            members_extracted=1,
            members_blocked=0,
        )
    )
    assert len(created) == 1
    assert created[0]["mininterval"] == 0  # type: ignore[index]
    assert created[0]["disable"] is False  # type: ignore[index]


def test_progress_callback_without_tqdm_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    from archivey.cli import progress as progress_mod

    class _TTY(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setitem(sys.modules, "tqdm", None)  # type: ignore[arg-type]
    assert (
        progress_mod.make_progress_callback(hide_progress=False, stream=_TTY()) is None
    )


def test_track_io_reports(sample_zip: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["test", str(sample_zip), "--track-io"]) == EXIT_OK
    err = capsys.readouterr().err
    assert "track-io:" in err
    assert "bytes_decompressed=" in err


def test_test_open_failure_still_prints_summary(
    sample_zip: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Open-time failures must count as FAIL and still reach the summary (F4).
    # Indexed archives also report untested remainder (P8).
    from archivey.exceptions import ReadError
    from archivey.internal.base_reader import BaseArchiveReader

    def _immediate(self: BaseArchiveReader, members: object = None) -> object:
        raise ReadError("simulated open failure")
        yield  # pragma: no cover — make this a generator

    monkeypatch.setattr(BaseArchiveReader, "stream_members", _immediate)
    assert main(["test", str(sample_zip)]) == EXIT_FAIL
    err = capsys.readouterr().err
    assert "FAIL:" in err
    # sample_zip has 3 file members; archive-wide FAIL consumes one slot.
    assert "0 OK, 1 failed, 2 not tested" in err


def test_test_early_abort_reports_not_tested(
    sample_zip: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After one OK, a poisoned stream leaves remaining indexed files as not tested."""
    from archivey.exceptions import ReadError
    from archivey.internal.base_reader import BaseArchiveReader

    real = BaseArchiveReader.stream_members

    def _one_then_die(self: BaseArchiveReader, members: object = None) -> object:
        yielded = False
        for item in real(self, members):
            if yielded:
                raise ReadError("simulated solid abort")
            yielded = True
            yield item

    monkeypatch.setattr(BaseArchiveReader, "stream_members", _one_then_die)
    assert main(["test", str(sample_zip)]) == EXIT_FAIL
    err = capsys.readouterr().err
    assert "1 OK, 1 failed, 1 not tested" in err


def test_test_summary_helper() -> None:
    from archivey.cli.test_cmd import _test_summary

    assert _test_summary(ok=4, failed=0, members_total=4) == "4 OK, 0 failed"
    assert (
        _test_summary(ok=0, failed=1, members_total=4) == "0 OK, 1 failed, 3 not tested"
    )
    assert _test_summary(ok=0, failed=1, members_total=None) == "0 OK, 1 failed"


def test_archive_stem_uses_format_extension() -> None:
    from archivey.cli.extract_cmd import _archive_stem
    from archivey.types import ArchiveFormat, ContainerFormat, StreamFormat

    assert _archive_stem(Path("photos.tar.gz"), format=ArchiveFormat.TAR_GZ) == "photos"
    assert _archive_stem(Path(".tar.gz"), format=ArchiveFormat.TAR_GZ) == "archive"
    tar_z = ArchiveFormat(ContainerFormat.TAR, StreamFormat.UNIX_COMPRESS)
    assert _archive_stem(Path("data.tar.Z"), format=tar_z) == "data"


def test_smart_dest_uses_filtered_tops_when_indexed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Indexed ZIP with multi-top archive, but filter selects a single root → cwd.
    monkeypatch.chdir(tmp_path)
    z = _zip(
        tmp_path / "pack.zip",
        {"a/x.txt": b"a", "b/y.txt": b"b", "c/z.txt": b"c"},
    )
    assert main(["extract", str(z), "b/*"]) == EXIT_OK
    assert (tmp_path / "b" / "y.txt").read_bytes() == b"b"
    assert not (tmp_path / "pack").exists()
    assert not (tmp_path / "a").exists()


def test_smart_dest_hoists_single_root_when_no_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Plain TAR has no cheap index — wrap then hoist a single top-level root (R4).
    import tarfile

    monkeypatch.chdir(tmp_path)
    archive = tmp_path / "bundle.tar"
    with tarfile.open(archive, "w") as tf:
        info = tarfile.TarInfo("root/a.txt")
        info.size = 1
        tf.addfile(info, io.BytesIO(b"x"))
    assert main(["extract", str(archive)]) == EXIT_OK
    err = capsys.readouterr().err
    assert "extracting into bundle/" in err
    assert "moved to root/" in err
    assert (tmp_path / "root" / "a.txt").read_bytes() == b"x"
    assert not (tmp_path / "bundle").exists()


def test_smart_dest_keeps_wrapper_for_multi_top_tar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tarfile

    monkeypatch.chdir(tmp_path)
    archive = tmp_path / "messy.tar"
    with tarfile.open(archive, "w") as tf:
        for name, data in (("a.txt", b"a"), ("b.txt", b"b")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    assert main(["extract", str(archive)]) == EXIT_OK
    assert (tmp_path / "messy" / "a.txt").read_bytes() == b"a"
    assert (tmp_path / "messy" / "b.txt").read_bytes() == b"b"
    assert not (tmp_path / "a.txt").exists()


def test_password_rejected_message_distinct_from_required() -> None:
    from archivey.exceptions import EncryptionError
    from archivey.internal.password import _PasswordCandidates

    candidates = _PasswordCandidates.from_input("wrong")
    with pytest.raises(EncryptionError, match="rejected") as caught:
        candidates.attempt(
            None,
            lambda _pwd: (_ for _ in ()).throw(EncryptionError("nope")),
        )
    assert "Password required" not in caught.value.message

    empty = _PasswordCandidates.from_input(None)
    with pytest.raises(EncryptionError, match="Password required"):
        empty.attempt(
            None,
            lambda _pwd: (_ for _ in ()).throw(EncryptionError("unreachable")),
        )


def _tar(path: Path, entries: dict[str, bytes]) -> Path:
    import tarfile

    with tarfile.open(path, "w") as tf:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def test_hoist_root_named_like_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # src.tar containing src/ — the "collision" is the wrapper itself; flatten in
    # place instead of renaming away (H2) or deleting extracted data (H1).
    monkeypatch.chdir(tmp_path)
    archive = _tar(tmp_path / "src.tar", {"src/f.txt": b"data"})
    for overwrite in ("rename", "replace", "error", "skip"):
        assert main(["extract", str(archive), "--overwrite", overwrite]) == EXIT_OK
        err = capsys.readouterr().err
        assert "removed wrapper; content at src/" in err
        assert "moved to src/" not in err
        assert (tmp_path / "src" / "f.txt").read_bytes() == b"data"
        assert not (tmp_path / "src (1)").exists()
        import shutil

        shutil.rmtree(tmp_path / "src")


def test_hoist_single_file_named_like_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # src.tar containing a single top-level FILE named "src".
    monkeypatch.chdir(tmp_path)
    archive = _tar(tmp_path / "src.tar", {"src": b"solo"})
    assert main(["extract", str(archive)]) == EXIT_OK
    assert (tmp_path / "src").read_bytes() == b"solo"
    assert not (tmp_path / "src (1)").exists()


def _seed_existing_root(tmp_path: Path) -> None:
    (tmp_path / "root").mkdir()
    (tmp_path / "root" / "keep.txt").write_bytes(b"MINE")
    (tmp_path / "root" / "clash.txt").write_bytes(b"MINE")


def _hoist_collision_archive(tmp_path: Path) -> Path:
    return _tar(
        tmp_path / "bundle.tar",
        {"root/new.txt": b"NEW", "root/clash.txt": b"ARCHIVE"},
    )


def test_hoist_merges_like_direct_extraction_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Equivalence contract: hoisting == extracting directly into cwd. Dirs merge;
    # the colliding file gets the library's "name (1)" treatment; nothing deleted.
    monkeypatch.chdir(tmp_path)
    archive = _hoist_collision_archive(tmp_path)
    _seed_existing_root(tmp_path)
    assert main(["extract", str(archive)]) == EXIT_OK
    root = tmp_path / "root"
    assert (root / "keep.txt").read_bytes() == b"MINE"
    assert (root / "clash.txt").read_bytes() == b"MINE"
    assert (root / "clash (1).txt").read_bytes() == b"ARCHIVE"
    assert (root / "new.txt").read_bytes() == b"NEW"
    assert not (tmp_path / "bundle").exists()
    assert not (tmp_path / "root (1)").exists()


def test_hoist_merges_like_direct_extraction_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    archive = _hoist_collision_archive(tmp_path)
    _seed_existing_root(tmp_path)
    assert main(["extract", str(archive), "--overwrite", "skip"]) == EXIT_OK
    root = tmp_path / "root"
    assert (root / "clash.txt").read_bytes() == b"MINE"
    assert (root / "new.txt").read_bytes() == b"NEW"
    assert not (tmp_path / "bundle").exists()


def test_hoist_merges_like_direct_extraction_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    archive = _hoist_collision_archive(tmp_path)
    _seed_existing_root(tmp_path)
    assert main(["extract", str(archive), "--overwrite", "replace"]) == EXIT_OK
    root = tmp_path / "root"
    assert (root / "clash.txt").read_bytes() == b"ARCHIVE"  # only this file replaced
    assert (root / "keep.txt").read_bytes() == b"MINE"
    assert (root / "new.txt").read_bytes() == b"NEW"
    assert not (tmp_path / "bundle").exists()


def test_hoist_collision_under_error_keeps_wrapper_and_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Direct extraction would fail on this collision; the hoist fails the same
    # way but keeps the unmoved remainder safely under the wrapper.
    monkeypatch.chdir(tmp_path)
    archive = _hoist_collision_archive(tmp_path)
    _seed_existing_root(tmp_path)
    assert main(["extract", str(archive), "--overwrite", "error"]) == EXIT_FAIL
    err = capsys.readouterr().err
    assert "Destination already exists" in err
    assert (tmp_path / "root" / "clash.txt").read_bytes() == b"MINE"
    # The conflicting file is still available under the wrapper, not lost.
    assert (tmp_path / "bundle" / "root" / "clash.txt").read_bytes() == b"ARCHIVE"


def test_hoist_never_deletes_colliding_tree_under_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Archive member "root/clash" is a FILE; on disk ./root/clash is a DIRECTORY.
    # replace must not rmtree the directory — it fails and keeps everything.
    monkeypatch.chdir(tmp_path)
    archive = _tar(tmp_path / "bundle.tar", {"root/clash": b"ARCHIVE"})
    (tmp_path / "root" / "clash").mkdir(parents=True)
    (tmp_path / "root" / "clash" / "precious.txt").write_bytes(b"MINE")
    assert main(["extract", str(archive), "--overwrite", "replace"]) == EXIT_FAIL
    assert (tmp_path / "root" / "clash" / "precious.txt").read_bytes() == b"MINE"
    assert (tmp_path / "bundle" / "root" / "clash").read_bytes() == b"ARCHIVE"


def test_cli_logging_leaves_no_global_state(sample_zip: Path) -> None:
    # The D4 handler must be scoped to one invocation: a leaked handler or
    # propagate=False blinds caplog-based library tests (pytest 8.3 floor).
    import logging

    root = logging.getLogger("archivey")
    assert main(["list", str(sample_zip)]) == EXIT_OK
    assert root.handlers == []
    assert root.propagate is True
    assert root.level == logging.NOTSET


# --- cli-product review follow-ups (P3 / P5 / P6 / P10–P13 / D1) -------------------------


def test_missing_archive_uses_prose_not_errno_repr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nope.zip"
    assert main(["list", str(missing)]) == EXIT_FAIL
    err = capsys.readouterr().err
    assert "cannot open" in err
    assert "no such file or directory" in err.lower()
    assert "[Errno" not in err


@pytest.mark.parametrize("verb", ["list", "x"])
def test_missing_archive_name_is_escaped_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], verb: str
) -> None:
    """The filename is escaped at the print site only — ``!r`` before it doubled it."""
    missing = tmp_path / "ev\u2028il.zip"
    assert main([verb, str(missing)]) == EXIT_FAIL
    err = capsys.readouterr().err
    assert "\u2028" not in err
    assert "ev\\u2028il.zip'" in err
    assert "ev\\\\u2028il.zip" not in err


def test_os_error_without_strerror_does_not_embed_a_repr() -> None:
    import errno

    from archivey.cli.main import _format_os_error

    exc = OSError(errno.EACCES, None, "ev\x1bil.zip")
    assert _format_os_error(exc) == (
        "archivey: cannot open 'ev\x1bil.zip': Permission denied"
    )


def test_extract_missing_archive_only_requires_archive(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["x"]) == EXIT_USAGE
    err = capsys.readouterr().err
    assert "required: archive" in err
    assert "patterns" not in err.split("required:")[-1]


def test_dash_x_hints_bare_verb(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["-x", "a.zip"]) == EXIT_USAGE
    err = capsys.readouterr().err
    assert "unrecognized arguments: -x" in err
    assert "bare words" in err
    assert "archivey x ARCHIVE" in err


def test_help_includes_examples(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--help"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "examples:" in out
    assert "archivey x archive.zip" in out


def test_password_eof_treated_as_no_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from archivey.cli import password as password_mod
    from archivey.config import PasswordRequest
    from tests.zipcrypto import build_zipcrypto_zip

    blob = build_zipcrypto_zip(b"secret", b"zc.txt", b"hello")
    path = tmp_path / "zc.zip"
    path.write_bytes(blob)

    monkeypatch.setattr(password_mod.sys.stdin, "isatty", lambda: True)

    def _eof(_prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr(password_mod.getpass, "getpass", _eof)
    provider = password_mod.resolve_password(None)
    assert callable(provider)
    assert provider(PasswordRequest(member=None, attempt=1)) is None

    # End-to-end: EOF at prompt must not dump a traceback (P5).
    err = io.StringIO()
    assert main(["t", str(path)], out=io.StringIO(), err=err) == EXIT_FAIL
    text = err.getvalue()
    assert "Traceback" not in text
    assert "EOFError" not in text
    assert "Password required" in text


def test_format_access_summary() -> None:
    from archivey.cli.format import format_access_summary
    from archivey.cost import AccessCost, CostReceipt, ListingCost, StreamCapability

    indexed = CostReceipt(
        listing_cost=ListingCost.INDEXED,
        access_cost=AccessCost.DIRECT,
        stream_capability=StreamCapability.SEEKABLE,
    )
    assert format_access_summary(indexed) == "random (indexed)"

    solid = CostReceipt(
        listing_cost=ListingCost.REQUIRES_DECOMPRESSION,
        access_cost=AccessCost.SOLID,
        stream_capability=StreamCapability.SEEKABLE,
        solid_block_count=1,
    )
    assert "solid (" in format_access_summary(solid)
    assert "1 solid block" in format_access_summary(solid)


def test_list_escapes_control_bytes_in_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    z = _zip(
        tmp_path / "hostile.zip", {"evil\x1b[31m.txt": b"x", "line1\rok.txt": b"y"}
    )
    assert main(["list", str(z)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "\x1b" not in out
    assert "\\x1b" in out
    assert "\\r" in out


def test_list_marks_anti_and_non_current() -> None:
    from datetime import datetime

    from archivey.cli.format import format_member_line
    from archivey.types import ArchiveMember, MemberType

    anti = ArchiveMember(type=MemberType.ANTI, name="gone.txt")
    assert format_member_line(anti).startswith("A-")

    old = ArchiveMember(
        type=MemberType.FILE,
        name="a.txt",
        size=1,
        modified=datetime(2026, 1, 1),
        is_current=False,
    )
    assert format_member_line(old).startswith("f~")

    enc = ArchiveMember(
        type=MemberType.FILE,
        name="a.txt",
        size=1,
        is_encrypted=True,
        is_current=False,
    )
    assert format_member_line(enc).startswith("fE")


def test_extract_summary_names_single_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    z = _zip(tmp_path / "one.zip", {"root/a.txt": b"a", "root/b.txt": b"b"})
    assert main(["extract", str(z)]) == EXIT_OK
    err = capsys.readouterr().err
    assert "→ root/" in err
    assert "→ ." not in err


def test_truncated_zip_message_is_prose_not_repr(tmp_path: Path) -> None:
    import zipfile as zf

    from archivey import open_archive
    from archivey.exceptions import CorruptionError

    buf = io.BytesIO()
    with zf.ZipFile(buf, "w") as archive:
        archive.writestr("a.txt", "hello")
    path = tmp_path / "truncated.zip"
    path.write_bytes(buf.getvalue()[:20])
    with pytest.raises(CorruptionError) as caught:
        open_archive(path)
    msg = str(caught.value)
    assert "BadZipFile" not in msg
    assert "ArchiveFormat.ZIP" not in msg
    assert "format=ZIP" in msg
    assert "truncated" in msg.lower() or "corrupt" in msg.lower()


def test_stored_zipcrypto_provider_none_is_password_required(tmp_path: Path) -> None:
    import zipfile as zf

    from archivey import open_archive
    from archivey.exceptions import EncryptionError
    from tests.zipcrypto import build_zipcrypto_zip

    blob = build_zipcrypto_zip(
        b"secret", b"zc.txt", b"hello world", compression=zf.ZIP_STORED
    )
    path = tmp_path / "zc_stored.zip"
    path.write_bytes(blob)

    with pytest.raises(EncryptionError, match="Password required") as caught:
        with open_archive(path, password=lambda _r: None) as ar:
            ar.read(next(m for m in ar.members() if m.is_file))
    assert "Wrong password" not in caught.value.message


# --- Q1 / P1: extract continue-on-error + exit 3 + --stop-on-error -----------------------


def test_extract_continues_after_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from archivey.cli.exit_codes import EXIT_POLICY

    monkeypatch.chdir(tmp_path)
    archive = _tar(
        tmp_path / "evil.tar",
        {
            "../escape.txt": b"bad",
            "safe1.txt": b"ok1",
            "safe2.txt": b"ok2",
            "dir/nested.txt": b"ok3",
        },
    )
    assert main(["extract", str(archive), "-d", "out"]) == EXIT_POLICY
    err = capsys.readouterr().err
    assert "blocked:" in err
    assert "escape.txt" in err or "../escape" in err
    assert "blocked" in err.split("→")[0]  # summary mentions blocked
    assert (tmp_path / "out" / "safe1.txt").read_bytes() == b"ok1"
    assert (tmp_path / "out" / "safe2.txt").read_bytes() == b"ok2"
    assert (tmp_path / "out" / "dir" / "nested.txt").read_bytes() == b"ok3"
    assert not (tmp_path / "escape.txt").exists()


def test_extract_stop_on_error_continues_on_policy_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from archivey.cli.exit_codes import EXIT_POLICY

    monkeypatch.chdir(tmp_path)
    # Policy block under --stop-on-error: continue, extract safe members, exit 3.
    archive = _tar(
        tmp_path / "evil.tar",
        {
            "../escape.txt": b"bad",
            "safe.txt": b"ok",
        },
    )
    code = main(["extract", str(archive), "-d", "out", "--stop-on-error"])
    assert code == EXIT_POLICY
    err = capsys.readouterr().err
    assert "blocked:" in err
    assert "extraction stopped" not in err
    assert (tmp_path / "out" / "safe.txt").read_bytes() == b"ok"


def test_extract_stop_on_error_aborts_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """--stop-on-error aborts on a genuine member failure (CRC), not a policy block."""
    monkeypatch.chdir(tmp_path)
    from tests.zip_corrupt import zip_with_flipped_cd_crc

    path = zip_with_flipped_cd_crc(
        tmp_path / "badcrc.zip",
        {"a.txt": b"hello", "b.txt": b"world", "c.txt": b"end"},
        corrupt_name="b.txt",
    )

    code = main(["extract", str(path), "-d", "out", "--stop-on-error"])
    assert code == EXIT_FAIL
    err = capsys.readouterr().err
    assert "extraction stopped" in err
    assert "1 member(s) extracted before the stop" in err
    assert (tmp_path / "out" / "a.txt").read_bytes() == b"hello"
    assert not (tmp_path / "out" / "b.txt").exists()
    assert not (tmp_path / "out" / "c.txt").exists()


def test_extract_corrupt_member_continues_with_exit_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CRC mismatch: recoverable members extracted; exit 1 (FAILED, not policy)."""
    monkeypatch.chdir(tmp_path)
    from tests.zip_corrupt import zip_with_flipped_cd_crc

    path = zip_with_flipped_cd_crc(
        tmp_path / "badcrc.zip",
        {"a.txt": b"hello", "b.txt": b"world", "c.txt": b"end"},
        corrupt_name="b.txt",
    )

    assert main(["extract", str(path), "-d", "out"]) == EXIT_FAIL
    err = capsys.readouterr().err
    assert "failed:" in err
    assert "b.txt" in err
    assert "extraction stopped" not in err
    assert (tmp_path / "out" / "a.txt").read_bytes() == b"hello"
    assert (tmp_path / "out" / "c.txt").read_bytes() == b"end"
    assert not (tmp_path / "out" / "b.txt").exists()


def test_extract_dest_before_patterns_still_filters(
    sample_zip: Path, tmp_path: Path
) -> None:
    """Documented ``x ARCHIVE -d DEST PATTERN`` must not drop the pattern (argparse)."""
    dest = tmp_path / "dest"
    assert main(["x", str(sample_zip), "-d", str(dest), "a.txt"]) == EXIT_OK
    assert (dest / "a.txt").read_bytes() == b"hello"
    assert not (dest / "b").exists()


def test_extract_unmatched_include_exits_one(
    sample_zip: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dest = tmp_path / "dest"
    dest.mkdir()
    assert main(["x", str(sample_zip), "-d", str(dest), "*.missing"]) == EXIT_FAIL
    err = capsys.readouterr().err
    assert "warning: pattern matched no members: '*.missing'" in err
    assert list(dest.iterdir()) == []


def test_extract_unmatched_dir_pattern_hints_dash_d(
    sample_zip: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    assert main(["x", str(sample_zip), "out"]) == EXIT_FAIL
    err = capsys.readouterr().err
    assert "warning: pattern matched no members: 'out'" in err
    assert "(did you mean -d out?)" in err


def test_extract_partial_unmatched_still_extracts(
    sample_zip: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dest = tmp_path / "dest"
    assert main(["x", str(sample_zip), "-d", str(dest), "a.txt", "nope.txt"]) == EXIT_OK
    err = capsys.readouterr().err
    assert "warning: pattern matched no members: 'nope.txt'" in err
    assert (dest / "a.txt").read_bytes() == b"hello"


def test_list_unmatched_include_warns_exit_zero(
    sample_zip: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["list", str(sample_zip), "*.rs"]) == EXIT_OK
    captured = capsys.readouterr()
    assert "warning: pattern matched no members: '*.rs'" in captured.err
    assert captured.out == ""


def test_test_unmatched_include_exits_one(
    sample_zip: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["t", str(sample_zip), "missing"]) == EXIT_FAIL
    err = capsys.readouterr().err
    assert "warning: pattern matched no members: 'missing'" in err
    assert "OK," not in err


def test_extract_dest_before_dash_named_pattern(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``-d`` before patterns must still accept ``-- -file.txt`` (fold + ``--``)."""
    monkeypatch.chdir(tmp_path)
    z = _zip(tmp_path / "dash.zip", {"-file.txt": b"x", "ok.txt": b"y"})
    dest = tmp_path / "out"
    assert main(["x", str(z), "-d", str(dest), "--", "-file.txt"]) == EXIT_OK
    assert (dest / "-file.txt").read_bytes() == b"x"
    assert not (dest / "ok.txt").exists()


def test_extract_stop_on_error_reports_extracted_and_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Q1.5: STOP abort reports extracted vs blocked counts separately."""
    monkeypatch.chdir(tmp_path)
    from tests.zip_corrupt import zip_with_flipped_cd_crc

    path = zip_with_flipped_cd_crc(
        tmp_path / "partial.zip",
        {
            "safe.txt": b"ok",
            "../evil.txt": b"bad",
            "corrupt.txt": b"payload",
            "later.txt": b"late",
        },
        corrupt_name="corrupt.txt",
    )

    code = main(["extract", str(path), "-d", "out", "--stop-on-error"])
    assert code == EXIT_FAIL  # Q8: abort → 1
    err = capsys.readouterr().err
    assert "1 member(s) extracted, 1 blocked before the stop" in err
    assert "extraction stopped" in err
    assert (tmp_path / "out" / "safe.txt").read_bytes() == b"ok"
    assert not (tmp_path / "out" / "later.txt").exists()


def test_extract_stop_on_error_reports_members_extracted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Q1.5: STOP after a successful member still reports how many were extracted."""
    monkeypatch.chdir(tmp_path)
    from tests.zip_corrupt import zip_with_flipped_cd_crc

    path = zip_with_flipped_cd_crc(
        tmp_path / "partial.zip",
        {"safe.txt": b"ok", "corrupt.txt": b"bad", "later.txt": b"late"},
        corrupt_name="corrupt.txt",
    )

    code = main(["extract", str(path), "-d", "out", "--stop-on-error"])
    assert code == EXIT_FAIL  # Q8: abort → 1
    err = capsys.readouterr().err
    assert "1 member(s) extracted before the stop" in err
    assert "blocked" not in err.split("before the stop")[0]
    assert "extraction stopped" in err
    assert (tmp_path / "out" / "safe.txt").read_bytes() == b"ok"
    assert not (tmp_path / "out" / "later.txt").exists()


def test_members_for_include_check_skips_forward_only(
    tmp_path: Path,
) -> None:
    """Forward-only readers must not be pre-scanned (would burn the sole pass)."""
    import io
    import tarfile

    from archivey import open_archive
    from archivey.cli.filters import members_for_include_check
    from archivey.cost import StreamCapability

    tar_path = tmp_path / "a.tar"
    with tarfile.open(tar_path, "w") as tf:
        info = tarfile.TarInfo("a.txt")
        info.size = 1
        tf.addfile(info, io.BytesIO(b"x"))

    class _NonSeekable(io.BytesIO):
        def seekable(self) -> bool:
            return False

        def seek(self, *args: object, **kwargs: object) -> int:
            raise OSError("not seekable")

        def tell(self) -> int:
            raise OSError("not seekable")

    with open_archive(_NonSeekable(tar_path.read_bytes()), streaming=True) as reader:
        assert reader.cost.stream_capability is StreamCapability.FORWARD_ONLY
        assert members_for_include_check(reader) is None
        # Sole pass still available for extract/test.
        pairs = list(reader.stream_members())
        assert [m.name for m, _ in pairs] == ["a.txt"]


# --- --abort-on / OVERWRITTEN reporting -------------------------------------


def _collide_zip(path: Path) -> Path:
    """Two members whose names differ only by case — one destination, two members."""
    return _zip(path, {"README": b"A", "readme": b"B"})


def test_extract_reports_overwritten_member(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The REPLACE merge that used to be silent in the report is now reported."""
    archive = _collide_zip(tmp_path / "c.zip")
    dest = tmp_path / "out"
    assert (
        main(["x", str(archive), "-d", str(dest), "--overwrite", "replace"]) == EXIT_OK
    )
    err = capsys.readouterr().err
    assert "overwritten:" in err
    assert sorted(p.name for p in dest.iterdir()) == ["README"]


def test_extract_abort_on_name_collision(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = _collide_zip(tmp_path / "c.zip")
    dest = tmp_path / "out"
    code = main(
        [
            "x",
            str(archive),
            "-d",
            str(dest),
            "--overwrite",
            "replace",
            "--abort-on",
            "name-collision",
        ]
    )
    assert code == EXIT_FAIL
    err = capsys.readouterr().err
    assert "Name collision" in err
    assert "extraction stopped" in err


def test_extract_abort_on_blocked_member(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = _zip(tmp_path / "t.zip", {"../escape.txt": b"x", "ok.txt": b"y"})
    dest = tmp_path / "out"
    code = main(["x", str(archive), "-d", str(dest), "--abort-on", "blocked-member"])
    assert code == EXIT_FAIL
    assert "extraction stopped" in capsys.readouterr().err
    assert not (dest / "ok.txt").exists()


def test_extract_unknown_abort_on_event_is_usage_error(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "t.zip", {"a.txt": b"x"})
    assert (
        main(["x", str(archive), "-d", str(tmp_path / "o"), "--abort-on", "nonsense"])
        == EXIT_USAGE
    )


def test_extract_abort_on_name_sanitized(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = _zip(tmp_path / "t.zip", {"foo.": b"x", "later.txt": b"y"})
    dest = tmp_path / "out"
    code = main(["x", str(archive), "-d", str(dest), "--abort-on", "name-sanitized"])
    assert code == EXIT_FAIL
    assert "extraction stopped" in capsys.readouterr().err
    assert not (dest / "later.txt").exists()


def test_extract_reports_nested_rewrite_with_full_relative_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Both sides of the arrow are full relative names — a basename would print
    'dir/foo. -> foo' and invent a destination the member never had."""
    archive = _zip(tmp_path / "t.zip", {"dir/foo.": b"x", "dir/keep.txt": b"y"})
    dest = tmp_path / "out"
    assert main(["x", str(archive), "-d", str(dest)]) == EXIT_OK
    assert "name rewritten: dir/foo. -> dir/foo" in capsys.readouterr().err


def _report_lines(err: str, marker: str) -> list[str]:
    """Stderr lines starting with ``marker``, split on ``\\n`` only.

    ``str.splitlines()`` is wrong here: it also splits on ``\\r`` and U+2028 — the exact
    characters under test — so an unescaped line would be broken in two and every "raw
    character absent" assertion would pass vacuously. A trailing ``\\r`` is stripped so a
    CRLF line ending does not read as an embedded CR.
    """
    return [
        ln[:-1] if ln.endswith("\r") else ln
        for ln in err.split("\n")
        if ln.startswith(marker)
    ]


# A member name carrying an ANSI erase-line plus a CR: printed raw, everything before
# the CR is wiped and the archive gets to author the whole terminal line. Windows cannot
# hold such a name at all (control bytes are illegal in NTFS names, WinError 123), so
# tests using it are Unix-only — the write fails there before any report line is reached.
_SPOOF_ANSI = "ev\x1b[2Kil\rSUCCESS.txt"
_ANSI_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="control bytes are illegal in Windows filenames; the member cannot be written",
)

# U+2028 LINE SEPARATOR: non-printable (so it still must be escaped) but legal in a
# Windows filename, which keeps the escaping itself covered on every platform.
_SPOOF_PORTABLE = "ev\u2028il.txt"


@pytest.mark.parametrize(
    "overwrite,marker",
    [("replace", "overwritten:"), ("skip", "not overwritten:")],
)
def test_extract_escapes_member_paths_in_overwrite_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], overwrite: str, marker: str
) -> None:
    """These lines print ``requested_path``, which is built from the member's own name.

    The portable rewrite does not strip non-printable characters under any policy, so an
    unescaped path here is the display-spoofing vector ``escape_member_name`` closes.
    """
    archive = _zip(
        tmp_path / "c.zip", {_SPOOF_PORTABLE: b"A", _SPOOF_PORTABLE.upper(): b"B"}
    )
    dest = tmp_path / "out"
    main(["x", str(archive), "-d", str(dest), "--overwrite", overwrite])
    err = capsys.readouterr().err
    # Scoped to the line under test: a stray library log line carrying the same raw path
    # would otherwise fail this for a reason it does not cover.
    lines = _report_lines(err, marker)
    assert lines
    assert all("\u2028" not in ln for ln in lines)  # not the raw separator
    assert any("\\u2028" in ln for ln in lines)  # …shown losslessly instead


def test_extract_escapes_member_paths_in_rename_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The ``renamed:`` line prints both sides as paths; both come from the member."""
    archive = _zip(
        tmp_path / "c.zip", {_SPOOF_PORTABLE: b"A", _SPOOF_PORTABLE.upper(): b"B"}
    )
    dest = tmp_path / "out"
    main(["x", str(archive), "-d", str(dest), "--overwrite", "rename"])
    err = capsys.readouterr().err
    lines = _report_lines(err, "renamed:")
    assert lines
    assert all("\u2028" not in ln for ln in lines)
    assert any("\\u2028" in ln for ln in lines)


@_ANSI_ONLY
@pytest.mark.parametrize(
    "overwrite,marker",
    [("replace", "overwritten:"), ("skip", "not overwritten:")],
)
def test_extract_escapes_ansi_spoof_in_overwrite_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], overwrite: str, marker: str
) -> None:
    """The real spoof: ESC[2K + CR would erase the line and rewrite it."""
    archive = _zip(tmp_path / "c.zip", {_SPOOF_ANSI: b"A", _SPOOF_ANSI.upper(): b"B"})
    dest = tmp_path / "out"
    main(["x", str(archive), "-d", str(dest), "--overwrite", overwrite])
    err = capsys.readouterr().err
    lines = _report_lines(err, marker)
    assert lines
    assert all("\x1b" not in ln for ln in lines)  # no raw escape sequence
    assert all("\r" not in ln for ln in lines)  # no carriage-return line rewrite
    assert any("\\x1b[2K" in ln for ln in lines)  # …shown losslessly instead


def test_extract_escapes_error_detail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``failed:`` appends the error, whose message embeds the destination path.

    Scoped to the CLI's own line: the library also emits a ``logger.warning`` carrying
    the same raw path, which reaches the same terminal through the CLI's stderr handler.
    That is a separate layer (a log formatter concern, not a print site) and is not what
    this test covers.
    """
    archive = _zip(
        tmp_path / "c.zip", {_SPOOF_PORTABLE: b"A", _SPOOF_PORTABLE.upper(): b"B"}
    )
    dest = tmp_path / "out"
    main(["x", str(archive), "-d", str(dest), "--overwrite", "error"])
    err = capsys.readouterr().err
    failed_lines = _report_lines(err, "failed:")
    assert failed_lines
    assert all("\u2028" not in ln for ln in failed_lines)
    assert any("\\u2028" in ln for ln in failed_lines)


def test_report_lines_do_not_double_path_separators(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Escaping must not turn a native path's separators into ``\\\\``.

    ``escape_member_name`` escapes backslashes, so feeding it a Windows path would double
    every separator. Report lines render relative to the extraction root (POSIX
    separators) first, so this holds on every platform — and this test would catch the
    regression on Linux, where the doubling is otherwise invisible.
    """
    archive = _zip(tmp_path / "c.zip", {"dir/README": b"A", "dir/readme": b"B"})
    dest = tmp_path / "out"
    main(["x", str(archive), "-d", str(dest), "--overwrite", "replace"])
    lines = _report_lines(capsys.readouterr().err, "overwritten:")
    assert lines == ["overwritten: dir/README"]
    assert "\\\\" not in lines[0]


def _summary_lines(err: str) -> list[str]:
    """The closing ``N extracted, …`` line(s), split on ``\\n`` only (see _report_lines)."""
    return [ln for ln in err.split("\n") if " extracted, " in ln]


# The single-root forms of the two spoof names: a directory, not a file.
_SPOOF_ANSI_ROOT = "ev\x1b[2Kil\rSUCCESS"
_SPOOF_PORTABLE_ROOT = "ev\u2028il"


@pytest.mark.parametrize(
    "root,raw,escaped",
    [
        pytest.param(_SPOOF_PORTABLE_ROOT, "\u2028", "ev\\u2028il", id="portable"),
        pytest.param(
            _SPOOF_ANSI_ROOT,
            "\x1b",
            "ev\\x1b[2Kil\\rSUCCESS",
            id="ansi",
            marks=_ANSI_ONLY,
        ),
    ],
)
def test_extract_summary_escapes_the_single_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    root: str,
    raw: str,
    escaped: str,
) -> None:
    """The summary names the sole top-level entry — the member's own name.

    It is the last line the operator reads, the one saying where the data went, so an
    unescaped name there lets the archive write it.
    """
    monkeypatch.chdir(tmp_path)
    archive = _zip(tmp_path / "one.zip", {f"{root}/a.txt": b"a", f"{root}/b.txt": b"b"})
    assert main(["x", str(archive)]) == EXIT_OK
    lines = _summary_lines(capsys.readouterr().err)
    assert len(lines) == 1
    assert raw not in lines[0]
    assert "\r" not in lines[0]
    assert lines[0].endswith(f"→ {escaped}/")


@pytest.mark.parametrize(
    "root,raw,escaped",
    [
        pytest.param(_SPOOF_PORTABLE_ROOT, "\u2028", "ev\\u2028il", id="portable"),
        pytest.param(
            _SPOOF_ANSI_ROOT,
            "\x1b",
            "ev\\x1b[2Kil\\rSUCCESS",
            id="ansi",
            marks=_ANSI_ONLY,
        ),
    ],
)
def test_extract_hoist_report_escapes_the_single_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    root: str,
    raw: str,
    escaped: str,
) -> None:
    """A plain tar has no index, so the CLI wraps, then hoists the sole root and says so.

    Both the ``moved to`` line and the summary print that root's name.
    """
    monkeypatch.chdir(tmp_path)
    archive = _tar(
        tmp_path / "bundle.tar", {f"{root}/a.txt": b"a", f"{root}/b.txt": b"b"}
    )
    assert main(["x", str(archive)]) == EXIT_OK
    err = capsys.readouterr().err
    moved = _report_lines(err, "moved to ")
    assert moved == [f"moved to {escaped}/"]
    summary = _summary_lines(err)
    assert len(summary) == 1
    assert raw not in summary[0]
    assert summary[0].endswith(f"→ {escaped}/")


def test_extract_escapes_a_wrapper_named_after_the_archive_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The wrapper directory is the archive's filename stem, which is not the CLI's.

    An archive extracted out of another archive gets its filename from that one, and a
    shell loop over ``*.tar`` hands it over without anyone typing it.
    """
    monkeypatch.chdir(tmp_path)
    archive = _tar(
        tmp_path / f"{_SPOOF_PORTABLE_ROOT}.tar", {"a.txt": b"a", "b.txt": b"b"}
    )
    assert main(["x", str(archive)]) == EXIT_OK
    err = capsys.readouterr().err
    assert _report_lines(err, "extracting into ") == ["extracting into ev\\u2028il/"]
    summary = _summary_lines(err)
    assert len(summary) == 1
    assert summary[0].endswith("→ ev\\u2028il/")
    assert "\u2028" not in err


def test_hoist_escapes_the_wrapper_it_flattens_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``ev<U+2028>il.tar`` holding ``ev<U+2028>il/``: the wrapper becomes the root."""
    monkeypatch.chdir(tmp_path)
    root = _SPOOF_PORTABLE_ROOT
    archive = _tar(tmp_path / f"{root}.tar", {f"{root}/f.txt": b"data"})
    assert main(["x", str(archive)]) == EXIT_OK
    err = capsys.readouterr().err
    assert _report_lines(err, "removed wrapper; ") == [
        "removed wrapper; content at ev\\u2028il/"
    ]
    assert "\u2028" not in err


# A hostile single root colliding with an existing entry of the same name, so the hoist
# has to resolve the collision itself and print the paths it chose.
_HOSTILE_ROOT = f"root{_SPOOF_PORTABLE_ROOT}"


def _hostile_collision_archive(tmp_path: Path, name: str = "bundle.tar") -> Path:
    (tmp_path / _HOSTILE_ROOT).mkdir()
    (tmp_path / _HOSTILE_ROOT / "clash.txt").write_bytes(b"MINE")
    return _tar(tmp_path / name, {f"{_HOSTILE_ROOT}/clash.txt": b"ARCHIVE"})


@pytest.mark.parametrize(
    "overwrite,expected",
    [
        (
            "rename",
            "renamed: rootev\\u2028il/clash.txt -> rootev\\u2028il/clash (1).txt",
        ),
        ("skip", "skipped: rootev\\u2028il/clash.txt"),
    ],
)
def test_hoist_escapes_the_collisions_it_resolves(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    overwrite: str,
    expected: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    archive = _hostile_collision_archive(tmp_path)
    assert main(["x", str(archive), "--overwrite", overwrite]) == EXIT_OK
    err = capsys.readouterr().err
    assert _report_lines(err, expected.split(":", 1)[0] + ":") == [expected]
    assert "\u2028" not in err


def test_hoist_escapes_a_collision_it_stops_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Under ``error`` the hoist names the collision and the wrapper it left behind."""
    monkeypatch.chdir(tmp_path)
    archive = _hostile_collision_archive(tmp_path, name=f"w{_SPOOF_PORTABLE_ROOT}.tar")
    assert main(["x", str(archive), "--overwrite", "error"]) == EXIT_FAIL
    err = capsys.readouterr().err
    assert _report_lines(err, "Destination already exists: ") == [
        "Destination already exists: rootev\\u2028il/clash.txt"
    ]
    assert _report_lines(err, "hoist stopped; ") == [
        "hoist stopped; remaining files left in wev\\u2028il/"
    ]
    assert "\u2028" not in err


def test_hoist_escapes_the_wrapper_when_the_move_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from archivey.cli import extract_cmd

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("refused")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(extract_cmd, "_merge_move", refuse)
    archive = _tar(tmp_path / f"w{_SPOOF_PORTABLE_ROOT}.tar", {"root/a.txt": b"a"})
    assert main(["x", str(archive)]) == EXIT_FAIL
    err = capsys.readouterr().err
    assert _report_lines(err, "files left in ") == ["files left in wev\\u2028il/"]
    assert "\u2028" not in err


# --- the hoist reports where the root landed, not where it was headed -------------


def test_hoist_names_the_free_name_a_rename_chose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The archive's sole file collides with the operator's ``a.txt``.

    The content lands at ``a (1).txt``; ``a.txt`` is the operator's own file, so a
    report naming it points at data the archive never wrote.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_bytes(b"MINE")
    archive = _tar(tmp_path / "bundle.tar", {"a.txt": b"ARCHIVE"})
    assert main(["x", str(archive)]) == EXIT_OK
    err = capsys.readouterr().err
    assert (tmp_path / "a (1).txt").read_bytes() == b"ARCHIVE"
    assert _report_lines(err, "moved to ") == ["moved to a (1).txt"]
    assert _summary_lines(err)[0].endswith("→ a (1).txt")


def test_hoist_names_no_destination_when_skip_discards_the_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Under ``skip`` nothing moves; ``moved to a.txt`` would name the operator's file."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").write_bytes(b"MINE")
    archive = _tar(tmp_path / "bundle.tar", {"a.txt": b"ARCHIVE"})
    assert main(["x", str(archive), "--overwrite", "skip"]) == EXIT_OK
    err = capsys.readouterr().err
    assert (tmp_path / "a.txt").read_bytes() == b"MINE"
    assert _report_lines(err, "skipped: ") == ["skipped: a.txt"]
    assert _report_lines(err, "moved to ") == []
    # Nothing from the archive is on disk, so nothing counts as extracted — the same
    # line a direct extraction hitting this collision prints.
    assert _summary_lines(err) == ["0 extracted, 0 renamed, 1 skipped → ."]


def test_hoist_skip_inside_a_merged_root_is_not_counted_as_extracted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """One skip per colliding child: only the file that moved counts as extracted."""
    monkeypatch.chdir(tmp_path)
    archive = _hoist_collision_archive(tmp_path)
    _seed_existing_root(tmp_path)
    assert main(["x", str(archive), "--overwrite", "skip"]) == EXIT_OK
    assert _summary_lines(capsys.readouterr().err) == [
        "1 extracted, 0 renamed, 1 skipped → root/"
    ]


def test_hoist_does_not_mark_a_file_root_as_a_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A sole file root against an operator *directory* of the same name.

    The trailing ``/`` was read off the pre-existing entry, so the report pointed at
    the operator's directory as if it were where the file went.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.txt").mkdir()
    archive = _tar(tmp_path / "bundle.tar", {"a.txt": b"ARCHIVE"})
    assert main(["x", str(archive)]) == EXIT_OK
    err = capsys.readouterr().err
    assert (tmp_path / "a (1).txt").read_bytes() == b"ARCHIVE"
    assert _report_lines(err, "moved to ") == ["moved to a (1).txt"]
    assert _summary_lines(err)[0].endswith("→ a (1).txt")


def test_relative_name_falls_back_to_forward_slashes() -> None:
    """A path outside the root is reported whole — ``/``-separated, never native.

    Pinned with a Windows-flavoured path so the check has teeth on a POSIX runner,
    where ``str()`` of a native path already has forward slashes.
    """
    from pathlib import PureWindowsPath

    from archivey.cli.extract_cmd import _relative_name

    landed = PureWindowsPath("C:/out/elsewhere/a.txt")
    assert _relative_name(landed, PureWindowsPath("D:/target")) == (
        "C:/out/elsewhere/a.txt"
    )


def test_unmatched_pattern_hint_escapes_the_suggested_dest(
    sample_zip: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The ``-d`` hint repeats the pattern; it is escaped like the ``!r`` form beside it."""
    assert main(["x", str(sample_zip), "ev\x1bil/"]) == EXIT_FAIL
    lines = _report_lines(capsys.readouterr().err, "warning: pattern matched")
    assert lines == [
        "warning: pattern matched no members: 'ev\\x1bil/' (did you mean -d ev\\x1bil?)"
    ]


def test_escape_path_renders_forward_slashes() -> None:
    """A native path is ``/``-separated before escaping, so separators never double."""
    from archivey.cli.format import escape_path

    assert escape_path(Path("dir") / "sub" / "a.txt") == "dir/sub/a.txt"
    assert escape_path(Path("dir") / "ev\x1bil") == "dir/ev\\x1bil"


def _write_zip_with_comment(path: Path, comment: bytes) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("a.txt", b"hi")
        zf.comment = comment
    return path


def test_info_escapes_the_archive_comment_on_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The comment reaches **stdout**, so ``2>/dev/null`` hides nothing.

    A ZIP comment is arbitrary bytes and ``info`` is the verb an operator runs to learn
    what a file is before touching it; printed raw, the archive picks that answer.
    """
    archive = _write_zip_with_comment(
        tmp_path / "cmt.zip", b"ev\x1b[2Kil\rSAFE ARCHIVE\nsecond line"
    )
    assert main(["info", str(archive)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "\x1b" not in out
    assert "\r" not in out
    lines = [ln for ln in out.split("\n") if ln.startswith("comment:")]
    assert lines == ["comment:     ev\\x1b[2Kil\\rSAFE ARCHIVE\\nsecond line"]


def test_info_leaves_an_ordinary_comment_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = _write_zip_with_comment(tmp_path / "cmt.zip", "Café release".encode())
    assert main(["info", str(archive)]) == EXIT_OK
    assert "comment:     Café release\n" in capsys.readouterr().out


def test_info_escapes_every_value_it_prints() -> None:
    """Not only the comment: version strings and ``extra`` entries come from backends.

    Pinned on the line helper because no fixture carries a hostile version or ``extra``
    value today; the point is that a new one cannot reach the terminal raw.
    """
    from archivey.cli.info_cmd import _line

    out = io.StringIO()
    _line("extra.x\x1bkey", "v\x1b[2K\rspoof", out)
    _line("solid", True, out)
    assert out.getvalue() == (
        "extra.x\\x1bkey: v\\x1b[2K\\rspoof\n" + "solid:       True\n"
    )


def test_info_open_failure_is_not_escaped_twice(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An archivey exception has escaped its own message; ``info`` must not redo it."""
    bad = tmp_path / f"{_SPOOF_PORTABLE_ROOT}.zip"
    bad.write_bytes(b"PK\x03\x04" + b"\x00" * 40)
    assert main(["info", str(bad)]) != EXIT_OK
    err = capsys.readouterr().err
    open_lines = _report_lines(err, "open:")
    assert len(open_lines) == 1
    assert "ev\\u2028il.zip" in open_lines[0]  # escaped once, by the exception
    assert "ev\\\\u2028il.zip" not in open_lines[0]  # …and not again


# --- O9: archive-derived text on the LOG path, not just the print path -------------


def _log_through_cli_handler(msg: str, *args: object, **kwargs: object) -> str:
    """Emit one record through the handler ``cli_logging`` installs, return the output."""
    import logging

    from archivey.cli.logging_config import cli_logging

    err = io.StringIO()
    with cli_logging(verbose=False, err=err):
        logging.getLogger("archivey.test").warning(msg, *args, **kwargs)
    return err.getvalue()


def test_log_records_escape_archive_derived_text() -> None:
    """The log path carries archive text too, and reaches the same stderr.

    It is closed at the source rather than at the handler: the exception escapes its own
    message, so interpolating it into a record cannot reintroduce the spoof. This is the
    real shape of the only library site that logs one (``extraction.py``'s
    ``"Skipping %s %r: %s"``).
    """
    exc = ArchiveyError("Destination already exists: /out/ev\x1b[2Kil\rSPOOF.txt")
    out = _log_through_cli_handler("Skipping file %r: %s", "ev\x1b[2Kil.txt", exc)
    assert "\x1b" not in out  # no raw escape sequence
    assert "\r" not in out.rstrip("\n")  # no carriage-return line rewrite
    assert "\\x1b[2K" in out  # …shown losslessly instead


def test_log_records_escape_diagnostic_messages() -> None:
    """``diagnostics_collector`` logs ``"%s"`` of a diagnostic message verbatim.

    That is the one library log site whose interpolation is not an exception and not
    ``%r``, so the diagnostic escapes its own message for the same reason an exception
    does.
    """
    from archivey.diagnostics import (
        Diagnostic,
        DiagnosticCode,
        MemberNameControlsContext,
    )

    diagnostic = Diagnostic(
        occurrence_id="1",
        code=DiagnosticCode.MEMBER_NAME_BIDI_CONTROL,
        message="Member name has controls: /out/ev\x1b[2Kil\rSPOOF.txt",
        context=MemberNameControlsContext(member_name="ev\x1b[2Kil.txt"),
    )
    out = _log_through_cli_handler("%s", diagnostic.message)
    assert "\x1b" not in out
    assert "\\x1b[2K" in out
    # …while the structured channel keeps the real value for a JSON sink.
    assert diagnostic.context.member_name == "ev\x1b[2Kil.txt"


def test_log_records_keep_tracebacks_readable() -> None:
    """Escaping the message must not collapse a multi-line traceback onto one line.

    The traceback is rendered by the stdlib from the exception, not escaped as a block —
    and its final line is safe on its own, because it is the exception's escaped message.
    """
    try:
        raise ArchiveyError("boom: /out/ev\x1b[2Kil\rSPOOF.txt")
    except ArchiveyError:
        out = _log_through_cli_handler("failed", exc_info=True)
    assert "Traceback (most recent call last):" in out
    assert out.count("\n") > 2  # still multi-line
    assert "\x1b" not in out  # …and the final line carries no raw escape
    assert "\\x1b[2K" in out


def test_library_log_records_reach_other_handlers_unescaped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``cli_logging`` leaves ``propagate`` alone, and no longer rewrites records.

    Escaping moved to the message's source, so the CLI handler is an ordinary
    ``logging.Formatter`` again — an embedding app's handler, or caplog here, sees
    exactly the record the library emitted, args included.
    """
    import logging

    from archivey.cli.logging_config import cli_logging

    err = io.StringIO()
    with caplog.at_level(logging.WARNING, logger="archivey.test"):
        with cli_logging(verbose=False, err=err):
            logging.getLogger("archivey.test").warning("name: %r", "ev\x1b[2Kil.txt")

    assert caplog.records[-1].getMessage() == "name: 'ev\\x1b[2Kil.txt'"
    assert caplog.records[-1].args == ("ev\x1b[2Kil.txt",)


def test_uncaught_exception_traceback_reaches_stderr_inert() -> None:
    """The route that no display site can guard: the interpreter prints the traceback.

    A formatter never sees this, and neither does a print site — the interpreter renders
    the exception itself, and its final line is ``str(exc)``. This is why the escaping
    lives at construction rather than at the CLI's handler. Run out-of-process so the
    real ``sys.excepthook`` path is exercised, not a captured string.
    """
    import subprocess

    code = (
        "from archivey.exceptions import ExtractionError\n"
        "raise ExtractionError('Destination exists: /out/ev\\x1b[2Kil\\rSPOOF.txt')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode != 0
    assert "\x1b" not in proc.stderr  # no raw escape sequence reached the terminal
    assert "\\x1b[2K" in proc.stderr  # …shown losslessly on the traceback's last line
    assert "Traceback (most recent call last):" in proc.stderr


def test_log_escaping_leaves_ordinary_messages_alone() -> None:
    """No cosmetic churn for the overwhelmingly common case."""
    out = _log_through_cli_handler("Skipping file %r: plain reason", "dir/file.txt")
    assert out == "WARNING: Skipping file 'dir/file.txt': plain reason\n"


def test_abort_notice_escapes_the_error_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The abort path prints the exception itself, not a report line.

    ``NameCollisionError``'s message embeds the already-written path, which is built
    from the member name — the same spoof class as the log path, reached through a
    different print site. Uses the Windows-legal spoof so the print site is covered on
    every platform; the ANSI variant below is the real erase-and-rewrite sequence.
    """
    archive = _zip(
        tmp_path / "c.zip", {_SPOOF_PORTABLE: b"A", _SPOOF_PORTABLE.upper(): b"B"}
    )
    dest = tmp_path / "out"
    main(["x", str(archive), "-d", str(dest), "--abort-on", "name-collision"])
    err = capsys.readouterr().err
    assert "Name collision" in err
    assert " " not in err
    assert "\\u2028" in err


@_ANSI_ONLY
def test_abort_notice_escapes_an_ansi_spoof(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The real spoof through the abort print site.

    Unix-only: on Windows the member cannot be written at all (WinError 123), so the
    run fails before two names ever collide. The name still reaches stderr there, in
    the WARNING reporting that it could not be written — escaped by ``%r``, which
    ``test_log_records_escape_archive_derived_text`` covers.
    """
    archive = _zip(tmp_path / "c.zip", {_SPOOF_ANSI: b"A", _SPOOF_ANSI.upper(): b"B"})
    dest = tmp_path / "out"
    main(["x", str(archive), "-d", str(dest), "--abort-on", "name-collision"])
    err = capsys.readouterr().err
    assert "Name collision" in err
    assert "\x1b" not in err
    assert "\r" not in err


def test_test_verb_escapes_failure_detail(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``archivey test`` appends the exception to its FAIL line; that is a print site too.

    Driven through the real verb: the member read raises an ``ArchiveyError`` whose
    message embeds a member-derived path, which is how a hostile name reaches this line.
    """
    from archivey.exceptions import ArchiveyError

    archive = _zip(tmp_path / "t.zip", {"a.txt": b"A"})

    def boom(*_args: object, **_kwargs: object):
        # Raise from the iterator, not the call: the FAIL branch wraps ``next(it)``,
        # while a call-time raise lands in main()'s top-level handler instead.
        def _gen():
            raise ArchiveyError(f"Error reading member: /out/{_SPOOF_ANSI}")
            yield  # pragma: no cover - unreachable, makes this a generator

        return _gen()

    monkeypatch.setattr(
        "archivey.internal.base_reader.BaseArchiveReader.stream_members", boom
    )
    main(["test", str(archive)])
    err = capsys.readouterr().err
    lines = _report_lines(err, "FAIL")
    assert lines, err
    assert all("\x1b" not in ln for ln in lines)
    assert any("\\x1b[2K" in ln for ln in lines)


def test_dunder_main_is_importable_without_running_the_cli() -> None:
    """``python -m archivey`` runs the module as ``__main__``; a plain import must not.

    Without the ``if __name__`` guard, importing ``archivey.__main__`` — which any
    package walker, docs autoapi pass or import-based coverage warm-up does — ran the
    CLI against whatever ``sys.argv`` the host happened to have and exited from inside
    the import.
    """
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-c", "import archivey.__main__; print('imported')"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "imported" in proc.stdout
    assert "usage:" not in proc.stderr


# --- S24-K3: a bare ``--`` takes the default verb ahead of the separator ---


def test_inject_default_list_puts_verb_before_separator() -> None:
    assert _inject_default_list(["--", "-w.zip"]) == ["list", "--", "-w.zip"]
    assert _inject_default_list(["--track-io", "--", "a.zip"]) == [
        "--track-io",
        "list",
        "--",
        "a.zip",
    ]
    # After ``--`` every token is a positional, so a verb-shaped word is an archive name.
    assert _inject_default_list(["--", "list"]) == ["list", "--", "list"]
    # A spelled verb before ``--`` is left alone.
    assert _inject_default_list(["list", "--", "a.zip"]) == ["list", "--", "a.zip"]


def test_double_dash_lists_dash_named_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    _zip(tmp_path / "-w.zip", {"inner.txt": b"x"})
    assert main(["--", "-w.zip"]) == EXIT_OK
    assert "inner.txt" in capsys.readouterr().out


# --- ARC-125: the bpo-26240 workaround matches the ``pattern`` metavar ---


def test_missing_archive_message_omits_optional_patterns(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["x"]) == EXIT_USAGE
    err = capsys.readouterr().err
    assert "the following arguments are required: archive\n" in err
    assert "pattern" not in err.splitlines()[-1]


# --- S24-K2: the CLI's own wrap directory never goes through a symlink ---


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - Windows
        pytest.skip(f"cannot create symlinks here: {exc}")


def test_smart_dest_steps_aside_from_dangling_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    z = _zip(tmp_path / "pkg.zip", {"a.txt": b"a", "b.txt": b"b"})
    _symlink_or_skip(tmp_path / "pkg", tmp_path / "nowhere")
    assert main(["x", str(z)]) == EXIT_OK
    assert (tmp_path / "pkg (1)" / "a.txt").read_bytes() == b"a"
    assert not (tmp_path / "nowhere").exists()


def test_smart_dest_does_not_follow_symlink_to_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    z = _zip(tmp_path / "pkg.zip", {"a.txt": b"a", "b.txt": b"b"})
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _symlink_or_skip(tmp_path / "pkg", elsewhere)
    assert main(["x", str(z), "--overwrite", "replace"]) == EXIT_OK
    assert list(elsewhere.iterdir()) == []
    assert (tmp_path / "pkg (1)" / "b.txt").read_bytes() == b"b"


# --- S24-K6: an incomplete ``test`` run exits nonzero even with no failure ---


def test_test_early_stop_without_error_exits_fail(
    sample_zip: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from archivey.internal.base_reader import BaseArchiveReader

    real = BaseArchiveReader.stream_members

    def _one_then_stop(self: BaseArchiveReader, members: object = None) -> object:
        for item in real(self, members):
            yield item
            return

    monkeypatch.setattr(BaseArchiveReader, "stream_members", _one_then_stop)
    assert main(["test", str(sample_zip)]) == EXIT_FAIL
    assert "1 OK, 0 failed, 2 not tested" in capsys.readouterr().err
