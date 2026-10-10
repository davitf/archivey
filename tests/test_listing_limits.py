"""Listing resource limits: materialization caps vs stream_members escape hatch."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from archivey import (
    ArchiveyConfig,
    ExtractionLimits,
    ListingLimits,
    ResourceLimitError,
    open_archive,
)
from archivey.internal.listing_limits import (
    ListingLimitTracker,
    member_metadata_bytes,
)
from archivey.types import ArchiveMember, MemberType


def _zip_with_members(names: list[str], *, comment: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name in names:
            z.writestr(name, b"x")
        if comment is not None:
            z.comment = comment
    return buf.getvalue()


def test_max_members_raises_on_members() -> None:
    data = _zip_with_members([f"f{i}.txt" for i in range(5)])
    cfg = ArchiveyConfig(listing_limits=ListingLimits(max_members=3))
    with open_archive(io.BytesIO(data), config=cfg) as reader:
        with pytest.raises(ResourceLimitError, match="max_members"):
            reader.members()
        assert reader._materialized is None
        with pytest.raises(ResourceLimitError, match="max_members"):
            reader.members_report()
        assert reader._materialized is None


def test_max_metadata_bytes_raises_on_long_names() -> None:
    # Each name is long enough that three of them exceed a tiny metadata budget.
    names = [f"{'n' * 200}_{i}.txt" for i in range(3)]
    data = _zip_with_members(names)
    cfg = ArchiveyConfig(listing_limits=ListingLimits(max_metadata_bytes=400))
    with open_archive(io.BytesIO(data), config=cfg) as reader:
        with pytest.raises(ResourceLimitError, match="max_metadata_bytes"):
            reader.members()


def test_huge_archive_comment_counts_toward_metadata() -> None:
    data = _zip_with_members(["a.txt"], comment=b"C" * 1000)
    cfg = ArchiveyConfig(listing_limits=ListingLimits(max_metadata_bytes=500))
    with open_archive(io.BytesIO(data), config=cfg) as reader:
        with pytest.raises(ResourceLimitError, match="max_metadata_bytes"):
            reader.members()


def test_defaults_allow_linux_scale_member_counts() -> None:
    # ~100k would be heavy for a unit test; assert the default numeric contract and that
    # a modest archive under defaults succeeds.
    assert ListingLimits().max_members == 1_048_576
    assert ListingLimits().max_metadata_bytes == 64 * 2**20
    data = _zip_with_members([f"f{i}.txt" for i in range(200)])
    with open_archive(io.BytesIO(data)) as reader:
        assert len(reader.members()) == 200


def test_unlimited_disables_guards() -> None:
    data = _zip_with_members([f"f{i}.txt" for i in range(20)])
    cfg = ArchiveyConfig(listing_limits=ListingLimits.UNLIMITED)
    with open_archive(io.BytesIO(data), config=cfg) as reader:
        assert len(reader.members()) == 20


def test_stream_members_unguarded_when_members_would_fail() -> None:
    data = _zip_with_members([f"f{i}.txt" for i in range(5)])
    cfg = ArchiveyConfig(listing_limits=ListingLimits(max_members=2))
    with open_archive(io.BytesIO(data), config=cfg) as reader:
        names = [m.name for m, _ in reader.stream_members()]
        assert names == [f"f{i}.txt" for i in range(5)]
        with pytest.raises(ResourceLimitError, match="max_members"):
            reader.members()


def test_matched_defaults_list_then_extract(tmp_path: Path) -> None:
    assert ListingLimits().max_members == ExtractionLimits().max_entries == 1_048_576
    src = tmp_path / "a.zip"
    src.write_bytes(_zip_with_members([f"f{i}.txt" for i in range(10)]))
    dest = tmp_path / "out"
    with open_archive(src) as reader:
        assert len(reader.members()) == 10
        reader.extract_all(dest)
    assert (dest / "f0.txt").exists()


def test_extract_all_runs_under_the_open_time_listing_limits(tmp_path: Path) -> None:
    # extract_all() takes no config=: the reader's open config governs the whole call,
    # so nothing per call can raise the listing ceiling set at open.
    src = tmp_path / "a.zip"
    src.write_bytes(_zip_with_members([f"f{i}.txt" for i in range(5)]))
    dest = tmp_path / "out"
    tight = ArchiveyConfig(listing_limits=ListingLimits(max_members=2))
    loose = ArchiveyConfig(listing_limits=ListingLimits(max_members=1000))
    with open_archive(src, config=tight) as reader:
        with pytest.raises(TypeError, match="config"):
            reader.extract_all(dest, config=loose)  # type: ignore[call-arg]
        with pytest.raises(ResourceLimitError, match="max_members"):
            reader.extract_all(dest)


def test_tar_extract_all_enforces_listing_limits(tmp_path: Path) -> None:
    """Scan-required backends must not bypass listing caps via unguarded stream_members."""
    import tarfile

    tar_path = tmp_path / "a.tar"
    with tarfile.open(tar_path, "w") as tf:
        for i in range(5):
            info = tarfile.TarInfo(name=f"f{i}.txt")
            payload = b"x"
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
    dest = tmp_path / "out"
    cfg = ArchiveyConfig(listing_limits=ListingLimits(max_members=2))
    with open_archive(tar_path, config=cfg) as reader:
        with pytest.raises(ResourceLimitError, match="max_members"):
            reader.extract_all(dest)


def _count_header_reads(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count the headers the TAR walk parses, one entry per ``next_entry`` call."""
    from archivey.internal.backends.tar_parser import TarWalker

    calls: list[int] = []
    original = TarWalker.next_entry

    def counting(self: TarWalker, budget: object) -> object:
        calls.append(1)
        return original(self, budget)  # type: ignore[arg-type]

    monkeypatch.setattr(TarWalker, "next_entry", counting)
    return calls


def test_tar_listing_stops_reading_headers_at_max_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap bounds how many headers the walk parses, not only what archivey keeps.

    A header bomb must not cost work in proportion to its size when ``max_members``
    refuses it early.
    """
    import tarfile

    tar_path = tmp_path / "many.tar"
    with tarfile.open(tar_path, "w", format=tarfile.USTAR_FORMAT) as tf:
        for i in range(200):
            tf.addfile(tarfile.TarInfo(name=f"f{i:03d}"))
    cfg = ArchiveyConfig(listing_limits=ListingLimits(max_members=5))
    header_reads = _count_header_reads(monkeypatch)
    with open_archive(tar_path, config=cfg) as reader:
        with pytest.raises(ResourceLimitError, match="max_members"):
            reader.members()
    assert len(header_reads) <= 6


def test_streaming_scan_members_enforces_listing_limits(tmp_path: Path) -> None:
    """scan_members on a streaming reader must enforce caps and not publish a cache."""
    import tarfile

    tar_path = tmp_path / "a.tar"
    with tarfile.open(tar_path, "w") as tf:
        for i in range(5):
            info = tarfile.TarInfo(name=f"f{i}.txt")
            payload = b"x"
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
    cfg = ArchiveyConfig(listing_limits=ListingLimits(max_members=2))
    with open_archive(tar_path, config=cfg, streaming=True) as reader:
        with pytest.raises(ResourceLimitError, match="max_members"):
            reader.scan_members()
        # Cache must stay unpublished after a limit trip.
        assert reader._materialized is None
        assert reader.members_report_if_available() is None


def test_metadata_accounting_counts_name_and_raw_name() -> None:
    member = ArchiveMember(
        type=MemberType.FILE,
        name="café.txt",
        raw_name="caf\xe9.txt".encode("latin-1"),
        comment="hi",
        extra={"note": "x", "nested": {"k": "v"}, "opaque": object()},
    )
    # Non-ASCII name uses the 4×len upper bound; ASCII comment/extra use len().
    expected = (
        4 * len("café.txt")
        + len("caf\xe9.txt".encode("latin-1"))
        + len("hi")
        + len("x")
        + len("v")
        # A nested dict's keys are archive-derived text and count; top-level keys
        # are format-defined literals and do not.
        + len("k")
    )
    assert member_metadata_bytes(member) == expected
    # Upper bound must not under-count real UTF-8 size (Unicode name-bomb safety).
    assert member_metadata_bytes(member) >= (
        len("café.txt".encode("utf-8", "surrogateescape"))
        + len("caf\xe9.txt".encode("latin-1"))
        + len("hi".encode("utf-8", "surrogateescape"))
        + len("x".encode("utf-8", "surrogateescape"))
        + len("v".encode("utf-8", "surrogateescape"))
    )


def test_tracker_ascii_exact_and_nonascii_upper_bound() -> None:
    ascii_member = ArchiveMember(type=MemberType.FILE, name="plain.txt")
    tracker = ListingLimitTracker(ListingLimits(max_metadata_bytes=10_000))
    tracker.account_member(ascii_member)
    assert tracker.metadata_bytes == len("plain.txt")

    tracker.reset()
    name = "a\udc80b"  # surrogateescape-style code points
    member = ArchiveMember(type=MemberType.FILE, name=name)
    tracker.account_member(member)
    assert tracker.metadata_bytes == 4 * len(name)
    assert tracker.metadata_bytes >= len(name.encode("utf-8", "surrogateescape"))


def test_metadata_accounting_never_undercounts_utf8() -> None:
    # Astral + BMP non-ASCII: 4*len is a safe ceiling over real UTF-8 size.
    name = "文件📂.txt"
    member = ArchiveMember(type=MemberType.FILE, name=name)
    weight = member_metadata_bytes(member)
    assert weight == 4 * len(name)
    assert weight >= len(name.encode("utf-8"))


def test_tar_listing_stops_reading_headers_at_max_metadata_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The byte cap bounds how many headers the walk parses, not only the member count.

    Long names must not be parsed many times over the byte budget before the refusal.
    """
    import tarfile

    tar_path = tmp_path / "long-names.tar"
    with tarfile.open(tar_path, "w", format=tarfile.GNU_FORMAT) as tf:
        for i in range(200):
            tf.addfile(tarfile.TarInfo(name=f"{i:03d}" + "n" * 9_997))
    cfg = ArchiveyConfig(listing_limits=ListingLimits(max_metadata_bytes=50_000))
    header_reads = _count_header_reads(monkeypatch)
    with open_archive(tar_path, config=cfg) as reader:
        with pytest.raises(ResourceLimitError, match="max_metadata_bytes"):
            reader.members()
    # Each name is 10 000 characters and the base weighs name and raw_name, so the
    # cap is passed on the third member.
    assert len(header_reads) <= 6


def test_metadata_accounting_counts_long_nested_extra_keys() -> None:
    """A PAX keyword inside ``extra["tar.pax_headers"]`` weighs its length.

    The bomb-scale case: a 100 000-byte nested key. The exact arithmetic, and the
    top-level keys that do not count, are
    ``test_metadata_accounting_skips_top_level_keys_and_counts_nested_keys``.
    """
    member = ArchiveMember(
        type=MemberType.FILE,
        name="f",
        extra={"tar.pax_headers": {"k" * 100_000: "v"}},
    )
    assert member_metadata_bytes(member) >= 100_000


def test_metadata_accounting_skips_top_level_keys_and_counts_nested_keys() -> None:
    """Only keys inside a nested dict weigh anything.

    Every top-level ``extra`` key is a format-defined literal (``zip.compress_type``,
    ``tar.type``), so charging it would tighten the cap per format for text no archive
    controls. A nested key, such as a PAX keyword, is sized by the archive.
    """
    bare = ArchiveMember(type=MemberType.FILE, name="a.txt")
    labelled = ArchiveMember(
        type=MemberType.FILE, name="a.txt", extra={"zip.compress_type": 8}
    )
    assert member_metadata_bytes(labelled) == member_metadata_bytes(bare)

    nested = ArchiveMember(
        type=MemberType.FILE,
        name="a.txt",
        extra={"tar.pax_headers": {"keyword": ""}},
    )
    assert member_metadata_bytes(nested) == member_metadata_bytes(bare) + len("keyword")


def test_tar_pax_keywords_count_toward_max_metadata_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PAX record's keyword is weighed like its value.

    TAR keeps every PAX record in ``extra["tar.pax_headers"]``. When only values were
    weighed, a member with a 100 000-byte keyword and a one-byte value weighed 4 bytes,
    so a small tar.gz held hundreds of megabytes of keywords under a 1 MiB cap.
    """
    import tarfile

    tar_path = tmp_path / "pax-keywords.tar"
    with tarfile.open(tar_path, "w", format=tarfile.PAX_FORMAT) as tf:
        for i in range(50):
            info = tarfile.TarInfo(name=f"f{i}")
            info.pax_headers = {f"{i:02d}" + "k" * 99_998: "v"}
            tf.addfile(info)
    cfg = ArchiveyConfig(listing_limits=ListingLimits(max_metadata_bytes=500_000))
    header_reads = _count_header_reads(monkeypatch)
    with open_archive(tar_path, config=cfg) as reader:
        with pytest.raises(ResourceLimitError, match="max_metadata_bytes"):
            reader.members()
    # Each keyword is 100 000 characters, so the walk passes the cap on the sixth
    # header and parses no further.
    assert len(header_reads) <= 6


def test_tar_drops_the_link_name_of_a_member_that_is_not_a_link(tmp_path: Path) -> None:
    """A long link name on a regular file is not presented, and the listing reads it.

    GNU tar stores a long link name in a LONGLINK block ahead of the header, which
    applies to whatever header follows. On a regular file it means nothing.
    """
    import tarfile

    tar_path = tmp_path / "longlink.tar"
    with tarfile.open(tar_path, "w", format=tarfile.GNU_FORMAT) as tf:
        for i in range(20):
            info = tarfile.TarInfo(name=f"f{i}")
            # Under the cap on its own: a header larger than max_metadata_bytes is
            # refused before it is read.
            info.linkname = "l" * 99_000
            tf.addfile(info)
    cfg = ArchiveyConfig(listing_limits=ListingLimits(max_metadata_bytes=100_000))
    with open_archive(tar_path, config=cfg) as reader:
        members = reader.members()
    assert len(members) == 20
    assert all(m.link_target is None for m in members)


@pytest.mark.parametrize("streaming", [False, True], ids=["random-access", "streaming"])
@pytest.mark.parametrize("fmt", ["gnu", "pax"])
def test_tar_link_name_drop_covers_both_walks_and_both_long_name_records(
    tmp_path: Path, fmt: str, streaming: bool
) -> None:
    """A long link name is dropped from a regular file and kept on a symlink.

    A GNU LONGLINK block or a PAX ``linkpath`` record applies to the header that
    follows, in both walks.
    """
    import tarfile

    tar_path = tmp_path / f"longlink-{fmt}.tar"
    target = "t" * 200
    tar_format = tarfile.GNU_FORMAT if fmt == "gnu" else tarfile.PAX_FORMAT
    with tarfile.open(tar_path, "w", format=tar_format) as tf:
        regular = tarfile.TarInfo(name="regular")
        regular.linkname = target
        tf.addfile(regular)
        link = tarfile.TarInfo(name="link")
        link.type = tarfile.SYMTYPE
        link.linkname = target
        tf.addfile(link)
    with open_archive(tar_path, streaming=streaming) as reader:
        if streaming:
            members = {m.name: m for m, _ in reader.stream_members()}
        else:
            members = {m.name: m for m in reader.members()}
    assert members["regular"].link_target is None
    assert members["link"].link_target == target
