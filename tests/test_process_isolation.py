"""``ArchiveyConfig.process_isolation``: every value decodes the same bytes.

Each fixture is read under the default (``KNOWN_CRASHES``), ``NATIVE_DECODERS`` (every
native decoder in a child process) and ``NONE`` (everything in this process), and the
member contents must match byte for byte. The corpus covers the stream formats and the
containers; archives written by the ``7z`` binary add the codecs the corpus builders
cannot write (Deflate64, PPMd, BCJ filters).
"""

from __future__ import annotations

import gzip
import io
import random
import warnings
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from archivey import MemberType, open_archive
from archivey.config import (
    AcceleratorMode,
    ArchiveyConfig,
    ProcessIsolation,
)
from archivey.exceptions import ArchiveyUsageError
from archivey.internal.config import StreamConfig
from archivey.internal.streams.codecs.bzip2_codec import _bzip2_wanted
from archivey.internal.streams.codecs.decoder_child import POOL
from archivey.internal.streams.codecs.rapidgzip_select import _rapidgzip_wanted
from tests.conftest import requires_binary, run_7z
from tests.sample_archives import CORPUS, corpus_archive_path, skip_unless_runnable

_ENTRIES = {"basic", "zip-compression-methods", "large", "compressed", "single-file"}
_PARAMS = [
    pytest.param(entry, key, id=f"{entry.id}-{key}")
    for entry in CORPUS
    if entry.id in _ENTRIES
    for key in entry.formats
    if key != "dir"
]
_PAYLOAD = (b"process-isolation payload\n" * 4000) + bytes(range(256)) * 64


@pytest.fixture(autouse=True)
def _empty_pool():
    POOL.close_idle()
    yield
    POOL.close_idle()


def _contents(path: Path, isolation: ProcessIsolation) -> dict[str, bytes]:
    config = ArchiveyConfig(process_isolation=isolation)
    with open_archive(path, config=config) as ar:
        return {m.name: ar.read(m) for m in ar.members() if m.type is MemberType.FILE}


def _assert_same_under_every_value(path: Path) -> None:
    expected = _contents(path, ProcessIsolation.KNOWN_CRASHES)
    assert expected
    for isolation in (ProcessIsolation.NATIVE_DECODERS, ProcessIsolation.NONE):
        assert _contents(path, isolation) == expected, isolation


@pytest.mark.parametrize(("entry", "key"), _PARAMS)
def test_corpus_reads_the_same_under_every_value(entry, key, tmp_path: Path) -> None:
    skip_unless_runnable(entry, key)
    _assert_same_under_every_value(corpus_archive_path(entry, key, tmp_path))


def _7z_write(tmp_path: Path, archive_type: str, switches: list[str]) -> Path:
    src = tmp_path / "payload.bin"
    src.write_bytes(_PAYLOAD)
    (tmp_path / "second.txt").write_bytes(b"second member\n" * 500)
    archive = tmp_path / f"out.{archive_type}"
    result = run_7z(
        ["a", f"-t{archive_type}", *switches, str(archive), src.name]
        + ["second.txt", "-y"],
        cwd=tmp_path,
        text=True,
    )
    if result.returncode != 0 or not archive.is_file():
        pytest.skip(f"7z cannot write {archive_type} {switches}: {result.stderr}")
    return archive


@requires_binary("7z")
@pytest.mark.parametrize(
    ("archive_type", "switches"),
    [
        ("zip", ["-mm=Deflate64"]),
        ("zip", ["-mm=PPMd"]),
        ("zip", ["-mm=BZip2"]),
        ("zip", ["-mm=LZMA"]),
        ("7z", ["-m0=PPMd"]),
        ("7z", ["-m0=Deflate64"]),
        ("7z", ["-m0=BZip2"]),
        ("7z", ["-m0=LZMA"]),
        ("7z", ["-m0=BCJ", "-m1=LZMA2"]),
        ("7z", ["-m0=Delta:4", "-m1=LZMA2", "-ms=off"]),
    ],
    ids=lambda v: v if isinstance(v, str) else "_".join(v),
)
def test_7z_written_archives_read_the_same_under_every_value(
    tmp_path: Path, archive_type: str, switches: list[str]
) -> None:
    pytest.importorskip("inflate64") if "Deflate64" in str(switches) else None
    pytest.importorskip("pyppmd") if "PPMd" in str(switches) else None
    _assert_same_under_every_value(_7z_write(tmp_path, archive_type, switches))


def test_none_with_rapidgzip_on_is_refused() -> None:
    with pytest.raises(ArchiveyUsageError, match="rapidgzip runs only in a child"):
        ArchiveyConfig(
            process_isolation=ProcessIsolation.NONE,
            use_rapidgzip=AcceleratorMode.ON,
        )


@pytest.mark.parametrize("which", ["gzip", "bzip2"])
def test_none_never_picks_rapidgzip_under_auto(which: str) -> None:
    """A stream past the ``AUTO`` size gate, with its size known, would use rapidgzip
    under the default; under ``NONE`` it uses the standard library, and no warning
    says so (``NONE`` is the caller's choice, not a fallback)."""
    big = 1 << 40
    base = StreamConfig(
        seekable=True,
        compressed_input_size=big,
        expected_decompressed_size=big,
    )
    none = replace(base, process_isolation=ProcessIsolation.NONE)
    wanted = _rapidgzip_wanted if which == "gzip" else _bzip2_wanted
    assert wanted(base, available=True)
    assert not wanted(none, available=True)


def test_gzip_under_none_reads_with_no_warning(tmp_path: Path) -> None:
    path = tmp_path / "data.bin.gz"
    path.write_bytes(gzip.compress(_PAYLOAD))
    config = ArchiveyConfig(process_isolation=ProcessIsolation.NONE)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with open_archive(path, config=config) as ar:
            (member,) = ar.members()
            assert ar.read(member) == _PAYLOAD


@requires_binary("7z")
def test_a_ppmd_member_pyppmd_cannot_take_in_pieces_reads_under_every_value(
    tmp_path: Path,
) -> None:
    """Fed to pyppmd in pieces, this valid member stops at a false end about 28 KiB
    short (pyppmd 1.3.1). Under the limit every setting hands it over whole, in the
    child or in this process, and it reads in full."""
    pytest.importorskip("pyppmd")
    block = random.Random(8).randbytes(1 << 20)
    (tmp_path / "repeats.bin").write_bytes(block * 8)
    archive = tmp_path / "repeats.7z"
    result = run_7z(
        ["a", "-t7z", "-m0=PPMd", str(archive), "repeats.bin"], cwd=tmp_path
    )
    if result.returncode != 0:
        pytest.skip("7z cannot write PPMd")
    for isolation in ProcessIsolation:
        assert _contents(archive, isolation) == {"repeats.bin": block * 8}, isolation


_SINGLE_FILE = [
    pytest.param(entry, key, id=key)
    for entry in CORPUS
    if entry.id == "single-file"
    for key in entry.formats
]


@pytest.mark.parametrize(("entry", "key"), _SINGLE_FILE)
def test_closing_a_member_stream_gives_its_child_back_at_once(
    entry, key, tmp_path: Path
) -> None:
    """``close`` hands the child back while the stream object is still referenced,
    for every stream codec: a decoder that keeps its native object where
    ``release_decoder_children`` does not look fails here, rather than waiting for
    the garbage collector without a sound."""
    skip_unless_runnable(entry, key)
    path = corpus_archive_path(entry, key, tmp_path)
    config = ArchiveyConfig(process_isolation=ProcessIsolation.NATIVE_DECODERS)
    with open_archive(path, config=config) as ar:
        (member,) = (m for m in ar.members() if m.type is MemberType.FILE)
        stream = ar.open(member)
        stream.read(10)
        POOL.close_idle()
        stream.close()
        assert POOL.idle_count() == 1, key
        del stream


def _multi_unit_sources() -> list[tuple[str, bytes]]:
    import bz2
    import lzma

    from archivey.internal.streams.codecs import deps

    parts = [bytes([65 + i]) * 3000 for i in range(_UNITS)]
    sources = [
        ("gz", b"".join(gzip.compress(p) for p in parts)),
        ("bz2", b"".join(bz2.compress(p) for p in parts)),
        ("xz", b"".join(lzma.compress(p) for p in parts)),
        # ``.lzma`` has no magic, so its own probe reads it first, through a wrapper
        # that clamps each stream's dictionary and holds the proxy one level down.
        (
            "lzma",
            b"".join(lzma.compress(p, format=lzma.FORMAT_ALONE) for p in parts),
        ),
    ]
    if deps.zstd is not None:
        sources.append(("zst", b"".join(deps.zstd.compress(p) for p in parts)))
    if deps.lz4_frame is not None:
        sources.append(("lz4", b"".join(deps.lz4_frame.compress(p) for p in parts)))
    return sources


_UNITS = 12


@pytest.fixture
def kept_decoder_children(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[], None]]:
    """Keep every decoder child alive; the call checks that at most one is leased.

    PyPy frees a dropped decoder only at a later collection. Keeping every decoder
    alive stands in for that: a decoder that relies on being freed holds its child,
    and each later member, stream, frame or block starts a process of its own.
    """
    from archivey.internal.streams.codecs import decoder_child

    kept: list[decoder_child.DecoderChild] = []
    built = decoder_child.DecoderChild.__init__

    def keep(self: decoder_child.DecoderChild, *args: object, **kwargs: object) -> None:
        built(self, *args, **kwargs)  # type: ignore[arg-type]  # forwards the real args
        kept.append(self)

    def check() -> None:
        leased = [c for c in kept if c._lease.process is not None]
        assert len(leased) <= 1, f"{len(leased)} decoders still hold a child"

    monkeypatch.setattr(decoder_child.DecoderChild, "__init__", keep)
    try:
        yield check
    finally:
        for child in kept:
            child.close()


@pytest.mark.parametrize(
    ("suffix", "data"),
    [pytest.param(s, d, id=s) for s, d in _multi_unit_sources()],
)
def test_a_replaced_decoder_gives_its_child_back_without_the_collector(
    tmp_path: Path,
    kept_decoder_children: Callable[[], None],
    suffix: str,
    data: bytes,
) -> None:
    path = tmp_path / f"units.{suffix}"
    path.write_bytes(data)
    config = ArchiveyConfig(process_isolation=ProcessIsolation.NATIVE_DECODERS)
    with open_archive(path, config=config) as ar:
        kept_decoder_children()
        for _, stream in ar.stream_members():
            assert stream is not None
            assert len(stream.read()) == 3000 * _UNITS
            kept_decoder_children()


def test_a_zisofs_block_decoder_gives_its_child_back_without_the_collector(
    kept_decoder_children: Callable[[], None],
) -> None:
    # Each zisofs block gets a decoder of its own, used once and dropped. Three other
    # sites drop a local decoder the same way and are not reached here: the zlib
    # Adler-32 confirmation, the xz probe head and the deflate resume check. Each
    # builds one decoder per call, so a leak there holds one child, not one per unit.
    pytest.importorskip("pycdlib")
    from tests.test_iso import _ZISOFS_PLAIN, _zisofs_image

    config = ArchiveyConfig(process_isolation=ProcessIsolation.NATIVE_DECODERS)
    with open_archive(io.BytesIO(_zisofs_image(_ZISOFS_PLAIN)), config=config) as ar:
        assert ar.read("zzz") == _ZISOFS_PLAIN
        kept_decoder_children()
