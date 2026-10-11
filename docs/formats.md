# Formats and extras

What each format can do, what optional packages or tools it needs, and the quirks that
most often surprise callers. For more depth, the maintainer handbook has pages on
[7z](https://github.com/davitf/archivey/blob/main/dev-docs/formats/7z.md),
[ISO](https://github.com/davitf/archivey/blob/main/dev-docs/formats/iso.md),
[RAR](https://github.com/davitf/archivey/blob/main/dev-docs/formats/rar.md),
[TAR](https://github.com/davitf/archivey/blob/main/dev-docs/formats/tar.md) and
[ZIP](https://github.com/davitf/archivey/blob/main/dev-docs/formats/zip.md), and on the single-file compressors: what they share in
[single-file.md](https://github.com/davitf/archivey/blob/main/dev-docs/formats/single-file.md), then
[gzip](https://github.com/davitf/archivey/blob/main/dev-docs/formats/gzip.md),
[bzip2](https://github.com/davitf/archivey/blob/main/dev-docs/formats/bzip2.md),
[xz, lzip and LZMA Alone](https://github.com/davitf/archivey/blob/main/dev-docs/formats/xz.md),
[zstd and LZ4](https://github.com/davitf/archivey/blob/main/dev-docs/formats/zstd-lz4.md),
[Brotli](https://github.com/davitf/archivey/blob/main/dev-docs/formats/brotli.md) and
[`.Z`](https://github.com/davitf/archivey/blob/main/dev-docs/formats/unix-compress.md).

## Quick matrix

| Format | Core? | Extra / tool | Listing | Random member access | Notes |
| --- | --- | --- | --- | --- | --- |
| ZIP | yes | — | indexed (central directory) | direct | Seekable source required |
| TAR | yes | — | scan headers | direct on uncompressed seekable TAR | Compressed TAR is solid for random opens |
| `.tar.gz` / `.bz2` / `.xz` | yes | — | needs decompression | solid | Prefer `stream_members()` |
| Directory | yes | — | indexed | direct | Same stream-capability defaults as archives |
| Single-file gz/bz2/xz | yes | — | one member | seek with `SEEKABLE` | See single-file section |
| 7z | yes (common codecs) | `[recommended]` for PPMd/Deflate64/zstd/brotli/AES | indexed | solid folders | Native reader; BCJ2 in pure Python |
| RAR | yes (metadata) | **`unrar` or `rar` binary for data**; `[recommended]` for header crypto | native metadata | solid when solid | No write |
| ISO | no | `[recommended]` (`pycdlib`) | indexed | direct | Seekable source required |
| `.zst` / `.tar.zst` | 3.14+ core; else `[recommended]` | `[recommended]` → `backports.zstd` | — | rewind seek unless indexed later | |
| `.lz4` / `.tar.lz4` | no | `[recommended]` | — | rewind seek | |
| `.Z` / `.tar.Z` | yes | — | — | CLEAR seek points when seekable | Best-effort truncation (nonzero leftover bits) |

**RAR member data needs RARLAB `unrar` or `rar` 6.0 or later on `PATH`, or `unar`.**
No pip extra can supply either — listing and metadata work without them, reading bytes
does not. When no usable RARLAB program is found, archivey uses `unar` 1.10 or later,
with the limits listed under [RAR](#rar), including a password passed on its command
line; set `ArchiveyConfig(rar_decompressor="unrar")` to never use it, or
`rar_decompressor="none"` to run neither program. `7z` is never used. How to get the binary:
[Install and extras](install.md#getting-rarlab-unrar-or-rar).

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
  ``UnsupportedFeatureError``. So does an LZMA member with ``lc + lp`` over 4, which
  7-Zip writes with ``-mm=LZMA:lc=8`` and liblzma cannot decode, and a PPMd member with
  restore method 2. Under ZipCrypto both read as the password-or-damage
  ``EncryptionError`` instead, because those settings are encrypted.
- A member's compressed data must hold one stream of its codec and nothing else, as
  7-Zip checks. Bytes after the stream (junk, zero bytes, a second DEFLATE, bzip2 or
  Zstd stream) raise ``CorruptionError`` once the data before them has been read, for
  every compression method, whatever the member's declared size and CRC cover; so does
  an LZMA member without an end marker, or a PPMd member, whose declared size stops
  short of its data. Under ZipCrypto they read as the password-or-damage
  ``EncryptionError`` instead, caused by that ``CorruptionError``, because the bytes are
  encrypted. The same holds for a 7z coder, except that a 7z Zstd or LZ4 coder reads
  concatenated frames as one stream, so a further frame is content that counts against
  the declared size. One zero byte after LZMA data without an end marker reads, because
  7-Zip's encoder sometimes writes it. A standalone compressed file reports bytes after
  its stream as a warning instead (see [Single-file compressors](#single-file-compressors)).
- An end record that disagrees with the central directory is a warning, not an error:
  an entry count that does not match, an archive comment length past the end of the
  file, or a directory entry whose name, extra field or comment runs past the
  directory. The members list and read; ``ARCHIVE_EOF_MARKER_MISSING`` follows them,
  which ``DiagnosticPolicy.strict()`` raises.
- Timestamps: DOS base; NTFS / Extended Timestamp extras override when present.
- An entry whose Unix mode is a device, FIFO or socket lists as `MemberType.OTHER`, so
  extraction skips it. The mode is read only when "version made by" says Unix.
- **Member-name encoding.** Names flagged UTF-8 decode as UTF-8. For an unflagged name
  (APPNOTE says cp437), many tools nonetheless write UTF-8 without setting the flag, so
  Archivey prefers UTF-8 when the stored bytes are valid UTF-8, and otherwise uses the
  `encoding=` you passed to `open_archive`, or without one a configurable legacy encoding
  (`ArchiveyConfig.zip_unflagged_fallback_encoding`, default `cp437`). When UTF-8 is
  inferred for an unflagged name, a `member_name_encoding_inferred` diagnostic records it.
  So `encoding=` decodes only the unflagged names that are not valid UTF-8, unlike
  Python's `zipfile` `metadata_encoding` or `unzip -O`, which apply to every unflagged
  name. Comments decode the same way: a member comment follows its name's flag, and the
  archive comment, which has no flag, decodes as an unflagged name. A byte the chosen
  encoding does not define stays in the comment as a surrogate escape, as in a name. One
  signal outranks the guess: an Info-ZIP Unicode Path extra field (`0x7075`)
  whose checksum matches the stored bytes names the member in UTF-8. `raw_name` is then
  the field's UTF-8 bytes and `extra["alternate_raw_name"]` holds the stored ones.
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
  `PackageNotInstalledError` but is still listed as encrypted. A failing HMAC raises
  `CorruptionError`, with one password or several: a wrong password gets past the
  two-byte check only once in 65 536 tries, so damage is by far the likelier cause.
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
- **Sparse members are extracted dense.** A GNU or PAX sparse member (`tar -S`) is
  written with its holes filled by zeros, so it takes its full size on disk. The zeros
  count as output for the [ratio limit](extracting.md#limits), which a TAR checks across
  the whole archive because a member has no compressed size. A 10 MiB sparse file packs
  into a 10 KiB tar, 1024:1, and `extract_all` raises `ResourceLimitError` against the
  default `max_ratio` of 1000. `member.is_sparse` tells you which members are sparse
  before you extract. For a sparse archive you trust, raise the limit:
  `reader.extract_all(dest, limits=ExtractionLimits(max_ratio=...))`, or
  `max_ratio=None`.
- **Mid-archive corruption can silently shorten the listing.** Stdlib `tarfile` treats a
  corrupt member header *after the first* as a clean end of archive — no exception is
  raised; iteration just stops early. Archivey backstops this with its end-of-archive
  marker check:
    - When the shortened scan stops on a **header `tarfile` rejected**, archivey raises
      `CorruptionError` **by default** — a well-formed tar never ends that way. This
      holds in random-access and streaming reads alike, whatever follows the bad header:
      more members, nothing (it is the archive's *final* block), or a block of zeros.
    - A tar that merely **ends cleanly on a member boundary without the two-block null
      trailer** (a trailer-less or `cat`-joined tar, or a truncation exactly at a member
      boundary — these are byte-identical) is warned about via `ARCHIVE_EOF_MARKER_MISSING`,
      not raised. When a provably complete listing matters (inventory/dedupe sweeps), set
      that code to `RAISE` in the diagnostic policy (`DiagnosticPolicy.strict()` does) to
      turn the warning into `DiagnosticRaisedError`.
    - A trailer whose **first block is zero and whose second is not** is reported the
      same way, `ARCHIVE_EOF_MARKER_MISSING` with
      `context.expected_marker="second_zero_block"`. The zero block ends the members,
      so every member is listed and reads normally, as GNU tar ("A lone zero block")
      and 7-Zip list them; `strict()` raises it. The trailing-data check below then
      runs from the block after the damaged one. This needs at least one member before
      the zero block: a file that is only a zero block and then other bytes is not shown
      to be a TAR archive, and it raises `CorruptionError`.
    - A **non-zero byte after the trailer** — trailing junk, or a second archive
      concatenated on — is reported as `ARCHIVE_TRAILING_DATA`, also a warning under the
      default policy and raised under `strict()`. Zero padding passes — `tar` writes
      10 KiB records, so "nothing but zeros" is the strongest rule that does not flag
      what `tar` itself produces. The check looks at most 1 MiB past the trailer, so a
      byte further out goes unseen; on a compressed tar that 1 MiB is decompressed to
      inspect it. Bytes after the compressed stream itself (after the gzip or xz stream
      ends) are reported the same way, with the codec's name as `format`; see
      [Single-file compressors](#single-file-compressors). A tail that does not
      decompress (a missing gzip footer) ends the check quietly rather than failing the
      listing.
    - Truncation *inside* a member's data always raises `TruncatedError` during iteration,
      whatever the policy.

## 7z

- **Native** header parse + stdlib codecs for the common set (LZMA/LZMA2/BCJ/Delta/
  Deflate/BZip2/stored). No `py7zr` on the read path.
- `[recommended]` adds PPMd, Deflate64, Zstd, Brotli, and AES.
- **BCJ2** (what 7-Zip writes for x86 executables at `-mx9`) reads on a core install,
  encrypted or not. Its decoder is pure Python, about half the speed of the same file under
  BCJ. It keeps no seek points, so a backward seek decodes again from the folder start.
- **ARM64** (what 7-Zip 23 writes for AArch64 executables) reads on a core install. Python's
  `lzma` cannot build that filter, so archivey decodes it in pure Python. On real AArch64
  code a read runs at roughly four fifths of the speed of the same file with no filter.
  The filter itself slows with branch density: on data where every word is a branch it
  runs about 15 times slower than on real code.
- Solid folders: `stream_members()` decodes each folder once; random `open()` of a mid-
  folder member may re-decode from the folder start.
- A device node, FIFO or socket that 7-Zip or p7zip stored on Unix lists as
  `MemberType.OTHER`, so extraction skips it. The mode is trusted for this only when the
  attribute's `0x8000` Unix-extension bit is set.
- **Member names** are UTF-16, so `encoding=` has no effect. A name made on Windows can
  hold a surrogate without its partner, which NTFS allows. Archivey keeps that code unit
  in `member.name` (`'hi\ud800'`) and lists every member, as 7-Zip does. The CLI shows
  the name escaped, as `hi\ud800`. Under the `STRICT` and `STANDARD` policies, extraction
  escapes the surrogate's UTF-8 bytes as it escapes any name that is not portable:
  `hi\ud800` is written `hi%ED%A0%80` on every OS, and `presented_name` holds the
  stored name. Under `TRUSTED` it writes what 7-Zip writes. On Linux and other POSIX
  systems that is the three-byte UTF-8 form, `hi` followed by `ed a0 80`; a filesystem
  that accepts only valid UTF-8, such as APFS, refuses those bytes, and the member fails
  with `ExtractionError`. On Windows it is the exact name. One exception: a unit in
  U+DC80 to U+DCFF looks the same as an undecodable byte (see
  [Names that do not decode](opening-and-listing.md#names-that-do-not-decode)), so
  extraction writes it as that byte, where 7-Zip writes three bytes. `member.raw_name`
  always holds the stored units. The archive comment is UTF-16 too: a lone surrogate
  stays in `ArchiveInfo.comment`, so be ready for it if you print the comment, and only
  a comment with an odd byte count raises `CorruptionError`.
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

- Metadata / listing: native RAR 1.5–RAR5 parser (works without `unrar`). The one
  exception is a compressed RAR 1.5 / 2.x comment, which the selected program
  (`unrar` or `unar`) decodes; without it, or when the decoded text fails its CRC16,
  `comment` is `None`.
- An entry from a Unix host whose mode is a device, FIFO or socket lists as
  `MemberType.OTHER`, so extraction skips it. `rar` itself skips such files when
  archiving.
- Member **data**: RARLAB `unrar` or `rar` **6.0 or later** on `PATH` (not `unrar-free`
  or `7z`). `unrar` is preferred when both exist. By default, when neither is found,
  archivey uses `unar` 1.10 or later if it is installed; see the next item. `unrar` gets
  passwords as bare `-p` with the secret on stdin (not in argv). Install:
  [Getting RARLAB unrar or rar](install.md#getting-rarlab-unrar-or-rar).
- **`unar` instead of `unrar`:** `ArchiveyConfig.rar_decompressor` chooses the program.
  The default, `"auto"`, uses `unrar` when a usable one is on `PATH` and `unar`
  otherwise; the choice is made once, when the archive is opened, and a read `unar`
  refuses is not retried with `unrar`. When `"auto"` picks `unar`, `ar.cost.notes` says
  so at open. `"unrar"` and `"unar"` use only that program. `"none"` runs no program
  at all, for when you don't want `unrar` or `unar` run on your archives: listing still
  works, and so does reading a stored (uncompressed) member that is not encrypted,
  also when it is split across volumes or sits in a solid archive. A compressed or
  encrypted member, or a split one with a volume missing, raises
  `UnsupportedFeatureError` before anything runs, and `ar.cost.notes` says so at open.
  `unar` 1.10 or later (`brew install unar`, `apt install unar`) is free software and
  easy to install on macOS, but it reads less than `unrar`. Archivey runs each `unar`
  once on a small RAR5 archive and does not use one that decodes it wrong, as the
  Debian and Ubuntu packages before 1.10.8+ds1-10 do (Ubuntu 22.04 to 26.04 among
  them); `"auto"` then treats `unar` as absent. Archivey refuses these reads with
  `UnsupportedFeatureError` before `unar` runs, because `unar` gets them wrong,
  sometimes with a success exit:
  - encrypted data in a RAR 2.x-4.x archive, and every member of a solid one that has
    it (RAR5 encryption is read);
  - a password that is not ASCII;
  - a multi-volume RAR5 set with encrypted headers (Homebrew's `unar` 1.10.8 returns
    nothing for it);
  - in a RAR5 solid archive, a member that comes after an empty file, a directory or a
    link;
  - a member compressed with the RAR 1.5 algorithm;
  - a multi-volume set with a prefix before the first volume (an SFX stub). A single
    prefixed file is copied to a temporary file first.
  - in `stream_members()` over a solid archive that has one of the members above, any
    readable member past the 4000th: that pass names each member it reads on the `unar`
    command line, which has a size limit. Such a member still opens on its own.

  **The password is visible to other local users.** `unar` accepts a password only on
  its command line (`-p <password>`), so while it runs, any user on the same machine can
  read the password from the process list (`ps`, `/proc/<pid>/cmdline`). `unrar` reads
  it from stdin instead. On a shared machine, install `unrar`, or set
  `rar_decompressor="unrar"` so that `unar` is never used. A
  wrong password makes `unar` write nothing and report success; archivey reports that
  as `EncryptionError`, and a RAR5 password check usually rejects a wrong password
  before `unar` runs at all.

  Stored members still need neither program. A member whose stored name contains `*` or
  `?` needs no `rar_allow_glob_member_concatenation`: `unar` selects members by index,
  not by name. With `"unrar"` or `"unar"` selected, archivey never switches between the
  two programs; with `unar` selected and missing or refused, a read raises
  `PackageNotInstalledError`.
- `[recommended]`: header-encrypted RAR5. BLAKE2sp verification needs **no** package —
  it is implemented natively on stdlib `hashlib`. RAR5 members with the HASHMAC flag
  verify tweaked digests via UnRAR’s `ConvertHashToMAC` when a password is available;
  tweaked values are not exposed as plain `member.hashes`.
- **A RAR5 archive cut exactly between two blocks is warned about, not raised.** RAR5
  always ends each volume with an end-of-archive block, so archivey lists the members
  before the cut and then emits `ARCHIVE_EOF_MARKER_MISSING`
  (`expected_marker="end_of_archive_block"`), which `DiagnosticPolicy.strict()` raises.
  A cut inside a member's data or inside a header lists the members before the cut and
  is `TruncatedError` on the listing, and other damage to an encrypted header is
  `CorruptionError`. In an encrypted header, both need the password proven first: by
  RAR5's password check value, or in RAR 1.5-4 by an encrypted header whose checksum
  matches, which in a volume set covers the later volumes too. Before that, a wrong
  password looks the same as a cut past a header's first 16-byte block or a header whose
  checksum does not match, so those still raise `EncryptionError` ("wrong password?") at
  open and list nothing, even with the right password. That happens inside the first
  encrypted header of a RAR 1.5-4 archive or volume set, and inside any header of a RAR5
  archive whose encryption record has no password check value. RAR 1.5-4 archives may
  legitimately lack the end block, so a cut between their blocks still lists as
  complete.
- **A damaged end-of-archive block keeps the listing.** When the block after the last
  member fails its header checksum (RAR 1.5-4 or RAR5), every member is listed and
  reads normally, and archivey emits `ARCHIVE_EOF_MARKER_MISSING` with
  `observed_kind="nonzero"` after them, which `DiagnosticPolicy.strict()` raises. This
  is what `unrar t` does: each member tests OK, then it reports one error. A damaged
  header counts as the end block only if it has an end block's shape and the file ends
  right after it; any other damaged header lists the members before it and then raises
  `CorruptionError`. The damaged block's next-volume flag is not trusted, so a volume
  set goes on to the next volume only when a member's own header says its data
  continues there. With encrypted headers this needs the password proven, as above;
  before that it is `EncryptionError`.
- **A damaged member header lists the members before it.** When a header after the main
  header fails its checksum, the members before it are listed and read normally, and
  the listing then ends with `CorruptionError`. No later member of the damaged header's
  volume is listed: its size field cannot be trusted, so archivey does not know where the
  next header starts. `unrar` searches on and lists them too. In a volume set, a member
  before the damage whose data continues is still followed into the next volume, whose
  members are listed before the error. A damaged main header still raises
  `CorruptionError` at open. With encrypted headers this needs the password proven, as
  above; before that it is `EncryptionError`.
- **A volume set with a volume missing lists what it has.** Whether the missing volume
  is the first, one in the middle or the last, the members whose headers are in the
  volumes present are listed, opened from any of them, and those wholly inside them read
  normally. A member with data in the missing volume raises `TruncatedError` when read,
  and so does every member past the gap in a solid archive. The listing then ends with
  `TruncatedError` naming the missing volumes — the same as a cut file, and what
  `unrar t` does. A later volume opened on its own by path is read the same way, as a
  set missing the rest; opened as a stream, with no name to number it, it is still
  refused with "Need first volume".
- **A member compressed with a version RAR does not know is unsupported.** A RAR5 member
  whose compression version is newer than RAR 7's, or a RAR 1.5-4 member whose unpack
  version is outside 13-29, lists normally and raises `UnsupportedFeatureError` when
  read, where `unrar` says "Unknown method". A stored member reads whatever it declares.
- **Password lists on encrypted data:** RAR5 records a password check per member, so a
  list is tried in order and the matching password is used. RAR3/4 records none, so with
  more than one password archivey judges each by decoding the member: up to 64 KiB of
  `unrar` output, which a wrong password usually fails, and, when several passwords get
  that far, the whole member against its CRC. A stored member is checked by decrypting it
  in archivey and comparing its CRC. The order of the list does not matter, but every
  wrong password before the right one costs a decode, so put the likely one first.
- **Partial reads of RAR3/4 encrypted data** emit `ENCRYPTED_MEMBER_UNVERIFIED`. With no
  password check, only the member's CRC at EOF catches a wrong password, and `unrar`
  can return the wrong key's bytes before that: a stored member always, a compressed
  one for some wrong passwords. Closing the stream before EOF, or after a seek, skips
  the CRC. RAR5 members and header-encrypted archives have checked the password already
  and never emit it.
- **File-version history (`-ver`):** revision rows appear in `members()` as names like
  `path;1` with `extra["rar.file_version"]` and `is_current=False`; the live path stays
  `is_current=True`. Default extract **skips** non-current rows.
- **Links and file copies.** A RAR5 hard link (`rar -oh`) is a `HARDLINK`. A RAR5
  file copy (`rar -oi`, which `unrar` lists as "File reference") stores a duplicate
  file once and names the earlier member that holds the bytes. It is a `FILE` with
  `extra["is_file_copy"] = True`; `link_target` is the source's stored path and
  `link_target_member` the source member. Reading it gives the source's bytes, and
  extraction writes an independent file, as `unrar` does, by copying the source's file
  when it has just written it. A copy whose source is not an earlier file member raises
  `LinkTargetNotFoundError` when read. `stream_members(file_copy_streams=False)` yields
  `None` for each copy; its bytes and digests are its source's.
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
  way; the one exception is a backslash in a RAR5 name written on Windows, which
  `unrar` on Linux and macOS reads as `_`. Setting `ArchiveyConfig.rar_decompressor` to
  `'unar'` reads those members by position instead.
- **Member names.** RAR5 stores names as UTF-8, and RAR 1.5-4 usually as UTF-16
  beside an 8-bit copy. A RAR 1.5-4 name that has only the 8-bit bytes does not say
  which code page they are in. Archivey tries UTF-8 first. When the bytes are not valid
  UTF-8, it decodes them with `encoding=` when you pass one, and otherwise with cp437 for
  a member written on DOS or Windows (WinRAR writes the OEM code page) and windows-1252
  for one written elsewhere. `raw_name` is
  always the stored bytes. `encoding=` has no effect on a RAR5 name. A RAR 1.5-4
  UTF-16 name can hold a surrogate without its partner, as a 7z name can: archivey
  keeps it in `member.name` and extracts it as it does a 7z name (see 7z above).
  `unrar` 7.00 on Linux instead extracts the name cut at that unit, so `hi\ud800.txt`
  becomes `hi`. If you read such a member through `unrar`, archivey selects it with
  `?` in place of each unit. When that pattern also matches an earlier member, the
  read raises `UnsupportedFeatureError` unless you set
  `rar_allow_glob_member_concatenation`, as it does for a member name that holds `*`
  or `?`.
- **Comments.** A RAR 1.5-4 comment is 8-bit text that does not say which code page
  it is in. Archivey reads it up to the first NUL, as UTF-8 if it is valid, otherwise
  with the `encoding=` you passed, and otherwise as windows-1252. A byte that code page
  does not define stays in the comment as a surrogate escape, as in a name. The one
  exception is a RAR 2.9-4 comment flagged as Unicode, which is UTF-16LE. A RAR5
  comment is UTF-8.
- **Several members under one name.** `unrar` emits every member a name selects, in
  archive order: two members with the same name, or two names `unrar` reads the same
  way. Archivey skips to the one you asked for, so each read returns that member's own
  bytes. A RAR5 name that is not valid UTF-8 is cut by `unrar` at its first invalid
  byte and is read through that shorter name the same way. A name `unrar` reads as
  empty, or one archivey cannot give back to `unrar` exactly, raises
  `UnsupportedFeatureError`; `rar_decompressor="unar"` reads it by position.
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
- **A stream source is copied to disk for `unrar`.** `unrar` reads only files, so a RAR
  opened from a `BytesIO` or a file object is copied whole to a temp file (a volume set,
  to a temp directory) on the first member read that needs `unrar`, and removed on close.
  Stored, unencrypted members are read in place and need no copy. The copy is
  bounded by `ArchiveyConfig.spool_limits` (`SpoolLimits.max_bytes`, default 1 GiB):
  over it, the read raises `ResourceLimitError` before anything is written. Open from a
  path to avoid the copy. See [Access and cost](access-and-cost.md#non-seekable-sources).
- Read-only — no RAR writer.

## ISO 9660

- Needs `[recommended]` (`pycdlib`) and a seekable source.
- `import archivey` patches pycdlib for the whole process: the `collections` name inside
  `pycdlib.pycdlib` becomes one whose `deque` skips a directory extent it has already
  queued. That stops pycdlib looping forever on a directory tree that points back at an
  ancestor. Other code using pycdlib in the same process gets the patch too. A valid tree
  never revisits an extent, so its results do not change.
- `import archivey` also wraps pycdlib's Rock Ridge parser, but the wrapper acts only
  while archivey itself opens an image, so other code using pycdlib sees no change. Inside
  archivey, a System Use entry of a type pycdlib does not know is skipped, as the SUSP
  specification says, instead of failing the whole image. A malformed entry ends that
  record's Rock Ridge data: the member lists from the entries before it, with a
  `MEMBER_HEADER_RECORD_SKIPPED` diagnostic, and a symlink cut this way lists with
  `link_target` unset and a `SYMLINK_TARGET_UNAVAILABLE` diagnostic. genisoimage writes
  such an entry for a long symlink target (from about 400 bytes with genisoimage
  1.1.11).
- zisofs (Rock Ridge transparent compression, `mkzftree` + `genisoimage -z`, `xorriso
  -set_filter_r --zisofs`) reads: the member lists the size its data decodes to, with
  `compression=(CompressionMethod(algo=DEFLATE),)`, and reads decoded, seeking by block.
  zisofs2 (`xorriso -zisofs version_2=on -set_filter_r --zisofs /`, under the `ZF` or
  the `Z2` tag), and a zisofs entry too short to parse, list with
  `CompressionAlgorithm.UNKNOWN` and refuse to read, with `UnsupportedFeatureError`.
- Rock Ridge and plain ISO 9660 names, and Rock Ridge link targets, decode as UTF-8
  first. Bytes that are not valid UTF-8 decode with `encoding=` when you pass one.
  Without it, a Rock Ridge name takes the Joliet name of the same file or directory
  when the image has a Joliet tree and the two line up, with a
  `member_name_encoding_inferred` diagnostic, and is escaped otherwise (see
  [Names that do not decode](opening-and-listing.md#names-that-do-not-decode)). Joliet
  names are UTF-16 and ignore `encoding=`. A Joliet name can hold a surrogate without
  its partner, as a 7z name can: archivey keeps it in `member.name` and extracts it as
  it does a 7z name (see 7z above).
- Namespace auto-selected: Rock Ridge → Joliet → plain ISO 9660; reported in
  `ArchiveInfo.extra["iso.namespace"]`.
- Plain ISO 9660 file names lose their `;N` version suffix (and the `.` of an empty
  extension), and `extra["iso.version"]` keeps the number. When a directory holds
  several versions of one name, the highest takes the bare name and the others list
  under their stored identifier (`FOO.;1`) with `is_current=False`, the same shape as
  RAR file-version history. Plain directory names have no version and keep any `;N`. Two
  files stored with the same identifier both list, the later one current, as in ZIP
  and TAR. That includes a file and its associated file (such as the resource fork on a
  Mac hybrid image), which list as two members with one name and nothing to tell the
  fork apart. Entries within a directory list in on-disc record order.
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

## Disk images

- A UDIF image (`.dmg`) is recognised by its `koly` block — the last 512 bytes, or
  the first 12 when an old image puts the block at the start — and refused with
  `UnsupportedFeatureError`. A compressed image stores its blocks as zlib, bzip2 or
  xz, so opening one used to extract the first block and treat the rest as trailing
  data. An uncompressed image whose disk is an ISO 9660 filesystem is reported as
  that ISO instead: `CD001` at byte 32 769 is checked first, and the image is read
  as an ISO. Reading a UDIF image itself is not supported. To get at the files,
  convert or mount the image first (for example with `7z x`, `dmg2img`, or
  `hdiutil attach` on macOS). A pipe is not rewound. A short image is still
  refused, because detection has already read through to its end. A longer
  zlib-first image on a pipe still opens as that stream.

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
- With the `[seekable]` rapidgzip accelerator on a seekable `.gz`, truncation detection is
  **best-effort** (empty→stdlib fallback + single-member ISIZE) — stronger than naked
  rapidgzip, weaker than stdlib alone. Do **not** rely on it when you need certainty;
  set `use_rapidgzip=OFF`. This caveat applies to **bare** `.gz` / `open_stream` (and
  bare zlib/raw deflate), not to ZIP/7z/… **members**: those already carry CRC/size and
  fail via `VerifyingStream` when the decoded payload is short or wrong.
- `.lz` surfaces a whole-member CRC-32 the same way **size** is exposed: whenever the
  source can be seeked — a file path and an in-memory stream both qualify, a pipe does
  not. Declaring `seekable_members=True` is not required and makes no difference:
  `seekable_members` is about `seek()` on a *member stream*, and the lzip trailer is a
  bounded backward peek. Same for the `.xz` size, read from the stream index. A `.xz`
  or `.lz` opened from another archive's member stream reports neither the size nor the
  CRC-32, seekable or not: member streams are excluded as a group. For multi-member
  lzip the value is derived by combining per-trailer CRCs with each member's
  uncompressed size so it equals `crc32` of the concatenated payloads.
- `.lz` is read in lzip format version 1, which every lzip since 1.0 writes. A member
  in version 0 (lzip before 1.0) or any later version raises `UnsupportedFeatureError`,
  wherever it is in the file: a member that starts with the `LZIP` magic is never
  skipped as trailing data.
- A header the format's own tool calls unsupported raises `UnsupportedFeatureError`,
  not `CorruptionError`: a gzip member with a method other than deflate or a reserved
  flag bit, an LZ4 frame in a version other than `01`, a zstd frame that needs a
  dictionary, and a `.Z` file with a code width over 16 bits. A damaged byte in one of
  those same fields raises the same error, because nothing tells the two apart; the
  message says a damaged header reads the same way (see
  [Errors and diagnostics](errors-and-diagnostics.md)).
- `.bz2` / `.xz` / zlib / brotli / `.Z` have no cheap whole-member stored digest
  (zlib's RFC 1950 Adler-32 is still verified by the decompressor on read; it is not
  surfaced on `member.hashes` because the wrapper has no size fields for a reliable
  single-stream trailer peek when concat/trailing junk is possible).
- `.Z` (unix-compress) is core (native LZW). Truncation is best-effort: nonzero leftover
  bits after the last complete code raise `TruncatedError` on the next `read()` after
  delivering available bytes; zero-leftover cuts remain silent. Forward decode works on
  non-seekable sources; CLEAR boundaries provide seek points when seekability is declared.
- **Bytes after the compressed stream** (a signature or checksum appended to a
  download, a tool that pads its output) do not stop the read. For gzip, zlib, bzip2,
  xz, lzip, LZMA Alone, zstd, LZ4 and Brotli, archivey returns the whole payload, then
  emits one `ARCHIVE_TRAILING_DATA` whose `observed_bytes` is the offset of the first
  appended byte. It is a warning under the default policy; under
  `DiagnosticPolicy.strict()` the read that reaches it raises `DiagnosticRaisedError`.
  Zero bytes after the end are padding and report nothing, as for TAR. A second stream
  of the same codec (a concatenated `.gz`, `.bz2`, `.lzma`, `.zst` or `.lz4`) is more
  data, not trailing bytes. For `.xz`, `.lz`, `.zst`, `.lz4` and `.bz2`, bytes that
  hold at least half of the codec's stream magic, but not all of it, are a later stream
  with a damaged header: the read raises `CorruptionError` rather than return the
  first stream alone. `.xz` and `.lz` keep their size and seeks when the appended
  bytes are within 1 MiB, unless they are crafted to hold thousands of fake end
  markers; further out the index is not found and the size reads as unknown.
  A damaged end marker on the last of several `.xz` streams or `.lz` members is
  corruption, not appended bytes: the size reads as unknown, and the read or seek
  that reaches the damage raises `CorruptionError`.
  Brotli has no end marker the library reports, so archivey finds the end by decoding
  the source again, which needs a seekable source: from a pipe, bytes after a Brotli
  stream raise `CorruptionError`. The check applies to a bare compressed file and to a
  compressed tar; inside a ZIP or 7z member such bytes raise `CorruptionError` (see
  [ZIP](#zip)). `.Z` is not
  covered: it has no end marker, so appended bytes decode as more data.
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
- A `.zst` frame declares its window, and the decoder keeps that much memory.
  `DecoderLimits.max_decoder_memory` (2 GiB by default) caps it, so the 2 GiB window of
  `zstd --long=31` reading standard input reads. A window over the cap raises
  `ResourceLimitError`. The cap is rounded down to a power of two for zstd. A window
  over 2 GiB is beyond what libzstd decodes at any setting and raises
  `UnsupportedFeatureError`. Detection reads a sample of the stream with no cap, so
  a frame declaring 2 GiB has that much address space reserved while `open_archive`
  detects it, whatever the cap.
- A `.zst` frame carries a content checksum only when its writer adds one, as a modern
  `.lz4` frame does. Archivey checks it when it is there; a frame without one can decode
  damaged data to wrong bytes with no error.
- The legacy LZ4 format (`lz4 -l`, used for Linux kernel images) reads as `.lz4`. It has
  no checksum, so damaged data can decode to wrong bytes with no error, as a modern
  frame written without one can. It has no end mark either, so a file cut exactly
  between two of its blocks reads short with no error.
- Brotli (`.br`), unix-compress (`.Z`) and LZMA Alone (`.lzma`) have no checksum
  either, so damaged data can decode to wrong bytes with no error. Archivey does not
  report this with a diagnostic on each file, because there is no check to skip. A
  `.Z` file has no end mark, so a cut can also read short with no error (see the `.Z`
  bullet above).
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
| single-file `.lz` | seekable source (one or many members; multi-member value is combined) | `crc32` |
| `.gz` / `.bz2` / `.xz` / zlib / `.zst` / `.lz4` / brotli / `.Z`, TAR, directory | — | none |

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
  a source too small for it never pays) → the 512-byte `koly` block at the end of a
  seekable source (a UDIF disk image; see Disk images) → content probes for the formats
  with no magic (see the next item) → the extension. A bzip2 or xz header that is the first block of such
  an image loses to that block. A step that matches nothing falls through to the next;
  nothing is ever rejected for failing an earlier one. A pipe is not rewound to read
  the block at the end.
- **Content probes run only for a matching name by default.** LZMA Alone, zlib and
  Brotli have no magic, so a trial decode of the first bytes (a content probe) is the
  only thing that recognises them, and ordinary binary files sometimes pass one. By
  default `open_archive` and `detect_format` run a probe only when the name ends in one of
  that format's extensions: `.lzma` or `.tlz` for LZMA Alone, `.zz` or `.zlib` for zlib,
  `.br` or `.brotli` for Brotli, and the `.tar.` form of each except `.tlz`. A matching probe confirms the name and still finds a TAR inside. A source with
  no name, or another extension, runs no probe: a nameless raw stream of these formats
  raises `FormatDetectionError`, and one named for another format gets that format's
  guess. If you read such sources, give the file its format's extension, pass `format=`,
  use `open_stream()` (which always runs every probe), or set
  `ArchiveyConfig(always_probe_content=True)`.
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
- **Brotli** has no magic, so detection uses a content probe (when it runs; see above)
  plus framing checks
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
