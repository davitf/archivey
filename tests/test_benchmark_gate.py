"""Pytest entry for the structural benchmark gate (solid no-re-decode + axes)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.fixtures import materialize_fixtures
from benchmarks.harness import (
    STRUCTURAL_BASELINE,
    _crypto_available,
    _rapidgzip_available,
    _structural_checks,
    _unrar_available,
    load_json,
    run_cases,
)


@pytest.mark.timeout(120)
def test_benchmark_structural_gate(tmp_path: Path) -> None:
    """PR gate: solid sequential decode bound + common-path byte counts."""
    fixtures = materialize_fixtures(tmp_path / "fixtures")
    if fixtures.solid_7z is None:
        pytest.importorskip("py7zr")
    work = tmp_path / "work"
    work.mkdir()
    results = run_cases(fixtures, work)
    by_case = {r.case: r for r in results}

    # Ensure the solid sequential case ran when 7z is available.
    sequential = by_case.get("sevenzip_solid_sequential")
    assert sequential is not None, "expected sevenzip_solid_sequential case"
    assert sequential.bytes_decompressed <= (sequential.unpacked_bytes or 0) * 2

    random = by_case.get("sevenzip_solid_random")
    assert random is not None
    # Random opens re-decode; recorded, not failed — but must be visible.
    assert random.bytes_decompressed >= sequential.bytes_decompressed

    # T3 / perf P6: RAR data, encrypted, and in-ZIP accelerator cases when deps exist.
    if _unrar_available():
        assert "rar_solid_sequential" in by_case
        assert "rar_solid_random" in by_case
        assert "rar_encrypted_read_all" in by_case
    if _crypto_available() and fixtures.zip_aes_path is not None:
        assert "zip_aes_read_all" in by_case
    assert "zip_lzma_read_all" in by_case
    assert "zip_read_all_accel_off" in by_case
    if _rapidgzip_available():
        assert "zip_read_all_accel_on" in by_case

    failures = _structural_checks(results, load_json(STRUCTURAL_BASELINE))
    assert not failures, "structural gate failures:\n" + "\n".join(failures)


def test_structural_baseline_committed() -> None:
    assert STRUCTURAL_BASELINE.is_file()
    data = json.loads(STRUCTURAL_BASELINE.read_text())
    cases = data["cases"]
    assert "sevenzip_solid_sequential" in cases
    # P6 remainder / T3 coverage must stay in the committed ci baseline.
    for name in (
        "rar_solid_sequential",
        "rar_solid_random",
        "rar_encrypted_read_all",
        "zip_aes_read_all",
        "zip_lzma_read_all",
        "zip_read_all_accel_off",
        "zip_read_all_accel_on",
    ):
        assert name in cases, f"missing structural baseline case {name}"


def test_format_text_report_table() -> None:
    """Friendly markdown report is readable without downloading JSON."""
    from benchmarks.harness import format_text_report

    payload = {
        "mode": "full",
        "scale": "realistic",
        "warmup": True,
        "scale_detail": {
            "common_members": 64,
            "common_member_size": 262144,
            "gzip_size": 33554432,
            "solid_members": 64,
            "solid_member_size": 262144,
        },
        "results": [
            {
                "case": "zip_read_all",
                "format": "zip",
                "operation": "read_all",
                "wall_s": 0.0158,
                "bytes_decompressed": 1_048_576,
                "source_seek_count": 3,
                "unpacked_bytes": 1_048_576,
                "stdlib_wall_s": 0.0134,
                "wall_ratio": 1.18,
                "notes": "",
            },
            {
                "case": "sevenzip_solid_sequential",
                "format": "7z",
                "operation": "read_all_sequential",
                "wall_s": 0.4,
                "bytes_decompressed": 1_048_576,
                "source_seek_count": 2,
                "unpacked_bytes": 1_048_576,
                "stdlib_wall_s": None,
                "wall_ratio": None,
                "notes": "solid invariant",
            },
        ],
    }
    report = format_text_report(payload)
    assert "# Benchmark report" in report
    assert "| Case | archivey | stdlib | ratio | vs VISION |" in report
    assert "`zip_read_all`" in report
    assert "1.18×" in report
    assert "within ≤1.3×" in report
    assert "solid invariant" in report
    assert "64 × 256.0 KiB" in report or "64 × 256 KiB" in report
    assert "wall-ratio *drift*" in report


def test_wall_drift_checks_regressions_and_noise() -> None:
    """Nightly drift gate: relative+abs dual threshold; seed/missing skipped."""
    from benchmarks.harness import CaseResult, _wall_drift_checks
    from benchmarks.wall_baseline import overlapping_wall_ratio_count

    def _case(name: str, ratio: float | None) -> CaseResult:
        return CaseResult(
            case=name,
            format="zip",
            operation="read_all",
            wall_s=0.02,
            bytes_decompressed=100,
            source_seek_count=1,
            wall_ratio=ratio,
        )

    previous = {
        "results": [
            {"case": "zip_read_all", "wall_ratio": 1.20},
            {"case": "gzip_read_all", "wall_ratio": 1.05},
            {"case": "no_peer", "wall_ratio": None},
        ]
    }
    # Clear regression: 1.20 → 1.80 (>1.25× and +0.15 abs).
    bad = _wall_drift_checks(
        [_case("zip_read_all", 1.80), _case("gzip_read_all", 1.06)],
        previous,
    )
    assert len(bad) == 1
    assert "zip_read_all" in bad[0]

    # Small bump under both thresholds — OK (noise).
    ok = _wall_drift_checks(
        [_case("zip_read_all", 1.30), _case("gzip_read_all", 1.10)],
        previous,
    )
    assert ok == []

    # Relative yes, abs no (old small): 0.4 → 0.51 (>1.25× but +0.11 < 0.15).
    assert (
        _wall_drift_checks(
            [_case("zip_read_all", 0.51)],
            {"results": [{"case": "zip_read_all", "wall_ratio": 0.4}]},
        )
        == []
    )

    # Abs yes, relative no: 2.0 → 2.40 (+0.40 but only 1.20× < 1.25×).
    assert (
        _wall_drift_checks(
            [_case("zip_read_all", 2.40)],
            {"results": [{"case": "zip_read_all", "wall_ratio": 2.0}]},
        )
        == []
    )

    # Improvement — OK.
    better = _wall_drift_checks([_case("zip_read_all", 1.00)], previous)
    assert better == []

    # No previous / empty — seed helper returns no failures (callers fail closed).
    assert _wall_drift_checks([_case("zip_read_all", 9.0)], None) == []
    assert _wall_drift_checks([_case("zip_read_all", 9.0)], {"results": []}) == []
    assert (
        overlapping_wall_ratio_count([_case("zip_read_all", 9.0)], {"results": []}) == 0
    )

    # New case not in previous — skip.
    assert _wall_drift_checks([_case("brand_new_case", 5.0)], previous) == []


def test_wall_baseline_provenance_and_republish(tmp_path: Path) -> None:
    """measured_at age drives the 30d force-run; re-publish preserves it."""
    from datetime import datetime, timedelta, timezone

    from benchmarks.wall_baseline import (
        MEASURE_MAX_AGE_SECONDS,
        measured_at_age_seconds,
        measurement_provenance,
        republish_files,
        stamp_republish,
        wall_ratio_map,
    )

    measured = datetime(2026, 6, 1, 6, 17, tzinfo=timezone.utc)
    payload = {
        "measured_at": measured.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_run_id": "111",
        "source_sha": "abc123",
        "results": [{"case": "zip_read_all", "wall_ratio": 1.2}],
    }
    now = measured + timedelta(days=31)
    age = measured_at_age_seconds(payload, now=now)
    assert age is not None
    assert age > MEASURE_MAX_AGE_SECONDS

    stamped = stamp_republish(payload, run_id="222")
    assert stamped["measured_at"] == payload["measured_at"]
    assert stamped["source_run_id"] == "111"
    assert stamped["republish_run_id"] == "222"
    assert "republished_at" in stamped

    src = tmp_path / "benchmark-wall-realistic.json"
    src.write_text(json.dumps(payload) + "\n")
    (tmp_path / "benchmark-wall-realistic.md").write_text("# Benchmark report\n\nok\n")
    out = tmp_path / "out"
    republish_files(src, out, run_id="333")
    written = json.loads((out / "benchmark-wall-realistic.json").read_text())
    assert written["measured_at"] == payload["measured_at"]
    assert written["republish_run_id"] == "333"
    md = (out / "benchmark-wall-realistic.md").read_text()
    assert "Re-published without re-measurement" in md
    assert "ok" in md

    fresh = measurement_provenance(run_id="444", sha="deadbeef")
    assert fresh["source_run_id"] == "444"
    assert fresh["source_sha"] == "deadbeef"
    assert fresh["measured_at"].endswith("Z")

    assert wall_ratio_map({"results": [{"case": "a", "wall_ratio": True}]}) == {}
    assert measured_at_age_seconds({"results": []}) is None
