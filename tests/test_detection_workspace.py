"""Instrumented sources and access-shape pins for the detection prefix workspace.

Measured on ``main`` before this change (seekable stream through ``detect_format``):

| source | reads | forward seeks | backward seeks |
| --- | --- | --- | --- |
| gzip | 5 | 0 | **5** — the same 30 bytes fetched five times |
| ISO | 2 | 0 | **2** — 4 096 bytes, rewind, then 32 774 re-read from zero |
| ZIP, TAR | 1 | 0 | 1 |

The workspace makes the shape normative: zero backward seeks, at most one seek towards
the end, and each source byte fetched at most once.
"""

from __future__ import annotations

import gzip
import io
import zipfile
from pathlib import Path

import pytest

from archivey import (
    BALANCED_BUDGET,
    THOROUGH_BUDGET,
    DetectionBudget,
    DetectionCapability,
    detect_format,
)
from archivey.detection_cost import TierSkipReason
from archivey.internal.detection_workspace import (
    PrefixWorkspace,
    candidate_origin_for_hit,
)
from archivey.internal.sfx import ScanNeedle, find_magic_in_prefix
from archivey.internal.streams.peekable import PeekableStream
from archivey.types import ArchiveFormat
from tests.streams_util import NonSeekableBytesIO


class InstrumentedBytesIO(io.RawIOBase):
    """Seekable source that counts reads, forward/backward seeks, and unique bytes."""

    def __init__(self, data: bytes) -> None:
        super().__init__()
        self._inner = io.BytesIO(data)
        self.size = len(data)  # cheap size for source_byte_size / REMAINING_KNOWN
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
    # The exit path restores the caller's entry position (one seek back). That is the
    # non-consumption contract, not a re-read rewind — the old defect was five rewinds
    # that each re-fetched the same prefix. Unique bytes == bytes read pins "fetched once".
    assert src.unique_bytes == src.bytes_read, (
        f"{label}: re-fetched bytes (unique={src.unique_bytes}, read={src.bytes_read})"
    )
    assert src.backward_seeks <= 1, (
        f"{label}: expected at most the exit restore, got {src.backward_seeks} "
        f"(reads={src.read_calls}, forward={src.forward_seeks})"
    )
    assert src.tell() == 0  # non-consuming
    # At most one seek towards the end (tail tier); BALANCED enables none.
    # Forward seeks may also include reposition-after-tail; under BALANCED: 0.
    assert src.forward_seeks <= 1
    assert src.tell() == 0


def test_path_detection_access_shape(tmp_path: Path) -> None:
    path = tmp_path / "a.gz"
    path.write_bytes(_gzip_bytes())
    info = detect_format(path)
    assert info.format == ArchiveFormat.GZ
    assert info.cost_receipt is not None
    assert info.cost_receipt.seeks == 0
    # Growing peeks must not re-count the same bytes.
    assert info.cost_receipt.unique_bytes_read <= max(
        info.cost_receipt.prefix_bytes, 4096
    )


def test_peekable_pipe_detection_access_shape() -> None:
    stream = PeekableStream(NonSeekableBytesIO(_gzip_bytes()))
    info = detect_format(stream)
    assert info.format == ArchiveFormat.GZ
    assert info.cost_receipt is not None
    assert info.cost_receipt.seeks == 0
    # ZIP tail is not enabled under BALANCED — distinct from capability-unavailable.
    assert any(
        s.tier == "zip_tail" and s.reason is TierSkipReason.NOT_ENABLED_BY_POLICY
        for s in info.unavailable_tiers
    )


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


def test_zero_seek_budget_withdraws_seek_from_a_file(tmp_path: Path) -> None:
    path = tmp_path / "a.zip"
    path.write_bytes(_zip_bytes())
    budget = DetectionBudget(
        max_prefix_bytes=4096,
        max_far_bytes=0,
        max_tail_bytes=65_557,
        max_seeks=0,  # withdraws SEEK even though the file is seekable
        max_scan_bytes=0,
        max_decode_input=0,
        max_decode_output=0,
        completion_window_bytes=0,
        max_index_bytes=0,
        max_probe_links=0,
        spool_non_seekable_up_to=0,
        collect_nonmaximal_candidates=False,
    )
    with PrefixWorkspace(path, budget) as ws:
        caps = ws.capabilities()
    assert DetectionCapability.SEEK not in caps
    assert DetectionCapability.TAIL not in caps
    assert DetectionCapability.PREFIX in caps


def test_spool_policy_grants_tail_to_a_pipe() -> None:
    payload = _zip_bytes()
    budget = DetectionBudget(
        max_prefix_bytes=4096,
        max_far_bytes=0,
        max_tail_bytes=65_557,
        max_seeks=1,
        max_scan_bytes=0,
        max_decode_input=0,
        max_decode_output=0,
        completion_window_bytes=0,
        max_index_bytes=0,
        max_probe_links=0,
        spool_non_seekable_up_to=len(payload) + 64,
        collect_nonmaximal_candidates=False,
    )
    with PrefixWorkspace(NonSeekableBytesIO(payload), budget) as ws:
        caps = ws.capabilities()
        assert DetectionCapability.TAIL in caps
        assert ws.receipt.spooled_bytes == len(payload)


def test_pipe_without_spool_records_tail_unavailable() -> None:
    stream = PeekableStream(NonSeekableBytesIO(_zip_bytes()))
    info = detect_format(stream, budget=THOROUGH_BUDGET)
    assert any(
        s.tier == "zip_tail" and s.reason is TierSkipReason.CAPABILITY_UNAVAILABLE
        for s in info.unavailable_tiers
    )


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


def test_remaining_known_from_entry_position() -> None:
    payload = b"\x00" * 10_000
    src = InstrumentedBytesIO(payload)
    src.seek(1000)
    with PrefixWorkspace(src, BALANCED_BUDGET) as ws:
        assert ws.remaining_known() == 9000
        # An overestimated total cannot prove a later offset reachable — we only report
        # what is measured from the entry position.
        assert DetectionCapability.REMAINING_KNOWN in ws.capabilities()
