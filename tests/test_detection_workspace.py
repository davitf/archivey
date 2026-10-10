"""Instrumented sources and access-shape pins for the detection prefix workspace.

Measured on ``main`` before this change (seekable stream through ``detect_format``):

| source | reads | forward seeks | backward seeks |
| --- | --- | --- | --- |
| gzip | 5 | 0 | **5** — the same 30 bytes fetched five times |
| ISO | 2 | 0 | **2** — 4 096 bytes, rewind, then 32 774 re-read from zero |
| ZIP, TAR | 1 | 0 | 1 |

The workspace makes the shape normative for the **prefix tiers**: zero backward seeks
for growing peeks, and each prefix byte fetched at most once. The UDIF trailer read
sits outside that buffer. A seekable bzip2 or xz file fetches its last 512 bytes
there, and a later tier that reads the file fetches them again.
Content-probe ``read_at`` on cheap random-access sources may seek and restore the
handle (bounded by the Brotli chain-walk link cap); those restores are not
"re-fetch rewinds".
"""

from __future__ import annotations

import bz2
import gzip
import io
import os
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

import pytest

from archivey import detect_format, open_archive
from archivey.config import ArchiveyConfig
from archivey.detection_cost import (
    BALANCED_BUDGET,
    THOROUGH_BUDGET,
    DetectionBudget,
    TierSkipReason,
)
from archivey.exceptions import FormatDetectionError
from archivey.internal import detection_workspace
from archivey.internal.detection_workspace import PrefixWorkspace
from archivey.internal.sfx import (
    ScanNeedle,
    candidate_origin_for_hit,
    find_magic_in_prefix,
    iter_magic_in_prefix,
)
from archivey.internal.source import ArchiveSource
from archivey.internal.streams.archive_stream import ArchiveStream
from archivey.types import ArchiveFormat
from tests.detection_cost_util import trailer_allowance, within_budget
from tests.streams_util import NonSeekableBytesIO


class InstrumentedBytesIO(io.RawIOBase):
    """Seekable source that counts reads, forward/backward seeks, and unique bytes."""

    def __init__(self, data: bytes) -> None:
        super().__init__()
        self._inner = io.BytesIO(data)
        self.size = len(data)  # cheap size for source_byte_size / remaining_known()
        self.read_calls = 0
        self.forward_seeks = 0
        self.backward_seeks = 0
        self.bytes_read = 0
        self._seen: set[int] = set()
        self.unique_bytes = 0
        self._pos = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def read(self, n: int = -1, /) -> bytes:
        data = self._inner.read(n)
        if data:
            self.read_calls += 1
            self.bytes_read += len(data)
            for i in range(self._pos, self._pos + len(data)):
                if i not in self._seen:
                    self._seen.add(i)
                    self.unique_bytes += 1
            self._pos += len(data)
        return data

    def readinto(self, b) -> int:  # type: ignore[override]
        mv = memoryview(b).cast("B")
        data = self.read(len(mv))
        mv[: len(data)] = data
        return len(data)

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        before = self._inner.tell()
        after = self._inner.seek(offset, whence)
        if after > before:
            self.forward_seeks += 1
        elif after < before:
            self.backward_seeks += 1
        self._pos = after
        return after

    def tell(self, /) -> int:
        return self._inner.tell()


def _gzip_bytes() -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(b"hello detection workspace\n")
    return buf.getvalue()


def _zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", b"hello")
    return buf.getvalue()


def _iso_bytes() -> bytes:
    # Minimal far-magic: CD001 at offset 32769 (volume descriptor type + magic).
    data = bytearray(32_774)
    data[32769:32774] = b"CD001"
    # Primary volume descriptor type byte immediately before CD001 is usually 1.
    data[32768] = 1
    return bytes(data)


# ---------------------------------------------------------------------------
# Characterisation baseline (documented) + target access shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,payload,expect_format",
    [
        ("gzip", _gzip_bytes(), ArchiveFormat.GZ),
        ("zip", _zip_bytes(), ArchiveFormat.ZIP),
        ("iso", _iso_bytes(), ArchiveFormat.ISO),
    ],
    # Short ids only: the ISO fixture is ~32 KiB of NULs; pytest's default id embeds
    # repr(payload) into PYTEST_CURRENT_TEST, which exceeds Windows' 32767-char env limit.
    ids=["gzip", "zip", "iso"],
)
def test_seekable_detection_has_zero_backward_seeks(
    label: str,
    payload: bytes,
    expect_format: ArchiveFormat,
) -> None:
    # Baseline on main: gzip 5 backward seeks, ISO 2, ZIP 1. After the workspace: at most
    # the exit restore.
    src = InstrumentedBytesIO(payload)
    info = detect_format(src)
    assert info.format == expect_format
    # These three return before the trailer step. The exit path restores the caller's
    # entry position (one seek back). That is the non-consumption contract, not a
    # re-read rewind — the old defect was five rewinds that each re-fetched the same
    # prefix. Unique bytes == bytes read pins "fetched once" for a detection that
    # never reads the tail. A bzip2 or xz file does, and has its own test.
    assert src.unique_bytes == src.bytes_read, (
        f"{label}: re-fetched bytes (unique={src.unique_bytes}, read={src.bytes_read})"
    )
    assert src.backward_seeks <= 1, (
        f"{label}: expected at most the exit restore, got {src.backward_seeks} "
        f"(reads={src.read_calls}, forward={src.forward_seeks})"
    )
    assert src.tell() == 0  # non-consuming
    # No tier on this path seeks towards the end. The bound leaves room for one
    # content-probe ``read_at``, which restores the position afterwards.
    assert src.forward_seeks <= 1
    assert src.tell() == 0


def test_seekable_bzip2_rereads_the_trailer_bytes() -> None:
    """A bzip2 near-magic hit reads the koly block, then the inner-TAR probe reads the file.

    The tail bytes are not kept, so the later read fetches them again. The delta is
    512. A trailer tier that cached the bytes, or that did not run, would report 0.
    """
    payload = bz2.compress(os.urandom(20_000))
    assert len(payload) > 4096
    src = InstrumentedBytesIO(payload)
    info = detect_format(src)
    assert info.format == ArchiveFormat.BZ2
    assert src.bytes_read - src.unique_bytes == 512
    assert src.backward_seeks == 2
    assert src.forward_seeks == 1
    assert src.tell() == 0
    assert info.cost_receipt is not None
    assert info.cost_receipt.unique_bytes_read == src.unique_bytes + 512


def test_seekable_koly_image_reads_the_trailer_once() -> None:
    """A koly hit returns at the trailer, before the inner-TAR probe re-reads the file."""
    payload = bz2.compress(os.urandom(20_000))
    trailer = bytearray(512)
    trailer[:12] = b"koly" + (4).to_bytes(4, "big") + (512).to_bytes(4, "big")
    src = InstrumentedBytesIO(payload + bytes(trailer))
    info = detect_format(src)
    assert info.format == ArchiveFormat.DMG
    assert src.bytes_read == src.unique_bytes
    assert src.backward_seeks == 2
    assert src.forward_seeks == 1
    assert src.tell() == 0


def _member_seeks(
    monkeypatch: pytest.MonkeyPatch, detect: Callable[[BinaryIO], object]
) -> list[int]:
    """Targets of every seek ``detect`` makes on a deflated ZIP member stream.

    The member has no magic, so detection reaches the UDIF trailer step and fails.
    It is opened with ``seekable_members=True``: a seek to its end decodes the whole
    member, and the seek back re-decodes it from the start.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("m.bin", bytes(range(256)) * 4096)
    targets: list[int] = []
    real_seek = ArchiveStream.seek

    def spy(self: ArchiveStream, offset: int, whence: int = io.SEEK_SET, /) -> int:
        pos = real_seek(self, offset, whence)
        targets.append(pos)
        return pos

    buf.seek(0)
    with (
        monkeypatch.context() as patch,
        open_archive(buf, seekable_members=True) as reader,
        reader.open("m.bin") as member,
    ):
        patch.setattr(ArchiveStream, "seek", spy)
        with pytest.raises(FormatDetectionError):
            detect(member)
    return targets


@pytest.mark.parametrize(
    "detect",
    [
        lambda m: detect_format(ArchiveSource.for_stream(m)),
        lambda m: open_archive(m),
    ],
    ids=["archive_source", "open_archive"],
)
def test_wrapped_member_stream_is_not_seeked_to_its_end(
    monkeypatch: pytest.MonkeyPatch, detect: Callable[[BinaryIO], object]
) -> None:
    # ``open_archive`` wraps a member stream in an ``ArchiveSource`` before detection
    # sees it. The workspace must still treat it as the ``ArchiveStream`` it is: no
    # trailer or probe seek towards the end, only the exit restore, the same as for the
    # bare stream.
    bare = _member_seeks(monkeypatch, detect_format)
    assert bare == [0]
    assert _member_seeks(monkeypatch, detect) == bare


def test_path_detection_access_shape(tmp_path: Path) -> None:
    path = tmp_path / "a.gz"
    path.write_bytes(_gzip_bytes())
    info = detect_format(path)
    assert info.format == ArchiveFormat.GZ
    assert info.cost_receipt is not None
    # Growing peeks must not re-count the same bytes.
    assert info.cost_receipt.unique_bytes_read <= max(
        info.cost_receipt.prefix_bytes, 4096
    )


def test_peekable_pipe_detection_access_shape() -> None:
    stream = ArchiveSource.for_stream(NonSeekableBytesIO(_gzip_bytes()))
    info = detect_format(stream)
    assert info.format == ArchiveFormat.GZ
    assert info.cost_receipt is not None
    # Every tier ran: a pipe under BALANCED skips nothing.
    assert info.unavailable_tiers == ()


def test_a_hint_sized_archive_source_still_knows_its_size() -> None:
    """Detection takes a caller's ``size`` hint through the source.

    ``ArchiveSource.size`` is the fact alone, so reading the workspace's total through
    ``source_byte_size`` would drop ``remaining_known()`` for a stream whose only cheap
    size is an fsspec ``size`` attribute. Fails against that.
    """
    from archivey.internal.volumes import resolve_source
    from tests.streams_util import ReadSizeRecorder

    # Seekable and raw, with ``size`` its only cheap length: not a fact.
    resolved = resolve_source(ReadSizeRecorder(bytes(5004)))
    try:
        assert resolved.source.size is None
        with PrefixWorkspace(resolved.source, BALANCED_BUDGET) as ws:
            assert ws.remaining_known() == 5004
    finally:
        resolved.source.close()


def test_growing_prefix_fetches_each_byte_once() -> None:
    payload = bytes(range(256)) * (2 * 1024 * 1024 // 256)  # 2 MiB patterned
    src = InstrumentedBytesIO(payload)
    with PrefixWorkspace(src, BALANCED_BUDGET) as ws:
        ws.peek_prefix(4096)
        ws.peek_prefix(32_774)
        ws.peek_prefix(2 * 1024 * 1024)
    assert src.backward_seeks <= 1  # exit restore only
    assert src.unique_bytes == 2 * 1024 * 1024
    assert ws.receipt.unique_bytes_read == 2 * 1024 * 1024


def test_candidate_relative_view_is_not_a_second_fetch() -> None:
    payload = b"PREFIX" + b"ustar" + b"TAIL" * 100
    src = InstrumentedBytesIO(payload)
    with PrefixWorkspace(src, BALANCED_BUDGET) as ws:
        # Absolute read at origin 6 for 5 bytes, then the same via a candidate view.
        absolute = ws.peek_range(6, 5)
        before = src.unique_bytes
        view = ws.candidate_view(6)
        relative = view(5)
        assert absolute == relative == b"ustar"
        assert src.unique_bytes == before  # no second fetch


def test_candidate_view_limit_does_not_read_past_the_ceiling() -> None:
    payload = b"PREFIX" + b"PK\x03\x04" + b"\x00" * 200
    src = InstrumentedBytesIO(payload)
    with PrefixWorkspace(src, BALANCED_BUDGET) as ws:
        ws.peek_prefix(10)
        before = src.unique_bytes
        view = ws.candidate_view(6, limit=10)
        got = view(100)
        assert got == payload[6:10]
        assert src.unique_bytes == before


def test_candidate_view_limit_notes_a_clamped_read() -> None:
    payload = b"PREFIX" + b"PK\x03\x04" + b"\x00" * 200
    src = InstrumentedBytesIO(payload)
    with PrefixWorkspace(src, BALANCED_BUDGET) as ws:
        ws.peek_prefix(10)
        exact = ws.candidate_view(6, limit=10)
        assert exact(4) == payload[6:10]
        assert not ws.take_clamped_view_read()
        truncated = ws.candidate_view(6, limit=10)
        assert truncated(100) == payload[6:10]
        assert ws.take_clamped_view_read()
        assert not ws.take_clamped_view_read()


def test_iter_magic_in_prefix_does_not_re_yield_at_peek_boundaries() -> None:
    data = bytearray(65536 * 2)
    data[65532:65536] = b"PK\x03\x04"
    hits = list(
        iter_magic_in_prefix(
            lambda n: bytes(data[:n]),
            [ScanNeedle(b"PK\x03\x04"), ScanNeedle(b"Rar!\x1a\x07\x01\x00")],
            limit=len(data),
        )
    )
    origins = [hit.candidate_origin for hit in hits]
    assert origins == [65532]


def test_negative_candidate_origin_is_discarded() -> None:
    assert candidate_origin_for_hit(100, 257) is None
    assert candidate_origin_for_hit(257, 257) == 0
    assert candidate_origin_for_hit(100_257, 257) == 100_000

    # ustar at absolute 100 would imply origin -157 → discarded.
    def peek_more_decoy(n: int) -> bytes:
        return (b"\x00" * 100 + b"ustar" + b"\x00" * 400)[:n]

    hit = find_magic_in_prefix(peek_more_decoy, (ScanNeedle(b"ustar", 257),), limit=512)
    assert hit is None

    # ustar at absolute 257 → candidate origin 0.
    def peek_more_tar(n: int) -> bytes:
        return (b"\x00" * 257 + b"ustar" + b"\x00" * 400)[:n]

    hit = find_magic_in_prefix(peek_more_tar, (ScanNeedle(b"ustar", 257),), limit=1024)
    assert hit is not None
    assert hit.candidate_origin == 0
    assert hit.needle == b"ustar"


def test_pipe_under_thorough_skips_no_tier() -> None:
    stream = ArchiveSource.for_stream(NonSeekableBytesIO(_zip_bytes()))
    info = detect_format(
        stream, config=ArchiveyConfig(detection_budget=THOROUGH_BUDGET)
    )
    assert info.format == ArchiveFormat.ZIP
    assert info.unavailable_tiers == ()


def test_seekable_stream_restored_on_error_path() -> None:
    # A mid-positioned stream must be restored even when detection raises.
    from archivey.exceptions import FormatDetectionError

    junk = b"not-an-archive-at-all-" * 200
    src = InstrumentedBytesIO(b"PAD" + junk)
    src.seek(3)
    with pytest.raises(FormatDetectionError):
        detect_format(src)
    assert src.tell() == 3
    assert src.backward_seeks >= 1  # the restore seek itself


def test_borrowed_stream_stays_at_entry_after_close() -> None:
    src = InstrumentedBytesIO(b"\x00" * 1000)
    src.seek(100)
    ws = PrefixWorkspace(src, BALANCED_BUDGET)
    ws.ensure(64)
    ws.close()
    assert src.tell() == 100
    ws.ensure(128)
    assert src.tell() == 100


def test_remaining_known_from_entry_position() -> None:
    payload = b"\x00" * 10_000
    src = InstrumentedBytesIO(payload)
    src.seek(1000)
    with PrefixWorkspace(src, BALANCED_BUDGET) as ws:
        # An overestimated total cannot prove a later offset reachable — we only report
        # what is measured from the entry position.
        assert ws.remaining_known() == 9000


def test_fast_sfx_scan_respects_max_scan_bytes(tmp_path: Path) -> None:
    # F2: FAST's max_scan_bytes must bound the SFX window (not only gate it on/off).
    from archivey.detection_cost import FAST_BUDGET

    mz = b"MZ" + b"\x00" * 62
    path = tmp_path / "stub.zip"
    path.write_bytes(mz + b"\x00" * (3 * 1024 * 1024))
    balanced = detect_format(
        path, config=ArchiveyConfig(detection_budget=BALANCED_BUDGET)
    )
    fast = detect_format(path, config=ArchiveyConfig(detection_budget=FAST_BUDGET))
    assert balanced.cost_receipt is not None and fast.cost_receipt is not None
    # The scan window is ``max_scan_bytes``. The trailer block is a separate
    # read at the end of the file, so it is allowed on top of that window.
    assert fast.cost_receipt.unique_bytes_read <= (
        FAST_BUDGET.max_scan_bytes + trailer_allowance()
    )
    assert fast.cost_receipt.unique_bytes_read < balanced.cost_receipt.unique_bytes_read
    assert fast.cost_receipt.scanned_bytes <= FAST_BUDGET.max_scan_bytes


def test_sfx_miss_charges_scanned_bytes(tmp_path: Path) -> None:
    # F4: a full-window miss must bill scanned_bytes, not leave it at 0.
    mz = b"MZ" + b"\x00" * 62
    path = tmp_path / "stub.zip"
    path.write_bytes(mz + b"\x00" * (2 * 1024 * 1024))
    info = detect_format(path, config=ArchiveyConfig(detection_budget=BALANCED_BUDGET))
    assert info.detected_by == "extension"
    assert info.cost_receipt is not None
    assert info.cost_receipt.scanned_bytes > 0
    assert info.cost_receipt.scanned_bytes <= BALANCED_BUDGET.max_scan_bytes


def test_sfx_miss_extension_guess_stays_within_budget(tmp_path: Path) -> None:
    # F18: full-scan SFX miss + any later probe seeks must still stay within budget.
    from archivey.detection_cost import FAST_BUDGET

    mz = b"MZ" + b"\x00" * 62
    path = tmp_path / "stub.zip"
    path.write_bytes(mz + b"\x00" * (3 * 1024 * 1024))
    for budget in (BALANCED_BUDGET, FAST_BUDGET):
        info = detect_format(path, config=ArchiveyConfig(detection_budget=budget))
        assert info.detected_by == "extension"
        assert info.cost_receipt is not None
        assert within_budget(info.cost_receipt, budget), info.cost_receipt


def test_within_budget_allows_probe_seeks_above_scan_ceiling() -> None:
    # Seek-based read_at charges unique_bytes without a scan-window home; the allowance
    # is the Brotli walk plus one trailer block.
    from archivey.detection_cost import DetectionCostReceipt
    from archivey.internal.streams.brotli_framing import (
        CHAIN_HEADER_READ,
        CHAIN_MAX_LINKS,
    )

    scan = BALANCED_BUDGET.max_scan_bytes
    allowance = CHAIN_MAX_LINKS * CHAIN_HEADER_READ + trailer_allowance()
    at_cap = DetectionCostReceipt(
        unique_bytes_read=scan + allowance,
        scanned_bytes=scan,
    )
    assert within_budget(at_cap, BALANCED_BUDGET)
    over = DetectionCostReceipt(
        unique_bytes_read=scan + allowance + 1,
        scanned_bytes=scan,
    )
    assert not within_budget(over, BALANCED_BUDGET)


def test_read_at_on_path_seeks_without_buffering_prefix(tmp_path: Path) -> None:
    # Decision 2A: far probe reads must not grow _buf through [0, offset).
    path = tmp_path / "big.bin"
    size = 4 * 1024 * 1024
    path.write_bytes(b"\x00" * (size - 4) + b"TAIL")
    with PrefixWorkspace(path, BALANCED_BUDGET) as ws:
        ws.peek_prefix(64)
        before = ws.buffered_length
        got = ws.read_at(size - 4, 4)
        assert got == b"TAIL"
        assert ws.buffered_length == before
        assert ws.receipt.unique_bytes_read == before + 4


def test_read_at_nonseekable_past_cap_records_budget_exhausted() -> None:
    # Decision 2B: capped buffer; past the cap → None + BUDGET_EXHAUSTED.
    from archivey.internal.detection_workspace import (
        PROBE_READ_AT_MAX_OFFSET_NONSEEKABLE,
    )

    payload = b"\x00" * (PROBE_READ_AT_MAX_OFFSET_NONSEEKABLE + 100)
    stream = ArchiveSource.for_stream(NonSeekableBytesIO(payload))
    with PrefixWorkspace(stream, BALANCED_BUDGET) as ws:
        assert ws.read_at(PROBE_READ_AT_MAX_OFFSET_NONSEEKABLE, 4) is None
        assert any(
            s.tier == "content_probe_read_at"
            and s.reason is TierSkipReason.BUDGET_EXHAUSTED
            for s in ws.skips
        )


def test_large_brotli_detection_does_not_read_most_of_file(tmp_path: Path) -> None:
    # F1 regression: benign large Brotli must not pull tens of MiB into the prefix.
    brotli = pytest.importorskip("brotli")
    raw = os.urandom(2 * 1024 * 1024)  # incompressible → large framed stream
    compressed = brotli.compress(raw)
    assert len(compressed) > 1024 * 1024
    path = tmp_path / "big.br"
    path.write_bytes(compressed)
    info = detect_format(path)
    assert info.format.stream is not None
    assert info.format.stream.name == "BROTLI"
    assert info.cost_receipt is not None
    # Seek-based probes: unique bytes stay near the near-prefix + small chain walks.
    assert info.cost_receipt.unique_bytes_read < 512 * 1024
    assert within_budget(info.cost_receipt, BALANCED_BUDGET)


# ---------------------------------------------------------------------------
# The ledger: a receipt over budget always names the tier that was cut short
# ---------------------------------------------------------------------------


def test_read_at_buffered_fallback_stays_inside_the_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # S19-K2: the buffered fallback used to grow the prefix to the 1 MiB constant
    # whatever the budget said, so FAST (256 KiB scan ceiling) went over budget with no
    # skip. Patching ``seek_is_expensive`` stands in for an ``ArchiveStream``.
    from archivey.detection_cost import FAST_BUDGET
    from archivey.internal.detection_workspace import (
        PROBE_READ_AT_MAX_OFFSET_NONSEEKABLE,
    )

    monkeypatch.setattr(detection_workspace, "seek_is_expensive", lambda stream: True)
    payload = io.BytesIO(b"\x00" * (PROBE_READ_AT_MAX_OFFSET_NONSEEKABLE + 100))
    with PrefixWorkspace(payload, FAST_BUDGET) as ws:
        assert ws.read_at(PROBE_READ_AT_MAX_OFFSET_NONSEEKABLE - 24, 24) is None
        assert ws.receipt.unique_bytes_read <= FAST_BUDGET.max_scan_bytes
        assert within_budget(ws.receipt, FAST_BUDGET)
        assert any(
            s.tier == "content_probe_read_at"
            and s.reason is TierSkipReason.BUDGET_EXHAUSTED
            for s in ws.skips
        )
        # Inside the ceiling the fallback still serves the read.
        assert ws.read_at(FAST_BUDGET.max_scan_bytes - 24, 24) == b"\x00" * 24


def test_within_budget_checks_far_bytes() -> None:
    # S19-K7: far_bytes was the one bounded counter within_budget never compared.
    from archivey.detection_cost import DetectionCostReceipt

    limit = BALANCED_BUDGET.max_far_bytes
    assert within_budget(DetectionCostReceipt(far_bytes=limit), BALANCED_BUDGET)
    assert not within_budget(DetectionCostReceipt(far_bytes=limit + 1), BALANCED_BUDGET)


def _mz_stub_bytes() -> bytes:
    return b"MZ" + b"\x00" * 62 + b"\x00" * (3 * 1024 * 1024)


def _big_tar_bz2() -> bytes:
    import bz2
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        data = os.urandom(850_000)
        info = tarfile.TarInfo("first.bin")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    return bz2.compress(buf.getvalue(), 9)


def _corrupt_bz2() -> bytes:
    import bz2

    data = bytearray(bz2.compress(os.urandom(4096) * 4, 9))
    for i in range(10, len(data) - 10):
        data[i] ^= 0x5A
    return bytes(data)


_INVARIANT_PAYLOADS = {
    "gzip.gz": _gzip_bytes(),
    "zip.zip": _zip_bytes(),
    "iso.iso": _iso_bytes(),
    "stub.zip": _mz_stub_bytes(),
    "big.tar.bz2": _big_tar_bz2(),
    "corrupt.bz2": _corrupt_bz2(),
}


def _tight_budget() -> DetectionBudget:
    from dataclasses import replace

    return replace(
        BALANCED_BUDGET,
        max_far_bytes=4096,
        max_scan_bytes=8192,
        max_decode_input=4096,
        max_decode_output=4096,
    )


@pytest.mark.parametrize("kind", ["path", "seekable", "pipe"])
@pytest.mark.parametrize("budget_name", ["balanced", "fast", "thorough", "tight"])
@pytest.mark.parametrize("name", sorted(_INVARIANT_PAYLOADS))
def test_over_budget_receipt_always_names_a_cut_short_tier(
    tmp_path: Path, name: str, budget_name: str, kind: str
) -> None:
    # The property a caller reads the receipt for: a receipt that fails
    # ``within_budget`` has a BUDGET_EXHAUSTED or CAPABILITY_UNAVAILABLE skip saying which
    # tier did it. Every payload here stays in budget today; the assertion is written as
    # the invariant so a future tier that overspends has to record why.
    from archivey.detection_cost import FAST_BUDGET
    from archivey.exceptions import FormatDetectionError

    budget = {
        "balanced": BALANCED_BUDGET,
        "fast": FAST_BUDGET,
        "thorough": THOROUGH_BUDGET,
        "tight": _tight_budget(),
    }[budget_name]
    payload = _INVARIANT_PAYLOADS[name]
    source: object
    if kind == "path":
        source = tmp_path / name
        source.write_bytes(payload)
    elif kind == "seekable":
        source = io.BytesIO(payload)
    else:
        source = ArchiveSource.for_stream(NonSeekableBytesIO(payload))
    try:
        info = detect_format(source, config=ArchiveyConfig(detection_budget=budget))  # type: ignore[arg-type]
    except FormatDetectionError:
        return  # extensionless stub: nothing to inspect, and nothing claimed
    receipt = info.cost_receipt
    assert receipt is not None
    incomplete = {
        TierSkipReason.BUDGET_EXHAUSTED,
        TierSkipReason.CAPABILITY_UNAVAILABLE,
    }
    assert within_budget(receipt, budget) or any(
        s.reason in incomplete for s in info.unavailable_tiers
    ), (receipt, info.unavailable_tiers)


@pytest.mark.parametrize("budget_name", ["balanced", "fast", "thorough", "tight"])
def test_two_pass_receipt_over_budget_also_names_a_cut_short_tier(
    tmp_path: Path, budget_name: str
) -> None:
    # The same invariant over the one receipt that sums two passes: a stub-only
    # ``vol.exe`` followed to its sibling ``vol.7z.001``.
    from archivey.detection_cost import FAST_BUDGET

    budget = {
        "balanced": BALANCED_BUDGET,
        "fast": FAST_BUDGET,
        "thorough": THOROUGH_BUDGET,
        "tight": _tight_budget(),
    }[budget_name]
    (tmp_path / "vol.exe").write_bytes(_mz_stub_bytes())
    (tmp_path / "vol.7z.001").write_bytes(b"7z\xbc\xaf\x27\x1c" + b"\x00" * 8192)
    info = detect_format(
        tmp_path / "vol.exe", config=ArchiveyConfig(detection_budget=budget)
    )
    assert info.format == ArchiveFormat.SEVEN_Z
    receipt = info.cost_receipt
    assert receipt is not None
    assert receipt.passes == 2
    incomplete = {
        TierSkipReason.BUDGET_EXHAUSTED,
        TierSkipReason.CAPABILITY_UNAVAILABLE,
    }
    assert within_budget(receipt, budget) or any(
        s.reason in incomplete for s in info.unavailable_tiers
    ), (receipt, info.unavailable_tiers)
