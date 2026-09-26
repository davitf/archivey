# Formats and extras

What each format can do, what optional packages or tools it needs, and the quirks that
most often surprise callers. For more depth, the maintainer handbook has pages on
[7z](https://github.com/davitf/archivey/blob/main/dev-docs/formats/7z.md),
[RAR](https://github.com/davitf/archivey/blob/main/dev-docs/formats/rar.md) and
[ZIP](https://github.com/davitf/archivey/blob/main/dev-docs/formats/zip.md).

## Quick matrix

| Format | Core? | Extra / tool | Listing | Random member access | Notes |
| --- | --- | --- | --- | --- | --- |
| ZIP | yes | — | indexed (central directory) | direct | Seekable source required |
| TAR | yes | — | scan headers | direct on uncompressed seekable TAR | Compressed TAR is solid for random opens |
| `.tar.gz` / `.bz2` / `.xz` | yes | — | needs decompression | solid | Prefer `stream_members()` |
| Directory | yes | — | indexed | direct | Same stream-capability defaults as archives |
| Single-file gz/bz2/xz | yes | — | one member | seek with `SEEKABLE` | See single-file section |
| 7z | yes (common codecs) | `[recommended]` for PPMd/Deflate64/zstd/brotli/AES | indexed | solid folders | Native reader; BCJ2 unsupported |
| RAR | yes (metadata) | **`unrar` or `rar` binary for data**; `[recommended]` for header crypto | native metadata | solid when solid | No write |
| ISO | no | `[recommended]` (`pycdlib`) | indexed | direct | Seekable source required |
| `.zst` / `.tar.zst` | 3.14+ core; else `[recommended]` | `[recommended]` → `backports.zstd` | — | rewind seek unless indexed later | |
| `.lz4` / `.tar.lz4` | no | `[recommended]` | — | rewind seek | |
| `.Z` / `.tar.Z` | yes | — | — | CLEAR seek points when seekable | Best-effort truncation (nonzero leftover bits) |

**RAR member data needs RARLAB `unrar` or `rar` 6.0 or later on `PATH`.** No pip extra
can supply it — listing and metadata work without it, reading bytes does not.
`rarfile` will use `unar` or `7z` if that is what is on `PATH`; archivey will not.
How to get the binary: [Install and extras](install.md#getting-rarlab-unrar-or-rar).

Recommended install: `archivey[recommended]`, or `archivey[all]` to add the `[seekable]`
rapidgzip accelerator. Full codec rationale: [library analysis](https://github.com/davitf/archivey/blob/main/dev-docs/library-analysis.md).
Third-party credits (deps, oracles, design refs): [Acknowledgements](acknowledgements.md).

## The `extra` bags

`ArchiveMember.extra` is a [`MemberExtra`](api.md#extra-bags) and
`ArchiveInfo.extra` is an [`ArchiveInfoExtra`](api.md#extra-bags).
Both are `dict[str, object]` subclasses: a subscript of a known key
(`extra["zip.compress_type"]`) carries that key's type, and unknown keys
(third-party or future) stay legal and read as `object`. The names are
importable. The `EXTRA_*` constants on `archivey.types` remain the names for
the keys that have constants.

Writes are not type-checked — a wrong-type assignment to a known key is
accepted, same as an unknown key. `.get()` returns `object` for every key.
Assign a `MemberExtra(...)` / `ArchiveInfoExtra(...)` rather than a bare dict;
mutating the existing bag in place is unchanged.

The format sections below mention a key only when it is part of that format's
behaviour. The complete list is on the two classes.

`ArchiveMember.created` is a birth time or `None`, never Unix `st_ctime` (inode
change). Several writers store `st_ctime` where a reader might expect a creation
time: a Rock Ridge ISO, a Unix RAR, a PAX TAR, a 7z written on Unix, and a ZIP
written on Unix by 7-Zip or libarchive. That time is `ArchiveMember.ctime` instead.
RAR, 7z and ZIP have one creation slot, so a member has at most one of the two. Rock
Ridge, and a PAX TAR from libarchive (`LIBARCHIVE.creationtime`), store both times
separately and can have both. For ZIP the writer's host decides: a creation
time is `created` only from a FAT, OS/2, NTFS or VFAT host, and `ctime` from any
other host, unknown included. 7-Zip and libarchive on macOS store `st_ctime` too. A
writer that marks itself Unix while storing a birth time (libarchive on Windows) gets
`ctime`, not `created`, rather than a risk of `st_ctime` in `created`.

## ZIP

- Stdlib ``zipfile`` for **central-directory parsing / listing**; member **data** decodes
  through archivey's shared codec layer (seekable source only, even with
  ``streaming=True``).
- Extended ZIP codecs with ``[recommended]`` installed: Deflate64 and PPMd
  (``inflate64`` / ``pyppmd`` — the same packages the 7z reader uses, which is why no
  extra is named after a format) and Zstd (``backports.zstd``, or stdlib on 3.14+). A
  missing backend raises ``PackageNotInstalledError``.
- Split sets made by 7-Zip's ``-v`` (``name.zip.001``…``name.zip.00N``) open from any
  part, as long as every part is in the same directory: those files are byte slices of
  one ordinary ZIP, and Archivey rejoins them for you. A missing part raises
  ``TruncatedError``.
- Spanned ZIP written by ``zip -s`` (``.z01``…``.zip``) is a different thing — its
  entries are addressed by disk number — and is rejected with
  ``UnsupportedFeatureError``; rejoin it with the tool that made it.
- Unsupported compression methods: listing succeeds; reading raises
  ``UnsupportedFeatureError``.
- Timestamps: DOS base; NTFS / Extended Timestamp extras override when present.
- **Member-name encoding.** Names flagged UTF-8 decode as UTF-8. For an unflagged name
  (APPNOTE says cp437), many tools nonetheless write UTF-8 without setting the flag, so
  Archivey prefers UTF-8 when the stored bytes are valid UTF-8, and otherwise falls back
  to a configurable legacy encoding (`ArchiveyConfig.zip_unflagged_fallback_encoding`,
  default `cp437`). When UTF-8 is inferred for an unflagged name, a
  `member_name_encoding_inferred` diagnostic records it. Passing `encoding=` to
  `open_archive` is authoritative — it is used verbatim and disables the sniff.
- **A wrongly-set UTF-8 flag can make the whole archive unlistable.** When general-purpose
  bit 11 claims UTF-8 but the stored bytes are not, stdlib `zipfile` raises while
  parsing the central directory, so the failure is archive-wide rather than confined to
  the one bad name. A native ZIP reader could recover the other entries; today it
  cannot. Rare, and it fails loudly.
- **ZipCrypto** checks a password against one byte, so a wrong one passes about one
  time in 256. Its data then fails the CRC or the decompressor, and Archivey raises
  `EncryptionError` saying the password may be wrong or the member corrupt, since a
  damaged member read with the right password fails the same way.
- ZipCrypto multi-password confirmation can be expensive on **STORED** members — see
  [access costs](access-and-cost.md). **WinZip AES** (method 99 / AE-1 and AE-2) decrypts via the
  `[recommended]` extra (PBKDF2 + AES-CTR + HMAC-SHA1); AE-2 members expose no `crc32`
  (integrity is the HMAC). Without it, an AES member raises
  `PackageNotInstalledError` but is still listed as encrypted.
- **PKWARE Strong Encryption** is not supported. Such members list as encrypted, and
  opening one raises `UnsupportedFeatureError`. An archive whose central directory is
  itself encrypted this way cannot be listed; Archivey raises `UnsupportedFeatureError`
  when it recognizes the layout, and `CorruptionError` otherwise.
- Encrypted members decode every compression method an unencrypted one does. ZipCrypto
  decryption is pure Python and slow (a few MiB/s); a ZipCrypto member seeks, but a
  backward seek decrypts it again from the start.
- ZipCrypto's check byte and WinZip AES's two-byte password check both admit some wrong
  passwords, so the member's CRC or HMAC at EOF is the real test. With several
  candidate passwords, each one that passes is checked further before it is used.
  Closing a member stream before EOF emits `ENCRYPTED_MEMBER_UNVERIFIED` when only one
  of those short checks accepted the password.

## TAR (and compressed TAR)

- Uncompressed seekable TAR: random access via `tarfile`.
- Compressed variants (`.tar.gz` etc.) behave as **solid** for random member opens —
  prefer a single forward pass.
- Hardlinks are first-class at extraction; unfiltered `extract_all` resolves them in one
  pass.
- `concurrent_members=True` uses a per-reader shared-handle lock (same shape as ISO).
- **Mid-archive corruption can silently shorten the listing.** Stdlib `tarfile` treats a
  corrupt member header *after the first* as a clean end of archive — no exception is
  raised; iteration just stops early. Archivey backstops this with its end-of-archive
  marker check:
    - When the shortened scan stops on a **rejected (non-null) header block**, archivey
      raises `CorruptionError` **by default** — a well-formed tar never ends that way. In
      random-access reads this holds even when the bad header is the archive's *final*
      block.
    - A tar that merely **ends cleanly on a member boundary without the two-block null
      trailer** (a trailer-less or `cat`-joined tar, or a truncation exactly at a member
      boundary — these are byte-identical) is warned about via `ARCHIVE_EOF_MARKER_MISSING`,
      not raised. When a provably complete listing matters (inventory/dedupe sweeps), set
      that code to `RAISE` in the diagnostic policy (`DiagnosticPolicy.strict()` does) to
      turn the warning into `DiagnosticRaisedError`.
    - A **non-zero byte after the trailer** — trailing junk, or a second archive
      concatenated on — is reported as `ARCHIVE_TRAILING_DATA`, also a warning under the
      default policy and raised under `strict()`. Zero padding passes — `tar` writes
      10 KiB records, so "nothing but zeros" is the strongest rule that does not flag
      what `tar` itself produces. The check looks at most 1 MiB past the trailer, so a
      byte further out goes unseen; on a compressed tar that 1 MiB is decompressed to
      inspect it. A tail that does not decompress (junk after the gzip stream, a missing
      gzip footer) ends the check quietly rather than failing the listing.
    - Truncation *inside* a member's data always raises `TruncatedError` during iteration,
      whatever the policy.
  - **Streaming caveat:** a corrupt header as the *final* block is caught in random-access
    reads but not in forward-only streaming, where it surfaces as the missing-trailer
    warning instead. A future native TAR reader may close this gap.

## 7z

- **Native** header parse + stdlib codecs for the common set (LZMA/LZMA2/BCJ/Delta/
  Deflate/BZip2/stored). No `py7zr` on the read path.
- `[recommended]` adds PPMd, Deflate64, Zstd, Brotli, and AES.
- **BCJ2** is detected and rejected (`UnsupportedFeatureError`) — never garbage output.
- Solid folders: `stream_members()` decodes each folder once; random `open()` of a mid-
  folder member may re-decode from the folder start.
- **AES + store/copy with no folder digest and no member CRC:** 7z has no password check
  value; a wrong password can yield garbage (matches 7-Zip). Archivey emits
  `DIGEST_UNVERIFIABLE` (`reason="no_integrity_anchor"`). Treat the payload as unverified.
- **Encrypted-folder password confirmation** decodes only until it can decide: the first
  member CRC covering at least 4 bytes, or 64 KiB of output for a compressed folder,
  whose codec rejects a wrong key within a few bytes. Peak memory is one 64 KiB chunk.
  Wall time grows with folder size only for **store/copy+AES** (or PPMd) whose only CRC
  is at the end of the folder, with several candidates: nothing rejects a wrong key
  before that CRC. Prefer a single known password there. `ExtractionLimits` do not
  apply here.
- **Partial reads after an unconfirmed password** emit `ENCRYPTED_MEMBER_UNVERIFIED`:
  when confirmation stopped at its 64 KiB budget without reaching a CRC, the member's own
  CRC at EOF is the only check, and a stream closed before EOF skips it.
- **Header-encrypted wrong password:** a decoded header with zero file records is
  rejected as `EncryptionError` (never a silent empty listing).
- `NumCyclesPower` is capped at ≤24 or the `0x3F` no-hash sentinel (7-Zip’s own clamp);
  values 25–62 raise `UnsupportedFeatureError`.
- Writing is not shipped in the current release (`py7zr` is a **dev oracle** only).

## RAR

- Metadata / listing: native RAR 1.5–RAR5 parser (works without `unrar`).
- Member **data**: RARLAB `unrar` or `rar` **6.0 or later** on `PATH` (not `unrar-free`,
  `unar`, or `7z` — `rarfile` accepts those last two; archivey does not). `unrar` is
  preferred when both exist. Passwords are passed as bare `-p` with the secret on stdin
  (not in argv). Install: [Getting RARLAB unrar or rar](install.md#getting-rarlab-unrar-or-rar).
- `[recommended]`: header-encrypted RAR5. BLAKE2sp verification needs **no** package —
  it is implemented natively on stdlib `hashlib`. RAR5 members with the HASHMAC flag
  verify tweaked digests via UnRAR’s `ConvertHashToMAC` when a password is available;
  tweaked values are not exposed as plain `member.hashes`.
- **Password lists on encrypted data:** RAR5 records a password check per member, so a
  list is tried in order and the matching password is used. RAR3/4 records none: `unrar`
  is given the first candidate, so put the right password first for those.
- **File-version history (`-ver`):** revision rows appear in `members()` as names like
  `path;1` with `extra["rar.file_version"]` and `is_current=False`; the live path stays
  `is_current=True`. Default extract **skips** non-current rows.
- **Compression:** M0 is `STORED`. M1–M5 is `CompressionAlgorithm.RAR` with `level` 1–5.
  Any other method byte stays `UNKNOWN` (`level` omitted). Unpack version is
  `extra["rar.extract_version"]` on every member whose FILE header recorded one,
  stored included: RAR3 copies the `UNP_VER` byte as stored; RAR5 reports `50`.
- **Glob characters in a stored member name.** `unrar` is told which member to emit with
  an include *mask* built from its name, so a name containing `*` or `?` also matches its
  siblings. Names like this are almost always constructed, so reading such a member
  raises `UnsupportedFeatureError`. On a non-solid archive the extra decode is also
  unbounded: `ExtractionLimits` apply to extraction, not to `open()` / `read()`, and the
  error names how many bytes it would have cost. On a solid archive those bytes are
  already inside `AccessCost.SOLID`, so the error names the flag rather than an
  avoidable extra decode. Set `ArchiveyConfig.rar_allow_glob_member_concatenation=True`
  to read it anyway. Two cases are *not* affected: a glob name that matches no other
  member (the accidental `report*.pdf` beside `report1.pdf`) reads normally with no
  flag, and a solid `stream_members()` pass reads everything, because it builds no mask
  at all. A glob in a *directory* component, or a backslash, is refused outright either
  way.
- Solid archives: one `unrar p` pipe for the whole of `stream_members()`. A random
  `open()` out of order is a separate `unrar` run that decodes from the start of the
  archive each time, so reading *n* members that way costs *n* full decodes — stream them
  in order when you can. With `seekable_members=True`, a backward `seek()` on a compressed
  member is the same cost: a new `unrar` run from the start. `CostReceipt.access_cost` is
  `SOLID` to say so.
- **A member read waits as long as `unrar` does.** `read()` on a RAR member stream has
  no time bound: archivey does not stop an `unrar` process that stalls. A solid archive
  can produce no bytes for a long time while `unrar` decodes the members ahead of the
  one you asked for, so no idle timeout would be safe.
- **Encrypted old-style comments are not decoded.** A RAR 1.5 / 2.x comment block with
  its password or salt flag set gives `comment` as `None`. No available tool writes such
  a comment, so there is nothing to test a decode path against.
- Read-only — no RAR writer.

## ISO 9660

- Needs `[recommended]` (`pycdlib`) and a seekable source.
- `import archivey` patches pycdlib for the whole process: the `collections` name inside
  `pycdlib.pycdlib` becomes one whose `deque` skips a directory extent it has already
  queued. That stops pycdlib looping forever on a directory tree that points back at an
  ancestor. Other code using pycdlib in the same process gets the patch too. A valid tree
  never revisits an extent, so its results do not change.
- Namespace auto-selected: Rock Ridge → Joliet → plain ISO 9660; reported in
  `ArchiveInfo.extra["iso.namespace"]`.
- Plain ISO 9660 names lose their `;N` version suffix (and the `.` of an empty
  extension), and `extra["iso.version"]` keeps the number. When a directory holds
  several versions of one name, the highest takes the bare name and the others list
  under their stored identifier (`FOO.;1`) with `is_current=False`, the same shape as
  RAR file-version history. Entries within a directory list in on-disc record order.
- A Rock Ridge device node, FIFO or socket lists as `MemberType.OTHER`, so extraction
  skips it. The `rr_moved` directory that holds relocated deep subtrees is not listed;
  those subtrees appear at their logical place.
- A bootable image lists its El Torito boot catalog (`boot.catalog`, `BOOT.CAT`) as an
  ordinary file, with the catalog's bytes as its data, as a mounted image shows it.
- A file of 4 GiB or more, stored in several extents, lists and reads as one member.
  Extents that are not back to back are refused with `UnsupportedFeatureError`.
- `ArchiveInfo.format_version` is `None`: ISO 9660 records no interchange level.
- A truncated image opens as long as its directories survive, and lists the sizes its
  records declare. A file the cut reaches reads the bytes that survive and then raises
  `TruncatedError`; files before the cut read normally. A file whose declared length
  cannot be recovered lists with `size` set to `None`.
- Raw CD sector images (the `.bin` of a `.bin`/`.cue` pair) are recognised and refused
  with `UnsupportedFeatureError` naming the sector layout; they are not read. Convert
  one to a plain `.iso` first (for example with `bchunk` or `bin2iso`).

## Directory

- A filesystem tree as a pseudo-archive (uniform API for tests and dir↔archive flows).
- Same default stream contract as archives: forward-only, one live stream, until you
  declare `SEEKABLE` / `CONCURRENT`.
- Hardlinks list as a tar lists them. When several names inside the root share one
  file, the first name the walk reaches is a `FILE` and each later name is a `HARDLINK`
  to it, with `size` `None`. The walk visits a directory's files by name, then its
  subdirectories, so which name keeps the data is fixed. With `streaming=True`,
  extracting a later name whose first name a filter left out fails, as for a streamed
  tar.

## Single-file compressors

- One synthetic member (name from the source path, or `data` for anonymous streams).
- `.gz` may expose `extra["gzip.original_filename"]` when the header carries `FNAME`.
- `.gz` has **no** `member.hashes` entry, before or after a read. The trailer CRC-32
  covers the whole member only when the file holds one gzip member, and proving that
  at open would mean reading the whole compressed file. The decoder still checks every
  member's CRC as it reads.
- With the `[seekable]` rapidgzip accelerator on a seekable `.gz` (and bare zlib / raw
  deflate), truncation raises `TruncatedError` as it does without the accelerator. The
  stdlib engine decodes the stream first, and rapidgzip takes over at the first backward
  seek only after the stdlib engine has decoded the whole input to a clean end.
- `.lz` surfaces a whole-member CRC-32 the same way **size** is exposed: whenever the
  source can be seeked — a file path and an in-memory stream both qualify, a pipe does
  not. Declaring `seekable_members=True` is not required and makes no difference:
  `seekable_members` is about `seek()` on a *member stream*, and the lzip trailer is a
  bounded backward peek. Same for the `.xz` size, read from the stream index. For
  multi-member lzip the value is derived by combining per-trailer CRCs with each
  member's uncompressed size so it equals `crc32` of the concatenated payloads.
- `.bz2` / `.xz` / zlib / brotli / `.Z` have no cheap whole-member stored digest
  (zlib's RFC 1950 Adler-32 is still verified by the decompressor on read; it is not
  surfaced on `member.hashes` because the wrapper has no size fields for a reliable
  single-stream trailer peek when concat/trailing junk is possible).
- `.Z` (unix-compress) is core (native LZW). Truncation is best-effort: nonzero leftover
  bits after the last complete code raise `TruncatedError` on the next `read()` after
  delivering available bytes; zero-leftover cuts remain silent. Forward decode works on
  non-seekable sources; CLEAR boundaries provide seek points when seekability is declared.
- `open_archive` decodes the first byte of a seekable source, so a file that is not
  the codec its name or detection claims (a `.gz` full of zeros, an empty `.bz2`) raises
  `CorruptionError` or `TruncatedError` from `open_archive` rather than from the first
  read. A valid empty stream still opens and reads as `b""`. The check goes one byte
  deep: damage further in still fails on the read. It costs one decoded block, which is
  noticeable only for `.bz2` (a block is up to 900 KB of output; about 30 ms on
  incompressible data). A pipe is not checked at open, because the check would consume
  bytes of its one pass; it fails on the first read as before.
- The `[seekable]` accelerator's bzip2 decoder reads input that is not bzip2 as an empty
  stream. When it yields nothing, Archivey decodes the source again with the standard
  library, so a corrupt `.bz2` raises the same error whether or not
  `seekable_members=True` engaged the accelerator.
- `archivey.open_stream(...)` matches the archive rule: non-seekable unless
  `seekable=True`.

## Stored digests (cheap dedupe)

`member.hashes` holds digests the archive **already stores** (or, for multi-member
lzip, derives via CRC combine from per-member stored CRCs), keyed by
[`HashAlgorithm`][archivey.HashAlgorithm] (values always ``bytes`` — CRC-32 is four
big-endian bytes via [`crc32_digest()`][archivey.crc32_digest]). They are readable without
decompressing when the backend documents them. They are **not** computed digests —
a full `read()` still verifies through the normal path.

| Format | When present | Keys |
| --- | --- | --- |
| ZIP | FILE / SYMLINK (central directory) | `crc32` |
| 7z | FILE | `crc32` |
| RAR5 | FILE with CRC32 and/or Blake2sp | `crc32` and/or `blake2sp` |
| single-file `.gz` | single member, seekable/path | `crc32` |
| single-file `.lz` | seekable source (one or many members; multi-member value is combined) | `crc32` |
| `.bz2` / `.xz` / zlib / brotli / `.Z`, TAR, directory | — | none |

### Cheap dedupe with stored hashes

Prefer digests the archive already stores — the matrix above says which formats
have one — and fall back to computing a digest while reading when they do not:

```python
import hashlib
import archivey
from archivey import HashAlgorithm

def content_key(reader, member):
    """Best available digest for a first-pass dedupe index."""
    if HashAlgorithm.BLAKE2SP in member.hashes:
        return ("stored", "blake2sp", member.hashes[HashAlgorithm.BLAKE2SP])
    if HashAlgorithm.CRC32 in member.hashes:
        return ("stored", "crc32", member.hashes[HashAlgorithm.CRC32])
    # No cheap stored digest (e.g. tar, bzip2): compute while reading.
    h = hashlib.sha256()
    with reader.open(member) as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return ("computed", "sha256", h.digest())

with archivey.open_archive("backups.zip") as reader:
    for member in reader:
        if member.is_file and member.is_current:
            print(member.name, content_key(reader, member))
```

Stored digests are weaker or format-specific; computed digests are stronger but cost a
full decode. Pick by provenance (`stored` vs `computed`) for your index policy.

## Detection

- **Strongest signal first**, and the filename is the last of them; wrong extensions are
  expected. In order: exact magic in the first 4 KiB → an SFX scan behind an executable
  stub → exact magic further in (ISO 9660's `CD001` at 32 769, on one extended peek that
  a source too small for it never pays) → content probes for the formats with no magic →
  the extension. A step that matches nothing falls through to the next; nothing is ever
  rejected for failing an earlier one.
- **zstd skippable frames** — a magic in `0x184D2A50`–`0x184D2A5F` plus a declared payload
  size — may precede the first real frame, so detection walks past them by their declared
  sizes within the peeked bytes and matches the regular frame behind. Skippable frames
  alone are not a zstd claim: there is nothing to open.
- **zlib** is gated on the RFC 1950 header grammar (`CM == 8`, `CINFO <= 7`, and the
  mod-31 check, `FDICT` included) rather than a list of common headers, so all seven
  legal window sizes are recognised. A preset dictionary archivey does not hold fails the
  decode and the candidate falls through.
- **LZMA Alone** accepts any 32-bit dictionary-size field, zero included — the format
  allows every value and decoders round below 4 KiB up to 4 KiB. It does *not* claim a
  header declaring an uncompressed size of exactly zero: that stream carries no payload,
  and 18 zero bytes are a valid empty one, so zero-filled padding would otherwise be
  detected as `.lzma`. A real size and the all-ones "unknown" sentinel are both accepted,
  and an empty `.lzma` still opens through its extension.
- Self-extracting (SFX) stubs are detected when the archive payload sits behind an
  executable header (Windows `MZ`/PE, Linux ELF, or a macOS Mach-O header that parses)
  or a `#!` launcher line (a zipapp `.pyz`, a Spring Boot jar). The 2 MiB scan behind
  the stub reports where the archive starts as `payload_offset`. A launcher is text, so
  behind `#!` only formats with a structural check (ZIP, 7z, RAR) are searched for.
  Among several 7z candidates, one that ends exactly at the end of the file is
  preferred over an earlier one that ends short of it.
- **Ties go to the earlier step, then to registration order.** Detection stops at the
  first step that matches, in the order above, and within a step the first backend
  registered wins. A file that is two formats at once (a polyglot) therefore gets one
  deterministic answer; pass `format=` to read it as the other.
- **`confidence` is provisional in 0.2.x**: what each step reports may be regraded
  later (a two-byte magic such as gzip's reported below `CERTAIN`, say). Branch on
  `format`, not on `confidence`. **`detected_by` is an open set**: new detection steps
  may add values, so handle an unknown one rather than matching every value.
- **Brotli** has no magic, so detection uses a content probe plus framing checks
  **when the source length is known** (paths, `BytesIO`, and short non-seekable
  peeks): a first meta-block that *declares* more bytes than the source holds is
  rejected; when the source is 64 KiB or less, the whole of it is decoded and a stream
  that still wants more input after a declared output drain is rejected; and
  a bounded walk of self-describing meta-blocks
  rejects a later link that overruns or a declared end with trailing bytes. On a
  non-seekable stream of unknown length those checks are skipped and today's
  probe behaviour remains. Probe-only confidence is `PROBABLE` when the first meta-block
  is compressed (or when the name ends in `.br`), and `GUESS` for uncompressed/metadata-first
  without that extension. A later decode failure on a **probe-only** result (no matching
  extension and no inner-TAR upgrade), at any confidence, sets
  `ArchiveyError.format_unconfirmed` and emits `PROBE_FORMAT_UNCONFIRMED` — the bytes
  may never have been Brotli, and a prefix of fabricated output may already have been
  delivered before the error.
- Confidence and evidence are part of `detect_format` / `FormatInfo` — see
  `format-detection` spec.
