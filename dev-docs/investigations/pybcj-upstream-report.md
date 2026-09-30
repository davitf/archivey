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

What archivey ships instead, which archives were affected, the full-size measurements and
the reproduction recipe are in [Archivey context](#archivey-context) at the end of this page.
The standing rule is in [`formats/7z.md`](../formats/7z.md) §2.3 and §6.

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

---

## Archivey context

Found in the 7z sweep. Two ordinary archives, written by 7-Zip, verified by `7z t` and
read back correctly by 7-Zip itself, were unreadable while archivey ran BCJ branch filters
through `pybcj`. Archivey now decodes every branch filter through liblzma, for every folder
shape, and `pybcj` is out of the `[recommended]` extra.

### Which archives reached pybcj

Measured by spying on `bcj.BCJDecoder` while archivey read a small archive of each shape,
so the rows say where a `pybcj` stage was built at all, not only where 2 GiB was reached:

| Folder coders | pybcj stage built with | Affected |
| --- | --- | --- |
| `BCJ` + `LZMA` (LZMA1) | the member's unpack size | yes |
| `BCJ` + `PPMd` | the member's unpack size | yes |
| `BCJ` + `BZip2` | the member's unpack size | yes |
| `BCJ` + `Deflate` | the member's unpack size | yes |
| `BCJ` + `Copy` | the member's unpack size | yes |
| `BCJ` + `LZMA2` | *(none built)* | **no** |

`BCJ` + `LZMA2` was exempt because `sevenzip_pipeline._plan_lzma_family` folds the branch
filter into one liblzma chain there (`[FILTER_X86, FILTER_LZMA2]`). Confirmed at full size:
the same 2.1 GiB payload written with `7z a -m0=BCJ -m1=LZMA2` read back through archivey
in 28 s to a SHA-256 matching the source. The other pairs staged BCJ separately: LZMA1
because of the BPO-21872 truncation `formats/7z.md` §2.3 documents, the rest because
liblzma will not run a raw chain whose only filter is a BCJ
(`lzma.LZMADecompressor(FORMAT_RAW, [{"id": FILTER_X86}])` raises
`LZMAError: Invalid or unsupported options`).

With `pybcj`, `reader.open(member)` on a 2 GiB-plus member raised the builtin
`OverflowError` from `sevenzip_pipeline.open_folder_pipeline` → `_execute_stage`, before a
byte was read; listing worked. `OverflowError` is not an `ArchiveyError`, and
`SevenZipReader._translate_exception` maps only `EOFError`, so `except ArchiveyError`
missed a failure on a valid archive. A crafted archive reaches the same call more cheaply,
since a header may declare a multi-GiB unpack size behind a few hundred bytes of pack
data, but the bug needs no crafting. The IA64 truncation surfaced as `TruncatedError` on
an archive `7z x` extracts byte for byte.

### What archivey does instead

A BCJ coder inside an LZMA2 chain is folded into that chain. A BCJ coder staged on its own
(after LZMA1, after a non-LZMA codec, or alone) runs as a raw liblzma chain of
`[<branch filter>, FILTER_LZMA2]` with its input framed as LZMA2 uncompressed chunks
(`_Lzma2Framer` in `streams/decompress.py`). The framing exists only because liblzma
rejects a chain whose last filter is not a compression filter; it compresses nothing and
costs 3 bytes per 64 KiB, 0.005% of the payload. The declared unpack size does not reach
the filter; it decides only whether the stream finished.

Measured on the 2.1 GiB BCJ+LZMA1 archive: 2 254 857 830 bytes in 30.7 s, SHA-256 matching
the source, against 28.0 s for the same payload as BCJ+LZMA2.

Besides clamping and per-window decoding (report 1), the third workaround is `pybcj`'s own
pure-Python fallback, `bcj._bcjfilter`. It accepts a 3 GiB stream size, but runs at
3.2 MiB/s against the C extension's 582 MiB/s on the same x86-like data (182x slower,
roughly eleven minutes for a 2.1 GiB member), and its output is not equivalent (report 2).

### Reproduction

Needs about 2.5 GB of free disk and a few minutes of CPU. The IA64 case needs neither.

```bash
python - <<'PY'
pat = bytes([0x8B, 0x45, 0xF8, 0xE8, 0x10, 0x20, 0x00, 0x00,
             0x89, 0x45, 0xFC, 0xE9, 0x00, 0x01, 0x00, 0x00])
chunk = (pat * (1 << 16))[: 1 << 20]
total = int(2.1 * (1 << 30))          # 2 254 857 830 bytes
with open("big.bin", "wb") as f:
    written = 0
    while written < total:
        n = min(len(chunk), total - written)
        f.write(chunk[:n])
        written += n
PY
7z a -m0=BCJ -m1=LZMA big_bcj_lzma1.7z big.bin
7z t big_bcj_lzma1.7z                            # Everything is Ok

head -c 2911 big.bin > ia64.bin
7z a -m0=IA64 -m1=LZMA ia64.7z ia64.bin
```

Archivey reads both. A reader built on `pybcj` raises
`OverflowError: signed integer is greater than maximum` opening the first member and gets
2896 of 2911 bytes from the second.

| | |
| --- | --- |
| Payload | 2 254 857 830 bytes (2.1 GiB) of repeating x86-like code |
| Archive | 93 760 017 bytes, `Method = BCJ LZMA:24`, one folder, `7z t` Everything is Ok |
| LZMA2 control | same payload, `Method = BCJ LZMA2:24`, 93 760 614 bytes |
| Measured | CPython 3.11.15, pybcj 1.0.7, py7zr 1.1.3, 7-Zip 23.01, Linux x86-64 |

The regression tests are in `tests/test_sevenzip_reader.py`: the IA64 case round-trips a
2911-byte member through `7z`, and the 2 GiB case pins `FilterDecoder` against an
`unpack_size` of 2^31 without building a fixture, since the size does not reach the filter.
