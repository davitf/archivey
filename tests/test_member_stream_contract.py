"""Cross-format contract for an opened member stream (``reader.open(member)``).

The per-format test files each check their backend's metadata mapping; this suite instead
asserts the *uniform* behaviour every backend's member stream must share, exercised against
a small **real** archive of each implemented format. It is the v2 stand-in for the kind of
all-formats consistency the frozen DEV oracle used to provide, scoped to the member-read
contract.

The payload is deliberately small (well under a 2 KiB ISO sector and not block-aligned), so
a backend that over-reads — e.g. a ``readinto`` that walks past the logical end into a
container's padding — is caught rather than masked by alignment.
"""

from __future__ import annotations

import gzip
import io
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest

from archivey import list_known_formats, open_archive
from archivey.types import (
    ArchiveFormat,
    CompressionAlgorithm,
    ContainerFormat,
    MemberType,
    StreamFormat,
)
from tests.conftest import requires
from tests.sample_archives import (
    CORPUS,
    FORMAT_KEYS,
    CorpusEntry,
    corpus_archive_path,
    skip_unless_runnable,
)

CONTENT = b"The quick brown fox jumps over.\n"  # 32 bytes; < one ISO sector
MEMBER = "data.txt"

# Seek tests declare SEEKABLE; other contract tests use the default forward-only streams.

# A builder makes a small archive holding one ``MEMBER`` with ``CONTENT`` and returns the
# (source, member-name) pair to open. Source may be a path or directory.
Builder = Callable[[Path], tuple[Path, str]]


def _directory(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "dir"
    root.mkdir()
    (root / MEMBER).write_bytes(CONTENT)
    return root, MEMBER


def _zip_deflated(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "a.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(MEMBER, CONTENT)
    return path, MEMBER


def _zip_stored(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "stored.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        z.writestr(MEMBER, CONTENT)
    return path, MEMBER


def _tar(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "a.tar"
    with tarfile.open(path, "w") as t:
        info = tarfile.TarInfo(MEMBER)
        info.size = len(CONTENT)
        t.addfile(info, io.BytesIO(CONTENT))
    return path, MEMBER


def _tar_gz(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "a.tar.gz"
    with tarfile.open(path, "w:gz") as t:
        info = tarfile.TarInfo(MEMBER)
        info.size = len(CONTENT)
        t.addfile(info, io.BytesIO(CONTENT))
    return path, MEMBER


def _gzip(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "data.txt.gz"
    with gzip.open(path, "wb") as f:
        f.write(CONTENT)
    return path, MEMBER  # single-file member name inferred from the source filename


def _sevenzip(tmp_path: Path) -> tuple[Path, str]:
    import py7zr

    path = tmp_path / "a.7z"
    with py7zr.SevenZipFile(path, "w") as z:
        z.writestr(CONTENT, MEMBER)
    return path, MEMBER


def _iso(tmp_path: Path) -> tuple[Path, str]:
    import pycdlib

    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, rock_ridge="1.09")
    iso.add_fp(io.BytesIO(CONTENT), len(CONTENT), "/DATA.TXT;1", rr_name=MEMBER)
    path = tmp_path / "a.iso"
    iso.write(str(path))
    iso.close()
    return path, MEMBER


@pytest.fixture(
    params=[
        pytest.param(_directory, id="directory"),
        pytest.param(_zip_deflated, id="zip_deflated"),
        pytest.param(_zip_stored, id="zip_stored"),
        pytest.param(_tar, id="tar"),
        pytest.param(_tar_gz, id="tar_gz"),
        pytest.param(_gzip, id="gzip"),
        pytest.param(_sevenzip, id="sevenzip", marks=requires("py7zr")),
        pytest.param(_iso, id="iso", marks=requires("pycdlib")),
    ]
)
def member(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[Path, str]:
    builder: Builder = request.param
    return builder(tmp_path)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def test_read_more_than_available_returns_all(member: tuple[Path, str]) -> None:
    source, name = member
    with open_archive(source) as ar, ar.open(name) as f:
        # A read size far beyond the member length returns exactly the member's bytes.
        assert f.read(10_000) == CONTENT


def test_read_at_eof_returns_empty(member: tuple[Path, str]) -> None:
    source, name = member
    with open_archive(source) as ar, ar.open(name) as f:
        assert f.read() == CONTENT
        assert f.read() == b""
        assert f.read(64) == b""


def test_readinto_oversized_buffer_truncates_at_eof(member: tuple[Path, str]) -> None:
    # readinto into a buffer larger than the remaining data must return the actual byte
    # count (not the buffer size) and fill only those bytes — never reading into a
    # container's sector/block padding past the member's logical end.
    source, name = member
    with open_archive(source) as ar, ar.open(name) as f:
        buf = bytearray(10_000)
        n = f.readinto(buf)
        assert n == len(CONTENT)
        assert bytes(buf[:n]) == CONTENT
        # A second readinto at EOF fills nothing.
        assert f.readinto(bytearray(64)) == 0


def test_piecewise_read_then_eof(member: tuple[Path, str]) -> None:
    source, name = member
    with open_archive(source) as ar, ar.open(name) as f:
        first = f.read(5)
        assert first == CONTENT[:5]
        assert first + f.read() == CONTENT
        assert f.read(1) == b""


# ---------------------------------------------------------------------------
# Seekability capability: off by default, on (and working) with MemberStreams.SEEKABLE
# ---------------------------------------------------------------------------


def test_default_open_is_not_seekable(member: tuple[Path, str]) -> None:
    # Without the SEEKABLE capability every backend hands out a forward-only stream.
    source, name = member
    with open_archive(source) as ar, ar.open(name) as f:
        assert f.seekable() is False
        with pytest.raises((io.UnsupportedOperation, ValueError)):
            f.seek(0)


def test_seekable_flag_enables_forward_and_backward_seek(
    member: tuple[Path, str],
) -> None:
    # With the capability the stream reports seekable and seeking actually works both
    # ways (backward seeks may re-decode from the start; the caller accepted that cost).
    source, name = member
    with open_archive(source, seekable_members=True) as ar, ar.open(name) as f:
        assert f.seekable() is True
        assert f.read() == CONTENT
        f.seek(0)  # backward to the start
        assert f.read() == CONTENT
        f.seek(10)  # forward into the middle
        assert f.read() == CONTENT[10:]
        assert f.tell() == len(CONTENT)


def test_seek_past_end_then_read_returns_empty(member: tuple[Path, str]) -> None:
    source, name = member
    with open_archive(source, seekable_members=True) as ar, ar.open(name) as f:
        if not f.seekable():
            pytest.skip("member stream is not seekable")
        f.seek(len(CONTENT) + 100)
        assert f.read() == b""
        assert f.read(64) == b""


def test_seek_to_start_rereads(member: tuple[Path, str]) -> None:
    source, name = member
    with open_archive(source, seekable_members=True) as ar, ar.open(name) as f:
        if not f.seekable():
            pytest.skip("member stream is not seekable")
        assert f.read(5) == CONTENT[:5]
        f.seek(0)
        assert f.read() == CONTENT


# ---------------------------------------------------------------------------
# Corpus enrollment: MemberStreams.SEEKABLE is a guarantee, not a request mask
# ---------------------------------------------------------------------------
#
# The small `member` fixture above never included RAR, AES ZIP, or encrypted 7z,
# which is how the original holes survived. These rows come from CORPUS.

_BY_ID: dict[str, CorpusEntry] = {e.id: e for e in CORPUS}

_DECRYPT_WRAPPER_REASON = "decrypting stream wrapper does not seek"
_DECRYPT_WRAPPER_XFAIL = pytest.mark.xfail(strict=True, reason=_DECRYPT_WRAPPER_REASON)


@dataclass(frozen=True)
class _SeekSpec:
    """One corpus archive enrolled in the seekability matrix.

    ``packing``:
      stored — fail if the member is not only STORED (RAR urandom trap).
      compressed — fail if the member is only STORED (or packed size >= size).
      None — no packing assertion.
    """

    entry_id: str
    key: str
    packing: str | None = None


_SEEK_ARCHIVES: tuple[_SeekSpec, ...] = (
    # ZIP store / deflate / bzip2 / lzma
    _SeekSpec("zip-compression-methods", "zip"),
    # ZipCrypto (stdlib decryptor already seeks)
    _SeekSpec("encrypted", "zip"),
    _SeekSpec("encrypted-mixed", "zip"),
    # WinZip AES (decrypt wrapper — xfail on encrypted members)
    _SeekSpec("encrypted", "zip-aes"),
    _SeekSpec("encrypted-mixed", "zip-aes"),
    # 7z solid LZMA2 (`basic`), stored COPY, encrypted (wrapper xfail)
    _SeekSpec("basic", "7z"),
    _SeekSpec("sevenzip-stored", "7z", packing="stored"),
    _SeekSpec("encrypted", "7z"),
    _SeekSpec("encrypted-header", "7z"),
    # RAR stored (existing corpus is urandom/`basic` tiny files — both store)
    _SeekSpec("basic", "rar", packing="stored"),
    _SeekSpec("large", "rar", packing="stored"),
    # RAR genuinely compressed (new corpus row; not the stored slice path)
    _SeekSpec("compressed", "rar", packing="compressed"),
    # RAR encrypted goes through unrar, not an archivey decrypt wrapper
    _SeekSpec("encrypted", "rar"),
    # TAR + compressed TAR + the original contract formats the hand list covered
    _SeekSpec("basic", "tar"),
    _SeekSpec("basic", "tar.gz"),
    _SeekSpec("basic", "tar.bz2"),
    _SeekSpec("basic", "tar.xz"),
    _SeekSpec("basic", "tar.zst"),
    _SeekSpec("basic", "tar.lz4"),
    _SeekSpec("basic", "tar.lz"),
    _SeekSpec("basic", "tar.zz"),
    _SeekSpec("basic", "tar.br"),
    _SeekSpec("basic", "dir"),
    _SeekSpec("basic", "iso"),
    _SeekSpec("basic", "iso-joliet"),
    _SeekSpec("single-file", "gz"),
    _SeekSpec("single-file", "bz2"),
    _SeekSpec("single-file", "xz"),
    _SeekSpec("single-file", "zst"),
    _SeekSpec("single-file", "lz4"),
    _SeekSpec("single-file", "lz"),
    _SeekSpec("single-file", "zz"),
    _SeekSpec("single-file", "br"),
    _SeekSpec("single-file-meta", "gz-meta"),
)


def _is_only_stored(member) -> bool:
    chain = member.compression
    return bool(chain) and all(c.algo is CompressionAlgorithm.STORED for c in chain)


def _decrypt_wrapper_member(key: str, corpus_member_password: str | None) -> bool:
    """ZIP AES and 7z encrypted members share the non-seeking decrypt wrapper."""
    if corpus_member_password is None:
        return False
    return key in ("zip-aes", "7z")


def _seek_member_params(*, requested: bool) -> list:
    params = []
    for spec in _SEEK_ARCHIVES:
        entry = _BY_ID[spec.entry_id]
        if spec.key not in entry.formats:
            raise AssertionError(
                f"seek matrix names {spec.entry_id}/{spec.key} but that entry "
                f"is built as {entry.formats}"
            )
        for member in entry.members:
            if member.type is not MemberType.FILE:
                continue
            marks = []
            if requested and _decrypt_wrapper_member(spec.key, member.password):
                marks.append(_DECRYPT_WRAPPER_XFAIL)
            params.append(
                pytest.param(
                    spec,
                    member.name,
                    marks=marks,
                    id=f"{spec.entry_id}/{spec.key}/{member.name}",
                )
            )
    return params


def _open_enrolled(spec: _SeekSpec, tmp_path: Path, *, seekable_members: bool):
    entry = _BY_ID[spec.entry_id]
    skip_unless_runnable(entry, spec.key)
    source = corpus_archive_path(entry, spec.key, tmp_path)
    return open_archive(
        source,
        seekable_members=seekable_members,
        password=list(entry.passwords) or None,
    )


def _resolve_file_member(ar, name: str):
    files = [m for m in ar.members() if m.is_file]
    matches = [m for m in files if m.name == name]
    if len(matches) == 1:
        return matches[0]
    # Single-file compressors infer the member name from the archive filename.
    if len(files) == 1:
        return files[0]
    pytest.fail(
        f"could not resolve file member {name!r}; have {[m.name for m in files]}"
    )


def _assert_packing(spec: _SeekSpec, member, *, name: str) -> None:
    if spec.packing is None:
        return
    algos = "+".join(c.algo.value for c in member.compression) or "(none)"
    if spec.packing == "stored":
        assert _is_only_stored(member), (
            f"{spec.entry_id}/{spec.key} {name!r} must be STORED for this row "
            f"(codec={algos}, packed={member.compressed_size}, size={member.size})"
        )
        return
    if spec.packing != "compressed":
        raise AssertionError(f"unknown packing {spec.packing!r}")
    packed_not_smaller = (
        member.compressed_size is not None
        and member.size is not None
        and member.size > 0
        and member.compressed_size >= member.size
    )
    if _is_only_stored(member) or packed_not_smaller:
        pytest.fail(
            f"{spec.entry_id}/{spec.key} {name!r} was stored; the compressed-RAR "
            f"row must not silently take the direct-slice path "
            f"(codec={algos}, packed={member.compressed_size}, size={member.size})"
        )


@pytest.mark.parametrize(("spec", "member_name"), _seek_member_params(requested=False))
def test_corpus_default_member_stream_is_not_seekable(
    spec: _SeekSpec, member_name: str, tmp_path: Path
) -> None:
    with _open_enrolled(spec, tmp_path, seekable_members=False) as ar:
        member = _resolve_file_member(ar, member_name)
        _assert_packing(spec, member, name=member_name)
        with ar.open(member) as f:
            assert f.seekable() is False
            with pytest.raises((io.UnsupportedOperation, ValueError)):
                f.seek(0)


@pytest.mark.parametrize(("spec", "member_name"), _seek_member_params(requested=True))
def test_corpus_seekable_members_seek_and_reread(
    spec: _SeekSpec, member_name: str, tmp_path: Path
) -> None:
    with _open_enrolled(spec, tmp_path, seekable_members=True) as ar:
        member = _resolve_file_member(ar, member_name)
        _assert_packing(spec, member, name=member_name)
        with ar.open(member) as f:
            assert f.seekable() is True
            prefix = f.read(16)
            f.seek(0)
            assert f.read(16) == prefix


# Formats / mechanisms this contract should cover but cannot construct here.
# Skip (not pass): a missing row would look like the format was certified.
_UNTESTED: tuple[tuple[str, str], ...] = (
    ("unix-compress", "no corpus builder for unix compress (.Z)"),
    ("lzma-alone", "no corpus builder for LZMA Alone"),
    ("zip-deflate64", "no corpus builder for ZIP Deflate64"),
    ("zip-ppmd", "no corpus builder for ZIP PPMd"),
    ("zip-zstd", "no corpus builder for ZIP Zstd"),
    ("7z-ppmd", "no corpus builder for 7z PPMd"),
    ("7z-deflate64", "no corpus builder for 7z Deflate64"),
    ("7z-bcj2", "7z BCJ2 is detected and rejected; cannot exercise seek"),
    (
        "7z-compressed-nonsolid",
        "py7zr multi-file writes are solid LZMA2; sevenzip-stored is a single COPY file (non-solid stored)",
    ),
    (
        "rar4",
        "corpus RAR fixtures are RAR5; RAR4 lives in targeted reader fixtures",
    ),
    ("multi-volume", "no corpus builder for split/multi-volume archives"),
    ("tar-lzma-alone", "no corpus builder for tar + LZMA Alone"),
    ("tar-unix-compress", "no corpus builder for tar + unix compress (.tar.Z)"),
)

_UNTESTED_FORMATS = {
    ArchiveFormat.Z: "unix-compress",
    ArchiveFormat.LZMA_ALONE: "lzma-alone",
    ArchiveFormat(ContainerFormat.TAR, StreamFormat.LZMA_ALONE): "tar-lzma-alone",
    ArchiveFormat(ContainerFormat.TAR, StreamFormat.UNIX_COMPRESS): "tar-unix-compress",
}


@pytest.mark.parametrize(
    ("label", "reason"),
    [pytest.param(label, reason, id=label) for label, reason in _UNTESTED],
)
def test_seekability_untested_mechanism(label: str, reason: str) -> None:
    pytest.skip(f"untested: {reason}")


def test_seekability_enrollment_covers_corpus_format_keys() -> None:
    enrolled = {spec.key for spec in _SEEK_ARCHIVES}
    missing = set(FORMAT_KEYS) - enrolled
    assert not missing, f"FORMAT_KEYS not enrolled in seek matrix: {sorted(missing)}"


def test_seekability_enrollment_covers_known_formats() -> None:
    enrolled = {FORMAT_KEYS[spec.key] for spec in _SEEK_ARCHIVES}
    untested = set(_UNTESTED_FORMATS)
    missing = set(list_known_formats()) - enrolled - untested - {ArchiveFormat.UNKNOWN}
    assert not missing, (
        f"known formats absent from seek matrix: {sorted(missing, key=str)}"
    )


# ---------------------------------------------------------------------------
# Uniform handle type
# ---------------------------------------------------------------------------


def test_member_streams_are_archive_streams(member: tuple[Path, str]) -> None:
    # Every member handle the library hands out — from open() and from
    # stream_members() alike — is an ArchiveStream, regardless of backend (even the
    # directory backend, which has nothing to decompress): uniform error
    # translation/stamping, the `size` advertisement, and one place to grow shared
    # handle features.
    from archivey.internal.streams.archive_stream import ArchiveStream

    source, name = member
    with open_archive(source) as ar:
        with ar.open(name) as f:
            assert isinstance(f, ArchiveStream)
        for m, stream in ar.stream_members():
            if m.is_file:
                assert isinstance(stream, ArchiveStream)
            if stream is not None:
                stream.close()


def test_stream_members_do_not_nest_archive_streams(tmp_path: Path) -> None:
    """``stream_members`` must hand out a single ArchiveStream, not wrap-over-wrap.

    Regression for the lazy-open double wrap (``_lazy_member_stream`` over
    ``_wrap_member_stream``): after first read, the public handle's inner must not
    itself be an ``ArchiveStream``.
    """
    from archivey.internal.streams.archive_stream import ArchiveStream

    path = tmp_path / "a.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("a.txt", b"payload")
    with open_archive(path) as ar:
        for member, stream in ar.stream_members():
            if not member.is_file or stream is None:
                continue
            assert isinstance(stream, ArchiveStream)
            assert stream.read() == b"payload"
            inner = stream._inner
            assert inner is not None
            assert not isinstance(inner, ArchiveStream)
            break
        else:
            pytest.fail("expected a file member")


def test_zip_member_fuses_verify_no_verifying_stream_layer(tmp_path: Path) -> None:
    """STORED ZIP: public ArchiveStream verifies directly over SlicingStream.

    After verify-fusion, the codec ArchiveStream collapses through and there is
    no VerifyingStream in the chain (review/stream-layering collapse design).
    """
    from archivey.internal.streams.archive_stream import ArchiveStream
    from archivey.internal.streams.streamtools.slice import SlicingStream
    from archivey.internal.streams.verify import VerifyingStream

    path = tmp_path / "stored.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("a.bin", b"hello-verify-fuse")
    with open_archive(path) as ar, ar.open("a.bin") as f:
        assert isinstance(f, ArchiveStream)
        assert f._verifier is not None
        assert not isinstance(f._inner, ArchiveStream)
        assert not isinstance(f._inner, VerifyingStream)
        assert isinstance(f._inner, SlicingStream)
        assert f.read() == b"hello-verify-fuse"


def test_member_stream_advertises_size(member: tuple[Path, str]) -> None:
    # The fsspec-style `size` attribute carries the decompressed length when the
    # archive metadata knows it (feeds nested-archive sizing and the bomb tracker).
    source, name = member
    with open_archive(source) as ar, ar.open(name) as f:
        size = getattr(f, "size", None)
        assert size is None or size == len(CONTENT)
