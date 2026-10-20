"""Scratch pytest plugin: per-test memory log (not for any PR)."""

import gc
import os
import time

_out = None
_prev = 0


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


def pytest_configure(config):
    global _out
    wid = os.environ.get("PYTEST_XDIST_WORKER", "main")
    _out = open(f"memlog-{wid}.tsv", "w", buffering=1)


def pytest_runtest_logfinish(nodeid, location):
    global _prev
    s = _status()
    rss = s.get("VmRSS", 0) // 1024
    extra = ""
    if os.environ.get("MEMLOG_GCSTATS"):
        try:
            st = gc.get_stats()
            extra = f"\t{st.total_gc_memory}\t{st.total_arena_memory}\t{st.total_rawmalloced_memory}"
        except Exception as e:  # noqa: BLE001
            extra = f"\tna:{type(e).__name__}"
    _out.write(
        f"{time.time():.0f}\t{rss}\t{rss - _prev}\t{s.get('VmSize', 0) // 1024}"
        f"\t{s.get('fds', 0)}\t{s.get('Threads', 0)}\t{nodeid}{extra}\n"
    )
    _prev = rss
