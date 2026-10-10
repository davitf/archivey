# The Brotli probe decodes up to the compressed block its chain walk stops at

## Why

A scan of 148 255 local files with no extension found 10 false content-probe claims, all
`BROTLI` / `GUESS`: 5 OLE files (stopped since by the OLE signature), 4 CPython 3.11 and
3.14 `.pyc` files over about 269 KiB, and 1 PostScript Type 1 font. Synthetic bodies
built from libmagic signatures gave about 100 more, and uniform random data over 64 KiB
is claimed 1.2 to 1.3 % of the time.

All of them have the same shape. The first bytes parse as an uncompressed or metadata
meta-block whose declared length runs past the 4 KiB window, and the source is over the
64 KiB completion window, so the probe never sees the whole source. The chain walk
follows the declared lengths, reaches a header that parses as compressed, and stops: it
cannot check a compressed block without decoding it. A real decoder fails within 256
bytes of that header in every case.

## What changes

- When the chain walk stops at a compressed block that the window decode did not reach,
  `BrotliCodec.content_probe` decodes the source from offset 0 to 4 KiB past that
  block's header (`CHAIN_DECODE_MARGIN`), and rejects on a decode error. A real stream
  decodes cleanly or runs out of input there, and is accepted.
- The decode stays within the walk's reach: it does not run when the end would pass
  1 MiB (`PROBE_READ_AT_MAX_OFFSET_NONSEEKABLE`), and then the walk's verdict stands.
- It is charged to `max_decode_input` through a new optional probe argument,
  `charge_decode(n)`, and does not run past the budget's read ceiling. When it does not
  run for budget reasons, `content_probe_decode` is recorded *budget exhausted*. Under
  `FAST` (64 KiB of decoding) most of these claims stay.
- `PrefixWorkspace.read_at` takes the part of a seek read the prefix already holds from
  the prefix, so the decode does not fetch those bytes twice.
- A probe's sample is served 4 KiB per read. The Brotli decoder replays a failed call one
  byte at a time to tell trailing bytes from damage; with its own 64 KiB reads, a 1 MiB
  sample took a second to reject.

Not done, by decision: stopping signatures for `.pyc` or PFB (the pyc magic changes with
every CPython version, and the PFB header length depends on the file), and a header-only
variant that parses the compressed block's Huffman tables without decoding.

## Impact

- `format-detection`: the chain-walk requirement gains the decode; the OLE and COFF
  rows of the framing-gate and chain-walk matrices say the probe now rejects them.
- `detection-cost`: the decode is a fourth tier on `max_decode_input`, bounded by the
  read ceiling, with its own skip record; `read_at` reuses prefix bytes.
- `compressed-streams`: the bounded read facility also serves this decode; the new
  `charge_decode` facility.
- Code: `brotli_codec.py`, `brotli_framing.py` (`walk_chain`, `probe_bytes_at`),
  `codecs/base.py`, `detection.py`, `detection_workspace.py`, `registry.py`.
