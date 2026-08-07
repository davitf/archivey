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

Every test names the merged finding id from
`review/simplicity-consistency/SUMMARY.md` so the two stay linked. The review was
delivered twice independently (PR #230 and PR #231) and merged; findings carried over
from the second pass are marked in the SUMMARY's provenance column and their guardrails
were moved here from `review/simplicity-consistency/tests/`, which `testpaths =
["tests"]` never collected — a guardrail CI does not run guards nothing.
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
from archivey.types import MemberType
from tests.sample_archives import CORPUS, FORMAT_KEYS, CorpusEntry, corpus_archive_path

_FILE = MemberType.FILE

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
# F1 — declared member-stream seekability leaks into member *metadata*
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["lz", "xz"])
def test_declared_seekability_changes_member_size(key: str, tmp_path: Path) -> None:
    """F1 (pin): ``member.size`` today depends on ``seekable_members``.

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
    reason="F1: seekable_members is a stream capability; it must not change metadata",
)
@pytest.mark.parametrize("key", ["lz", "xz"])
def test_member_size_does_not_depend_on_declared_seekability(
    key: str, tmp_path: Path
) -> None:
    """F1 (red half): the same archive should report the same ``size`` either way.

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
    reason="F1: VISION 'hashes without decompression' — lzip CRC-32 is a trailer read",
)
def test_lzip_surfaces_crc32_without_declaring_seekable_members(
    tmp_path: Path,
) -> None:
    """F1 (red half): a dedupe caller doing a plain ``open_archive`` gets the CRC-32.

    ``format-single-file-compressors`` promises the lzip CRC-32 "when the seekable lzip
    index is available"; today the gate is the caller's ``seekable_members`` flag, so
    the founding dedupe use case (`VISION.md`) misses it on the default open.
    """
    from archivey.types import HashAlgorithm

    path = _archive("single-file", "lz", tmp_path)
    with open_archive(path) as reader:
        assert HashAlgorithm.CRC32 in reader.members()[0].hashes


def test_gzip_crc32_is_not_gated_on_declared_seekability(tmp_path: Path) -> None:
    """F1 (guardrail): gzip already does it the right way — keep it that way.

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
# F6 — index-topology table vs the directory backend
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("streaming", [False, True])
def test_directory_report_peek_returns_none(streaming: bool, tmp_path: Path) -> None:
    """F6 (pin): the directory backend has no upfront index to peek at."""
    path = _archive("basic", "dir", tmp_path)
    with open_archive(path, streaming=streaming) as reader:
        assert reader.members_report_if_available() is None


@pytest.mark.xfail(
    strict=True,
    reason="F6: access-mode-and-cost lists 'Leading (directory, ISO)' as a complete "
    "report in both modes; the directory backend returns None",
)
@pytest.mark.parametrize("streaming", [False, True])
def test_directory_report_peek_matches_index_topology_spec(
    streaming: bool, tmp_path: Path
) -> None:
    """F6 (red half): spec and code disagree — which one is wrong is a maintainer call.

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
    """F6 (guardrail): the backends the topology table covers correctly still do."""
    path = _archive("basic", key, tmp_path)
    with open_archive(path) as reader:
        report = reader.members_report_if_available()
        assert report is not None
        assert report.error is None
        assert len(report.members) > 0


def test_tar_has_no_report_peek_before_a_pass(tmp_path: Path) -> None:
    """F6 (guardrail): the no-index row of the topology table — format law, pinned."""
    path = _archive("basic", "tar", tmp_path)
    with open_archive(path) as reader:
        assert reader.members_report_if_available() is None


# ---------------------------------------------------------------------------
# F7 — an explicit wrong format= can succeed with an empty listing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strict_eof", [False, True])
def test_wrong_explicit_format_on_iso_yields_an_empty_listing(
    strict_eof: bool, tmp_path: Path
) -> None:
    """F7 (pin): ``format=TAR`` over an ISO opens and lists zero members.

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
    reason="F7: same class as the directory format= override rejected in #225 — an "
    "asserted format that is wrong should not succeed on the wrong data",
)
def test_wrong_explicit_format_does_not_silently_succeed(tmp_path: Path) -> None:
    """F7 (red half): asserting the wrong format should not return a clean empty reader."""
    path = _archive("basic", "iso", tmp_path)
    with pytest.raises(Exception):  # noqa: B017 - shape is the open question, not the type
        with open_archive(path, format=ArchiveFormat.TAR) as reader:
            reader.members()


def test_wrong_explicit_format_is_loud_for_most_formats(tmp_path: Path) -> None:
    """F7 (guardrail): the non-zero-prefixed formats do fail loudly — keep them loud."""
    path = _archive("basic", "zip", tmp_path)
    with pytest.raises(Exception):  # noqa: B017
        with open_archive(path, format=ArchiveFormat.TAR) as reader:
            reader.members()


# ---------------------------------------------------------------------------
# F2 — encoding= is honoured by some backends and silently discarded by others
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
    """F2 (guardrail): the backends that consume ``encoding=`` still consume it."""
    base, alt = _names_with_and_without_encoding(_archive("basic", key, tmp_path))
    assert base != alt


@pytest.mark.parametrize("key", ["iso", "7z", "dir"])
def test_encoding_argument_is_silently_discarded(key: str, tmp_path: Path) -> None:
    """F2 (pin): these backends accept ``encoding=`` and ignore it, with no signal."""
    base, alt = _names_with_and_without_encoding(_archive("basic", key, tmp_path))
    assert base == alt


@pytest.mark.xfail(
    strict=True,
    reason="F2: same class as the directory format= override — an explicit argument "
    "that cannot be honoured should be refused, not discarded",
)
@pytest.mark.parametrize("key", ["iso", "7z", "dir"])
def test_unusable_encoding_argument_is_refused(key: str, tmp_path: Path) -> None:
    """F2 (red half): ignoring an explicit caller assertion is the #225/P8 failure mode."""
    path = _archive("basic", key, tmp_path)
    with pytest.raises(ArchiveyUsageError):
        open_archive(path, encoding="cp500").close()


# ---------------------------------------------------------------------------
# F8 — pipe support: loud and uniform (good), but not queryable (the finding)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["zip", "iso", "7z"])
def test_trailing_index_formats_refuse_a_pipe_loudly(key: str, tmp_path: Path) -> None:
    """F8 (guardrail): the refusal is one typed error with one message shape.

    This half of the seed turned out to be **fine**, and pinning it is what keeps it
    fine: a backend that started failing softly here would be a regression.
    """
    path = _archive("basic", key, tmp_path)
    data = path.read_bytes()
    with pytest.raises(StreamNotSeekableError):
        open_archive(_NonSeekable(data), streaming=True)


@pytest.mark.parametrize("key", ["tar", "tar.gz"])
def test_front_indexed_formats_accept_a_pipe(key: str, tmp_path: Path) -> None:
    """F8 (guardrail): the other side of the same rule."""
    path = _archive("basic", key, tmp_path)
    with open_archive(_NonSeekable(path.read_bytes()), streaming=True) as reader:
        assert sum(1 for _ in reader) > 0


# ---------------------------------------------------------------------------
# F11 — the two entry points disagree about what a directory is
# ---------------------------------------------------------------------------


def test_open_stream_reports_a_directory_as_not_found(tmp_path: Path) -> None:
    """F11 (pin): ``open_stream`` says "not found" for a path that exists."""
    d = tmp_path / "tree"
    d.mkdir()
    with pytest.raises(FileNotFoundError, match="Compressed stream not found"):
        open_stream(d)


@pytest.mark.xfail(
    strict=True,
    reason="F11: the path exists and is a directory; 'not found' is the wrong story, "
    "and open_archive opens the same path happily",
)
def test_open_stream_directory_error_names_the_real_problem(tmp_path: Path) -> None:
    """F11 (red half): whatever the type, the message must not claim the path is absent.

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
    """F11 (guardrail): the asymmetry itself, pinned so a fix has to address both sides."""
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
    is still the intended trade-off is F16 / Q11 in `review/simplicity-consistency/QUESTIONS.md`.

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


# ---------------------------------------------------------------------------
# F3 — raw ValueError crosses open_archive for volume-sequence misuse
# (merged from the second review pass; verified independently here)
# ---------------------------------------------------------------------------


def test_empty_source_sequence_raises_raw_valueerror(tmp_path: Path) -> None:
    """F3 (pin): `open_archive([])` raises a bare `ValueError`.

    `resolve_source` runs at `core.py:194`, before any backend translator exists, so
    nothing on that path can type the error.
    """
    with pytest.raises(ValueError) as excinfo:
        open_archive([])
    assert type(excinfo.value) is ValueError  # not an ArchiveyError/UsageError subclass


def test_non_seekable_volume_sequence_raises_raw_valueerror() -> None:
    """F3 (pin): a non-seekable volume stream raises a bare `ValueError` too.

    Note the inconsistency this pins: the *single*-source version of the same refusal
    is a typed `StreamNotSeekableError` (see the pipe guardrails above).
    """
    with pytest.raises(ValueError) as excinfo:
        open_archive([_NonSeekable(b"x" * 100), _NonSeekable(b"y" * 100)])
    assert type(excinfo.value) is ValueError


@pytest.mark.xfail(
    strict=True,
    reason="F3: caller misuse and capability refusal are both typed everywhere else",
)
def test_empty_source_sequence_is_a_usage_error() -> None:
    """F3 (red half): an empty sequence is caller misuse, so `ArchiveyUsageError`."""
    with pytest.raises(ArchiveyUsageError):
        open_archive([])


@pytest.mark.xfail(
    strict=True,
    reason="F3: a non-seekable volume is the same refusal as a non-seekable single "
    "source, which is already StreamNotSeekableError",
)
def test_non_seekable_volume_sequence_is_a_stream_error() -> None:
    """F3 (red half): match the single-source spelling of the same refusal."""
    with pytest.raises(StreamNotSeekableError):
        open_archive([_NonSeekable(b"x" * 100), _NonSeekable(b"y" * 100)])


# ---------------------------------------------------------------------------
# F4 — ZIP maps every ValueError to CorruptionError, including "already closed"
# ---------------------------------------------------------------------------


def _zip_bytes() -> bytes:
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", b"hello")
    return buf.getvalue()


def test_zip_underlying_close_is_reported_as_corruption() -> None:
    """F4 (pin): closing the underlying `ZipFile` under a live reader reads as damage.

    Distinct from the settled `#225` behaviour: a normal `reader.close()` followed by
    `open()` already raises `ArchiveyUsageError` (pinned by the uniform-surface test
    above). Only this path — the underlying handle closed while the reader still
    believes it is open — lands in the ZIP translator's blanket `ValueError` arm.
    """
    from archivey import CorruptionError

    with open_archive(io.BytesIO(_zip_bytes())) as reader:
        member = next(m for m in reader.members() if m.type is _FILE)
        reader._archive.close()  # type: ignore[attr-defined]  # deliberate: simulate the fault
        with pytest.raises(CorruptionError):
            reader.open(member)


@pytest.mark.xfail(
    strict=True,
    reason="F4: an already-closed handle is a lifecycle fault, not archive damage; "
    "reporting it as CorruptionError sends a caller hunting a bad file",
)
def test_zip_underlying_close_is_a_usage_error() -> None:
    """F4 (red half): the archive bytes are fine — the handle is not."""
    with open_archive(io.BytesIO(_zip_bytes())) as reader:
        member = next(m for m in reader.members() if m.type is _FILE)
        reader._archive.close()  # type: ignore[attr-defined]
        with pytest.raises(ArchiveyUsageError):
            reader.open(member)


# ---------------------------------------------------------------------------
# F5 — single-file compressed_size is still Path-gated
# ---------------------------------------------------------------------------

_SINGLE_FILE_KEYS = ["gz", "bz2", "xz", "zst", "lz4", "lz", "zz", "br"]


@pytest.mark.parametrize("key", _SINGLE_FILE_KEYS)
def test_single_file_compressed_size_is_path_gated(key: str, tmp_path: Path) -> None:
    """F5 (pin): `compressed_size` is filled for a Path and `None` for a `BytesIO`.

    This is the residual the `#225` Path/seekable sweep did not reach, and it holds for
    **every** single-file codec, not just gzip: `single_file_reader.py:173` uses
    `os.path.getsize` behind an `isinstance(..., Path)` check with no seekable-stream
    fallback, while the trailer/CRC probes beside it already handle both.
    """
    path = _archive("single-file", key, tmp_path)
    with open_archive(path) as reader:
        from_path = reader.members()[0].compressed_size
    with open_archive(io.BytesIO(path.read_bytes())) as reader:
        from_stream = reader.members()[0].compressed_size

    assert isinstance(from_path, int)
    assert from_stream is None


@pytest.mark.xfail(
    strict=True,
    reason="F5: a seekable stream can answer this with one SEEK_END, exactly as the "
    "trailer probes next to it already do",
)
@pytest.mark.parametrize("key", ["gz", "xz"])
def test_single_file_compressed_size_is_not_path_gated(
    key: str, tmp_path: Path
) -> None:
    """F5 (red half): source shape must not decide whether the field exists."""
    path = _archive("single-file", key, tmp_path)
    with open_archive(path) as reader:
        from_path = reader.members()[0].compressed_size
    with open_archive(io.BytesIO(path.read_bytes())) as reader:
        from_stream = reader.members()[0].compressed_size

    assert from_path == from_stream


def test_container_formats_report_compressed_size_from_either_shape(
    tmp_path: Path,
) -> None:
    """F5 (guardrail): the container backends already do it right — keep them there."""
    path = _archive("basic", "zip", tmp_path)
    with open_archive(path) as reader:
        from_path = next(m for m in reader.members() if m.type is _FILE).compressed_size
    with open_archive(io.BytesIO(path.read_bytes())) as reader:
        from_stream = next(
            m for m in reader.members() if m.type is _FILE
        ).compressed_size
    assert from_path == from_stream and from_path is not None


# ---------------------------------------------------------------------------
# F9 — header encryption needs the password at open (format law), and the
# user guide's laziness bullet does not say so
# ---------------------------------------------------------------------------


def test_header_encrypted_7z_needs_the_password_at_open(tmp_path: Path) -> None:
    """F9 (guardrail): format law — the listing itself is ciphertext.

    Pinned because it bounds the `#225` laziness fix: *data* encryption stays lazy,
    *header* encryption cannot. `docs/reading-members.md` currently states the lazy
    half without the bound (finding F11).
    """
    from archivey import EncryptionError

    entry = _entry("encrypted-header")
    if "7z" not in entry.formats:
        pytest.skip("no header-encrypted 7z corpus entry")
    availability = format_availability(FORMAT_KEYS["7z"])
    if availability.support is FormatSupport.NONE:
        pytest.skip(f"7z not readable here: {availability.missing}")
    path = corpus_archive_path(entry, "7z", tmp_path)

    with pytest.raises(EncryptionError):
        open_archive(path)
    with open_archive(path, password=entry.passwords[0]) as reader:
        assert len(reader.members()) > 0


def test_data_encrypted_members_still_list_without_a_password(tmp_path: Path) -> None:
    """F9 (guardrail): the other half — data encryption stays lazy, per `#225`."""
    entry = _entry("encrypted")
    key = next((k for k in ("zip", "7z") if k in entry.formats), None)
    if key is None:
        pytest.skip("no data-encrypted corpus entry in a measurable format")
    availability = format_availability(FORMAT_KEYS[key])
    if availability.support is FormatSupport.NONE:
        pytest.skip(f"{key} not readable here: {availability.missing}")
    path = corpus_archive_path(entry, key, tmp_path)

    with open_archive(path) as reader:  # no password at all
        assert len(reader.members()) > 0


# ---------------------------------------------------------------------------
# F10 — the bidi/RTL warning is ambient only
# ---------------------------------------------------------------------------


def test_bidi_name_warning_has_no_diagnostic_code() -> None:
    """F10 (pin): the RTL/bidi warning is a bare `logger.warning`, not queryable data.

    `VISION.md`: "anything the library can only *warn* about should ideally also be
    queryable as data — a logging warning most applications never see is a surprise
    deferred, not avoided." Every other advisory in the library has a `DiagnosticCode`;
    this one does not, which makes it the single ambient-only advisory.
    """
    from archivey.diagnostics import DiagnosticCode

    codes = {c.value for c in DiagnosticCode}
    assert not any("bidi" in c or "bidirectional" in c or "rtl" in c for c in codes)
    # ... while name *normalization*, its neighbour in the same helper, does have one.
    assert "member_name_normalized" in codes


# ---------------------------------------------------------------------------
# F19 — the rewind diagnostic is silent for a degenerate seek index
# ---------------------------------------------------------------------------


def _single_block_xz(tmp_path: Path) -> Path:
    """A one-block .xz over incompressible data — what `lzma.compress` produces.

    `xz` without threading writes a single block too, so this is the common shape,
    not a contrived one.
    """
    import lzma
    import random

    path = tmp_path / "one_block.xz"
    path.write_bytes(lzma.compress(random.Random(7).randbytes(1_000_000)))
    return path


def test_single_block_xz_rewind_is_silent(tmp_path: Path) -> None:
    """F19 (pin): a full backward seek on a single-block xz emits no diagnostic.

    The predicate for `STREAM_REWIND_REDECOMPRESSES` is codec identity, decided once
    at open (`codecs.py` `rewind_warning`), and XZ returns `None` unconditionally
    because the format *can* carry a block index. A single-block file's index is
    degenerate — one seek point at the origin — so the seek re-decodes from byte 0
    exactly like an index-less codec, and says nothing.

    The consequence that matters is not the missing message: a `DiagnosticPolicy` set
    to `RAISE` to guard against quadratic seeks does not fire here either.
    """
    path = _single_block_xz(tmp_path)
    with open_stream(path, seekable=True) as stream:
        payload_len = len(stream.read())
        stream.seek(10)  # full rewind: nothing before it but the origin
        stream.read(4)
        assert payload_len > 0
        assert dict(stream.diagnostics.counts) == {}


@pytest.mark.xfail(
    strict=True,
    reason="F19: a degenerate index re-decodes from the start like no index at all; "
    "the predicate should be the seek's re-decode distance, not the codec's name",
)
def test_full_rewind_emits_regardless_of_codec(tmp_path: Path) -> None:
    """F19 (red half): the same work should produce the same signal.

    A backward seek that re-decodes the whole stream is the event the diagnostic
    exists for. Whether the codec *could* have carried a useful index is irrelevant
    when this particular file does not.
    """
    from archivey.diagnostics import DiagnosticCode

    path = _single_block_xz(tmp_path)
    with open_stream(path, seekable=True) as stream:
        stream.read()
        stream.seek(10)
        stream.read(4)
        assert DiagnosticCode.STREAM_REWIND_REDECOMPRESSES.value in dict(
            stream.diagnostics.counts
        )


# ---------------------------------------------------------------------------
# F20 — a zero-filled file with a .tar extension opens as an empty archive
# ---------------------------------------------------------------------------


def test_content_detection_refuses_a_zero_filled_file(tmp_path: Path) -> None:
    """F20 (guardrail): magic-byte detection correctly declines to call zeros a TAR.

    This is the layer that behaves well, and it is pinned so a future detection change
    cannot quietly start accepting the shape the other two layers already accept.
    """
    from archivey import FormatDetectionError, detect_format

    path = tmp_path / "zeros.bin"
    path.write_bytes(b"\x00" * 32768)
    with pytest.raises(FormatDetectionError):
        detect_format(path)


@pytest.mark.parametrize("strict_eof", [False, True])
def test_zero_filled_dot_tar_opens_empty_via_extension(
    strict_eof: bool, tmp_path: Path
) -> None:
    """F20 (pin): the *extension* path accepts what content detection refuses.

    32 KiB of zeros named ``z.tar`` opens as TAR with zero members, no error and no
    diagnostic — and ``strict_archive_eof=True`` does not change that, because the two
    null trailer blocks really are present. The knob asserts "the trailer is complete",
    not "the archive ends here".

    This is the realistic form of the wrong-format problem: a zero-truncated file with a
    plausible extension is exactly the shape `VISION.md`'s founding corpus is full of.
    The already-decided fix for an explicit ``format=`` does not reach this path, because
    there is no explicit format to disagree with.
    """
    path = tmp_path / "z.tar"
    path.write_bytes(b"\x00" * 32768)
    config = ArchiveyConfig(strict_archive_eof=strict_eof)
    with open_archive(path, config=config) as reader:
        assert reader.format == ArchiveFormat.TAR
        assert reader.members() == []
        assert dict(reader.diagnostics.counts) == {}


@pytest.mark.parametrize("strict_eof", [False, True])
def test_strict_archive_eof_ignores_trailing_junk(
    strict_eof: bool, tmp_path: Path
) -> None:
    """F20 (pin): `strict_archive_eof` never looks past the second trailer block.

    A valid one-member TAR with 4 KiB of arbitrary junk appended reads as one member with
    no diagnostic, *including* under strict. Pinned because the knob is documented as
    what you set for a "provably complete listing", and this is the gap between that
    promise and what it checks.
    """
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo("a.txt")
        info.size = 3
        tar.addfile(info, io.BytesIO(b"abc"))

    path = tmp_path / "with_junk.tar"
    path.write_bytes(buf.getvalue() + b"JUNK" * 1024)
    config = ArchiveyConfig(strict_archive_eof=strict_eof)
    with open_archive(path, format=ArchiveFormat.TAR, config=config) as reader:
        assert [m.name for m in reader.members()] == ["a.txt"]
        assert dict(reader.diagnostics.counts) == {}


def test_legitimately_empty_tar_stays_valid(tmp_path: Path) -> None:
    """F20 (guardrail): an empty TAR is legal, and any "zero members raises" rule breaks it.

    `tar cf empty.tar --files-from /dev/null` is a real thing and `tarfile` accepts it.
    Pinned so the cost of the strictest option in O8 stays visible.
    """
    import io
    import tarfile

    buf = io.BytesIO()
    tarfile.open(fileobj=buf, mode="w").close()
    path = tmp_path / "empty.tar"
    path.write_bytes(buf.getvalue())
    with open_archive(path, format=ArchiveFormat.TAR) as reader:
        assert reader.members() == []
