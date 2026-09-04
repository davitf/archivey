# RAR

Current maintainer truth for the RAR backend. RAR is the only format whose read path
crosses a **process boundary**: archivey parses every header itself and hands member
bytes to a separate `unrar` process. Most of what is peculiar here follows from that.
Registers keep the status — this page states the behaviour and links the row.

## At a glance

| | |
| --- | --- |
| Read | Yes — metadata natively, member data through RARLAB `unrar` |
| Write | **Not shipped**, for any format — no `archivey.create`, no writer module (`PLAN.md` phase 9) |
| Source | Seekable only, in both access modes |
| Listing cost | `INDEXED` |
| Access cost | `SOLID` for a solid archive, `DIRECT` otherwise; `solid_block_count` is always `None` (§1) |
| Stream capability | `SEEKABLE` — about the *source*. A member stream backed by `unrar` is a pipe and is never seekable (§2.3) |
| Core dependencies | None to list an unencrypted archive. Member data needs the RARLAB `unrar` binary on `PATH`, which no pip extra can supply |
| Optional | `[recommended]` (`cryptography`): header decryption, **RAR3/RAR4 and RAR5 alike** — both derive an AES key through the same stage. BLAKE2sp needs nothing; it is implemented on stdlib `hashlib` |
| Refuses | Non-seekable sources · a non-RARLAB `unrar` (no fallback to `unar` / `7z` / `bsdtar` / `unrar-free`) · a member whose name contains `*` or `?`, on the `unrar` path only · a later volume opened without its first · writing |

**A spec requirement with nothing behind it.**
[`format-rar`](../../openspec/specs/format-rar/spec.md) SHALL-requires
`PackageNotInstalledError` when `unrar` is "missing **or incompatible**". Only the first
half exists: `find_rarlab_unrar` reads the banner for `UNRAR` plus
`Alexander Roshal`/`RARLAB` and applies **no version floor**, so an ancient RARLAB build
passes identification and then fails per member. Either the floor gets written or the
requirement should stop claiming it.

Two further things the spec *permits* and nobody built — a permission unexercised is not
a broken claim, but both shape the cost model below. There is no `unrar x` anywhere in
`src/`, so neither the managed-temp-directory strategy for repeated solid random reads nor
the one-shot `unrar x` for `extract_all()` exists, and every solid random open is its own
whole-archive decode (§2.3). The small-member optimization is marked deferred by the spec
itself. [`docs/formats.md`](../../docs/formats.md#rar) still tells **users** that "random
solid opens may use explicit temp materialization"; they do not, and a reader will not hear
"may" as "does not".

## 1. Shape

Four properties generate most of this page.

```
  [ SFX stub? ]  [ magic ]  [ MAIN ]  [ FILE hdr | packed data ] × n  [ ENDARC ]
                     │          │            │
   Rar!\x1a\x07\x00 ─┤          │            └── data_offset ─┐
   Rar!\x1a\x07\x01\x00         │                             │
        version 4 / 5      each header states                 │
                           its own size                       │
  ─────────────────────────────────────────────────────────── │ ───────────────
   archivey's parser reads everything above                    │
                                                    ╔══════════▼═══════════╗
                                                    ║  unrar  (subprocess) ║
                                                    ╚══════════════════════╝
```

**The metadata is ours; the bytes are another program's.** RAR compression is proprietary,
and the UnRAR licence forbids using *its source* to re-create a RAR compressor. So the
header layout was reverse-engineered and the entropy coder was not, and archivey parses
headers natively while `unrar` decodes payloads (ADR [0002](../decisions/0002-native-rar-metadata-unrar-data.md), threat-model C1).
Everything about that boundary is a consequence:

- **Listing needs no `unrar` at all** — names, sizes, timestamps, modes, flags, hashes,
  link targets and even header decryption come out of the parser (§2.2). A stored,
  unencrypted, non-solid, unsplit member is read by slicing the source, so an archive of
  those reads end to end with no subprocess (§2.3).
- **`unrar` seeks the archive, so it cannot be piped one** — which is why a stream source
  is copied to a temp file the first time a member cannot be read directly, the whole
  archive and silently ([`open-issues.md`](../open-issues.md) P11). This is worth stating
  because the binary's own switch list invites the opposite conclusion: `-si[name]` is
  documented as "Read data from standard input", and it is a `rar` **compressor** switch —
  on `unrar` it is a command-line error. Measured
  (`scripts/exploration/rar_unrar_input_matrix.py`, RARLAB unrar 7.00):

  | Handed the archive as | |
  | --- | --- |
  | `unrar p -si` with no path, archive on stdin | **rc 7** — command-line error |
  | `unrar p -` | **rc 7** — `-` is not stdin to `unrar` |
  | `unrar p /dev/stdin` with a **pipe** behind fd 0 | **rc 2** — fatal, non-seekable |
  | `unrar p <a FIFO path>` | **rc 2** — same |
  | `unrar p /dev/stdin` with a **seekable file** behind fd 0 | rc 0 |
  | `unrar p /proc/self/fd/N`, N an inherited **memfd** | rc 0 |

  So there is no "stream it through" option, and never was: the only question a stream
  source poses is *where* the seekable copy lives (disk or RAM) and *how much* of the
  archive it holds (§7). An anonymous seekable `memfd` is the one door left open, and it
  does not survive multi-volume (§2.2).
- **A member name becomes an argv token**, which makes the name a parsing surface for a
  program that was never told the name is untrusted (§2.3, §4).
- **Two numbers cross back.** `unrar` is run with `-inul` and its stderr goes to
  `DEVNULL`, so its own diagnostic text is discarded: the only signals are the exit code
  and how many bytes arrived. Every RAR data error archivey reports is reconstructed from
  those two.
- **A member stream is a pipe**, so `seekable_members=True` cannot be honoured on it
  (§2.3, §5) and a rewind is not merely expensive — it is unavailable.
- **Identity of the binary costs a process.** `find_rarlab_unrar` runs `unrar` with no
  arguments and sniffs the banner, then caches a **successful** answer for the life of the
  process. A rejected binary is not cached, so a lookalike on `PATH` costs one probe per
  attempted read rather than one per process.

**Blocks chain forward and each header states its own size.** There is no index; the walk
reads a header, uses its declared size to find the next, and stops at `ENDARC`. So:
reading the structure needs seek, and a non-seekable source is refused in both access modes
(§2.1); the whole member table is built at open, which is why listing is `INDEXED` and why
the parser carries its own ceiling of 1 048 576 members before `ListingLimits` ever apply;
every length is a variable-length integer whose byte count the input chooses, so bounds
have to be on **bytes consumed**, not on the decoded value — the one place that was
violated was a quadratic pre-read loop in front of the capped decoder
([`hostile-input.md`](../../review/archive/2026-07-16-rar-reader/hostile-input.md) F2, fixed);
a member split across a volume boundary is rejoined from continuation *flags*, which needs
an identity check or a crafted flag folds an unrelated member into the previous one (F6,
fixed); and a volume set is *discovered* by naming convention rather than by a recorded
disk number, unlike a spanned ZIP — though discovery is where the resemblance ends. RAR5
records a volume number in MAIN and the parser checks the set against it, refusing a
headless set (`Need first volume`) or an out-of-order one; RAR3 records only a flag, so
there the name really is the whole chain.

**Two on-disk generations hide behind one magic.** `Rar!\x1a\x07\x00` is the RAR3 family —
which is RAR 1.5, 2.x and 3.x, all one block layout, reported as `format_version == "4"` —
and `Rar!\x1a\x07\x01\x00` is RAR5. They disagree about nearly every metadata question, so
the reader is version-conditional almost everywhere:

| | RAR3 family (`version 4`) | RAR5 (`version 5`) |
| --- | --- | --- |
| Timestamps | DOS wall clock → **naive** `datetime` | Unix/FILETIME → **aware UTC**, sub-second |
| Symlink target | stored as the **member's data** | a **header redirect**, no data stream |
| Symlink digest | a genuine CRC32 of the target string | **none surfaced** — the field covers zero bytes (§2.2) |
| Names | dual fields: a compressed UTF-16 name plus an 8-bit name | one UTF-8 field |
| Header encryption | no check value — a wrong password is only visible as a structural failure | `ENCRYPTION` block usually carries a check value |
| Encrypted-member digests | plain | key-**tweaked** MACs when `XENC_TWEAKED` is set (§2.2) |

The absent RAR3 check value is why a wrong header password used to surface as
`CorruptionError` and abort a whole candidate list on the first wrong entry
(F1, fixed — candidate iteration works on RAR3 today, §8).

**Solidity is archive-wide and its blocks are invisible.** RAR exposes no per-solid-block
boundaries, so `ArchiveInfo.is_solid` is one flag and `CostReceipt.solid_block_count` is
`None` by construction rather than by omission. Consequences: a whole streaming pass is
**one** `unrar` process whose stdout is an undelimited concatenation of payload members;
splitting that back apart relies entirely on the archive's own declared sizes *and* on
knowing exactly which member kinds `unrar` emits bytes for, which is version-dependent —
get it wrong for one kind and every later member is offset ([`open-issues.md`](../open-issues.md)
P6); and a random open of a solid member is a **fresh whole-archive decode** inside
`unrar`, one process per open, because there is no earlier point to resume from.

## 2. The pipeline here

Each stage: who does the work, what is RAR-specific rather than general, what is refused —
and, for the stage that delegates, what crosses the boundary.

### 2.1 Identify

Two magics at offset 0 and one extension, `.rar`. Both magics are also the scan needles for
a prefixed archive, deliberately rather than their shared `Rar!\x1a\x07` prefix: matching
each id separately resolves RAR4 vs RAR5 at the hit instead of re-reading to disambiguate.

The hit validator (`internal/rar_detect.py`) is the **main header that follows the marker**,
and it is checksummed, which makes it a stronger validator than ZIP's field-range checks:

- **RAR5** — read the first block's declared length (capped at 64 KiB for identity, well
  under the parser's 2 MiB header cap), verify its CRC32, then require the block type to be
  `MAIN` or `ENCRYPTION`. A CRC failure after a plausible header is `DAMAGED`, not
  `NOT_THIS_FORMAT`: the payload was identified and is broken, which a later evidence
  ledger can use.
- **RAR3** — require block type `0x73` (`MAIN`), a header size between the structural
  minimum and 64 KiB, and a matching 16-bit header CRC.

Everything else about finding an archive behind a stub — the cue tiers, the 2 MiB
`SFX_MAX`, why validation is the correctness gate and the cue only a cost gate — is shared
and lives on [`topics/prefixed-archives.md`](../topics/prefixed-archives.md). RAR's own
residue is the two-needle choice above, the CRC-checked validator, and the fact that
`SFX_MAX` is shared with `rar_parser`'s *own* stub scan so the parser and the detector
cannot disagree about how far to look.

A self-extracting **and** split RAR set (`rv.part1.sfx`, `rv.part2.rar`, …) is readable from
none of its files, because sibling discovery wants the archive extension immediately before
the part number — [`open-issues.md`](../open-issues.md) P17, shared with 7z and ZIP.

### 2.2 Open and list

`rar_parser.py` walks the block chain and builds the member table; no `unrar`, no `rarfile`.
`reader.get()` and name lookup are served from that table.

**Volumes are resolved before parsing.** `name.partN.rar` (RAR5 and newer RAR4) and
`name.rar` + `name.r00`, `name.r01`, … (older RAR4) are both discovered from any member of
the set — the old scheme through a two-digit pattern, so a set that runs past `.r99` (WinRAR
continues `.s00`, `.s01`, …) is not discovered at all — and headers are read across the volumes in order with split members stitched into
one logical member. `ArchiveInfo.is_multivolume` is `True` and
`ArchiveInfo.extra["rar.volume_count"]` carries the count. A lone volume 1 is
`TruncatedError` ("expects another volume"); a lone later volume is
`UnsupportedFeatureError` ("Need first volume") rather than a partial listing. Stream
volumes are copied into a temp directory named `…partN.rar` so `unrar` can walk the set
later (P11 again) — and the **names** are the point, not just the seekability: `unrar`
discovers later volumes by filename on disk, so neither a `memfd` nor a byte-concatenation
serves them. Measured on the two-part `tinyvol` fixture, where the whole payload is 1 600
bytes: part 1 on disk beside its sibling gives rc 0 and all 1 600, part 1 as an anonymous
`memfd` gives **rc 3 after 839**, and the two volumes concatenated into one file give the
same **rc 3 after 839** — both stop at the volume-1 boundary.

**Header encryption is native.** A RAR5 or RAR3 header-encrypted archive lists with a
password and `cryptography` installed, with no `unrar` involved. Without a password it is
`EncryptionError`; with a password and no crypto backend, `PackageNotInstalledError`. A
password *list* is iterated correctly on both generations.

**`encoding=` is not applied.** RAR names are decoded by the parser, so the argument is
dropped — but not silently: supplying it emits `ENCODING_ARGUMENT_UNUSED`, which is the
interface-wide answer for an argument a format cannot honour, and a structured diagnostic
rather than a log line.

**Metadata mapping.** Everything comes out of the native parser; there is no library in
between to blame or to defer to.

| `ArchiveMember` field | Source | Absent when |
| --- | --- | --- |
| `name` | Decoded header name, normalized (backslash is a separator); a file-version row is presented as `path;n`, matching WinRAR and `unrar` | — |
| `raw_name` | Stored name bytes, verbatim — including RAR3's `path;n` bytes, which are not rewritten | — |
| `size` / `compressed_size` | Header sizes; RAR3 `FILE_LARGE` extends both to 64 bits, and the packed skip is extended with them so the walk does not misparse past a >4 GiB member (F5, fixed) | — |
| `modified` | RAR4 DOS time → naive local; RAR5 Unix/FILETIME → aware UTC. Out-of-range values are swallowed rather than aborting the listing | The header carries none, or every value was out of range |
| `accessed` / `created` | **Never set.** RAR5 carries both in its `0x03` time extra and the parser reads past them without keeping them (`rar_parser.py`, `_parse_rar5_xtime`); RAR3 EXTTIME is the same shape. ZIP surfaces all three, so this is a parity gap rather than a format limit | Always |
| `mode` | Unix host: `S_IMODE` of the stored attributes, masked before the C helper so a hostile vint cannot raise `OverflowError` mid-listing | Non-Unix host. A Win32 host puts its attribute word in `windows_attrs`; a FAT, OS/2, Macintosh or BeOS host gets **neither** field |
| `type` | Directory flag; RAR5 `file_redir` gives `HARDLINK` for hard links and file copies, `SYMLINK` for Unix/Windows symlinks and junctions (a junction also sets `extra` `is_junction`) | — |
| `link_target` | RAR5: the redirect's target string, at list time. RAR4: the member's **data**, read directly when it is stored and unencrypted | An encrypted or compressed RAR4 target with no direct bytes — left unset; listing still succeeds |
| `compression` | Method id → `CompressionMethod`. Stored members report `STORED`; a compressed member reports `UNKNOWN` **with the level**, because the algorithm has no public name to give | — |
| `hashes` | `crc32` and/or `blake2sp` as bytes | A RAR5 **redirect** (see below), or an encrypted member whose digests are tweaked |
| `is_encrypted` | Per-member encryption flag | — |
| `is_current` | `False` for a file-version history row, `True` for the live revision | — |
| `extra` | `rar.file_version` on a history row; `rar.tweaked_crc32` / `rar.tweaked_blake2sp` on a tweaked-digest member | — |
| `comment` | **Never set.** A RAR3 per-member solid comment block *is* parsed, into `_raw.comment`, and then not surfaced. The *archive* comment does reach `ArchiveInfo.comment` | Always |

Two digest rules are worth stating because they look like missing data and are not:

- **A RAR5 redirect surfaces no digest.** A symlink, hard link or file copy keeps its
  target in a header field and stores no data stream, so its CRC32 field covers zero bytes
  and RARLAB writes `crc32(b"") == 0`. That value describes nothing and is identical for
  every redirect in every archive, so a de-duplicating caller reading `member.hashes` would
  see every link as the same content. Measured against the other formats: ZIP `0x2d212004`
  over 9 stored bytes, 7z `0x2b4106af` over 45, TAR none, RAR5 `0x00000000` over **zero**
  ([`rar-corpus-sweep-diagnosis.md`](../investigations/rar-corpus-sweep-diagnosis.md)). The
  rule keys on the *redirect*, never on the member type — **RAR3/4 stores the target as
  member data**, so its CRC32 is a genuine digest and is kept.
- **Tweaked digests are kept out of `hashes`.** With RAR5's tweaked-encryption flag set, the
  stored CRC32 and BLAKE2sp are key-derived MACs (`ConvertHashToMAC`), not checksums of the
  plaintext — the format transforms them precisely so a stored digest is not an oracle for
  guessing encrypted content. Comparing one to a plaintext digest would report corruption on
  a good archive. They are exposed under `extra` and verified by applying the same forward
  transform once a password is available; without one, each emits
  `DIGEST_UNVERIFIABLE(reason="tweaked_checksum")`.

**File-version history is listed, not hidden.** A `-ver` archive's prior revisions appear as
`path;n` with `is_current=False`, the live revision keeps the plain path, and `read("path;1")`
returns that revision's bytes. The `;n` split only fires when the suffix after the last `;`
is all digits and only when the version flag is set, so an ordinary `a;b.txt` is not
misattributed.

### 2.3 Member data

This is the boundary. Three routes, and which one a member takes is decided entirely from
its header:

| Route | When | Cost |
| --- | --- | --- |
| **Direct slice** — no subprocess | Stored (`-m0`), unencrypted, non-solid, not split, not spanning volumes | A read of the source range. Measured: reading every member of `basic_nonsolid__.rar` spawns **zero** processes |
| **Named `unrar p`** | Any member the row above does not cover — which in a solid archive is normally all of them, since the direct-slice test includes the member's own solid flag rather than the archive's | One process per open, and in a solid archive each decodes from the archive start. Concurrent opens do not share that work: three overlapping reads are three live processes and three full decodes |
| **One unnamed `unrar p` pipe** | A streaming pass over a solid archive | One process for the whole pass. Measured on `basic_solid__.rar`: one streaming pass = 1 spawn; opening each of its 4 members = 4 |

The pipe is spawned on the **first read into the pass**, not at pass start, so listing a
solid archive through `stream_members()` — or an extraction whose selector matches nothing —
never starts `unrar` and is never asked for a password.

**The argv is constructed defensively, because the member name is attacker-controlled.**
`unrar p -inul [-ver] (-p | -p-) [-n./<member>] <archive>`:

- **The member is never positional.** It is the value of the `-n` include mask, prefixed
  `./`. Passed positionally, a member literally named `-inul` is parsed by `unrar` as a
  *switch* — which drops the filter, so `unrar` prints **every** member's data concatenated
  and exits 0, and the caller asking for one member receives another's bytes; and a member
  named `@atfile` is parsed as a **list-file**, making `unrar` open an attacker-chosen local
  path. Both were confirmed end to end against committed fixtures
  ([`unrar-boundary.md`](../../review/archive/2026-07-16-rar-reader/unrar-boundary.md) F3).
  A `--` end-of-switches guard fixes the first and **not** the second — `@` expansion still
  happened after `--` — which is why the include mask is the fix: inside `-n`, a leading `-`
  is not a switch, and a value starting with `.` is not a list-file. The `./` also anchors
  the mask to the exact archive path instead of matching a basename at any depth.
- **`*` and `?` are refused.** `unrar` masks treat those as wildcards with **no escape** —
  `[` and `]` are literal and `\` does not escape — so a name containing one cannot be
  addressed to exactly one member. Rather than risk emitting a different member's bytes, the
  read raises `UnsupportedFeatureError`. This applies to the `unrar` route only: the same
  name on the direct-slice route reads fine.
- **`-ver` is added** when the target is a history row, or when a solid pass contains any
  versioned payload FILE, because the mask excludes history rows otherwise and the demux
  would go out of alignment.
- **The password goes to stdin**, not into argv: the switch is a bare `-p` and the secret is
  written to the child's stdin, so it never appears in `/proc/<pid>/cmdline`. With no
  password the switch is `-p-`, which disables the interactive prompt so `unrar` cannot
  block on stdin.

**A path is required, and a stream source pays for it.** `unrar` cannot read the archive
from a pipe, so the first member that needs it triggers a copy of the **entire archive** to
`tempfile.mkstemp(suffix=".rar")` — mode `0600`, removed on reader close. There is no
diagnostic and no `CostReceipt` note: measured on a `BytesIO` source, `cost.notes` and
`diagnostics` are byte-identical to a path source. The trigger is per-member, so a stored
member costs nothing and the next compressed member in the same archive costs a full copy.
[`open-issues.md`](../open-issues.md) P11.

**What crosses back is an exit code and a byte count.** `-inul` suppresses `unrar`'s
messages and its stderr is discarded, so archivey reconstructs every data error from those
two signals:

| Signal | Mapped to | Note |
| --- | --- | --- |
| exit 11 | `EncryptionError` | RARLAB's bad-password code |
| exit 2 or 3, encrypted member, zero bytes out | `EncryptionError` | RAR4 reports a wrong password this way rather than as 11. A genuinely corrupt encrypted member that also emits nothing is mislabelled; the bias is deliberate, since a caller cannot make progress on either without the right password |
| exit 2 or 3 | `CorruptionError` | **Only when archivey has no hash of its own.** A member with a CRC32 or BLAKE2sp is verified here, and that check is authoritative — `unrar`'s code is suppressed to avoid legacy-format false positives |
| exit 10, named open | `CorruptionError` | "no files matched" — **also suppressed** when archivey has a hash, and the read then fails from the fused length check as `TruncatedError` instead, which is the observable difference |
| exit 0 or 1 | pass | |
| negative | pass | archivey terminated the process on an early close |

Mapping runs on the completing (empty) read as well as on close, so a fault surfaces from
`read()` rather than only from `close()`. Independently of the exit code, every RAR member
read is bounded by its declared size and checked against its digest in one fused stage:
over-long is `CorruptionError` at the boundary, short is `TruncatedError`. That is what
covers the members `unrar`'s exit code cannot speak for — a hash-less member, a partial read,
or an empty stream that reaches EOF cleanly.

**Without the binary**, a compressed or encrypted read raises `PackageNotInstalledError`
naming RARLAB `unrar` and naming the lookalikes that are *not* accepted; listing and stored
reads are unaffected. There is no silent fallback (§3, threat-model C1).

### 2.4 Extract

Path traversal, symlink escape, name collisions, cross-platform name safety and the
byte/ratio/member caps are the shared extraction spine —
[`safe-extraction`](../../openspec/specs/safe-extraction/spec.md) and
[`threat-model.md`](../threat-model.md). A blocked member does not end the run.

Three things are RAR's own. File-version history rows are skipped by default and recorded
as `SUPERSEDED`, through the spine's existing `is_current=False` behaviour — their `path;n`
names are unique, so the shared last-entry-wins pass leaves them alone. Extracting a solid
archive rides the single streaming pipe rather than opening members one at a time. And the
spec's alternative — one `unrar x` into a managed temp directory, for either repeated solid
random reads or `extract_all()` — is permitted and **not implemented**, so repeated random
opens of a large solid archive cost one full decode each.

### 2.5 Write

Not shipped, and not RAR-specific: no format has a writer. RAR would be the least likely
candidate regardless — the compressor is the half of the format the licence explicitly
forbids reimplementing.

## 3. In the wild

**The format is defined by one vendor's tool, and that tool is not redistributable.** RAR
compression is proprietary with no published decompressor specification; RARLAB `unrar` is
freeware, and the `rar` *writer* is trialware. So the same archivey code path works or
fails depending on what the host has installed, and listing and reading have different
requirements. That asymmetry is the whole reason for the native-metadata split.

**Nothing else on a normal machine is a safe substitute**, which is why the fallback is
refused rather than merely discouraged. Measured across the candidates
([`alternative-rar-decompressors.md`](../investigations/alternative-rar-decompressors.md)):

| Candidate | Verdict |
| --- | --- |
| **`unar` / MacPaw XADMaster** | **Still open as a candidate, and silently wrong today.** On a RAR5 **solid** archive containing any empty FILE, reading a *non-empty* member fails — Debian's 1.10.1 SIGSEGVs with 0 bytes, and the newer 1.10.7/1.10.8 lineage (what Homebrew ships) exits **0 with empty output**, on stdout *and* on extract-to-disk. The newer behaviour is the dangerous one, and skipping the empty members in the argv does not help; the solid decoder still walks that slot. It matches `unrar p` on everything else measured, which is why it is not closed — see below. [`known-issues.md`](../known-issues.md) |
| **`7z`** | A codec lottery, and short of what this backend needs even when it wins. Ubuntu's `7zip` advertises RAR under *Formats* while the *Codecs* list has no `Rar5` until `7zip-rar` is installed — so it lists and extracts stored members, then says `Unsupported Method` on anything solid or typically compressed. With the plugin the ALL-pipe matches `unrar p` on our fixtures, but it takes the password **on argv**, reports a **missing member as rc=0**, and cannot address **`path;n`** — the three things §2.3, §4 and file-version reads depend on. And it is still a RARLAB-derived non-free codec under another name. Homebrew's `7zz` compiles it out entirely |
| **`bsdtar`** | No solid, no password — and on a stored non-solid fixture, `--to-stdout` wrote **~7 GB** before the probe harness capped it, from an archive of a few KiB |
| **`unrar-free` 0.1.3** | Extract-to-disk only; no stdout at all |

`7z`, `bsdtar`, `unrar-free` and Homebrew's `7zz` are **closed**. **`unar` is not** — it is
the one candidate still on the table, because Homebrew dropping the `rar` cask made macOS
the hard install (below) and `brew install unar` is easy. Three things would have to happen
before it could ship, and none has: an **early-fail gate** in the backend, refusing
`format == RAR and info.is_solid and any FILE with size == 0` from the native listing alone
before `unar` is ever spawned (gating RAR4 too, conservatively); the fixture matrix run
against a **Homebrew bottle**, since the measurements above are apt and a local build; and
the XADMaster bug **filed upstream**. The gate's predicate is generalized from one fixture
family, and ANTI members and packed-nonzero/unpacked-zero empties are untested — so it may
be under-inclusive. What is not on the table under any of that is a silent fallback: a
second engine would be an explicit opt-in, never a probe of `PATH` (threat-model C1).

**Writing RAR4 needs an old binary.** RAR 7 dropped `-ma4`, so `scripts/gen_rar_fixtures.py`
downloads a checksum-pinned RAR 6.24 into the user cache purely to build the RAR4 fixtures.
Any RAR4 archive in the wild today was written by something older than a current WinRAR.

**The writer being trialware is also why the corpus fixtures are committed.** The declarative corpus builds each entry
in every format it declares, and eight entries declare `rar`. All eight ran **nowhere**:
building them needs the trialware writer, which CI does not install, so `skip_unless_runnable`
skipped the whole column while the sweep reported green. When the writer was finally installed
and the column run, four of the eight failed, in two shapes — and the instructive part is that the
first diagnosis was wrong. Both shapes looked like stale assertions; one was, and the other
was the assertion finally doing its job and catching a reader bug (the RAR5 redirect digest,
§2.2). With both corrected the suite went from 2 284 passed / 65 skipped to **2 326 / 23** —
42 tests that had previously run nowhere. The archives are now committed under
`tests/fixtures/corpus/rar/` and pinned by a manifest, against the corpus's own
generate-everything design, because the alternative made the test matrix a licensing
decision (ADR [0016](../decisions/0016-committed-rar-corpus-fixtures.md)).

**macOS is the hard install.** Homebrew disabled the `rar` cask over Gatekeeper: RARLAB's
own macOS `unrar` is ad-hoc signed on ARM and unsigned on Intel, with no notarization ticket,
so a quarantined download is blocked. Homebrew core will not ship an `unrar` formula either
— that removal was the licence, in 2020, and is independent of Gatekeeper. Published user
guidance is [`docs/install.md`](../../docs/install.md#getting-rarlab-unrar); CI compiles a
pinned RARLAB source tree instead of trusting a third-party tap.

**Old archives are still readable and still surprising.** RAR 1.5 and 2.x archives (extract
version ≤ 20) share the RAR3 block layout, so they list and read; extract version alone is
never a rejection. `rar15-comment.rar` and `rar202-comment-nopsw.rar` are borrowed from
`rarfile`'s own corpus because modern `rar` cannot emit them. RAR3's two name fields are the
other legacy trap: the compressed name is UTF-16, which truncates a non-BMP character to a
single code unit — an emoji arrives as a private-use `U+F600` — and the 8-bit field is
preferred where it recovers the real character, without overriding a private-use character
that is genuinely present in both.

`rarfile`'s full test corpus can be run against the native parser as an oracle by pointing
`ARCHIVEY_RARFILE_TEST_FILES` at its `test/files` directory; it is opt-in and skips by
default.

## 4. Threat surface

RAR-specific only. General extraction and name hazards are §2.4.

- **The member name is an argument to another program.** This is the format's distinguishing
  hazard and the one no other backend has: CWE-88 argument injection reachable by anyone who
  can hand over an archive, with two outcomes — the wrong member's bytes returned at exit 0,
  and an arbitrary local-file read driven by a `@`-prefixed name. Closed by the `-n./` include
  mask plus the wildcard refusal (§2.3), and the spec's "constrain unrar argv by call site"
  requirement is what it was written to protect. Worth remembering that the *stated*
  constraint was already satisfied when the hole existed: the backend controlled the argv it
  intended to build, and the hostile name axis was the one nobody had enumerated.
- **Listing is attacker-controlled work with no decompression.** A small file can declare an
  enormous member table; the parser ceiling of 1 048 576 bounds the walk before
  `ListingLimits` are evaluated at materialization. [`threat-model.md`](../threat-model.md) O1.
- **RAR3 names are themselves compressed, and that decode is unbounded.** The *retained*
  name bytes are roughly 1:1 with header bytes, because a successful decode consumes one
  8-bit-field byte per emitted character. The **transient** cost is not. `_UnicodeFilename`
  has an RLE branch that emits up to 129 UTF-16 code units per encoding byte, and when the
  8-bit name field runs out `_std_byte()` marks the decode failed, **returns `?` and lets the
  loop continue** — so an empty 8-bit field bounds nothing, and the buffer is built in full
  before being discarded. Measured: 5 001 bytes of crafted encoding data decode to 516 000
  characters, ~103 per input byte; end to end, a hand-built RAR3 archive costs about 45
  seconds of CPU per megabyte of input inside `parse_rar_archive`, linear in the input, with
  a transient buffer of ~13 MB per 64 KiB header. Same class as the vint loop above — a byte
  count the attacker chooses, spent before any member exists — and the one bound the parser
  is still missing. Not registered anywhere yet; the reproducer is in §8.
- **Variable-length integers are a CPU bomb, not a memory one.** The input chooses how many
  continuation bytes to supply, so a decoder that re-copies its accumulated bytes each
  iteration is quadratic in a length the attacker picks — a few megabytes of `0x80` burned CPU
  proportional to the square of a length the attacker picked, with nothing allocated and
  nothing obviously malformed to reject. Bounding
  the decoded *value* does not help; the bound has to be on bytes consumed. Fixed; the
  mutation and Atheris harnesses would not have found it, because a multi-kilobyte run of one
  byte is not a shape bit-flip mutation produces.
- **The solid pipe is demultiplexed from the archive's own numbers, against a policy that
  is in no field.** A crafted size, or a member kind whose emission behaviour archivey
  models wrongly, shifts every subsequent member. The uncomfortable part is that no stored
  size predicts what `unrar p` prints, and the two generations fail in opposite directions —
  measured on the `symlinks_solid__` pair, where every link emits **zero** bytes:

  | | RAR5 link | RAR4 link |
  | --- | --- | --- |
  | packed size | **0** | 6–12 |
  | unpacked size | 6–12 | 6–12 |
  | bytes `unrar p` emits | 0 | 0 |

  So keying the demux on `packed > 0` is wrong for RAR4 and keying it on `unpacked > 0` is
  wrong for RAR5; the only correct predictor is `unrar`'s own semantic rule — print
  regular-file data, skip directories, links and copies — which `is_payload_file()`
  re-implements. Per-member digest verification is the backstop, so a desync surfaces as a
  checksum failure rather than as silent wrong data, and pinning the emission rule per
  generation is the named hardening ([`open-issues.md`](../open-issues.md) P6).
- **A stream source materializes the archive to disk.** The temp file is `0600` and the temp
  volume directory `0700`, and both are removed on close; the exposure is disk space and
  lifetime, not readability by other users. The unsignalled cost is P11.
- **The password is kept off the process table** by going to `unrar`'s stdin (§2.3), which is
  otherwise inherent to delegating to a CLI.
- **Encrypted members expose no plaintext digest**, by design of the format rather than by
  our choice — the tweaked MAC exists so the stored value cannot confirm a guess about the
  content (§2.2).

## 5. Sharp edges

*Where it lives*: **format** — inherent, no implementation fixes it · **library** — the
`unrar` binary's behaviour, fixable only upstream or by replacing it · **archivey** — ours.

| What you see | Where it lives | More |
| --- | --- | --- |
| Listing an archive works on a machine where reading it fails | **format** | The compressor is proprietary and its reference tool is non-free, so no distribution installs it by default and no pip extra can ship it (§1, §3). `PackageNotInstalledError` names it and names the lookalikes that will not be accepted |
| `seekable_members=True` is accepted, and the member stream is still not seekable | **archivey** | Honoured only on the direct-slice route. An `unrar`-backed member is a pipe: `seekable()` is `False` and `seek()` raises `io.UnsupportedOperation`, while `reader.member_streams` still reports `SEEKABLE` and no diagnostic is emitted. [`docs/access-and-cost.md`](../../docs/access-and-cost.md) describes the flag's "otherwise" case as re-decompressing from the start, which is not what happens here. No register row covers it yet |
| Reading one member of a solid archive out of order decodes the whole archive, and doing it twice decodes it twice | **format** / **archivey** | No per-block boundaries to resume from (§1); the spec's temp-directory strategy that would amortize it is unimplemented (§2.4). `AccessCost.SOLID` is the signal |
| Handing over a `BytesIO` writes a full-size copy of the archive to `/tmp`, with nothing in `diagnostics` or `cost.notes` | **archivey** | `unrar` needs a path. The trigger is per-member, so the first stored member is free and the next compressed one is not. [`open-issues.md`](../open-issues.md) P11 |
| A member whose name contains `*` or `?` cannot be read, though it lists fine | **library** | `unrar` include masks treat both as wildcards and offer no escape, so the member cannot be addressed unambiguously; `UnsupportedFeatureError` is preferred over possibly returning another member's bytes (§2.3). A stored member of the same name reads fine, since it never reaches `unrar` |
| A corrupt encrypted member can be reported as a wrong password | **library** / **archivey** | `unrar` reports both as exit 2/3 with empty output on RAR4 and exposes no signal to separate them — that half is upstream's. Resolving the ambiguity toward `EncryptionError` is ours and is reversible (§2.3) |
| An SFX archive that is also split is unreadable from every one of its files | **archivey** | Sibling discovery needs the archive extension immediately before the part number; an SFX first member replaces it. Shared with 7z and ZIP — [`open-issues.md`](../open-issues.md) P17 and [`topics/prefixed-archives.md`](../topics/prefixed-archives.md) §6 |
| A RAR on a pipe or socket cannot be opened at all, in either access mode | **format** | Block headers are chained forward but the walk still seeks; nothing is buffered for you (ADR [0010](../decisions/0010-no-silent-buffer-nonseekable.md)) |
| `encoding=` is accepted and has no effect | **archivey** | RAR names are decoded by the parser. The call logs that the value will not be applied rather than failing |
| A compressed member reports its compression as `UNKNOWN` with a level | **format** | The algorithm is proprietary and has no public name to report. The level is what the header carries |
| Every RAR5 symlink and hard link has no `hashes` entry, where ZIP and 7z have one | **format** | The stored field covers zero bytes, so the only honest answer is no digest (§2.2). RAR3/4 keeps its genuine one |
| Opening several members of a solid archive at once runs one whole-archive decode **per open**, concurrently | **format** / **archivey** | There are no block boundaries to share (§1), so `concurrent_members=True` makes overlapping reads correct without making them cheap: measured, three open streams are three live `unrar` processes, each decoding from the start. Teardown is clean — all three are reaped on close. `AccessCost.SOLID` is the only signal and it does not scale with the number of open streams |
| A RAR 1.5 / 2.x archive comment is not returned, though `rarfile` returns it | **archivey** | The parser skips old-style embedded comment sub-blocks by design. Measured on the two legacy fixtures: `rarfile` gives `'RARcomment -----'` and `'RARcomment'`, archivey gives `None` for both. RAR5 and RAR3 comments are read normally |
| `member.comment` is always `None`, including where a RAR3 solid comment block exists | **archivey** | The block is parsed into `_raw.comment` and never mapped onto the member (§2.2) |

## 6. Decisions

| Choice | Why | Rejected |
| --- | --- | --- |
| Native metadata parser; `unrar` for member data only | Listing works with no binary and no `rarfile` dependency, and archivey's cost and streaming model is not bent to another library's | `rarfile`, which couples listing to its own decompressor stack — kept as a test oracle (ADR [0002](../decisions/0002-native-rar-metadata-unrar-data.md)) |
| RARLAB `unrar` **only**, no silent fallback | The alternatives are measurably worse in ways a caller cannot see: `unar` returns empty files with a success exit on a whole archive class, `7z` depends on a plugin that may or may not be installed, `bsdtar` writes gigabytes on a stored member. A degraded backend chosen behind the caller's back is the failure mode `PackageNotInstalledError` exists to prevent | Probing `PATH` the way `rarfile` does (threat-model C1, [`alternative-rar-decompressors.md`](../investigations/alternative-rar-decompressors.md)) |
| Pass the member as `-n./<name>`, never positionally | It is the only construction that neutralizes both hostile prefixes; `--` handles the switch case and leaves `@listfile` expansion intact | `--` alone; shell quoting (there is no shell — argv is a list) |
| Refuse a name containing `*` or `?` on the `unrar` path | `unrar` masks have no escape for them, so the alternatives are a wrong member's bytes or nothing | Passing the name through and relying on the CRC to catch the mismatch |
| Trust archivey's own digest over `unrar`'s exit code | Two authorities disagreeing about corruption produce false positives on legacy archives; the one that checks the bytes we actually returned wins. Exit codes stay the fallback for members with no hash | Mapping every non-zero exit unconditionally |
| Password on stdin, not in argv | Command-line arguments are world-readable through the process table for the life of the subprocess | `-p<password>`, which is what the CLI documents |
| A stream source gets a temp **file**, not a pipe | `unrar` seeks the archive and refuses every non-seekable input, so there is no streaming option to prefer — the only real choices are where the seekable copy lives and how much of the archive it holds (§1) | Piping the archive, or piping a synthesized header plus one member's compressed block — both refused before a byte is read. `rarfile`'s own version of that trick is a small temp *file* for the same reason, and it falls back to a whole-archive temp file exactly where we do |
| Spawn the solid pipe on first read, not at pass start | A pass nobody reads from — listing through `stream_members()`, an extraction whose selector matches nothing — should cost no process and should never prompt for a password | Opening the pipe when the pass begins |
| Solidity is one archive-level flag, `solid_block_count = None` | RAR exposes no block boundaries, so any number would be invented. `None` says "unknown", which is true | Reporting 1, which reads as "one small block" |
| A RAR5 redirect surfaces no digest; RAR3/4's is kept | Keying on the storage shape rather than the member type keeps a genuine digest where one exists and drops a constant that describes nothing. The value dropped is exactly the one a de-duplicating caller would read | Keying on member type, which would have thrown away RAR3/4's real digest; surfacing `crc32(b"")` for symmetry |
| Commit the RAR corpus archives, pinned by a manifest | Otherwise the corpus's RAR column is a licensing decision and runs on Linux only, while the release headlines a native RAR reader | Installing the trialware writer on CI; reworking digest expectations for a platform dependence that measurement showed does not exist (ADR [0016](../decisions/0016-committed-rar-corpus-fixtures.md)) |

## 7. Open questions

Gaps in what *we* know — each would change something here if answered, and none can be
settled by reading more code. Distinct from §5, which is behaviour a caller already sees.

- **Should the stream-source temp copy report its cost?** The whole-archive copy is in
  neither channel today; [`open-issues.md`](../open-issues.md) P11 owns the question and
  leans toward a `CostReceipt.notes` entry over a diagnostic. What is genuinely open here is
  whether the *boundary* deserves its own note rather than the copy — the sibling cost, an
  out-of-order solid `open()` being a whole decode each time, is **already decided**:
  [`open-issues.md`](../open-issues.md) P9 says not a diagnostic, because `access_cost`
  already carries it, and only a once-per-reader `warnings.warn` is still parked.
- **Should the stream-source copy go to RAM instead of disk?** An anonymous `memfd` is
  seekable, never enters the filesystem namespace, and is freed on close, and `unrar` reads
  one happily (§1) — so it is a real alternative to `mkstemp` for a single-volume archive.
  What it does *not* do is make the copy smaller: it trades unbounded disk for unbounded RAM,
  which for a large archive is the worse of the two, and it is Linux-only, and it cannot serve
  a volume set at all because `unrar` needs sibling names on disk (§2.2). So the question is
  not "memfd or temp file" but whether there is a size below which RAM is obviously right —
  and that threshold interacts with the deferred small-member optimization, which would bound
  the copy to one member and make the disk-vs-RAM question much less interesting. Nobody has
  measured either.
- **What should `seekable_members=True` do on a pipe-backed member?** Three defensible
  answers — refuse the flag for RAR the way a non-seekable source is refused, honour it by
  respawning `unrar` on a backward seek (which is a whole-archive decode for a solid member
  and an O(member) one otherwise), or report through `CostReceipt` that it was not applied.
  Today it is accepted, quietly not honoured on the `unrar` route, and honoured on the direct
  one, so the same archive answers differently per member. No measurement exists for what a
  respawn would cost, and no corpus evidence for how often a caller seeks inside a RAR member
  at all.
- **Should `unar` become an opt-in second engine?** It is the one candidate the
  decompressor matrix left open, and Homebrew dropping the `rar` cask is what keeps it open
  (§3). Blocked on three things nobody has done: the fixture matrix against a Homebrew
  bottle rather than apt and a local build, an upstream XADMaster report, and a judgement on
  whether the early-fail gate predicate is under-inclusive — it is generalized from one
  fixture family, and ANTI members and packed-nonzero/unpacked-zero empties are untested.
  None of that is answerable by reading code.
- **Is the wrong-password-versus-corruption bias measurable, or only plausible?** The exit-2/3
  mapping for an encrypted member that emits nothing assumes wrong passwords vastly outnumber
  corrupt encrypted members. That is a reasonable prior and it is untested: no archive has
  turned up in our corpora where a genuinely corrupt encrypted member produced this shape, so
  the cost of the mislabel is unknown.

## 8. Verify

```bash
./scripts/test.sh tests/test_rar_reader.py tests/test_rar_oracle.py \
    tests/test_rarfile_corpus.py tests/test_volumes.py tests/test_sfx.py
```

About 24 of the ~140 tests those five files hold are `unrar`-gated, and they **skip
quietly**. The parser, detection and SFX tests all still run, so a container without the
binary reports green with the entire data path untested — which is the trap, not the count
(`AGENTS.md` §Session setup).

Two claims here are about the **external binary** rather than about archivey, so no test can
hold them — they are probes instead, re-runnable when a new `unrar` lands:

```bash
python3 scripts/exploration/rar_unrar_input_matrix.py       # §1 input modes, §2.2 volumes, §4 emission
python3 scripts/exploration/rar_decompressor_matrix.py      # §3 the decompressor table
```

| Claim | Pinned by |
| --- | --- |
| Cost receipt: `DIRECT` for a nonsolid archive, `SOLID` with `solid_block_count is None` for a solid one | `tests/test_cost_receipt.py::test_cost_receipt_per_format[rar]`, `tests/test_rar_reader.py::test_basic_solid_stream_and_random` |
| Listing and stored reads with **no binary on `PATH` at all**, and a compressed read there naming RARLAB `unrar` | `tests/test_rar_reader.py::test_listing_and_stored_reads_need_no_unrar` |
| A stored nonsolid archive is read end to end with zero subprocesses (the §2.3 measurement) | `::test_stored_nonsolid_archive_spawns_no_unrar_process` |
| The finder rejects a missing or non-RARLAB binary, and the message names the lookalikes | `::test_missing_unrar_raises`, `::test_unrar_not_installed_message_names_lookalikes`, `::test_non_rarlab_unrar_rejected` |
| A non-RARLAB binary on `PATH` is rejected, and the one we run is RARLAB's | `::test_non_rarlab_unrar_rejected`, `::test_unrar_on_path_is_the_rarlab_build` |
| A solid pass spawns `unrar` only on the first read | `::test_solid_pass_spawns_unrar_only_on_the_first_read` |
| Hostile member names (`-inul`, `@atfile`) read **their own** bytes, RAR4 and RAR5 | `::test_hostile_member_name_reads_its_own_bytes` |
| A wildcard in a member name is refused rather than mis-addressed | `::test_unrar_member_include_switch_rejects_wildcards` |
| The password reaches `unrar` on stdin, not in argv | `tests/test_crypto_findings.py::test_f4_password_arg_is_bare_or_dash`, `::test_f4_password_passed_via_stdin_not_argv` |
| Exit-code mapping: 11, 2/3, 10, hash-present suppression, solid-pipe suppression, negative rc | `tests/test_rar_reader.py::test_unrar_owned_stream_maps_exit_11_to_encryption_error` and the nine tests after it |
| A missing stdout pipe is a typed error, not a `RuntimeError` | `::test_open_unrar_p_missing_stdout_pipe_is_typed` |
| Header-encrypted listing with a password, and a wrong password as `EncryptionError` on both generations | `::test_encrypted_header_lists_with_password`, `::test_header_encryption_wrong_password_is_encryption_error` |
| Encrypted member data requires a password | `::test_encrypted_data_requires_password` |
| Tweaked digests kept out of `hashes`, and BLAKE2sp verified / cross-checked against `unrar` | `::test_blake2sp_only_hash`, `::test_blake2sp_verified_no_unverifiable_diagnostic`, `::test_blake2sp_corrupt_payload_raises`, `::test_blake2sp_unrar_oracle_crosscheck` |
| RAR5 redirect digests dropped without losing RAR4's genuine ones | `tests/test_review_simplicity_consistency.py::test_rar4_link_digests_survive_the_rar5_fix`, `tests/test_corpus_sweep.py::test_corpus_conformance` (8 RAR entries) |
| Solid symlink / hardlink demux does not consume pipe bytes | `tests/test_rar_reader.py::test_solid_symlink_demux_and_link_targets`, `::test_solid_hardlink_demux_and_targets` |
| File-version rows list, read, stay out of `extract_all`, and keep solid demux aligned | `::test_file_version_list_and_read`, `::test_file_version_extract_all_skips_history`, `::test_file_version_solid_demux_aligned` |
| Volume sets (`partN` and `.rNN`), stream volumes, and refusal of an incomplete or later-first set | `::test_multi_volume_roundtrip`, `::test_multi_volume_rnn_roundtrip`, `::test_multi_volume_stream_materialization`, `::test_incomplete_multi_volume_raises`, `tests/test_volumes.py::test_discover_rar_part_volumes`, `::test_discover_old_rar_rnn_volumes`, `::test_multi_volume_rar_opens_volume_set_or_rejects_stub` |
| RAR 1.5 / 2.x list and read; extract version ≤ 20 is not a rejection | `tests/test_rar_reader.py::test_rar15_and_rar2_list_and_read`, `::test_extract_version_20_payload_accepted` |
| RAR3 non-BMP name recovery from the 8-bit field | `::test_fix_rar3_astral_truncation`, `::test_rar3_non_bmp_filename_not_truncated` |
| Bounded hostile parsing: the header-size vint, hostile packed sizes, hostile modes, out-of-range timestamps | `::test_rar5_header_size_vint_is_bounded`, `::test_load_vint_single_and_multi_byte`, `::test_rar5_hostile_packed_size_is_corruption`, `::test_rar_reader_masks_hostile_unix_mode`, `::test_rar5_out_of_range_windowstime_is_tolerated` |
| The >4 GiB RAR3 packed skip, and split-continuation identity checks | `::test_rar3_large_packed_member_skips_full_64bit_size`, `::test_rar3_mismatched_split_continuation_is_corruption` and the three tests after it |
| Member-table ceiling at parse, `ListingLimits` at materialization | `::test_rar_parser_bounds_member_count`, `::test_rar_members_enforces_listing_limits` |
| The SFX needle validator, its `DAMAGED` verdict, and a decoy skipped for the real payload | `tests/test_sfx.py::test_rar_main_header_validator`, `::test_rar_main_header_validator_crc_fail_is_damaged`, `::test_rar_clamped_header_peek_is_valid_when_remaining_is_known`, `::test_mz_rar5_crc_fail_skips_to_the_real_payload`, `::test_shebang_script_mentioning_rar_magic_is_not_rar`, `::test_shebang_plus_real_rar_detects` |
| Metadata and bytes match `rarfile` on our fixtures and on the corpus | `tests/test_rar_oracle.py::test_native_rar_matches_rarfile_metadata_and_bytes`, `::test_corpus_rar_matches_rarfile` |
| Mutation and coverage-guided fuzzing of the header walk | `tests/fuzz_rar_parser.py::test_parse_rar_archive_fuzz_harness`, `tests/test_mutation_fuzz.py` (`basic-solid-rar5` / `basic-solid-rar4` entries), Atheris targets `rar_header` and `rar` |

**Building fixtures.** `uv run python scripts/gen_rar_fixtures.py` regenerates most of
`tests/fixtures/rar/`; it needs the RARLAB `rar` writer, and because RAR 7 dropped `-ma4` it
downloads a checksum-pinned RAR 6.24 into the user cache for the RAR4 variants. The corpus
archives under `tests/fixtures/corpus/rar/` are committed and manifest-pinned instead
(ADR 0016), so the corpus RAR column runs everywhere `unrar` exists. The two RAR 1.5 / 2.0
comment archives are borrowed from `rarfile` and cannot be regenerated. The hostile-argv
fixtures were built by `make_hostile_fixtures.py` in the review folder and pushed by the
maintainer, since the review container had no writer. Four tests still shell out to `rar a`
at runtime and skip without it — three SFX tests and a live multi-volume roundtrip; the gap
and what would close it are in
[`tests/fixtures/rar/README.md`](../../tests/fixtures/rar/README.md).

## 9. References

- Format: **no numbered spec is cited here, and that is a gap rather than a fact about the
  format.** RARLAB ships a `technote.txt` describing the RAR 5.0 archive layout inside the
  UnRAR source tarball — the tree `scripts/install-rarlab-unrar.sh` already compiles — and
  this repo cites it once elsewhere (`review/archive/2026-07-19-api-coherence/QUESTIONS.md`).
  It was not reachable from this container (rarlab.com is 403 through the egress proxy), so
  nobody has checked what it covers. If it covers the RAR5 block layout, this section should
  cite its sections the way [`zip.md`](zip.md) cites APPNOTE's. RAR3/4 has no published
  description regardless. The compressor is documented nowhere; the header layout here
  follows the `archivey-dev` `rar-native-metadata-reader` exploration, and the RAR3 SHA-1
  string-to-key and Unicode filename decompression are adapted from
  [`rarfile`](https://github.com/markokr/rarfile) 4.3 under the ISC licence
- Specs: [`format-rar`](../../openspec/specs/format-rar/spec.md) ·
  [`access-mode-and-cost`](../../openspec/specs/access-mode-and-cost/spec.md) ·
  [`safe-extraction`](../../openspec/specs/safe-extraction/spec.md) ·
  [`packaging-and-extras`](../../openspec/specs/packaging-and-extras/spec.md)
- Code: `internal/backends/rar_parser.py` (headers, both generations, volumes, header
  crypto) · `rar_reader.py` (member mapping, solid demux, temp materialization, exit
  mapping) · `rar_unrar.py` (binary discovery, argv construction) · `internal/rar_detect.py`
  (SFX hit validator) · `internal/volumes.py` (sibling discovery, shared with 7z and ZIP)
- Decisions: ADR [0002](../decisions/0002-native-rar-metadata-unrar-data.md) (native
  metadata, `unrar` data) · ADR [0016](../decisions/0016-committed-rar-corpus-fixtures.md)
  (committed corpus fixtures) · ADR [0003](../decisions/0003-member-streams-opt-in.md)
  (member streams opt-in) · ADR [0010](../decisions/0010-no-silent-buffer-nonseekable.md)
  (no silent buffering)
- Review: [`2026-07-16-rar-reader`](../../review/archive/2026-07-16-rar-reader/SUMMARY.md)
  — F1–F6 with
  [`unrar-boundary.md`](../../review/archive/2026-07-16-rar-reader/unrar-boundary.md) and
  [`hostile-input.md`](../../review/archive/2026-07-16-rar-reader/hostile-input.md)
- Investigations:
  [`alternative-rar-decompressors.md`](../investigations/alternative-rar-decompressors.md)
  (the decompressor matrix, macOS install) ·
  [`rar-corpus-sweep-diagnosis.md`](../investigations/rar-corpus-sweep-diagnosis.md)
  (why the RAR column ran nowhere; the cross-format symlink digest table)
- Probes: `scripts/exploration/rar_unrar_input_matrix.py` (input modes, volume discovery,
  emission policy) · `scripts/exploration/rar_decompressor_matrix.py` (the §3 table). The
  input-mode and emission questions were first investigated in
  [PR #101](https://github.com/davitf/archivey/pull/101), which was never merged; its
  conclusions are stated here and its measurements are what the first script re-runs, so the
  PR is provenance rather than a live reference
- Registers: [`open-issues.md`](../open-issues.md) P6, P11, P17 ·
  [`threat-model.md`](../threat-model.md) O1, C1 · [`known-issues.md`](../known-issues.md)
  (MacPaw `unar` silent-wrong)
- Topic: [`prefixed-archives.md`](../topics/prefixed-archives.md) (the shared SFX machinery)
- User-facing: [`docs/formats.md`](../../docs/formats.md#rar) ·
  [`docs/install.md`](../../docs/install.md#getting-rarlab-unrar) ·
  [`docs/gotchas.md`](../../docs/gotchas.md)
