"""Scratch: what PyPy exposes to free a decoder now, and whether it works."""

import bz2
import lzma
import struct

import _bz2
import _lzma


def biggest():
    big = 0
    total = 0
    with open("/proc/self/maps") as f:
        for line in f:
            parts = line.split()
            lo, hi = (int(x, 16) for x in parts[0].split("-"))
            if "w" in parts[1] and parts[1].endswith("p"):
                total += (hi - lo) >> 20
                big = max(big, (hi - lo) >> 20)
    return f"biggest={big}MB private_total={total}MB"


print("lzma m memusage:", [n for n in dir(_lzma.m) if "mem" in n])
print("lzma dec attrs:", [a for a in vars(lzma.LZMADecompressor())])
print("bz2 module:", [n for n in dir(_bz2) if not n.startswith("__")])
d = bz2.BZ2Decompressor()
print("bz2 dec attrs:", [a for a in vars(d)] if hasattr(d, "__dict__") else dir(d))

written = bytearray(lzma.compress(b"hello" * 100, format=lzma.FORMAT_ALONE))
written[1:5] = struct.pack("<L", 0xFFFFFFFF)
d = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
print(len(d.decompress(bytes(written))), "alone 4GiB live", biggest())
_lzma.ffi.release(d.lzs)
print("after ffi.release", biggest())
try:
    _lzma.ffi.release(d.lzs)
    print("second release ok")
except Exception as e:
    print("second release", repr(e))
print("memusage" , hasattr(_lzma.m, "lzma_memusage"))

b = bz2.compress(b"x" * 1000)
for i in range(400):
    bz2.BZ2Decompressor().decompress(b)
print("400 bz2 dropped", biggest())
