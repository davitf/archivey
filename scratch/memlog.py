"""Scratch pytest plugin: per-test memory log (not for any PR)."""

import gc
import os
import time

import pytest

_out = None
_prev = 0
_prev_big = 0
_dumped = False


def _status():
    d = {}
    with open("/proc/self/status") as f:
        for line in f:
            k, _, v = line.partition(":")
            if k in ("VmRSS", "VmSize", "VmHWM", "Threads"):
                d[k] = int(v.split()[0])
    try:
        d["fds"] = len(os.listdir("/proc/self/fd"))
    except OSError:
        pass
    return d


def _maps():
    """(size_mb, perms, name, range) of each private writable mapping."""
    out = []
    with open("/proc/self/maps") as f:
        for line in f:
            parts = line.split()
            lo, hi = (int(x, 16) for x in parts[0].split("-"))
            perms = parts[1]
            name = parts[5] if len(parts) > 5 else "[anon]"
            if "w" in perms and perms.endswith("p"):
                out.append(((hi - lo) >> 20, perms, name, parts[0]))
    return out


def pytest_configure(config):
    global _out
    wid = os.environ.get("PYTEST_XDIST_WORKER", "main")
    _out = open(f"memlog-{wid}.tsv", "w", buffering=1)


def _meminfo():
    keep = ("CommitLimit", "Committed_AS", "MemAvailable")
    d = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, _, v = line.partition(":")
            if k in keep:
                d[k] = int(v.split()[0]) // 1024
    return d


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    global _dumped
    outcome = yield
    exc = call.excinfo
    if _dumped or exc is None:
        return
    if isinstance(exc.value, OSError) and exc.value.errno == 12:
        _dumped = True
        m = sorted(_maps(), reverse=True)[:12]
        wid = os.environ.get("PYTEST_XDIST_WORKER", "main")
        with open(f"enomem-{wid}.txt", "w") as f:
            f.write(f"{item.nodeid} {call.when}\n{_status()} {_meminfo()}\n")
            for row in m:
                f.write(f"{row}\n")


def pytest_runtest_logfinish(nodeid, location):
    global _prev, _prev_big
    s = _status()
    rss = s.get("VmRSS", 0) // 1024
    maps = _maps()
    big = max((r[0] for r in maps), default=0)
    total_priv = sum(r[0] for r in maps)
    extra = ""
    if os.environ.get("MEMLOG_GCSTATS"):
        try:
            st = gc.get_stats()
            extra = f"\t{st.total_gc_memory}\t{st.total_arena_memory}\t{st.total_rawmalloced_memory}"
        except Exception as e:  # noqa: BLE001
            extra = f"\tna:{type(e).__name__}"
    _out.write(
        f"{time.time():.0f}\t{rss}\t{rss - _prev}\t{s.get('VmSize', 0) // 1024}"
        f"\t{s.get('fds', 0)}\t{big}\t{nodeid}\t{total_priv}\t{big - _prev_big}{extra}\n"
    )
    _prev = rss
    _prev_big = big
