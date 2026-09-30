# Unix compress (`.Z`)

Current maintainer truth for the `compress` format (`.Z`, LZW) as a single-file format and
inside `.tar.Z`. archivey decodes it with its own LZW decoder, adapted from `uncompresspy`,
with no dependency. The format has no end marker, no size and no checksum, so the main
thing to know is what archivey can and cannot say about a damaged or cut file. What `.Z`
shares with the other codecs, the one-member reader, the seek table and the truncation
contract, is on [`single-file.md`](single-file.md). Registers keep the status; this page
states the behaviour and links the row.

## At a glance

| | |
| --- | --- |
| Read | `.Z` as a one-member archive and inside `.tar.Z` |
| Write | **Not shipped** |
| Backends | Native: `internal/streams/unix_compress.py`, always available |
| Seeking | From the nearest CLEAR code, when the source is seekable. A file with no CLEAR code re-decodes from the start |
| Size | `None`. The format has no size field |
| Digests | None. The format has no checksum |
| Metadata | None beyond the shared fields |
| Truncation | **Best-effort.** About half of the cuts measured on a 16-bit file read as a shorter complete file with no error (§1) |
| Refuses | A header with reserved flag bits set (`UnsupportedFeatureError`); a maximum code width outside 9 to 16 (`CorruptionError`) |

**Three things a reader might expect and will not find.** A cut `.Z` file is not always
reported: the format has nothing that would show the cut. Damaged data decodes to wrong
bytes unless the damage produces an impossible code. And bytes after the data are not
reported as `ARCHIVE_TRAILING_DATA`, as they are for every other codec: with no end
marker they decode as more codes, and usually end in `TruncatedError` (§3).

## 1. Shape

Three properties generate most of this page.

```
header:  1f 9d  flags: block_mode(0x80) reserved(0x60) max_bits(0x1f)
codes:   9-bit codes …, widening by one bit each time the table fills, up to max_bits
         CLEAR (256) in block mode: reset the table to 9 bits, then pad to a group
         boundary
         …                                       ← no end marker, no trailer
```

**The stream ends where the file ends.** There is no end-of-data code, no length and no
checksum. The decoder stops when input runs out. A cut that falls on a code boundary leaves
a shorter file that is exactly as valid as a complete one. The only evidence of a cut is a
partial code left over at the end: bits that do not make a whole code and are not zero.
Once codes are 16 bits wide, every other byte is a code boundary, so about half of all cuts
leave no evidence; measured on a 3.7 MB file of 16-bit codes, 100 of 200 evenly spaced cuts
read with no error, and on a 12-bit file (`compress -b12`), 69 of 200.

**Only CLEAR codes are restart points.** In block mode the writer emits a CLEAR code when
compression gets worse, which resets the table. Decoding can restart at a CLEAR without
anything before it. How many there are depends on the data; a file can have none.

**Codes are packed in groups.** The original `compress` reads codes in groups of eight, so
after a CLEAR, or a width change, the writer pads to the end of the current group. A decoder
that does not skip the same padding reads garbage after the first CLEAR. In block mode a
width lasts a whole number of groups, so only a CLEAR leaves padding. Without block mode
(`compress -C`) the first free code is 256, not 257, so the first width lasts 257 codes and
the widening to 10 bits pads too. The padding after a CLEAR is also where a cut can be
detected even at a code boundary: a source that ends while it is still owed is cut. The
padding at a widening is written only when another code follows, so a stream may end
there with none of it; a stream that ends partway into it is cut.

## 2. The pipeline here

### 2.1 Identify

`.Z` is the magic `1f 9d` at offset 0, reported `CERTAIN`. The inner-TAR probe then decodes
512 bytes and upgrades a match to TAR over `.Z` ([`single-file.md`](single-file.md) §2.1).
`.Z` and `.tar.Z` are the registered extensions.

### 2.2 Open and list

The member has no metadata of its own: `size` is `None`, `modified` is `None`, and `hashes`
is empty ([`single-file.md`](single-file.md) §2.2).

### 2.3 Member data

`UnixCompressDecompressorStream` is `DecompressorStream` over the native LZW decoder
(`LzwState`). The header is checked first: reserved flag bits (`0x60`) are
`UnsupportedFeatureError`, since a writer that set them meant something archivey does not
know; a maximum code width below 9 or above 16 is `CorruptionError`. Then codes are decoded
forward, with no need to seek, so a pipe reads too (in streaming mode, per
[`single-file.md`](single-file.md) §2.2).

When the source is seekable, each CLEAR code becomes a seek point, and a backward seek
resumes at the nearest one. Consecutive CLEAR codes with nothing between them produce one
point, not several at the same offset.

The decoder raises archivey's errors itself; there is no library exception to translate. An
impossible code (one past the next table entry) is `CorruptionError`. At the end of input,
`TruncatedError` is armed when any of these holds:

- the source ended inside the three-byte header, including a zero-byte source;
- the source ended while CLEAR padding was still owed, or after part of a widening's
  padding;
- bits were left over after the last whole code, and they are not all zero.

Otherwise the data ends there, with no error. As with every codec, sized reads return the
prefix before the error ([`single-file.md`](single-file.md) §2.3).

### 2.4 Extract

Nothing here is `.Z`-specific ([`single-file.md`](single-file.md) §2.4).

### 2.5 Write

Not shipped.

## 3. In the wild

Measured with `ncompress` 5.0, as listed on [`single-file.md`](single-file.md) §3.

| Producer | archivey |
| --- | --- |
| `compress` | Reads |
| `compress -b12` | Reads |
| A file followed by `junk` | `TruncatedError`: the junk decodes as codes and leaves a partial code |
| A 16-bit file cut at 200 even points | 100 `TruncatedError`, 100 read short with no error |
| A 12-bit file cut at 200 even points | 131 `TruncatedError`, 69 read short with no error |
| A zero-byte `.Z` | `TruncatedError` |
| `compress -b9` | Reads without error, but a 4 KB text file came back 12 bytes short and different from byte 684. `7z` and `unar` return the same bytes; ncompress and GNU gzip 1.12 refuse the file as corrupt. See §7 |

Without block mode (`compress -C`) no installed tool writes files, so the tests carry
their own encoder. GNU gzip 1.12 reads its output like archivey at 10 to 16 bits. The
Apple gzip on macOS returned different bytes for the long zero runs, so the tests use
only GNU gzip as the reference.

## 4. Threat surface

`.Z`-specific only; the shared items are [`single-file.md`](single-file.md) §4.

- **Native code, but Python.** The decoder is archivey's own, in Python, and covered by the
  fuzzers; there is no native library to crash.
- **Memory is bounded by the decoder.** The table holds at most 2¹⁶ entries and no header
  field sizes an allocation, but an entry can be up to about 64 KiB long, so storing each
  entry as its full expansion would reach about 2 GiB. A zero run builds that shape, and
  130 KB of crafted input fills it. The decoder stores an entry of up to 256 bytes flat.
  A longer entry is a link to an earlier code plus a tail of up to 128 bytes, rebuilt on
  use by walking the links (about one step per 128 bytes of output). The table stays under
  about 19 MiB in the worst case (18.5 to 18.8 MiB measured on CPython 3.11 to 3.14; up to
  20.3 MiB on a free-threaded build, whose object headers are larger). That is well under
  the `DecoderLimits.max_decoder_memory` default, so that limit is not consulted.
- **Expansion.** Each code emits at most the longest string in the table, so one read's
  output is bounded per call like every codec's ([`single-file.md`](single-file.md) §4).
- **A cut or damaged file can pass.** The format carries no length or checksum (§1). A
  caller that needs to know a `.Z` file is whole must get the length or a digest from
  somewhere else.
- **CLEAR codes make seek points.** A file of back-to-back CLEAR codes would put several
  points at one offset, which the seek table does not allow; empty points are merged, and
  the table is capped like every codec's.

## 5. Sharp edges

*Where it lives*: **format** — inherent, no implementation fixes it · **library** — an
upstream library's behaviour, fixable only there or by replacing it · **archivey** — ours.

| What you see | Where it lives | More |
| --- | --- | --- |
| A cut `.Z` file reads as complete, with no error | **format** | No end marker (§1). Detection is best-effort |
| Damaged data reads with no error | **format** | No checksum |
| Trailing bytes after the data give `TruncatedError` | **format** | They decode as codes (§3) |
| `UnsupportedFeatureError` naming reserved flags | **archivey** | A header bit this decoder does not know; no known writer sets it |
| A backward seek re-decodes from the start | **format** | No CLEAR codes in this file, or a non-seekable source |
| `member.size` is `None` | **format** | No size field |

## 6. Decisions

| Choice | Why | Rejected |
| --- | --- | --- |
| Decode with a native LZW kernel adapted from `uncompresspy` (PR #89) | No dependency; forward decoding from a pipe; CLEAR codes as seek points | `uncompresspy` as an extra, which needed a seekable source |
| Refuse reserved flags as unsupported, not corrupt | A writer that set them may have meant a variant; the file is not shown to be damaged | Ignoring them |
| Refuse a maximum width above 16 (PR #128) | 16 is the format's ceiling in `compress` and `ncompress`; a larger width sizes a table no writer makes | Accepting it |
| Report a cut only where there is evidence | Calling every file that ends on a code boundary truncated would refuse every valid file | Guessing |
| A zero-byte source is `TruncatedError` (PR #420) | Every other codec raises on an empty source, and a `.Z` without its header is cut | An empty member |
| Merge consecutive empty CLEAR points (PR #116) | Several points at one offset broke the seek table's invariant | Asserting |

## 7. Open questions

**9-bit files.** ncompress 5.0's `-b9` output is refused by ncompress's own decoder and
by GNU gzip, and `7z`, `unar` and archivey agree with each other on it but not with the
input (§3). Without block mode at 9 bits, archivey and `7z` read the test encoder's
streams back to their input and GNU gzip refuses them. GNU gzip and ncompress appear to
move to 10-bit codes when the table fills even at `-b9` (inferred from their source, not
traced). No 9-bit file from another producer is at hand to say which reading is right,
so 9 bits is not claimed.

The truncation gap is the format's, not an open question;
[`docs/formats.md`](../../docs/formats.md) tells users it is best-effort.

## 8. Verify

```bash
./scripts/test.sh tests/test_codecs.py tests/test_single_file.py tests/test_detection.py \
    tests/test_audit_tar_streams.py -k "unix_compress or _z_"
```

| Claim | Pinned by |
| --- | --- |
| Reads, from a pipe too | `tests/test_codecs.py::test_unix_compress_backend_roundtrip`, `::test_unix_compress_non_seekable_source_streams`, `tests/test_single_file.py::test_unix_compress_non_seekable_streams_fine`, `::test_unix_compress_non_seekable_requires_streaming_mode` |
| Header checks | `tests/test_codecs.py::test_unix_compress_reserved_header_flags_unsupported`, `::test_unix_compress_maxbits_above_16_rejected`, `::test_unix_compress_maxbits_16_accepted`, `::test_unix_compress_short_source_that_is_not_z_is_corrupt` |
| The three kinds of truncation evidence | `::test_unix_compress_source_cut_inside_the_header_is_truncated`, `::test_unix_compress_cut_inside_clear_padding_is_truncated`, `::test_unix_compress_truncated_raises_on_next_read`, `::test_unix_compress_valid_stream_has_zero_leftover_padding` |
| Truncation keeps raising, after a rewind too | `::test_unix_compress_truncated_readall_raises`, `::test_unix_compress_truncated_readall_then_rewind_raises_again` |
| CLEAR codes are seek points, merged when empty | `::test_unix_compress_clear_seek_points`, `::test_unix_compress_consecutive_clear_seek_points_no_assert`, `tests/test_detection.py::test_detect_format_atheris_z_clear_collisions_do_not_assert` |
| One call's output is bounded | `tests/test_codecs.py::test_unix_compress_read_one_bounds_internal_buffer` |
| The table stays under about 19 MiB, 21 MiB on a free-threaded build (§4) | `tests/test_codecs.py::test_unix_compress_worst_case_table_stays_under_the_stated_bound`, `tests/test_audit_tar_streams.py::test_unix_compress_dictionary_memory_is_bounded` |
| Files written without block mode decode like GNU `gzip -d` at 10 to 16 bits | `tests/test_codecs.py::test_unix_compress_non_block_mode_decodes_like_the_reference`, `::test_unix_compress_non_block_mode_streams_match_gzip`, `::test_unix_compress_non_block_mode_may_end_at_a_widening`, `::test_unix_compress_non_block_mode_cut_inside_widening_padding_is_truncated` |
| Linked long entries decode exactly | `tests/test_codecs.py::test_unix_compress_long_dictionary_entries_decode_exactly`, `::test_unix_compress_repeated_longest_code_decodes_exactly` |
| `.tar.Z` is found; a bare `.Z` stays bare | `tests/test_detection.py::test_unix_compress_without_inner_tar_stays_bare_z`, `tests/test_libarchive_corpus.py::test_tar_z_detection_upgrades_via_inner_probe` |

**Building fixtures.** No Python library writes `.Z`. The `ncompress` package installs
`compress`; it is a development tool only.

## 9. References

- The `compress` format has no formal specification; `ncompress` 5.0's source is the
  reference implementation, including the code-group padding after CLEAR
- [`uncompresspy`](https://github.com/kYwzor/uncompresspy), BSD 3-Clause, the origin of the
  LZW kernel (notice at the end of `unix_compress.py`)
- Decisions: [`library-analysis.md`](../library-analysis.md) §unix-compress
- Code: `internal/streams/unix_compress.py` (`LzwState`, `UnixCompressDecoder`,
  `UnixCompressDecompressorStream`) · `internal/streams/codecs.py` (`UnixCompressCodec`)
- Handbook: [`single-file.md`](single-file.md) · [`tar.md`](tar.md)
