"""Temporary PyPy diagnostics for rapidgzip; removed before review."""

import bz2
import gzip
import io
import subprocess
import sys

CASES = {
    "gz bytesio p1": "rapidgzip.open(io.BytesIO(GZ), parallelization=1)",
    "gz bytesio p0": "rapidgzip.open(io.BytesIO(GZ), parallelization=0)",
    "gz path p1": "rapidgzip.open('t.gz', parallelization=1)",
    "gz path p0": "rapidgzip.open('t.gz', parallelization=0)",
    "gz pyreader p1": "rapidgzip.open(PyReader(GZ), parallelization=1)",
    "gz pyreader p0": "rapidgzip.open(PyReader(GZ), parallelization=0)",
    "bz2 bytesio p0": "rapidgzip.IndexedBzip2File(io.BytesIO(BZ), parallelization=0)",
    "bz2 path p0": "rapidgzip.IndexedBzip2File('t.bz2', parallelization=0)",
    "child path": "RapidgzipChildStream('t.gz', label='x', max_memory=None)",
    "child stream": "RapidgzipChildStream(open('t.gz','rb'), label='x', max_memory=None)",
    "child bz2 path": "RapidgzipChildStream('t.bz2', label='x', bzip2=True, max_memory=None)",
}

PRELUDE = """
import io, rapidgzip
from archivey.internal.streams.codecs.rapidgzip_child import RapidgzipChildStream
GZ = open('t.gz','rb').read()
BZ = open('t.bz2','rb').read()
class PyReader(io.RawIOBase):
    def __init__(self, data):
        self._b = io.BytesIO(data)
    def readable(self): return True
    def seekable(self): return True
    def readinto(self, buf):
        view = memoryview(buf).cast('B')
        chunk = self._b.read(len(view))
        view[:len(chunk)] = chunk
        return len(chunk)
    def seek(self, o, w=0): return self._b.seek(o, w)
    def tell(self): return self._b.tell()
"""

data = b"hello world " * 100000
open("t.gz", "wb").write(gzip.compress(data))
open("t.bz2", "wb").write(bz2.compress(data))
for name, expr in CASES.items():
    code = PRELUDE + f"\nf = {expr}\nprint(len(f.read()))\nf.close()\n"
    proc = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = (proc.stdout + proc.stderr).strip().splitlines()
    print(f"== {name}: rc={proc.returncode}")
    for line in out[-12:]:
        print("   ", line[:200])
