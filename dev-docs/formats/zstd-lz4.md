# zstd and LZ4

Current maintainer truth for Zstandard (`.zst`) and the LZ4 frame format (`.lz4`) as
single-file formats and inside TAR, and for zstd as a ZIP method and LZ4 as a 7z coder.
They share a page because they share a shape: frames that can be concatenated, an optional
content size and an optional checksum in each frame, the same range of skippable frames,
and a decoder library archivey drives as a black box. What they share with the other
codecs, the one-member reader, the seek table and the truncation contract, is on
[`single-file.md`](single-file.md). Registers keep the status; this page states the
behaviour and links the row.

## At a glance

| | |
| --- | --- |
| Read | zstd and LZ4 frames as one-member archives and inside `.tar.zst` / `.tar.lz4`; zstd as a ZIP method; LZ4 as a 7z coder |
| Write | **Not shipped** |
| Backends | zstd: the standard library's `compression.zstd` on Python 3.14 and later, `backports.zstd` from `[recommended]` before it. LZ4: `lz4.frame` from `[recommended]` |
| Seeking | A backward seek decodes again from the start |
| Size | `None`, even when the frame header declares it |
| Digests | None listed. A frame's content checksum, when present, is checked on read |
| Metadata | None beyond the shared fields |
| Truncation | Always raised, as `TruncatedError` |
| Refuses | zstd: a frame whose window is over 128 MiB, as `CorruptionError` (§5). LZ4: the legacy frame format is not detected |

**Four things a reader might expect and will not find.** `member.size` is `None` for a
frame that records its content size. `DecoderLimits.max_decoder_memory` does not govern
zstd: the library's own 128 MiB window limit does, and a frame over it fails as corruption,
not as a resource limit (§5). A zstd or LZ4 frame written without a checksum can decode
damaged data to wrong bytes with no error. And the legacy LZ4 format that `lz4 -l` writes,
used for Linux kernel images, is not recognised (§3).

## 1. Shape

Three properties generate most of this page.

```
zstd frame:   28 b5 2f fd  frame header (window, [dict id], [content size])
              blocks …  [content checksum: xxh64 low 32 bits]
LZ4 frame:    04 22 4d 18  FLG BD [content size(8)] [dict id(4)] HC
              blocks …  end mark 00 00 00 00  [content checksum: xxh32]
skippable:    50..5f 2a 4d 18  size(4)  payload      ← either format, any number, anywhere
legacy LZ4:   02 21 4c 18  blocks of 8 MiB …          ← no frame header, no checksum
```

**A file is a run of frames, and nothing counts them.** Each frame is complete in itself:
`pzstd` writes one frame per chunk, and `cat a.zst b.zst` is valid. Skippable frames can
sit before, between or after them; the seekable-zstd format stores its seek table in one.
So a size in one frame header is not the file's size, and no digest covers the whole file.

**The checksum is optional.** `zstd` writes one by default and `zstd --no-check` does not;
LZ4's content checksum is set by the writer too. Without it, a changed byte can decode to
different bytes of the same length and pass: measured by flipping one bit mid-file in a
`zstd --no-check` file, which read back with no error.

**The window is the writer's choice: up to 2 GiB from the `zstd` command.** A zstd frame header declares
its window, and the decoder needs that much memory. The `zstd` command sizes the window to
the input when it knows the input's size, so `zstd --long=31` on a file writes a small
window, and the same command reading standard input declares 2 GiB. LZ4 blocks are at most
4 MiB, and matches reach back 64 KiB.

## 2. The pipeline here

### 2.1 Identify

zstd is the magic `28 b5 2f fd` at offset 0, and LZ4 `04 22 4d 18`; each is `CERTAIN`.
The inner-TAR probe then decodes 512 bytes and upgrades a match to `TAR_ZST` or `TAR_LZ4`
([`single-file.md`](single-file.md) §2.1).

For zstd, detection also walks a leading run of skippable frames
(`streams/zstd_framing.py`): each declares its size, so the next frame's offset is
arithmetic, with nothing decoded. A regular frame must follow. A source of only skippable
frames has no payload and is not claimed, since claiming it would open a fabricated empty
member. The walk stays inside the peeked prefix: a skippable frame larger than that ends it
with no answer rather than extending the read.

LZ4 has no such walk, so an LZ4 file that starts with a skippable frame is not detected by
content; named `.lz4`, it opens through the extension. The legacy LZ4 magic is not in the
table (§3).

### 2.2 Open and list

The member has no metadata of its own: `size` is `None`, `modified` is `None`, and `hashes`
is empty. The content size an LZ4 frame (`lz4 --content-size`) or a zstd frame records is
not read, because it describes one frame and nothing says the file has one (§7).

### 2.3 Member data

**zstd.** `ZstdCodec` opens `compression.zstd.open` or `backports.zstd.open`, the same API
(ADR 0009). It reads every frame in turn, skips skippable frames and checks each content
checksum. A frame that ends early raises `EOFError`, reported as `TruncatedError`; any
`ZstdError` is `CorruptionError`, including the window refusal (§5). A backward seek
decodes again from the start, and the rewind report says so
([`single-file.md`](single-file.md) §2.3).

**LZ4.** `Lz4Codec` opens `lz4.frame.open`, which reads concatenated frames and checks the
content and block checksums when present. `EOFError` is `TruncatedError`; a `RuntimeError`
whose message starts with "LZ4" is `CorruptionError`. Dependent blocks (`lz4 -BD`) decode
normally. A backward seek decodes again from the start.

Neither codec is decoded by archivey's own engine, so neither has a seek table. A seekable
zstd reader using the frames as restart points is deferred (§7).

### 2.4 Extract

Nothing here is specific to these formats ([`single-file.md`](single-file.md) §2.4).

### 2.5 Write

Not shipped.

## 3. In the wild

Measured with the tools listed on [`single-file.md`](single-file.md) §3.

| Producer | archivey |
| --- | --- |
| `zstd`, `zstd --no-check`, `pzstd`, two frames concatenated | Reads; `size=None` |
| A zstd frame behind a skippable frame | Detected and read |
| `zstd --long=31` on a file | Reads: the window is sized to the input |
| `zstd --long=31` from standard input (2 GiB window) | **`CorruptionError: … Frame requires too much memory for decoding`** |
| A zstd file followed by `junk` | `CorruptionError: … Unknown frame descriptor`; `zstd -t` refuses it too |
| One bit flipped mid-file, `zstd` / `zstd --no-check` | `CorruptionError` from the checksum / **read with no error** |
| `lz4`, `lz4 -BD`, two frames concatenated | Reads |
| `lz4 --content-size` | Reads; `size=None` |
| An LZ4 file followed by `junk` | `TruncatedError` |
| An LZ4 frame behind a skippable frame, no extension | Not detected |
| `lz4 -l` (legacy frame, magic `02 21 4c 18`) | **Not detected**; named `.lz4` it opens by extension and fails with `CorruptionError`, stamped `format_unconfirmed` |

The legacy format is what Linux kernel images and initramfs files compressed with LZ4 use.

## 4. Threat surface

Specific to these formats; the shared items are [`single-file.md`](single-file.md) §4.

- **The window is an allocation.** zstd's own default limit, 128 MiB (a window log of 27),
  bounds it; `DecoderLimits` does not reach the zstd decoder. LZ4's memory is fixed by the
  format.
- **The decoders are native code.** Both run in the caller's process. `compression.zstd`
  is the standard library's; `lz4` is a C extension. Neither is fuzzed by archivey's own
  harness beyond the corpus.
- **No checksum, no detection.** A frame written without one gives wrong bytes for damaged
  input. The format allows it and nothing archivey can do finds the damage.
- **Skippable frames are walked without reading.** The walk is arithmetic over bytes
  already peeked; a 4 GiB skippable frame costs nothing.

## 5. Sharp edges

*Where it lives*: **format** — inherent, no implementation fixes it · **library** — an
upstream library's behaviour, fixable only there or by replacing it · **archivey** — ours.

| What you see | Where it lives | More |
| --- | --- | --- |
| `CorruptionError: … Frame requires too much memory for decoding` on a valid file | **library** / **archivey** | The frame's window is over zstd's 128 MiB default. archivey reports it as corruption and does not map it to `DecoderLimits`. Tracked internally |
| `member.size` is `None` although the frame header has a content size | **archivey** | One frame's size is not the file's (§1, §7) |
| An `lz4 -l` file is not detected, and fails when named `.lz4` | **archivey** / **library** | The legacy magic is not registered, and `lz4.frame` does not read the legacy format. Tracked internally |
| An LZ4 file starting with a skippable frame is not detected by content | **archivey** | The skippable-frame walk is zstd's only |
| A damaged `--no-check` file reads with no error | **format** | No checksum (§1) |
| Trailing junk is `CorruptionError` for zstd and `TruncatedError` for LZ4 | **library** | Each library's choice; the cross-codec picture is [`single-file.md`](single-file.md) §3 |
| A backward seek re-decodes from the start | **format** / **archivey** | No seek table for either (§7) |

## 6. Decisions

| Choice | Why | Rejected |
| --- | --- | --- |
| zstd through `compression.zstd`, with `backports.zstd` before Python 3.14 (PR #24, ADR 0009) | The standard library's API, so the backport disappears with 3.13; truncation raises; backward seeks work | `zstandard`, which returned a short read with no error on a cut frame and could not seek backward; `pyzstd`, whose API became `compression.zstd` |
| Walk skippable frames at detection, but require a regular frame (PR #270) | The seekable-zstd format puts a skippable frame first; a file of only skippable frames has no content | Registering the sixteen skippable magics as zstd, which would claim an empty file |
| Keep the walk inside the peeked prefix | A skippable frame can declare 4 GiB, and seeing past it is not worth a longer read | Extending the peek |
| LZ4 through `lz4.frame` | The maintained binding, reading concatenated frames | Writing a frame decoder |
| Do not report a frame's content size | It covers one frame, and proving there is one frame means reading to the end | Reporting it when present |

## 7. Open questions

- **Content size for a single-frame file.** A seekable source could confirm there is one
  frame by walking the frame headers and block sizes without decoding; that would give
  `size` for most files. Tracked internally.
- **Seekable zstd.** The frames of a `pzstd` file, or the seek table of the seekable-zstd
  format, are restart points. `indexed_zstd` is frame-granular and bundles the same native
  core as `indexed_bzip2`, the library ADR 0008 keeps out, so a native frame index is
  preferred. Parked in [`IDEAS.md`](../IDEAS.md) §Efficient seekable zstd.
- **The zstd window under `DecoderLimits`.** Passing the cap as the decoder's maximum window
  would make the refusal a `ResourceLimitError` a caller can lift. Tracked internally, with
  the legacy LZ4 frame.

## 8. Verify

```bash
./scripts/test.sh tests/test_single_file.py tests/test_codecs.py tests/test_detection.py \
    tests/test_seekable_streams.py tests/test_zip_native_codecs.py -k "zstd or lz4 or zst"
```

| Claim | Pinned by |
| --- | --- |
| Both read as one member | `tests/test_single_file.py::test_zstd_roundtrip`, `::test_lz4_roundtrip` |
| A cut zstd frame is `TruncatedError` | `tests/test_codecs.py::test_truncated_zstd_translates_to_truncated` |
| Skippable frames before a regular frame; alone they are not a claim; the walk stays in the prefix | `tests/test_detection.py::test_zstd_behind_one_skippable_frame`, `::test_zstd_behind_chained_skippable_frames`, `::test_zstd_skippable_frames_alone_are_not_a_zstd_claim`, `::test_zstd_skippable_frame_larger_than_the_prefix_is_not_claimed`, `::test_zstd_skippable_walk_arithmetic` |
| Backward seeks re-decode and are reported | `tests/test_seekable_streams.py::test_zstd_rewinds_and_warns_on_backward_seek`, `::test_lz4_warns_on_rewind` |
| zstd as a ZIP method | `tests/test_zip_native_codecs.py::test_zip_zstd_handbuilt_roundtrip`, `::test_zip_zstd_without_backend_raises` |
| `pyzstd` is not a runtime dependency | `tests/test_extras_imported.py::test_pyzstd_and_python_xz_are_not_in_any_extra` |

The window refusal, the legacy LZ4 frame and the LZ4 skippable-frame gap have no test; they
were measured with the tools in §3.

**Building fixtures.** `compression.zstd` or `backports.zstd` and `lz4.frame` write both
formats. `zstd`, `pzstd` and `lz4` install from the distribution.

## 9. References

- RFC 8878 (Zstandard): §3.1.1 frame header and window descriptor, §3.1.2 skippable frames
- LZ4 frame format description (github.com/lz4/lz4, `doc/lz4_Frame_format.md`), including
  the legacy frame and skippable frames
- The seekable-zstd format (github.com/facebook/zstd, `contrib/seekable_format`)
- Decisions: [ADR 0009](../decisions/0009-zstd-stdlib-backports.md) ·
  [ADR 0008](../decisions/0008-single-accelerator-rapidgzip.md) ·
  [`library-analysis.md`](../library-analysis.md) §zstd, §lz4
- Code: `internal/streams/codecs.py` (`ZstdCodec`, `Lz4Codec`) ·
  `internal/streams/zstd_framing.py`
- Handbook: [`single-file.md`](single-file.md) · [`zip.md`](zip.md) (zstd method) ·
  [`7z.md`](7z.md) (LZ4 coder) · [`tar.md`](tar.md)
