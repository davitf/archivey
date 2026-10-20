#!/usr/bin/env python3
"""Per-member cost and throughput, fitted from a ladder of archives.

A whole-archive wall time mixes three costs: a fixed one per open, one per member, and
one per byte of data. A single fixture only gives their sum, so a ratio against the
stdlib on it says little about an archive of a different shape. This module times a
ladder of archives, from many tiny members to a few large ones, and fits

    list_ms = open_ms + members x list_us
    read_ms = open_ms + members x read_us + MiB / throughput

``open_ms`` and ``list_us`` come from listing the two end rungs. ``read_us`` and the
throughput come from reading the same two rungs with ``open_ms`` held fixed: the tiny
rung has almost no bytes, so it fixes the per-member cost, and the large rung has
almost no members, so it fixes the throughput. The middle rungs are not used in the
fit. Each one gets a prediction and the error against what it measured; a large error
means the linear model does not hold for that format, and the fitted numbers should
not be quoted.

Both sides are fitted the same way: archivey (``open_archive``) and the stdlib peer
(``zipfile`` / ``tarfile`` read in one forward pass). Throughput depends on the codec
and on how well the data compresses, so it is one number per ladder, here DEFLATE ZIP
and uncompressed TAR on the harness's semi-compressible payload.

Run::

    uv run python -m benchmarks.cost_model

``benchmarks.harness --mode full`` runs it too and adds a "Cost model" section to its
report and a ``cost_model`` key to its JSON. Nothing here is a gate.
"""

from __future__ import annotations

import argparse
import json
import tarfile
import tempfile
import time
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from archivey import open_archive
from benchmarks.fixtures import _payload

# (members, member size) from many tiny members to a few large ones. The ends drive
# the fit; the middle rungs check it.
LADDER: tuple[tuple[int, int], ...] = (
    (4096, 16),
    (512, 16 * 1024),
    (64, 256 * 1024),
    (4, 4 * 1024 * 1024),
)
# A middle rung whose measured time is further than this from the prediction is
# flagged in the report.
CHECK_TOLERANCE = 0.25
MIB = 1024 * 1024


@dataclass
class Rung:
    members: int
    member_size: int
    list_ms: float
    read_ms: float


@dataclass
class Fit:
    open_ms: float
    list_us_per_member: float
    read_us_per_member: float
    throughput_mib_s: float | None
    """None when the fit came out non-positive: the bytes cost too little to measure."""

    def predict_list(self, members: int) -> float:
        return self.open_ms + members * self.list_us_per_member / 1e3

    def predict_read(self, members: int, nbytes: int) -> float:
        per_mib = 1e3 / self.throughput_mib_s if self.throughput_mib_s else 0.0
        return (
            self.open_ms
            + members * self.read_us_per_member / 1e3
            + nbytes / MIB * per_mib
        )


def fit(rungs: list[Rung]) -> Fit:
    """Fit the model on the first and last rungs of ``rungs``."""
    tiny, large = rungs[0], rungs[-1]
    # Listing: two points, two unknowns.
    list_ms_per_member = (tiny.list_ms - large.list_ms) / (tiny.members - large.members)
    open_ms = tiny.list_ms - tiny.members * list_ms_per_member
    # Reading: open_ms held fixed, then two points for per-member and per-MiB.
    n1, b1, t1 = tiny.members, tiny.members * tiny.member_size / MIB, tiny.read_ms
    n2, b2, t2 = large.members, large.members * large.member_size / MIB, large.read_ms
    t1 -= open_ms
    t2 -= open_ms
    det = n1 * b2 - n2 * b1
    read_ms_per_member = (t1 * b2 - t2 * b1) / det
    ms_per_mib = (n1 * t2 - n2 * t1) / det
    return Fit(
        open_ms=open_ms,
        list_us_per_member=list_ms_per_member * 1e3,
        read_us_per_member=read_ms_per_member * 1e3,
        throughput_mib_s=1e3 / ms_per_mib if ms_per_mib > 0 else None,
    )


def _build_zip(path: Path, members: int, size: int) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for i in range(members):
            zf.writestr(f"f{i:05d}.bin", _payload(i, size))


def _build_tar(path: Path, members: int, size: int) -> None:
    import io

    with tarfile.open(path, "w") as tf:
        for i in range(members):
            data = _payload(i, size)
            info = tarfile.TarInfo(f"f{i:05d}.bin")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


def _ay_list(path: Path) -> None:
    with open_archive(path) as reader:
        list(reader.members())


def _ay_read(path: Path) -> None:
    with open_archive(path) as reader:
        for _member, stream in reader.stream_members():
            if stream is not None:
                stream.read()


def _zip_list(path: Path) -> None:
    with zipfile.ZipFile(path) as zf:
        zf.infolist()


def _zip_read(path: Path) -> None:
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if not info.is_dir():
                zf.read(info)


def _tar_list(path: Path) -> None:
    with tarfile.open(path, "r:") as tf:
        tf.getmembers()


def _tar_read(path: Path) -> None:
    with tarfile.open(path, "r:") as tf:
        for member in tf:
            if member.isfile():
                f = tf.extractfile(member)
                if f is not None:
                    f.read()


LADDERS: dict[str, dict[str, Any]] = {
    "zip": {
        "build": _build_zip,
        "suffix": ".zip",
        "peer": "zipfile",
        "peer_list": _zip_list,
        "peer_read": _zip_read,
    },
    "tar": {
        "build": _build_tar,
        "suffix": ".tar",
        "peer": "tarfile",
        "peer_list": _tar_list,
        "peer_read": _tar_read,
    },
}


def _best_ms(fns: dict[str, Callable[[], None]], rounds: int) -> dict[str, float]:
    """The fastest of ``rounds`` timings of each function, alternating their order.

    The minimum, not the median: the fit subtracts rung times from each other, and
    the minimum is the least noisy estimate of a cost with only upward noise.
    """
    for fn in fns.values():
        fn()  # warm the page cache and imports
    best = dict.fromkeys(fns, float("inf"))
    names = list(fns)
    for i in range(rounds):
        for name in names if i % 2 == 0 else reversed(names):
            t0 = time.perf_counter()
            fns[name]()
            best[name] = min(best[name], (time.perf_counter() - t0) * 1e3)
    return best


def measure_ladder(fmt: str, root: Path, *, rounds: int = 9) -> dict[str, Any]:
    """Build (or reuse) ``fmt``'s ladder under ``root``, time it, and fit both sides."""
    spec = LADDERS[fmt]
    ours: list[Rung] = []
    theirs: list[Rung] = []
    for members, size in LADDER:
        path = root / f"ladder-{members}x{size}{spec['suffix']}"
        if not path.exists():
            spec["build"](path, members, size)
        t = _best_ms(
            {
                "ay_list": lambda p=path: _ay_list(p),
                "ay_read": lambda p=path: _ay_read(p),
                "peer_list": lambda p=path: spec["peer_list"](p),
                "peer_read": lambda p=path: spec["peer_read"](p),
            },
            rounds,
        )
        ours.append(Rung(members, size, t["ay_list"], t["ay_read"]))
        theirs.append(Rung(members, size, t["peer_list"], t["peer_read"]))
    out: dict[str, Any] = {"format": fmt, "peer": spec["peer"], "sides": {}}
    for side, rungs in (("archivey", ours), (spec["peer"], theirs)):
        f = fit(rungs)
        checks = []
        for r in rungs[1:-1]:
            nbytes = r.members * r.member_size
            for op, measured, predicted in (
                ("list", r.list_ms, f.predict_list(r.members)),
                ("read", r.read_ms, f.predict_read(r.members, nbytes)),
            ):
                err = (measured - predicted) / measured if measured > 0 else 0.0
                checks.append(
                    {
                        "members": r.members,
                        "member_size": r.member_size,
                        "op": op,
                        "measured_ms": measured,
                        "predicted_ms": predicted,
                        "error": err,
                    }
                )
        out["sides"][side] = {
            "fit": asdict(f),
            "rungs": [asdict(r) for r in rungs],
            "checks": checks,
        }
    return out


def run_cost_model(root: Path, *, rounds: int = 9) -> list[dict[str, Any]]:
    root.mkdir(parents=True, exist_ok=True)
    return [measure_ladder(fmt, root, rounds=rounds) for fmt in LADDERS]


def _fmt_size(n: int) -> str:
    for unit, scale in (("MiB", MIB), ("KiB", 1024)):
        if n >= scale:
            return f"{n // scale} {unit}"
    return f"{n} B"


def _fmt_rate(us_per_member: float) -> str:
    """A per-member cost as members per second, the figure a reader can weigh
    against their own archive."""
    if us_per_member <= 0:
        return "too fast to fit"
    rate = 1e6 / us_per_member
    if rate >= 1e6:
        return f"{rate / 1e6:.1f}M members/s"
    return f"{rate / 1e3:.0f}k members/s"


def format_cost_model(results: list[dict[str, Any]]) -> list[str]:
    """Markdown lines for the harness report."""
    lines = [
        "",
        "## Cost model",
        "",
        "Fitted from the end rungs of a ladder "
        + ", ".join(f"{n} × {_fmt_size(s)}" for n, s in LADDER)
        + " (see `benchmarks/cost_model.py`). Reading includes listing; throughput "
        "is the data rate once per-member costs are taken out. Not a gate.",
        "",
        "| format | side | open | listing | reading | throughput |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    flagged: list[str] = []
    for res in results:
        for side, data in res["sides"].items():
            f = data["fit"]
            thr = f["throughput_mib_s"]
            lines.append(
                f"| {res['format']} | {side} | {f['open_ms']:.2f} ms | "
                f"{_fmt_rate(f['list_us_per_member'])} | "
                f"{_fmt_rate(f['read_us_per_member'])} | "
                + (f"{thr:.0f} MiB/s |" if thr else "too fast to fit |")
            )
            for c in data["checks"]:
                if abs(c["error"]) > CHECK_TOLERANCE:
                    flagged.append(
                        f"- {res['format']} {side} {c['op']} "
                        f"{c['members']} × {_fmt_size(c['member_size'])}: measured "
                        f"{c['measured_ms']:.2f} ms, predicted {c['predicted_ms']:.2f} ms "
                        f"({c['error']:+.0%})"
                    )
    lines.append("")
    if flagged:
        lines.append(
            f"Middle rungs off the fit by more than {CHECK_TOLERANCE:.0%} "
            "(treat the fitted numbers for these with care):"
        )
        lines.extend(flagged)
    else:
        lines.append(
            f"Every middle rung is within {CHECK_TOLERANCE:.0%} of the fit's prediction."
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-dir", type=Path, default=None)
    parser.add_argument("--rounds", type=int, default=9)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv)
    root = args.fixture_dir or Path(tempfile.mkdtemp(prefix="archivey-ladder-"))
    results = run_cost_model(root, rounds=args.rounds)
    print("\n".join(format_cost_model(results)))
    if args.json_out is not None:
        args.json_out.write_text(json.dumps(results, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
