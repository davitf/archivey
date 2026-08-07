"""Guardrails for the simplicity & consistency review (`review/simplicity-consistency/`).

Two kinds of test live here, and the difference is the point:

- **Guardrails** (plain assertions) pin a cross-format rule the review classified as
  *format law* or *settled*, so it cannot silently change. Passing today, and expected
  to keep passing.
- **Red halves** (``@pytest.mark.xfail(strict=True)``) assert the behaviour the review
  argues is *correct*, for divergences it classified as **accidents**. They fail today
  on purpose. When a fix lands, the xfail turns into an XPASS and ``strict=True`` fails
  the suite — that is the signal to delete the marker, not to widen it.

Nothing here changes library behaviour: the review is analysis-only until the
maintainer picks pay items (`brief.md` §Hard constraints). Pinning a divergence is
**not** endorsing it.

Every red half names the finding id from
`review/simplicity-consistency/SUMMARY.md` so the two stay linked.
"""

from __future__ import annotations

import io
import shutil
from pathlib import Path

import pytest

from archivey import (
    ArchiveFormat,
    ArchiveyConfig,
    ArchiveyUsageError,
    FormatSupport,
    StreamNotSeekableError,
    format_availability,
    open_archive,
    open_stream,
)
from tests.sample_archives import CORPUS, FORMAT_KEYS, CorpusEntry, corpus_archive_path

_BY_ID: dict[str, CorpusEntry] = {e.id: e for e in CORPUS}


def _entry(entry_id: str) -> CorpusEntry:
    return _BY_ID[entry_id]


def _archive(entry_id: str, key: str, tmp_path: Path) -> Path:
    """Build one corpus archive, skipping cleanly when this env cannot make it.

    Mirrors ``test_corpus_sweep._skip_unless_runnable``'s intent without importing it:
    a missing reader or a missing builder binary is an *unmeasured* cell, not a pass.
    """
    entry = _entry(entry_id)
    if key not in entry.formats:
        pytest.skip(f"corpus entry {entry_id!r} is not built as {key!r}")
    availability = format_availability(FORMAT_KEYS[key])
    if availability.support is FormatSupport.NONE:
        pytest.skip(f"format {key!r} not readable here: {availability.missing}")
    try:
        return corpus_archive_path(entry, key, tmp_path)
    except FileNotFoundError as exc:  # a builder binary is missing
        pytest.skip(f"builder for {key!r} unavailable: {exc}")


class _NonSeekable(io.RawIOBase):
    """Forward-only byte source — the pipe shape."""

    def __init__(self, data: bytes) -> None:
        super().__init__()
        self._inner = io.BytesIO(data)

    def readable(self) -> bool:
        return True

    def read(self, n: int = -1, /) -> bytes:
        return self._inner.read(n)

    def readinto(self, b) -> int:  # type: ignore[override]
        return self._inner.readinto(b)

    def seekable(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# P1 — declared member-stream seekability leaks into member *metadata*
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["lz", "xz"])
def test_declared_seekability_changes_member_size(key: str, tmp_path: Path) -> None:
    """P1 (pin): ``member.size`` today depends on ``seekable_members``.

    This pins the divergence as it is, so a change to it is visible in the diff.
    ``seekable_members`` is documented as being about ``seek()`` on a member stream;
    it also decides whether the xz index / lzip trailer is read for the size.
    """
    path = _archive("single-file", key, tmp_path)
    with open_archive(path) as reader:
        without = reader.members()[0].size
    with open_archive(path, seekable_members=True) as reader:
        with_flag = reader.members()[0].size

    assert without is None
    assert isinstance(with_flag, int)


@pytest.mark.xfail(
    strict=True,
    reason="P1: seekable_members is a stream capability; it must not change metadata",
)
@pytest.mark.parametrize("key", ["lz", "xz"])
def test_member_size_does_not_depend_on_declared_seekability(
    key: str, tmp_path: Path
) -> None:
    """P1 (red half): the same archive should report the same ``size`` either way.

    The size comes from the xz index / lzip trailer — a bounded peek over a source that
    is already seekable. Nothing about it needs the caller to want ``seek()``.
    """
    path = _archive("single-file", key, tmp_path)
    with open_archive(path) as reader:
        without = reader.members()[0].size
    with open_archive(path, seekable_members=True) as reader:
        with_flag = reader.members()[0].size

    assert without == with_flag


@pytest.mark.xfail(
    strict=True,
    reason="P1: VISION 'hashes without decompression' — lzip CRC-32 is a trailer read",
)
def test_lzip_surfaces_crc32_without_declaring_seekable_members(
    tmp_path: Path,
) -> None:
    """P1 (red half): a dedupe caller doing a plain ``open_archive`` gets the CRC-32.

    ``format-single-file-compressors`` promises the lzip CRC-32 "when the seekable lzip
    index is available"; today the gate is the caller's ``seekable_members`` flag, so
    the founding dedupe use case (`VISION.md`) misses it on the default open.
    """
    from archivey.types import HashAlgorithm

    path = _archive("single-file", "lz", tmp_path)
    with open_archive(path) as reader:
        assert HashAlgorithm.CRC32 in reader.members()[0].hashes


def test_gzip_crc32_is_not_gated_on_declared_seekability(tmp_path: Path) -> None:
    """P1 (guardrail): gzip already does it the right way — keep it that way.

    The gzip trailer CRC-32 is surfaced from a bounded peek regardless of
    ``seekable_members``. This is the behaviour the lzip/xz rows should converge on,
    so it is pinned rather than left to drift toward the gated shape.
    """
    from archivey.types import HashAlgorithm

    path = _archive("single-file", "gz", tmp_path)
    for kwargs in ({}, {"seekable_members": True}):
        with open_archive(path, **kwargs) as reader:  # type: ignore[arg-type]
            assert HashAlgorithm.CRC32 in reader.members()[0].hashes


# ---------------------------------------------------------------------------
# P2 — index-topology table vs the directory backend
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("streaming", [False, True])
def test_directory_report_peek_returns_none(streaming: bool, tmp_path: Path) -> None:
    """P2 (pin): the directory backend has no upfront index to peek at."""
    path = _archive("basic", "dir", tmp_path)
    with open_archive(path, streaming=streaming) as reader:
        assert reader.members_report_if_available() is None


@pytest.mark.xfail(
    strict=True,
    reason="P2: access-mode-and-cost lists 'Leading (directory, ISO)' as a complete "
    "report in both modes; the directory backend returns None",
)
@pytest.mark.parametrize("streaming", [False, True])
def test_directory_report_peek_matches_index_topology_spec(
    streaming: bool, tmp_path: Path
) -> None:
    """P2 (red half): spec and code disagree — which one is wrong is a maintainer call.

    Recorded as a red half rather than a spec edit because `CONTRIBUTING.md` says to
    pause and ask on a spec/design discrepancy instead of silently picking a winner.
    """
    path = _archive("basic", "dir", tmp_path)
    with open_archive(path, streaming=streaming) as reader:
        report = reader.members_report_if_available()
        assert report is not None
        assert report.error is None


@pytest.mark.parametrize("key", ["iso", "zip", "7z"])
def test_leading_and_trailing_index_backends_do_offer_a_report_peek(
    key: str, tmp_path: Path
) -> None:
    """P2 (guardrail): the backends the topology table covers correctly still do."""
    path = _archive("basic", key, tmp_path)
    with open_archive(path) as reader:
        report = reader.members_report_if_available()
        assert report is not None
        assert report.error is None
        assert len(report.members) > 0


def test_tar_has_no_report_peek_before_a_pass(tmp_path: Path) -> None:
    """P2 (guardrail): the no-index row of the topology table — format law, pinned."""
    path = _archive("basic", "tar", tmp_path)
    with open_archive(path) as reader:
        assert reader.members_report_if_available() is None


# ---------------------------------------------------------------------------
# P3 — an explicit wrong format= can succeed with an empty listing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strict_eof", [False, True])
def test_wrong_explicit_format_on_iso_yields_an_empty_listing(
    strict_eof: bool, tmp_path: Path
) -> None:
    """P3 (pin): ``format=TAR`` over an ISO opens and lists zero members.

    An ISO's first 32 KiB system area is zero-filled, which is byte-identical to a TAR
    end-of-archive marker, so the TAR reader sees a valid empty archive.
    ``strict_archive_eof=True`` does not catch it either.
    """
    path = _archive("basic", "iso", tmp_path)
    config = ArchiveyConfig(strict_archive_eof=strict_eof)
    with open_archive(path, format=ArchiveFormat.TAR, config=config) as reader:
        assert reader.format == ArchiveFormat.TAR
        assert reader.members() == []


@pytest.mark.xfail(
    strict=True,
    reason="P3: same class as the directory format= override rejected in #225 — an "
    "asserted format that is wrong should not succeed on the wrong data",
)
def test_wrong_explicit_format_does_not_silently_succeed(tmp_path: Path) -> None:
    """P3 (red half): asserting the wrong format should not return a clean empty reader."""
    path = _archive("basic", "iso", tmp_path)
    with pytest.raises(Exception):  # noqa: B017 - shape is the open question, not the type
        with open_archive(path, format=ArchiveFormat.TAR) as reader:
            reader.members()


def test_wrong_explicit_format_is_loud_for_most_formats(tmp_path: Path) -> None:
    """P3 (guardrail): the non-zero-prefixed formats do fail loudly — keep them loud."""
    path = _archive("basic", "zip", tmp_path)
    with pytest.raises(Exception):  # noqa: B017
        with open_archive(path, format=ArchiveFormat.TAR) as reader:
            reader.members()


# ---------------------------------------------------------------------------
# P4 — encoding= is honoured by some backends and silently discarded by others
# ---------------------------------------------------------------------------


def _names_with_and_without_encoding(path: Path) -> tuple[list[str], list[str]]:
    with open_archive(path) as reader:
        base = [m.name for m in reader.members()]
    # cp500 (EBCDIC) re-maps even ASCII, so "unchanged" means "not applied at all"
    # rather than "applied but this corpus has no non-ASCII names".
    with open_archive(path, encoding="cp500") as reader:
        alt = [m.name for m in reader.members()]
    return base, alt


@pytest.mark.parametrize("key", ["zip", "tar"])
def test_encoding_argument_is_applied(key: str, tmp_path: Path) -> None:
    """P4 (guardrail): the backends that consume ``encoding=`` still consume it."""
    base, alt = _names_with_and_without_encoding(_archive("basic", key, tmp_path))
    assert base != alt


@pytest.mark.parametrize("key", ["iso", "7z", "dir"])
def test_encoding_argument_is_silently_discarded(key: str, tmp_path: Path) -> None:
    """P4 (pin): these backends accept ``encoding=`` and ignore it, with no signal."""
    base, alt = _names_with_and_without_encoding(_archive("basic", key, tmp_path))
    assert base == alt


@pytest.mark.xfail(
    strict=True,
    reason="P4: same class as the directory format= override — an explicit argument "
    "that cannot be honoured should be refused, not discarded",
)
@pytest.mark.parametrize("key", ["iso", "7z", "dir"])
def test_unusable_encoding_argument_is_refused(key: str, tmp_path: Path) -> None:
    """P4 (red half): ignoring an explicit caller assertion is the #225/P8 failure mode."""
    path = _archive("basic", key, tmp_path)
    with pytest.raises(ArchiveyUsageError):
        open_archive(path, encoding="cp500").close()


# ---------------------------------------------------------------------------
# P5 — pipe support: loud and uniform (good), but not queryable (the finding)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["zip", "iso", "7z"])
def test_trailing_index_formats_refuse_a_pipe_loudly(key: str, tmp_path: Path) -> None:
    """P5 (guardrail): the refusal is one typed error with one message shape.

    This half of the seed turned out to be **fine**, and pinning it is what keeps it
    fine: a backend that started failing softly here would be a regression.
    """
    path = _archive("basic", key, tmp_path)
    data = path.read_bytes()
    with pytest.raises(StreamNotSeekableError):
        open_archive(_NonSeekable(data), streaming=True)


@pytest.mark.parametrize("key", ["tar", "tar.gz"])
def test_front_indexed_formats_accept_a_pipe(key: str, tmp_path: Path) -> None:
    """P5 (guardrail): the other side of the same rule."""
    path = _archive("basic", key, tmp_path)
    with open_archive(_NonSeekable(path.read_bytes()), streaming=True) as reader:
        assert sum(1 for _ in reader) > 0


# ---------------------------------------------------------------------------
# P6 — the two entry points disagree about what a directory is
# ---------------------------------------------------------------------------


def test_open_stream_reports_a_directory_as_not_found(tmp_path: Path) -> None:
    """P6 (pin): ``open_stream`` says "not found" for a path that exists."""
    d = tmp_path / "tree"
    d.mkdir()
    with pytest.raises(FileNotFoundError, match="Compressed stream not found"):
        open_stream(d)


@pytest.mark.xfail(
    strict=True,
    reason="P6: the path exists and is a directory; 'not found' is the wrong story, "
    "and open_archive opens the same path happily",
)
def test_open_stream_directory_error_names_the_real_problem(tmp_path: Path) -> None:
    """P6 (red half): whatever the type, the message must not claim the path is absent.

    Asserted as the *absence* of "not found" rather than the presence of "directory":
    the message interpolates the path, and a pytest tmp dir carries the test's own name,
    so a positive substring check would pass for the wrong reason.
    """
    d = tmp_path / "tree"
    d.mkdir()
    with pytest.raises(Exception) as excinfo:  # noqa: B017 - type is the open question
        open_stream(d)
    message = str(excinfo.value).replace(str(d), "<path>").lower()
    assert "not found" not in message


def test_open_archive_opens_the_directory_open_stream_rejects(tmp_path: Path) -> None:
    """P6 (guardrail): the asymmetry itself, pinned so a fix has to address both sides."""
    d = tmp_path / "tree"
    d.mkdir()
    (d / "a.txt").write_bytes(b"hello")
    with open_archive(d) as reader:
        assert [m.name for m in reader.members()] == ["a.txt"]


# ---------------------------------------------------------------------------
# The uniform surface that *is* uniform — pinned so it stays that way
# ---------------------------------------------------------------------------

_UNIFORM_KEYS = ["zip", "tar", "tar.gz", "iso", "7z", "dir", "gz"]


@pytest.mark.parametrize("key", _UNIFORM_KEYS)
def test_reader_surface_is_uniform_across_formats(key: str, tmp_path: Path) -> None:
    """The review's main negative result: these rows agree on every measured backend.

    The probe found no format that diverges on any of them. Pinning them here is the
    cheap half of the review — a future backend that gets one wrong fails a test rather
    than becoming a Gotchas bullet.
    """
    entry_id = "single-file" if key == "gz" else "basic"
    path = _archive(entry_id, key, tmp_path)

    with open_archive(path) as reader:
        with pytest.raises(TypeError):
            len(reader)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            "some-name" in reader  # type: ignore[operator]
        assert reader.get("no-such-member") is None
        with pytest.raises(KeyError):
            reader.read("no-such-member")

        member = next(m for m in reader.members() if m.type.name == "FILE")
        stream = reader.open(member)
        try:
            with pytest.raises(ArchiveyUsageError):
                reader.open(member)
            with pytest.raises(io.UnsupportedOperation):
                stream.seek(0)
        finally:
            stream.close()

    with pytest.raises(ArchiveyUsageError):
        reader.format  # noqa: B018 - property access after close must raise


@pytest.mark.parametrize("key", _UNIFORM_KEYS)
def test_streaming_mode_is_uniform_across_formats(key: str, tmp_path: Path) -> None:
    """Streaming enforcement agrees on every backend that can stream from a path."""
    entry_id = "single-file" if key == "gz" else "basic"
    path = _archive(entry_id, key, tmp_path)

    from archivey import UnsupportedOperationError

    with open_archive(path, streaming=True) as reader:
        for op in (
            lambda: reader.members(),
            lambda: reader.get("x"),
            lambda: reader.open("x"),
            lambda: reader.read("x"),
        ):
            with pytest.raises(UnsupportedOperationError):
                op()

        assert sum(1 for _ in reader) >= 0
        with pytest.raises(UnsupportedOperationError):
            for _ in reader:
                pass


def test_rar_column_is_unmeasured_without_the_rar_writer() -> None:
    """Documents *why* the review's RAR column is unmeasured rather than ``N/A``.

    ``unrar`` (the decompressor) is enough to *read* RAR, but the corpus builds its RAR
    fixtures with the RARLAB ``rar`` writer. Its absence is **deliberate**, not an
    environment gap: `.github/workflows/ci.yml` installs unrar only and actively deletes
    ``rar`` on macOS ("keep writer off the PATH here"), because the RAR fixtures'
    digest expectations are Linux-fixture-oriented. `scripts/setup-dev-env.sh` matches.

    The consequence is worth stating rather than discovering twice: the 41 RAR cases of
    the cross-format conformance sweep run on no CI leg and in no provisioned dev
    environment, so the RAR column of that regression net is unexercised. Whether that
    is still the intended trade-off is Q9 in `review/simplicity-consistency/QUESTIONS.md`.

    The assertion is the coupling itself: RAR readability does not imply RAR
    measurability, so a green suite on an unrar-only box says nothing about the RAR
    column.
    """
    rar_is_readable = format_availability(ArchiveFormat.RAR).support is not (
        FormatSupport.NONE
    )
    if shutil.which("rar") is not None:
        pytest.skip("rar writer present — the RAR corpus column is measurable here")
    assert rar_is_readable, "unrar present: RAR reads fine, yet no RAR fixture is built"
