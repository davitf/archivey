"""Measure the memory a piece of test code allocates, without the collector's own.

Use :func:`traced_peak` for any ``tracemalloc`` peak a test bounds. Starting and
stopping ``tracemalloc`` by hand leaves the garbage collector free to run inside the
window, and on free-threaded CPython 3.13 a collection allocates working memory in
proportion to every live object in the process, which tracemalloc counts. With
300 000 live objects, as a long serial test run can leave, one collection added
0.8 MB (an explicit ``gc.collect()``) to 1.2 MB (an automatic one, with more pending
garbage) on CPython 3.13.16t. Whether the automatic collection fires inside a given
window depends on what earlier tests allocated, so a peak test can fail in the full
suite and never alone. The ISO shared-continuation-area test in
``test_audit2_iso_dir_detect.py`` did, on the free-threaded CI job, peaking at 5.3x
to 6.5x the image against a 4x bound (6.9x to 9.3x locally). On free-threaded 3.14.8
an automatic collection over the same 300 000 objects added about 110 KB (a bare
``gc.collect()`` over 600 000 containers, 28 KB), and on a GIL build almost nothing.
"""

from __future__ import annotations

import gc
import tracemalloc
from typing import Callable


def traced_peak(action: Callable[[], object]) -> int:
    """The tracemalloc peak while ``action`` runs, in bytes.

    ``gc.collect()`` first, so garbage earlier tests left, and its finalizers, stay out
    of the window. The collector is then disabled until ``action`` returns, so no
    collection runs inside the window. Cyclic garbage ``action`` itself creates stays
    counted in the peak, so a bound measured this way is no looser.

    Callers that must not count first-use costs (lazy imports, caches) run ``action``
    once before calling this.
    """
    gc.collect()
    was_enabled = gc.isenabled()
    gc.disable()
    tracemalloc.start()
    try:
        action()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
        if was_enabled:
            gc.enable()
