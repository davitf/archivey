"""Scratch: many small xz streams, each with a fresh decoder, on PyPy."""

import lzma
import sys

def biggest():
    big = (0, "")
    total = 0
    with open("/proc/self/maps") as f:
        for line in f:
            parts = line.split()
            lo, hi = (int(x, 16) for x in parts[0].split("-"))
            perms = parts[1]
            name = parts[5] if len(parts) > 5 else "[anon]"
            if "w" in perms and perms.endswith("p"):
                size = (hi - lo) >> 20
                total += size
                if size > big[0]:
                    big = (size, name)
    with open("/proc/self/status") as f:
        rss = next(int(line.split()[1]) // 1024 for line in f if line.startswith("VmRSS"))
    return f"biggest={big[0]}MB {big[1]} private_total={total}MB rss={rss}MB"



import _lzma  # noqa: E402

VARIANT = sys.argv[1]

data = lzma.compress(b"x" * 100)


def run(label, after):
    for i in range(1, 801):
        d = lzma.LZMADecompressor(memlimit=1 << 31)
        after(d, "pre")
        d.decompress(data)
        after(d, "post")
        if i % 200 == 0:
            print(label, i, biggest())
    import gc

    gc.collect()
    print(label, "collected", biggest())


if VARIANT == "plain":
    run("plain", lambda d, w: None)
elif VARIANT == "pressure8M":
    import __pypy__

    run("pressure8M", lambda d, w: w == "pre" and __pypy__.add_memory_pressure(8 << 20))
elif VARIANT == "release":
    run("release", lambda d, w: w == "post" and _lzma.ffi.release(d.lzs))
