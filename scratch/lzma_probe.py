"""Scratch: what a dropped LZMA decoder with a big dictionary does to the address space."""

import gc
import lzma
import os
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


DICT = 1 << 30
data = lzma.compress(os.urandom(1 << 16), format=lzma.FORMAT_RAW,
                     filters=[{"id": lzma.FILTER_LZMA2, "dict_size": DICT}])
print(sys.implementation.name, "start", biggest())


def one():
    d = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA2, "dict_size": DICT}])
    d.decompress(data)
    return d


d = one()
print("one live", biggest())
del d
print("one dropped", biggest())
gc.collect()
print("after collect", biggest())
for i in range(24):
    one()
    if i % 4 == 3:
        print(f"dropped {i + 1}", biggest())
gc.collect()
print("after collect", biggest())
gc.collect()
print("after 2nd collect", biggest())

# A small Python object at the heap top after the decoders, then collect.
for i in range(8):
    one()
keep = [bytearray(100) for _ in range(10000)]
gc.collect()
print("after collect with live small objects", biggest())

pid = os.fork()
if pid == 0:
    os._exit(0)
os.waitpid(pid, 0)
print("fork ok")

if hasattr(sys, "pypy_version_info"):
    import __pypy__

    def one_pressure():
        d = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA2, "dict_size": DICT}])
        __pypy__.add_memory_pressure(DICT)
        d.decompress(data)

    for i in range(24):
        one_pressure()
        if i % 4 == 3:
            print(f"pressure dropped {i + 1}", biggest())
