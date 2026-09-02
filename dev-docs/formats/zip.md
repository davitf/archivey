# ZIP

Current maintainer truth for the ZIP backend: what the format forces, who does which
part of the work, and where the sharp edges are. Registers keep the status — this page
states the behaviour and links the row.

## At a glance

| | |
| --- | --- |
| Read | Yes |
| Write | **Not shipped.** `format-zip` specifies streaming write; there is no writer in `src/` (`PLAN.md` phase 9) |
| Source | Seekable only, in both access modes |
| Listing cost | `INDEXED` |
| Access cost | `DIRECT` |
| Stream capability | `SEEKABLE` |
| Core dependencies | None — ZIP reads on a zero-dependency install |
| Optional | `[recommended]`: Deflate64 (`inflate64`), PPMd (`pyppmd`), Zstd (`backports.zstd`, stdlib on 3.14+), WinZip AES (`cryptography`) |
| Refuses | Non-seekable sources · split/spanned sets · unknown compression methods, at read · AES without `cryptography` |

The extras are named for what they provide, not for ZIP, because every one of those
codecs is shared with 7z or TAR. See [`packaging-and-extras`](../../openspec/specs/packaging-and-extras/spec.md).

## 1. Shape

Four properties generate most of this page. Everything below is a consequence of one of
them.

```
[ optional prefix ] [ LFH+data ] [ LFH+data ] … [ CD entry ] [ CD entry ] … [ EOCD ]
                      ^                              |                         |
                      └──────────────────────────────┘ header_offset           |
                      └──────────────────── offset_cd ──────────────────┘      |
                                                     backwards search ◄────────┘
```

**The index is at the end.** The central directory is authoritative and is found by
scanning backwards from the last bytes for the End of Central Directory record. So:
reading the structure requires positioning at the end, which is why a non-seekable source
is refused outright (§2.1, §5); a ZIP can be preceded by arbitrary bytes without any of
its own numbers changing meaning, which is why prefixed ZIPs work (§3); appending is
expressed by writing a new directory at the end, which is why in-place update is possible
in the format and refused here (§6); and a scan that lands on the wrong `PK\x03\x04` is
usually self-correcting, because the reader still finds the real EOCD from the tail
(§2.1).

The backwards search bound is derived, not chosen: `comment_length` is a `uint16`, so the
record cannot begin more than 65535 + 22 bytes before the end. A larger bound cannot find
a valid EOCD and a smaller one rejects legal archives, so it is not configurable.

**Members are independent.** Each member has its own local header and its own compressed
byte range, with no cross-member state. So random access is `DIRECT`, seeking within a
member and holding two member streams open are both cheap, and there is no solid-block
cost model. Both capabilities are still off by default — see §6.

**Sizes and the CRC may arrive after the data.** With general-purpose bit 3 set, the local
header carries zeros and a data descriptor follows the compressed bytes. The central
directory carries the real values, so a random-access read is unaffected; a forward-only
reader would see them late. This is also why the ZipCrypto verification byte is the high
byte of the DOS time rather than of the CRC when bit 3 is set.

**Names are bytes plus one unreliable flag.** General-purpose bit 11 declares UTF-8;
unflagged names are nominally CP437. Producers set the flag on names that are not UTF-8
and omit it on names that are, and there is no in-band way to recover the intent. So
decoding is a judgement (§2.2), `raw_name` keeps the stored bytes so a wrong decode can be
undone, and a lying flag can cost the whole archive (§5).

## 2. The pipeline here

Each stage: who does the work, what is ZIP-specific rather than general, what is refused.

### 2.1 Identify

ZIP declares three magics at offset 0 — `PK\x03\x04` (local header), `PK\x05\x06` (empty
archive) and `PK\x07\x08` (spanned marker) — and five extensions: `.zip`, `.jar`, `.pyz`,
`.whl`, `.apk`.

Only the local header is offered as a scan needle for prefixed archives. The other two are
legitimate ZIP magic *at offset 0* but as needles inside a 2 MiB stub window they would
claim any executable containing those four bytes. A needle hit is confirmed by
`validate_zip_local_header` (`internal/zip_detect.py`) before it is reported: version-needed
in range, no reserved general-purpose bits, a known method id, a non-empty name, and
name+extra within the source. That last pair is what rejects `PK\x03\x04` followed by the
zero-fill an ELF or PE stub pads with.

The validator's method table is deliberately **wider** than the set archivey can decode: a
member using a method we refuse to read is still a ZIP, and identity is not a support
claim.

Two things are ZIP-specific rather than general in the shared detector:

- Prefixed ZIPs are found by the cued forward scan and reported as `ZIP` with a
  `payload_offset`, never as a stream codec. The cue set (`MZ`, ELF, Mach-O, `#!`), the
  scan window and the budget tiers are general, and belong on a prefixed-archives topic
  page rather than here; what stays ZIP's is the needle, the validator, and the offset
  conventions in §3.
- A **tail probe** — locating the EOCD directly instead of scanning forward — is designed
  and not shipped. Until it lands, a prefixed ZIP behind bytes that fire no cue (a JPEG
  polyglot, a plain concatenation) is not detected, though `open_archive(..., format=ZIP)`
  reads it. Under `open_archive` the probe would be nearly free, since the backend reads
  the EOCD anyway; under a bare `detect_format()` it is a tail read on every file,
  including the overwhelming majority that are not ZIPs. That asymmetry, not false
  positives, is why it is gated.

### 2.2 Open and list

Stdlib `zipfile` parses the central directory and builds the member map. `reader.get()`
and name lookup are satisfied from that map with no further archive I/O.

**Split sets are rejected before anything else.** A filename matching `.z01`/`.zNN` raises
`UnsupportedFeatureError` at open. A ZIP64 locator claiming more than one disk makes stdlib
raise, and archivey re-types it by matching the exception text. Neither path reads the
32-bit EOCD disk fields, and the other split-naming convention in the wild is missed
entirely (§5).

**Name decoding.** A set bit 11 is honoured as UTF-8. An explicit `encoding=` is passed to
stdlib as `metadata_encoding` and used verbatim, which also disables the sniff below. An
unflagged name is decoded as UTF-8 when the bytes are valid UTF-8, and otherwise with
`ArchiveyConfig.zip_unflagged_fallback_encoding` (default `cp437`, which decodes every
byte, so no `UnicodeDecodeError` escapes).

Choosing UTF-8 for an unflagged name emits `MEMBER_NAME_ENCODING_INFERRED`; a pure-ASCII
name does not, because ASCII decodes identically under both and nothing was overridden.
The sniff is validation rather than guessing: UTF-8 is self-checking, so a clean decode is
near-conclusive evidence. No equivalent is possible for the legacy tail — see §5.

Backslashes are decoded by origin, not globally. A DOS/FAT-origin entry's `\` is a path
separator; a Unix-origin entry keeps it as a literal filename character.

**Metadata mapping.**

| `ArchiveMember` field | Source | Absent when |
| --- | --- | --- |
| `name` | Central-directory name bytes, decoded as above | — |
| `raw_name` | The stored bytes, verbatim | — |
| `mode` | `external_attr >> 16` | The producer was not Unix-like, or `external_attr` is 0 — then `None`, never a substituted default |
| `modified` / `accessed` / `created` | DOS date-time (naive local, 2-second granularity) ← NTFS extra `0x000A` (UTC) ← Extended Timestamp `0x5455` (UTC), later overriding earlier | 1980 sentinel, or every layer invalid — with `MEMBER_TIMESTAMP_INVALID` |
| `type` | Unix mode where present, else the directory marker and symlink hints | — |
| `link_target` | The member's **data**, not its metadata | The archive is encrypted and no password is available — `SYMLINK_TARGET_UNAVAILABLE(reason="password_required")`, with no secret in the payload |
| `compression` | `compress_type` → `CompressionMethod` | — |
| `is_encrypted` | `flag_bits & 0x1` | — |
| `hashes["crc32"]` | Central-directory CRC, as four big-endian bytes | WinZip AE-2 members — the format zeroes the field and the HMAC is the integrity signal |
| `comment`, `create_system` | Central directory | Not recorded |
| `extra` | `zip.compress_type`, plus `zip.aes_vendor_version` / `zip.aes_strength` / `zip.aes_actual_method` on AE members | — |

Raw extra-field blobs are not surfaced. Duplicate names are legal and are not merged:
members keep a positional `member_id`, name lookup is last-wins, and currency is computed
in the reader spine so ZIP behaves like every other format.

### 2.3 Member data

**Stdlib's decoders are not used.** archivey locates the member's raw compressed bytes with
a bounded local-file-header parse (fixed header plus the local name and extra lengths, with
absurd-length rejection), slices the source, and dispatches on the ZIP method id to the
shared codec layer.

The reason is coverage and uniformity, in that order. Stdlib decodes STORED, DEFLATE, BZIP2
and LZMA; the codec layer adds Deflate64 (9), Zstd (93) and PPMd (98), which the registry
already advertised for ZIP. It also puts ZIP member reads on the same `VerifyingStream` and
the same exception translation as every other backend, so a corrupt body raises
`CorruptionError` and a cut-short one raises `TruncatedError` through shared code.

Encryption is the one place the split is uneven:

| | Path | Notes |
| --- | --- | --- |
| Traditional ZipCrypto | stdlib `zipfile`'s decryptor | One-byte verifier, so ~1 in 256 wrong passwords passes it |
| WinZip AES (method 99, extra `0x9901`) | archivey, natively | PBKDF2-HMAC-SHA1 · AES-CTR · HMAC-SHA1 truncated to 10 bytes; then the codec layer for the real method |

ZipCrypto's weak verifier is why multiple password candidates need confirmation before one
is accepted. For a compressed member the decompressor rejects a wrong key within a few
bytes on structural grounds, so a bounded prefix decode confirms cheaply. A **STORED**
member has no decompressor to do that, so the only discriminator is the whole-stream CRC —
all surviving candidates are resolved in one shared ciphertext pass computing each
candidate's CRC in constant memory, earliest match winning. That cost is irreducible for
the format; see `open-issues.md` §Irreducible.

For AES, a wrong password fails fast on the 2-byte verification value with no bytes
returned; a tampered ciphertext fails on the HMAC at the terminal read. AE-1 surfaces and
verifies `crc32` alongside the HMAC; AE-2 surfaces neither.

An unknown method id lists fine and raises `UnsupportedFeatureError` on read — never
guessed output. A missing optional package raises `PackageNotInstalledError` naming the
extra.

### 2.4 Extract

Nothing here is ZIP-specific. Path traversal, symlink escape, name collisions,
cross-platform name safety and the byte/ratio/member caps are all the shared extraction
spine — see [`safe-extraction`](../../openspec/specs/safe-extraction/spec.md) and
[`threat-model.md`](../threat-model.md). A blocked member does not end the run; the rest of
the archive still extracts.

### 2.5 Write

Not shipped. See the table at the top and §6.

## 3. In the wild

**ZIP is the container other formats are built on.** JARs, wheels, APKs, zipapps, OOXML
and the ODF family are all ZIPs, and `open_archive` reads them as such with nothing to
distinguish them from a backup. A census of 643 readable ZIPs on one Linux image found 363
JARs (`META-INF/MANIFEST.MF`), 176 ODF-family (a `mimetype` entry stored first, 176/176,
none compressed), 11 wheels, and 91 with no marker at all — of which 85 were named `.zip`
and none was user data. Recognising the role is post-1.0 and, if built, must report "not
recognised" rather than "this is data"; the analysis is in
[`IDEAS.md`](../IDEAS.md) §Archive role. For ZIP the cheap tests are nearly free, because
the first entry's name and its `compress_type` are both already in the central directory.

**Prefixed ZIPs are an idiom, not an abuse** — `zipapp`, pex, shiv, Spring Boot executable
JARs, self-extracting installers, appended-ZIP polyglots. Two write conventions exist and
store different numbers:

| | Stored offsets count from | EOCD adjustment |
| --- | --- | --- |
| Written in place (`zipapp`: open one file, emit a stub, write entries through the same handle) | Byte 0 of the file, stub included | 0 |
| Concatenated (`cat stub payload.zip`) | The start of the ZIP data | The prefix length |

The difference is self-correcting: the EOCD's own position is known once it is found, so
`(eocd_pos - size_cd) - offset_cd` recovers the base under either convention and every
entry offset is read through it. This is why `payload_offset` is defined as the absolute
position of the earliest local file header rather than as the adjustment — the adjustment
is 0 for `zipapp`, which would report the motivating case as unprefixed. An empty archive
has no local header to point at, so there `payload_offset` is the EOCD-derived base.

**Producers disagree about encodings.** Info-ZIP and others write valid UTF-8 names without
setting bit 11, which is the case the sniff exists for; `tests/fixtures/external/encoding_infozip_jules.zip`
is a real sample.

**Producers disagree about split naming.** Info-ZIP and WinZip write `name.z01 … name.zip`;
7-Zip writes `name.zip.001 … name.zip.00N`. Only the first is recognised (§5).

**Producers disagree about encryption defaults.** 7-Zip's `-tzip` default is ZipCrypto and
`-mem=AES256` selects WinZip AES; stdlib `zipfile` writes neither. That is why the
encrypted corpus rows shell out to `7z` and skip silently without it.

ZIP64 is not exotic. A central directory of 70 000 entries lists correctly through stdlib.

## 4. Threat surface

ZIP-specific only. General extraction and name hazards are §2.4.

- **Listing is attacker-controlled work.** A small file can declare an enormous number of
  entries, or entries with enormous names and comments, with nothing decompressed. Capped
  by `ListingLimits`; see [`threat-model.md`](../threat-model.md) O1.
- **ZIP is the canonical bomb.** Declared sizes are attacker-controlled and so is the
  stored CRC, so neither bounds the output; the caps are enforced against bytes actually
  written, plus a live ratio measured from bytes consumed. Nesting is not tracked — a
  zip-of-zips amplifies one level at a time (O6).
- **Overlapping entries** are a distinct crafted shape, caught by stdlib's open-time
  overlap guard and translated to `CorruptionError`.
- **ZipCrypto password confirmation is a measurable side channel by design**: the STORED
  path reads the member to disambiguate candidates. Documented cost, not a leak.
- The real-world case behind the cross-platform name policies arrived as a ZIP: a macOS
  archive containing a `stuff_etc.` folder, a trailing dot Win32 silently trims. Because
  the offending segment is a directory, rejecting it takes every member beneath it. See
  [ADR 0013](../decisions/0013-cross-platform-name-safety-policies.md) and O3.

## 5. Pitfalls

*Where it lives*: **format** — inherent, no implementation fixes it · **library** — stdlib
`zipfile`'s behaviour, fixable only upstream or by replacing it · **archivey** — ours.

| What you see | Where it lives | More |
| --- | --- | --- |
| A ZIP on a pipe or socket cannot be opened at all, in either access mode, and is never buffered for you | **format** | The index is at the end (§1). A native forward-walking reader would change this |
| One member name whose UTF-8 flag lies makes the **whole archive** unlistable | **library** | Stdlib decodes flagged names strictly while parsing the central directory, so the failure is archive-wide rather than confined to the bad entry. [`open-issues.md`](../open-issues.md) P4 |
| A `.z01`…`.zip` split set is refused with "rejoin first" | **format** for now | ZIP addresses entries by (disk, offset), which stdlib cannot resolve and naive concatenation cannot fake. [`open-issues.md`](../open-issues.md) P2 |
| A `.zip.001`…`.zip.00N` split set — 7-Zip's naming — is not recognised as split at all: the first part reports `CorruptionError`, the rest report `FormatDetectionError` | **archivey** | Valid input, wrong error. The `.z01` rule is a filename pattern that does not cover this convention. Unregistered as of this writing; same shape as `open-issues.md` P17 |
| An EOCD declaring a non-zero disk number opens and lists normally | **archivey** | Those fields are parsed by stdlib and never checked; only the ZIP64 locator path refuses, and it does so by matching stdlib's exception text. `format-zip` has a scenario claiming otherwise |
| A truncated or corrupt archive fails at open, not per member — nothing is salvaged | **library** | Stdlib needs a readable central directory before anything is listable. A native reader could walk local headers forward |
| A legacy name that is not valid UTF-8 renders garbled and no setting fixes it | **format** | Every candidate codepage decodes every byte, so there is no oracle, and a filename is far too short for a statistical detector. The garble is honest and `raw_name` round-trips; a wrong guess is neither. Opt-in detection is post-1.0 ([`IDEAS.md`](../IDEAS.md)) |
| A wrong ZipCrypto password can be accepted and surface later as corruption | **format** | One-byte verifier. Confirmation narrows it; nothing eliminates it |
| A prefixed ZIP behind bytes that fire no cue is not detected, though it opens with `format=ZIP` | **archivey** | The tail probe is designed and unshipped (§2.1) |
| `format-zip` describes streaming write; there is no writer | **archivey** | Documentation, not behaviour. Writing is `PLAN.md` phase 9 |

## 6. Decisions

| Choice | Why | Rejected |
| --- | --- | --- |
| Stdlib `zipfile` for the central directory | Zero-dependency, no packaging burden, well-tested parser | `python-libarchive-c` — faster and broader, at the cost of a native dependency ([ADR 0006](../decisions/0006-stdlib-zipfile.md)) |
| archivey's codec layer for member data | Stdlib decodes four methods; the codec layer decodes seven, and unifies CRC verification and error translation with the other backends | Staying on `ZipExtFile`, which left ZIP advertising codecs it could not decode |
| Refuse a non-seekable source rather than spool it | Silent buffering hides an unbounded memory or disk cost the caller did not ask for | Transparent `SpooledTemporaryFile`; still possible later as an explicit opt-in ([ADR 0010](../decisions/0010-no-silent-buffer-nonseekable.md)) |
| Seeking and concurrent member streams off by default, on ZIP too | Both are free here and expensive or wrong elsewhere; leaving them on lets someone test on ZIP and ship a footgun on TAR or 7z. The strict default is reversible before 1.0; the permissive one is not | Enabling them where they are cheap ([ADR 0003](../decisions/0003-member-streams-opt-in.md)) |
| Sniff unflagged names for UTF-8 validity; do not guess legacy codepages | Validation is near-conclusive; guessing has no oracle and a plausible wrong name is worse than a visible garble | An off-the-shelf charset detector, which can override a *valid* UTF-8 string with a legacy guess |
| Reject split sets rather than approximate them | Naive segment concatenation is unreliable and would mis-read data rather than fail | Concatenating segments and hoping |
| Extras named by capability, not by format | The codecs are shared, so `[7z]` told a ZIP reader to install support for a different format — the name lied, not the message | Per-format extras |
| Create-only writing, when writing lands | ZIP append is legal in the format and turns an interrupted write into a corrupt archive | In-place append |

## 7. Verify

```bash
./scripts/test.sh tests/test_zip.py tests/test_zip_aes.py \
    tests/test_zip_native_codecs.py tests/test_zip_multipassword.py
```

| Claim | Pinned by |
| --- | --- |
| Cost receipt, central-directory lookup without I/O | `tests/test_zip.py::test_cost_receipt`, `::test_central_directory_lookup_no_io` |
| Non-seekable refused at open | `::test_non_seekable_zip_fails_fast`, `::test_non_seekable_zip_fails_fast_via_detection` |
| Split segment refused | `::test_split_segment_name_rejected` |
| Timestamp precedence, invalid and out-of-range fallbacks | `::test_extended_timestamp_beats_ntfs`, `::test_ntfs_timestamps_used_when_no_extended_timestamp`, `::test_extended_timestamp_out_of_range_degrades_to_diagnostic` |
| Encoding sniff, fallback, override, escalation | `::test_unflagged_utf8_name_is_sniffed` and the four tests after it |
| Backslash by origin | `::test_backslash_converted_for_dos_windows_entry`, `::test_backslash_kept_literal_for_unix_entry` |
| Symlink target from member data; encrypted target withheld | `::test_symlink_member`, `::test_encrypted_symlink_listing_without_password` |
| Duplicate names read independently | `::test_duplicate_member_names_read_independently` |
| Overlapping-entry bomb | `::test_overlapping_entries_bomb_translated_to_corruption` |
| AE-1/AE-2, wrong password, tampered ciphertext | `tests/test_zip_aes.py` |
| ZipCrypto candidate confirmation, STORED CRC pass | `tests/test_zip_multipassword.py` |
| Cross-format member equivalence, per-method decode, AE-2 CRC absence | `tests/test_corpus_sweep.py` (13 ZIP corpus entries) |

**Building fixtures.** Stdlib `zipfile` cannot write encryption, so encrypted fixtures shell
out to `7z` (`-mem=AES256` for WinZip AES, ZipCrypto by default) and skip when it is absent —
one of the ~109 tests that vanish quietly on an unprovisioned container. `-mm=Deflate64`,
`-mm=PPMd`, `-mm=BZip2` and `-mm=LZMA` produce the extended methods. The backslash fixtures
are committed rather than generated because `zipfile` rewrites `ZipInfo.filename` on Windows;
the reader uses `orig_filename` for the same reason.

## 8. References

- APPNOTE: §4.3.6 overall format · §4.3.9 data descriptor · §4.3.16 EOCD · §4.4.4
  general-purpose flags (bits 3 and 11) · §4.4.6 MS-DOS date/time · §4.4.15 external file
  attributes · §4.5.5 extended timestamp · §7 traditional encryption · §8 splitting and
  spanning · Appendix D CP437
- Specs: [`format-zip`](../../openspec/specs/format-zip/spec.md) ·
  [`compressed-streams`](../../openspec/specs/compressed-streams/spec.md) ·
  [`format-detection`](../../openspec/specs/format-detection/spec.md)
- Code: `internal/backends/zip_reader.py` · `internal/zip_detect.py` ·
  `internal/zipcrypto.py` · `internal/zip_aes.py`
- Investigations: [`archive-format-detection-algorithm.md`](../investigations/archive-format-detection-algorithm.md)
  (tail-tier design, corpus counts) ·
  [`rar-corpus-sweep-diagnosis.md`](../investigations/rar-corpus-sweep-diagnosis.md)
  (per-format symlink digest and size comparison)
- User-facing: [`docs/formats.md`](../../docs/formats.md#zip) ·
  [`docs/gotchas.md`](../../docs/gotchas.md)
