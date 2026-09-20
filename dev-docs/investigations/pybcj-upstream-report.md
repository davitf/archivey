# pybcj upstream report — three defects in the branch-filter decoders

Status: **not yet filed** (no matching issue at <https://github.com/miurahr/pybcj/issues>
as of 2026-09-19). Tracked internally; lower priority than the libraries archivey still
depends on, because archivey no longer calls pybcj at all.

Three reports against [miurahr/pybcj](https://github.com/miurahr/pybcj), all measured on
2026-09-19 against pybcj 1.0.7. Archivey's BCJ filters run through liblzma as of PR #370,
so none of these block archivey; they are written up because they are real, reachable on
archives 7-Zip writes and reads back correctly, and still live for every other caller.
py7zr inherits report 1 unchanged, and has a separate defect of its own —
[`py7zr-upstream-report.md`](py7zr-upstream-report.md).

Archivey context — what we ship instead, and why the two cheaper fixes below are not
options — is in `dev-docs/known-issues.md` → "7z BCJ branch filters".

---

## Report 1 (the real one): decoders cannot be constructed for streams of 2 GiB or more

**Title:** `*Decoder(size)` rejects any stream of 2 GiB or more (`OverflowError`), making
large BCJ-filtered 7z members unreadable

**Body:**

Every BCJ decoder takes the stream size as a C signed `int`, so a stream of 2 GiB or more
cannot be decoded at all — the decoder cannot even be constructed.

```python
>>> import bcj
>>> bcj.BCJDecoder(2147483647)     # 2 GiB - 1
<bcj.BCJDecoder object at 0x...>
>>> bcj.BCJDecoder(2147483648)     # 2 GiB
Traceback (most recent call last):
  ...
OverflowError: signed integer is greater than maximum
```

All six decoders behave the same way: `BCJDecoder`, `ARMDecoder`, `ARMTDecoder`,
`PPCDecoder`, `SparcDecoder`, `IA64Decoder`. A size at or above 2^63 fails a step earlier
with `Python int too large to convert to C long`, which suggests the argument is read as a
C `long` and then range-checked down to `int`.

### Why this is reachable in practice

This is not a crafted input. 7-Zip writes affected archives itself:

```bash
# any file of 2 GiB or more
7z a -m0=BCJ -m1=LZMA big.7z big.bin
7z t big.7z          # Everything is Ok
```

7-Zip reads that archive back without complaint. py7zr does not:

```python
>>> import py7zr
>>> py7zr.SevenZipFile("big.7z").extract(path="out")
Traceback (most recent call last):
  ...
  File ".../py7zr/compressor.py", line 671, in __init__
    self.chain.append(self._get_alternative_decompressor(coders[i], unpacksizes[i], password))
  File ".../py7zr/compressor.py", line 467, in __init__
    self.decoder = bcj.BCJDecoder(size)
OverflowError: signed integer is greater than maximum
```

`unpacksizes[i]` is the folder's declared unpack size, which is a 64-bit field in the 7z
format. py7zr is passing it unchanged, which is the natural thing to do; the ceiling is in
pybcj's signature. (I maintain a different 7z reader that passed the same value and failed
identically, so this is not a py7zr-specific mistake.)

`BCJ` + `LZMA2` is unaffected, because that combination is normally folded into a single
liblzma filter chain and never reaches pybcj. `BCJ` with LZMA1, PPMd, BZip2, Deflate or
Copy all reach it.

### Suggested fix

Parse the size as a 64-bit value (`unsigned long long` / `Py_ssize_t`) and store it in a
64-bit field. The value is only ever compared against a running position, so widening it
should not change any decode path. liblzma's own BCJ filters have no such limit.

### Why callers cannot work around it

Two obvious workarounds both produce wrong output, so a caller cannot paper over this:

- **Clamping the declared size** does not work, because the size is not a buffer hint — it
  is the position at which the filter releases its final look-ahead bytes. Measured over
  300 random payloads against liblzma's `FILTER_X86` as the reference: a declared size
  equal to the true size was correct 300/300; half the true size was wrong 232/300; `1` was
  wrong 257/300; an overstated size was wrong 300/300. Output was either 4 bytes short or
  the same length with wrong bytes.
- **Decoding in windows with a fresh decoder** does not work either, because the x86 filter
  is position-dependent and there is no way to tell a decoder it starts at a nonzero
  offset. A 64 KiB x86 payload split into 16 KiB windows came back with 9318 of 65536 bytes
  wrong.

A `start_offset` argument would make the second workaround viable, but widening the size
argument is the simpler fix.

### Environment

pybcj 1.0.7, py7zr 1.1.3, CPython 3.11.15, Linux x86-64, 7-Zip 23.01.

---

## Report 2 (minor): the pure-Python fallback does not match the C extension at the tail

**Title:** `_bcjfilter` pure-Python fallback output differs from the C extension in the
final bytes

**Body:**

`bcj/_bcjfilter.py` is the fallback used when `_bcj` cannot be imported, so the two should
agree byte for byte. They do not — the pure-Python decoder's last few bytes differ.

```python
import bcj, lzma
from bcj._bcjfilter import BCJDecoder as PyBCJ

pat = bytes([0x8B, 0x45, 0xF8, 0xE8, 0x10, 0x20, 0x00, 0x00,
             0x89, 0x45, 0xFC, 0xE9, 0x00, 0x01, 0x00, 0x00])
data = pat * 4096                       # 64 KiB of x86-like code

reference = lzma.LZMADecompressor(
    format=lzma.FORMAT_RAW,
    filters=[{"id": lzma.FILTER_X86}, {"id": lzma.FILTER_LZMA2}],
).decompress(bytes([0x01]) + (len(data) - 1).to_bytes(2, "big") + data + b"\x00")

print(bcj.BCJDecoder(len(data)).decode(data) == reference)   # True
print(PyBCJ(len(data)).decode(data) == reference)            # False — last 2 bytes differ
```

liblzma and the C extension agree; the pure-Python fallback does not. The divergence is
confined to the end of the stream, which points at the look-ahead release in
`BCJFilter.decode` (`if self.current_position > self.stream_size - self._readahead`).

Incidentally, the pure-Python class accepts a 3 GiB `stream_size` without complaint, so it
does not share the limit in report 1.

### Environment

pybcj 1.0.7, CPython 3.11.15, Linux x86-64.

---

## Report 3: the IA64 decoder drops the trailing partial block

**Title:** `IA64Decoder` truncates output when the stream length is not a multiple of 16

**Body:**

`IA64Decoder` silently drops the final incomplete 16-byte block, so any stream whose length
is not a multiple of 16 decodes short. liblzma's IA64 filter passes the remainder through
unfiltered, which is what 7-Zip does too.

```python
import bcj

pat = bytes([0x8B, 0x45, 0xF8, 0xE8, 0x10, 0x20, 0x00, 0x00,
             0x89, 0x45, 0xFC, 0xE9, 0x00, 0x01, 0x00, 0x00])
src = (pat * 20)[:21]                       # 21 bytes: 16 + a partial block

encoder = bcj.IA64Encoder()
filtered = encoder.encode(src) + encoder.flush()
assert len(filtered) == len(src)

decoded = bcj.IA64Decoder(len(src)).decode(filtered)
print(len(decoded))                          # 16, expected 21
assert decoded == src                        # fails
```

21 bytes is the smallest reproduction; it scales — a 2911-byte stream comes back as 2896.
The other five filters are not affected: over 120 random payloads each, x86, ARM, ARMT, PPC
and SPARC matched liblzma exactly, while IA64 differed on 80 of 120.

### Why this matters in practice

7-Zip writes such archives and reads them back correctly:

```bash
head -c 2911 /usr/bin/ls > payload.bin        # any size not a multiple of 16
7z a -m0=IA64 -m1=LZMA ia64.7z payload.bin
7z t ia64.7z                                  # Everything is Ok
```

A reader built on pybcj gets 2896 bytes for that member and can only report the archive as
truncated. `decode()` gives the caller no way to tell the difference — the shortfall looks
like a normal partial return.

### Suggested fix

Emit the trailing bytes that do not fill a block, as the C filter's `ip`-bounded loop in
liblzma does, rather than leaving them in the buffer when the stream ends.

### Environment

pybcj 1.0.7, CPython 3.11.15, Linux x86-64.
