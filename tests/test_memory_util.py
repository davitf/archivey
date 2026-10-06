"""The memory-measuring helper keeps the garbage collector's own allocations out."""

from __future__ import annotations

import gc

from tests.memory_util import traced_peak


def test_traced_peak_leaves_out_collections_over_a_large_live_heap() -> None:
    """A collection that comes due inside the window adds nothing to the peak.

    The run allocates 5000 lists while 300 000 objects are alive and a collection is
    due every few hundred allocations. On free-threaded 3.13 without the collector
    disabled, the peak was 1.5 MB against 340 KB with no large heap. The GIL builds
    and free-threaded 3.14 pass either way; this fails only where the cost exists.
    """

    def allocate() -> None:
        kept = [[] for _ in range(5000)]
        del kept

    allocate()
    baseline = traced_peak(allocate)
    heap = [{"k": [index]} for index in range(150_000)]
    threshold = gc.get_threshold()
    # A second threshold of 0 makes every first-generation trigger collect, however
    # many objects are alive; the free-threaded build otherwise waits for a quarter of
    # the live heap.
    gc.set_threshold(1, 0)
    try:
        peak = traced_peak(allocate)
    finally:
        gc.set_threshold(*threshold)
    del heap
    assert peak < baseline * 5 // 4, (peak, baseline)


def test_traced_peak_restores_the_collector() -> None:
    assert gc.isenabled()
    traced_peak(lambda: None)
    assert gc.isenabled()
    gc.disable()
    try:
        traced_peak(lambda: None)
        assert not gc.isenabled()
    finally:
        gc.enable()
