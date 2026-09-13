## Why

Three formats archivey supports are not detected on inputs their own decoders accept.
All three are reproduced on `main` (`e54eff7`), and in every case `open_archive` works
once the format is forced — only detection refuses, which is the same shape as the
`zipapp` gap `prefixed-archive-detection` exists to close:

| input | `detect_format` | decoder |
| --- | --- | --- |
| zstd stream behind a skippable frame | `FormatDetectionError` | `backports.zstd` decompresses it |
| zlib stream with any window below 32 KiB (headers `18 95`, `28 91`, `38 8d`, `48 89`, `58 85`, `68 81`) | `FormatDetectionError` | `zlib` decompresses it |
| LZMA Alone with `dict_size == 0` | `FormatDetectionError` | `lzma` decompresses it, and the spec allows the value |

These are false **negatives** — the population `VISION.md` names as the founding workload
is a backup corpus, where an unopenable file is the failure that matters. They are also
cheap: each is a few lines of structural parsing, and none needs the evidence redesign in
`dev-docs/investigations/archive-format-detection-algorithm.md` §5, which names all three.

The Alone one cannot be fixed alone. Its guard is an **ordering workaround**, and its own
comment says so: `dict_size == 0` is rejected partly to stop a zero-filled ISO system area
decoding as an empty Alone stream before ISO's far magic runs. Verified by lifting the
guard on `main` — a zero-system-area ISO then detects as `LZMA_ALONE` / `PROBABLE`. So the
false negative is a symptom of detection consulting its weakest signal before its most
exact one, and the fix is the reorder.

## What Changes

- **Far magic runs before the content probes.** Exact magic at a fixed offset (today ISO's
  `CD001` at 32 769) SHALL be consulted before the magic-less probes, size-gated so a
  source too short for the window pays nothing. This closes a live silent wrong answer of
  its own — a bootable ISO, whose reserved 32 KiB system area holds bootloader code, is
  detected as `BROTLI` and opened as one fabricated `*.uncompressed` member while the
  filesystem stays readable by other tools.
- **Replace the LZMA Alone `dict_size != 0` guard with a zero-*size* one.** Zero is a legal
  32-bit dictionary value; the LZMA specification requires decoders to round values below
  4 KiB up to 4 KiB, and a Python-produced Alone stream still decodes after its dictionary
  field is zeroed. Removing it needs the reorder above, which is why they ship together —
  and, found at implementation, it also needs a replacement: 18 zero bytes are a *valid,
  complete, empty* Alone stream, so lifting the guard alone made every zero-filled source
  larger than the peeked prefix detect as `LZMA_ALONE`. The probe now refuses a header
  declaring an uncompressed size of exactly zero, which carries no payload to open. The
  two fields are independent, so the false negative is still fixed (design §5).
- **Widen the zlib probe gate to the RFC 1950 grammar** — `CM == 8`, `CINFO <= 7`,
  `(CMF * 256 + FLG) % 31 == 0`, `FDICT` accounted for — replacing the four-entry allow-list
  `78 01 / 78 5e / 78 9c / 78 da`, which recognises only 32 KiB-window, no-dictionary
  streams. Six of the seven legal window sizes are missed today.
- **Recognise zstd skippable frames preceding the first regular frame.** A frame whose
  magic is in `0x184D2A50 .. 0x184D2A5F` declares its own payload size, so detection walks
  the declared sizes and requires a regular `28 B5 2F FD` frame after them. Skippable
  frames alone are not a zstd claim.

No **BREAKING** change: every case is a `FormatDetectionError` today, so nothing that
currently succeeds changes its answer.

## Capabilities

### New Capabilities

### Modified Capabilities

- `format-detection` — the detection algorithm's step order gains far magic ahead of the
  content probes; the magic-byte table states zstd's skippable-frame prefix; the
  magic-less-probe requirement drops the Alone zero-dictionary rejection and states zlib's
  gate as the RFC 1950 grammar rather than an allow-list.

## Decisions

- **The reorder ships here, not in `prefixed-archive-detection`.** #257's Impact claims the
  same move, but that change is now sequenced after the evidence-ledger work and the Alone
  fix cannot wait for it. Hoisting the reorder makes this change self-contained and leaves
  #257 smaller. #257's revised delta drops its far-magic paragraph; recorded in its
  Sequencing rather than left to be discovered at archive time.
- **Widening zlib is a deliberate trade, taken in the false-negative direction.** The gate
  goes from 4 accepted headers (about 2 to the minus 14 on random data) to 66 (about 2 to
  the minus 10), a 16.5-fold increase in header-level false-positive surface, and the
  compensating rule — grading a stored-block-only decode on its header alone — belongs to
  `detection-evidence-ledger`. A probe hit already decodes and already stamps
  `format_unconfirmed` on failure, and a missed real stream is a hard failure while a
  probe false positive is a graded one.
- **Skippable-frame-only sources are not zstd.** They carry no compressed payload, so
  reporting `ZST` would produce an empty fabricated member. Requiring a regular frame after
  the walk keeps identification tied to something openable.

## Impact

- Modules: `src/archivey/internal/detection.py` (`_detect_format_body` step order),
  `src/archivey/internal/streams/codecs.py` (`_alone_header_plausible`, `_ZLIB_HEADERS` and
  `ZlibCodec.content_probe`, `ZstdCodec` magic/structural check).
- Public API: unchanged. `detect_format` returns a `FormatInfo` on three input classes that
  raise today.
- Tests: a skippable-first zstd fixture (including several chained skippable frames); zlib
  streams for every legal `CINFO` and one with `FDICT`; an Alone stream with a zeroed
  dictionary field; the bootable-ISO reorder case (boot-code-shaped system area) as a
  regression pin, and a zero-system-area ISO to show the Alone guard's removal does not
  reintroduce it.
- Docs: `docs/formats.md` §Detection opens with "Magic bytes first, then extension", which
  never mentioned the probes or far magic; state the order the implementation now has.
