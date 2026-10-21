"""Wall time of archivey against third-party readers: py7zr, rarfile, libarchive-c.

Run::

    uv run --extra all python -m benchmarks.peers --scale realistic

This is a report, not a gate. ``benchmarks.harness`` compares against the stdlib and
the nightly fails when those ratios drift; the peers here stay out of it because
their ratios do not fit its ceiling (libarchive lists a solid 7z without decoding it,
rarfile starts one ``unrar`` process per member) and libarchive-c needs the system
``libarchive`` library, which a runner may not have. A missing peer is reported as
skipped.

Each row times archivey and one peer on the same file, alternating the order, and
reports the median of each side. Before timing, ``read_all`` checks that both sides
return the same number of bytes, so a peer that silently reads less cannot look fast.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import logging
import shutil
import statistics
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from archivey import ExtractionLimits, open_archive
from benchmarks.fixtures import FixtureSet, materialize_fixtures
from benchmarks.harness import _unrar_available

Op = Callable[[Path], object]


# --- archivey ---


def _ay_list(path: Path) -> None:
    with open_archive(path) as reader:
        list(reader.members())


def _ay_read_all(path: Path) -> int:
    total = 0
    with open_archive(path) as reader:
        for _member, stream in reader.stream_members():
            if stream is not None:
                total += len(stream.read())
    return total


def _ay_extract(path: Path, dest: Path) -> None:
    # The bench payloads repeat a 4 KiB block, so solid LZMA2 packs them past the
    # default 1000:1 ratio guard. The peers have no such guard; turn it off here.
    with open_archive(path) as reader:
        reader.extract_all(dest, limits=ExtractionLimits.UNLIMITED)


# --- py7zr ---


def _py7zr_list(path: Path) -> None:
    import py7zr

    with py7zr.SevenZipFile(path) as archive:
        archive.list()


def _py7zr_read_all(path: Path) -> int:
    import py7zr
    from py7zr.io import BytesIOFactory

    factory = BytesIOFactory(limit=1 << 40)
    with py7zr.SevenZipFile(path) as archive:
        archive.extractall(factory=factory)
    return sum(f.size() for f in factory.products.values())


def _py7zr_extract(path: Path, dest: Path) -> None:
    import py7zr

    with py7zr.SevenZipFile(path) as archive:
        archive.extractall(dest)


# --- rarfile (member data through one ``unrar p`` process per member) ---


def _rarfile_list(path: Path) -> None:
    import rarfile

    with rarfile.RarFile(path) as archive:
        archive.infolist()


def _rarfile_read_all(path: Path) -> int:
    import rarfile

    total = 0
    with rarfile.RarFile(path) as archive:
        for info in archive.infolist():
            if not info.is_dir():
                total += len(archive.read(info))
    return total


def _rarfile_extract(path: Path, dest: Path) -> None:
    import rarfile

    with rarfile.RarFile(path) as archive:
        archive.extractall(dest)


# --- libarchive-c (reads RAR itself, without unrar) ---


def _libarchive_list(path: Path) -> None:
    import libarchive

    with libarchive.file_reader(str(path)) as archive:
        for entry in archive:
            _ = entry.pathname


def _libarchive_read_all(path: Path) -> int:
    import libarchive

    total = 0
    with libarchive.file_reader(str(path)) as archive:
        for entry in archive:
            if entry.isfile:
                total += len(b"".join(entry.get_blocks()))
    return total


def _libarchive_extract(path: Path, dest: Path) -> None:
    import libarchive

    with contextlib.chdir(dest):
        libarchive.extract_file(str(path))


def _peer_available(name: str) -> str | None:
    """None when ``name`` can run here, else the reason it cannot."""
    try:
        importlib.import_module({"libarchive-c": "libarchive"}.get(name, name))
    # Without the system library, libarchive-c fails in ctypes: OSError when the
    # library will not load, AttributeError when a symbol is missing.
    except (ImportError, OSError, AttributeError) as exc:
        return f"{name} not importable ({type(exc).__name__})"
    return None


PEER_OPS: dict[str, dict[str, Callable[..., object]]] = {
    "py7zr": {
        "list": _py7zr_list,
        "read_all": _py7zr_read_all,
        "extract": _py7zr_extract,
    },
    "rarfile": {
        "list": _rarfile_list,
        "read_all": _rarfile_read_all,
        "extract": _rarfile_extract,
    },
    "libarchive-c": {
        "list": _libarchive_list,
        "read_all": _libarchive_read_all,
        "extract": _libarchive_extract,
    },
}
AY_OPS: dict[str, Callable[..., object]] = {
    "list": _ay_list,
    "read_all": _ay_read_all,
    "extract": _ay_extract,
}


def _median_pair(
    ay: Callable[[], object],
    peer: Callable[[], object],
    rounds: int,
    reset: Callable[[], None],
) -> tuple[float, float]:
    """Median wall time of each side, alternating which side runs first.

    ``reset`` runs untimed before every call (extract uses it to empty the target).
    """
    times: dict[int, list[float]] = {0: [], 1: []}
    sides = (ay, peer)
    reset()
    ay()  # warmup: page cache, imports
    reset()
    peer()
    for i in range(rounds):
        for side in (0, 1) if i % 2 == 0 else (1, 0):
            reset()
            t0 = time.perf_counter()
            sides[side]()
            times[side].append(time.perf_counter() - t0)
    return statistics.median(times[0]), statistics.median(times[1])


def _cases(fx: FixtureSet) -> list[tuple[str, Path | None, list[str], list[str]]]:
    """(label, path, operations, peers). A ``None`` path is reported as skipped."""
    full = ["list", "read_all", "extract"]
    return [
        ("zip", fx.zip_path, full, ["libarchive-c"]),
        ("tar", fx.tar_path, full, ["libarchive-c"]),
        ("7z solid", fx.solid_7z, full, ["py7zr", "libarchive-c"]),
        ("7z many (list)", fx.many_7z, ["list"], ["py7zr", "libarchive-c"]),
        ("7z non-solid (list)", fx.nonsolid_7z, ["list"], ["py7zr", "libarchive-c"]),
        ("rar solid", fx.solid_rar, full, ["rarfile", "libarchive-c"]),
        ("rar many (list)", fx.many_rar, ["list"], ["rarfile", "libarchive-c"]),
        (
            "rar non-solid (list)",
            fx.nonsolid_rar,
            ["list"],
            ["rarfile", "libarchive-c"],
        ),
    ]


def run(scale: str, rounds: int) -> None:
    """Print one markdown table row per (archive, operation, peer) as it finishes."""
    fx = materialize_fixtures(scale=scale)
    work = Path(tempfile.mkdtemp(prefix="archivey-peers-"))
    print("| Archive | Operation | Peer | archivey | peer | archivey / peer |")
    print("| --- | --- | --- | ---: | ---: | ---: |", flush=True)
    try:
        for label, path, ops, peers in _cases(fx):
            for peer in peers:
                for op in ops:
                    why = _peer_available(peer)
                    if path is None:
                        why = "fixture not built (writer missing)"
                    elif label.startswith("rar") and op != "list":
                        # archivey (and rarfile) read RAR data through unrar.
                        if not _unrar_available():
                            why = why or "RARLAB unrar not on PATH"
                    if why is not None:
                        print(f"| {label} | {op} | {peer} | — | — | {why} |")
                        continue
                    assert path is not None
                    print(_row(label, path, op, peer, rounds, work), flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _row(label: str, path: Path, op: str, peer: str, rounds: int, work: Path) -> str:
    ay_op, peer_op = AY_OPS[op], PEER_OPS[peer][op]
    dest = work / "out"

    def reset() -> None:
        shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir()

    if op == "extract":
        ay, other = (lambda: ay_op(path, dest)), (lambda: peer_op(path, dest))
    else:
        ay, other = (lambda: ay_op(path)), (lambda: peer_op(path))
    if op == "read_all":
        got, want = other(), ay()
        assert got == want, f"{label}: {peer} read {got} bytes, archivey {want}"
    ay_s, peer_s = _median_pair(ay, other, rounds, reset)
    return (
        f"| {label} | {op} | {peer} | {ay_s * 1e3:.1f} ms | {peer_s * 1e3:.1f} ms "
        f"| {ay_s / peer_s:.2f}× |"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scale", choices=("ci", "realistic"), default="realistic")
    parser.add_argument("--rounds", type=int, default=7, help="timed calls per side")
    args = parser.parse_args(argv)
    # Diagnostic logs (ZIP name-encoding advisories) skew short samples; see harness.
    logging.disable(logging.CRITICAL)
    run(args.scale, args.rounds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
