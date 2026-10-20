"""Temporary: does python-lz4 corrupt memory on PyPy? Removed before review."""

import gc
import os
import sys

import lz4.block
import lz4.frame

data = os.urandom(1000) * 300
frame = lz4.frame.compress(data, block_size=lz4.frame.BLOCKSIZE_MAX64KB)
mode = sys.argv[1]
for i in range(3000):
    if mode == "partial":
        d = lz4.frame.LZ4FrameDecompressor()
        out = d.decompress(frame[: len(frame) // 2], max_length=1000)
        out += d.decompress(b"", max_length=5000)
        del d
    elif mode == "full":
        d = lz4.frame.LZ4FrameDecompressor()
        out = d.decompress(frame + b"tail", max_length=-1)
        assert d.eof and d.unused_data == b"tail", (d.eof, d.unused_data)
        assert out == data
    elif mode == "memoryview":
        d = lz4.frame.LZ4FrameDecompressor()
        buf = bytearray(frame)
        with memoryview(buf) as view:
            out = d.decompress(view[:5000], max_length=2000)
    elif mode == "block":
        blk = lz4.block.compress(data[:8000], store_size=False)
        assert lz4.block.decompress(blk, uncompressed_size=8 * 1024 * 1024)[:10]
    elif mode == "bad":
        d = lz4.frame.LZ4FrameDecompressor()
        try:
            d.decompress(frame[:20] + b"\xff" * 2000)
        except RuntimeError:
            pass
    if i % 100 == 0:
        gc.collect()
        junk = [bytearray(i % 997 + 1) for _ in range(2000)]
print(mode, "ok")
