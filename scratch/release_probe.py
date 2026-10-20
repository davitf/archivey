"""Scratch: what a PyPy lzma decoder does when used after its stream is released."""

import lzma
import subprocess
import sys

CASE = sys.argv[1] if len(sys.argv) > 1 else ""
if CASE:
    import _lzma

    data = lzma.compress(b"hello" * 100)
    d = lzma.LZMADecompressor()
    d.decompress(data[:20])
    _lzma.ffi.release(d.lzs)
    if CASE == "null":
        d.lzs = _lzma.ffi.NULL
    try:
        print(CASE, "decompress after release ->", len(d.decompress(data[20:])))
    except Exception as e:
        print(CASE, "decompress after release raised", repr(e))
    sys.exit(0)

for case in ("plain", "null"):
    r = subprocess.run([sys.executable, __file__, case], capture_output=True, text=True)
    print(case, "rc", r.returncode, r.stdout.strip(), r.stderr.strip()[-300:])
