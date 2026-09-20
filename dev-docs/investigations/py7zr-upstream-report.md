# py7zr upstream report — LZMA1+IA64 extraction hangs forever

Status: **not yet filed** (no matching issue at <https://github.com/miurahr/py7zr/issues>
as of 2026-09-19). Tracked internally; lower priority than the libraries archivey still
depends on, because archivey does not use py7zr to read archives.

Measured 2026-09-19 against py7zr 1.1.3. py7zr also inherits pybcj report 1 (the 2 GiB
decoder ceiling) unchanged — that one is pybcj's to fix, and is in
[`pybcj-upstream-report.md`](pybcj-upstream-report.md). What follows is py7zr's own.

---

## Report: `LZMA1` + `IA64` extraction spins forever at 100% CPU

**Title:** Extracting a 7z folder with the IA64 branch filter over LZMA1 never returns

**Body:**

py7zr decides per coder whether a branch filter runs inside the liblzma chain or as a
separate pybcj stage. The separate stage is correct and necessary when the chain's
compressor is not LZMA2, because liblzma can silently truncate a branch filter's trailing
look-ahead when LZMA1 carries no end-of-stream marker — which is what the 7-Zip CLI writes.
`compressor.py:634` calls this out as the "hack for LZMA1+BCJ which should be
native+alternative", and it works.

**IA64 is missing from every list that drives that decision** — `compressor.py:620`,
`:767` and `:836` each name `FILTER_X86`, `FILTER_ARM`, `FILTER_ARMTHUMB`,
`FILTER_POWERPC` and `FILTER_SPARC` — and `algorithm_class_map` (`compressor.py:560`) has
no `FILTER_IA64` entry to fall back to either. So `LZMA1` + `IA64` is the one combination
that still goes into a single combined liblzma chain, where the last bytes never arrive.

The result is not an error. `py7zr.py:1507` is:

```python
while out_remaining > 0:
    tmp = decompressor.decompress(fp, min(out_remaining, max_block_size))
    if len(tmp) > 0:
        out_remaining -= len(tmp)
        ...
```

Once the chain stops producing, `tmp` is empty on every iteration, `out_remaining` never
reaches zero, and the loop spins at 100% CPU with no timeout and no way for the caller to
tell that anything is wrong.

### Reproduction

```bash
python3 - <<'EOF'
block = bytes([0x8B,0x45,0xF8,0xE8,0x10,0x20,0x00,0x00,
               0x89,0x45,0xFC,0xE9,0x00,0x01,0x00,0x00])
open("payload.bin", "wb").write((block * 200)[:2911])
EOF

7z a -t7z -m0=IA64 -m1=LZMA ia64.7z payload.bin
7z t ia64.7z        # Everything is Ok
```

```python
import py7zr
with py7zr.SevenZipFile("ia64.7z", "r") as z:
    z.extract(path="out", targets=["payload.bin"])   # never returns
```

`faulthandler.dump_traceback_later()` pins it in that loop:

```
File ".../py7zr/py7zr.py", line 1508 in decompress
File ".../py7zr/py7zr.py", line 1449 in _extract_single
File ".../py7zr/py7zr.py", line 1377 in extract_single
File ".../py7zr/py7zr.py", line 1292 in extract
```

The other five branch filters extract the same payload correctly (`-m0=BCJ`, `ARM`, `ARMT`,
`PPC`, `SPARC`), which is what makes the missing list entry visible: only IA64 differs, and
only because it never reaches the pybcj stage the others do.

2911 bytes is not special — any length that is not a multiple of 16 reproduces it. A length
that is a multiple of 16 leaves nothing in the filter's look-ahead and extracts normally.

### Suggested fixes

Two independent changes, both worth making:

1. **Give the loop an exit.** Break out of `while out_remaining > 0` when a `decompress()`
   call returns nothing and the source is exhausted, and raise. A hang is strictly worse
   than an error, and this also covers any future coder that stops producing early.
2. **Add IA64 to the branch-filter lists.** `compressor.py:620`, `:767` and `:836`, plus a
   `FILTER_IA64` entry in `algorithm_class_map`, so IA64 is staged separately like the
   other five. Note this depends on pybcj's `IA64Decoder` being fixed first: it currently
   drops the trailing partial 16-byte block, which would turn the hang into silently short
   output. See `pybcj-upstream-report.md` report 3. Fix 1 is safe to make on its own and
   does not wait for anything.

### Environment

py7zr 1.1.3, pybcj 1.0.7, CPython 3.11.15, Linux x86-64, 7-Zip 23.01.
