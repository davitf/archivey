# Redundancy sweep — formats, access modes, and the copies between them

Sweep of `src/archivey` on `claude/decompressor-stream-refactor-5b07wi`
(`47ed256f`). About 20k lines under `src/archivey`. No library code was changed.
An earlier review (`review/complexity.md`, `review/deep-simplification.md`) fixed
the handle-lock helper, the shared FILETIME module, and the error boundary.
This pass asks what is still copied, and which of those copies make two formats
or two access modes disagree.

## How the pass was split

One inventory, then six reads, then a cross-check.

1. An AST pass listed every class, which methods subclasses override, and a
   rough similarity of those bodies. The useful clusters were the seven
   `BaseArchiveReader` subclasses (the same four abstract hooks, plus
   `_iter_with_data`, `_ensure_link_target`, `_to_member`), the seven
   `ReadBackend.open_read` methods, and the `StreamCodec` methods `translate`,
   `open`, and `rewind_warning`.
2. Six reads, each returning candidates only:
   reader-hook overrides; `ArchiveMember` construction; password and
   verification; codecs and stream wrappers; parsers, detection, and volumes;
   extraction, link resolution, and listing limits.
3. The candidates were read again against the current bodies. Copies that are
   three identical lines, and copies that are really a different header
   grammar, are not proposed as merges below. They are in the short table and
   in "Leave separate".

The open change `openspec/changes/decompressor-stream-composition` already
proposes replacing the `DecompressorStream` / `SegmentedDecompressorStream`
subclass tree with one stream and a `Decoder`. That proposal is not repeated
here. The codec and reader findings below are the layer above it.

## Why there are so many corner cases

Most of them are not accidental copies. They fall into three piles, and only
the third is straightforward to delete.

**The format really differs.** Where the index lives, how a name is encoded,
which integer encoding the header uses, and how a password becomes key bytes
are not one algorithm with a parameter. ZIP's central directory, TAR's forward
headers, 7z's folder coders, and RAR's `unrar` pipe cannot share a parser.
Those branches should stay. The "Leave separate" section names the ones that
looked similar and are not.

**The access mode differs, and the base already has a machine for it.**
Indexed random access, one forward pass, and a solid block are three ways to
produce the same `(member, stream)` sequence. `BaseArchiveReader` owns the
indexed and forward-pass machines (`_iter_with_data`, `_begin_forward_pass`,
`_get_members_registered`). TAR streaming, solid RAR, and 7z reimplement the
edges of that machine (when to close the previous stream, when a symlink
target is known, when a password failure is "try the next one") because the
default hook was too wide to override. The corner case is the override. The
fix is a narrower hook, not another copy of the loop.

**A shared helper exists, and a later backend copied a variant.** FILETIME,
password attempts, CRC-32, "wrap this payload in `VerifyingStream`", and
`open_read` are in this pile. Each copy is a place the next format will drift.

The half-size library is not available. The merges below delete repeated
control flow. They do not delete format parsers.

## Merges

### R1. One pass driver; backends open one member

`_iter_with_data` is still an override that reimplements the same loop.

| Site | What the loop does |
|------|--------------------|
| `base_reader.py` `_iter_with_data` (around 428–441) | Close the previous stream, yield a lazy stream, **leave the last stream open**. The comment says `stream_members` owns that close. |
| `base_reader.py` `stream_members` (around 1181–1192) | Close the current stream on advance, and again in `finally`. |
| `rar_reader.py` `_iter_with_data` (around 434–472) | Same close-previous loop, plus `SolidBlockReader`, plus a `finally` that closes the last stream **and** the solid reader. Nonsolid RAR delegates to `super()`. |
| `sevenzip_reader.py` `_iter_with_data` (around 639–678) | Same shape, with a folder switch: close the old `SolidBlockReader`, open the next folder. `finally` closes the last stream and the solid reader. |
| `tar_reader.py` `_iter_with_data` (around 303–325) | Streaming only. Uses `_begin_forward_pass`, opens `extractfile` **eagerly** under the handle lock, and does not close the previous stream. Non-streaming TAR calls `super()`. |

`stream_members` is the only caller of `_iter_with_data`. The last stream is
therefore closed twice on the RAR and 7z paths (the override `finally`, then
`stream_members`). `ArchiveStream.close` is safe to call twice, so this is
not a live-read bug. It is two owners of one lifetime, and the base comment
describes only one of them.

`_member_stream_size` is the same one-liner in `rar_reader.py` (81–82) and
`sevenzip_reader.py` (227–228): `member.size if member.size is not None else 0`.

**Merge.** The base owns the loop: close the previous yielded stream, yield,
and on the way out close the pass context (the solid reader), not the last
stream. A backend hook returns the stream for one member, or `None` for a
non-file. RAR and 7z keep a small piece of pass state (the `SolidBlockReader`,
and for 7z the current folder index). TAR streaming keeps eager `extractfile`
inside that hook so the handle lock is not held across the consumer's read.

**Stays per format.** How the block is opened (`unrar p` versus a 7z folder
pipeline). RAR's nonsolid path, which is the base lazy open. TAR's lock scope.
Empty 7z members and RAR non-payload members.

**Risk.** A shared loop that closes the solid reader before the caller finishes
the last stream is wrong. A shared loop that holds TAR's handle lock across
`yield` deadlocks. The hook has to be "open this member", not "here is my
copy of the for-loop".

This is the change `review/deep-simplification.md` called S3, now with RAR as
the fourth copy that note expected.

### R2. Link resolution is two full passes and one weaker pass

Materialized listing and end-of-stream listing run the same two loops.

`_get_members_registered` (`base_reader.py` around 587–594), inside
`_internal_member_opens()`:

```python
for member in members:
    if member.is_link:
        self._ensure_link_target(member)
for member in members:
    if member.is_link and member.link_target:
        self._resolve_link(member, by_name_lists)
```

`_finalize_pass_links` (`base_reader.py` around 806–811) is the same two loops
over `_pass_scanned` / `_pass_by_name_lists`, without the internal-open
wrapper.

`_resolve_link` (774–797) walks the whole chain and stops on a cycle.

`_stamp_progressive_member` (826–836) does something smaller, once per member,
while the pass is still running: one lookup with `allow_forward_fallback=False`,
then at most one extra hop through `target.link_target_member`. A forward
target, and a chain whose middle link is not resolved yet, stays unset until
`_finalize_pass_links`.

**Merge.** One function for the two full passes. The materialized caller keeps
the internal-open wrapper around it, because those reads can open member
bytes. The progressive caller does not need that wrapper at EOF if the pass
is single-owner; if it does, the wrapper belongs at the call site.

**Do not merge** the mid-pass hop into `_resolve_link` without a decision.
Calling the full walk per member during the pass would read symlink payloads
earlier, and it still cannot see members that have not been yielded.

**Contract to write down**, next to `_stamp_progressive_member`: during a
forward pass, `link_target_member` is a best-effort backward link, one extra
hop at most. After the pass reaches EOF, `_finalize_pass_links` is the same
walk as `members()`. A caller that inspects `link_target_member` on a member
just yielded by `stream_members` is not looking at the final chain.

The two-pipeline parent of this (materialize by draining the forward pass,
one id stamp, one publication) is S2 in `review/deep-simplification.md`. It
is still a real simplification. It touches the concurrency snapshot. It
should not ride along with R1.

### R3. Symlink target bytes: one decode, three policies

`_ensure_link_target` is overridden by ZIP, RAR, and 7z. All three start with
the same guard: return unless `type` is `SYMLINK` and `link_target` is still
`None`. All three decode payload bytes with `utf-8` / `surrogateescape` when
they read bytes. TAR, ISO, directory, and RAR5 redir set `link_target` while
building the member, from metadata, and never use this hook.

After the guard, the policies diverge:

- ZIP (`zip_reader.py` 870–907) reads the entry. `EncryptionError` emits
  `SYMLINK_TARGET_UNAVAILABLE` with `reason="password_required"` and leaves
  the target unset. Other read errors are translated.
- 7z (`sevenzip_reader.py` 948–957) reads via `_open_member`. `EncryptionError`
  returns. No diagnostic.
- RAR (`rar_reader.py` 501–525) prefers `file_redir` metadata, else reads a
  stored uncompressed body. Encrypted or compressed targets with no direct
  bytes return with the target unset. No diagnostic.

**Merge.** A base helper for the guard, the decode, and the encryption
outcome. The backend supplies bytes, or raises. The outcome (diagnostic
versus silent unset) should be one policy. Today it is three.

**Stays per format.** Where the bytes live: ZIP local data, RAR redir or M0
stored body, 7z folder slice. TAR and ISO must keep setting the target at
listing time. Forcing them through a payload read would scan data during
`members()`.

**Risk.** 7z `_open_member` for a symlink inside a solid folder decodes from
the start of the folder. A helper that calls `_open_member` for every symlink
during listing is the current 7z behavior; moving ZIP onto that same call
would also run ZipCrypto confirmation. The shared piece is the outcome, not
the open.

### R4. Password attempts: one machine, three edge copies

`_PasswordCandidates.attempt` (`password.py` 155–217) is the candidate loop,
the provider loop, the "already tried" stop, and `record_success`. It treats
`EncryptionError` as "try the next password". Anything else propagates.
`on_failure` is a parameter of `attempt` and has no caller.

**ZIP stored still reimplements the loop** (`zip_reader.py`
`_open_stored_confirmed`, from 737). That is still the right reason: ZipCrypto
STORED needs a weak check-byte filter and then one CRC pass over every
survivor (`parallel_plaintext_crc32` / `first_crc_match`). `attempt` tries one
password at a time and cannot express that batch. The compressed path does
use `attempt`, through `_finish_password_attempt`.

The cost is that `_open_zip_entry` (619–640) has three machines (lazy,
stored, compressed), and the stored provider loop can drift from `attempt`
(dedup of repeated provider passwords, provider `EncryptionError` not rewritten).
A batch hook on `_PasswordCandidates` — collect weak survivors, then one
confirm over the list — would put the loop back in one place and leave the
CRC pass in ZIP.

**7z uses `attempt` twice, with different failure types.** See B1. That is
the bug-shaped case, not a merge by itself.

**Exhaustion messages are rewritten at each call site.** `attempt` already
puts `last_error.message` on `_PasswordCandidatesExhausted`. RAR
(`rar_reader.py` 322–328) uses that message, with a header-specific fallback.
7z header decode (617–618) and folder unlock (861–864) always replace it
with "Password required to decrypt the 7z header" or "… this 7z member".
ZIP `_finish_password_attempt` (849–865) often re-raises `last_error` after
stamping, and has a second copy of the "ambiguous integrity" paragraph that
also appears in the stored path.

**Merge.** One exhaustion helper: stamp, prefer `last_error` when it is
specific, else a caller-supplied fallback. Keep the fallback strings. Give
`attempt` a batch confirm, or delete `on_failure` until a caller needs it.

**Stays per format.** ZipCrypto check-byte and batched CRC. 7z KDF and the
per-folder password cache. RAR's split between header decrypt (the parser,
multi-candidate) and payload (`unrar`, the one password `record_success`
already promoted). Those are different primitives. A single "normalize
password" would break one of UTF-8, RAR's UTF-16LE truncation, and 7z's
UTF-16LE KDF bytes.

### R5. `StreamCodec` methods that are rows in a table

`STREAM_CODECS` is already the registry. Many subclasses still override
`translate`, `open`, and `rewind_warning` with the same shape and a different
token.

- `translate`: `EOFError` becomes `TruncatedError("<name> stream is truncated")`
  on gzip, bzip2, LZMA, deflate, zstd, lz4, PPMd, and deflate64. The
  corruption branch is the part that differs (gzip `BadGzipFile` and
  `zlib.error`; deflate's `"incomplete"` / `"truncated"` substrings; bzip2's
  `"Invalid data stream"`).
- `rewind_warning`: lzma-alone, zlib, zstd, lz4, and brotli return
  `RewindWarning("<name>")` and ignore `config`. Gzip and bzip2 add the
  accelerator gate (return `None` when rapidgzip is in use).
- Optional codecs (zstd, lz4, brotli, PPMd, deflate64): `_backend_present` is
  `module is not None`, and `open` raises `PackageNotInstalledError` with an
  install hint, then constructs a stream.

Gzip and bzip2 `_translate_accelerator` are not the same function. Each is a
list of harness-found substrings. The shared tail is the non-seekable pair:
`ValueError` "has no valid fileno", and `UnsupportedOperation` containing
"seek", both mapped to `StreamNotSeekableError`. Only the message backend
name differs (`codecs.py` 596–603 and 709–716).

**Merge.** Default `rewind_warning` from a class attribute, with gzip and
bzip2 keeping the accelerator override. A small `_eof_truncated(exc, label)`
and `_accelerator_non_seekable(exc, label)`. A table for optional modules
(module, requirement string, extras name) so `open` and `_backend_present`
cannot drift. Leave each codec's corruption predicates as data on that codec.

**Stays per codec.** Gzip versus raw-deflate truncation policy (gzip does not
treat `"incomplete"` inside `zlib.error` as truncation; `_ZlibErrorCodec`
does). Accelerator substring lists. The construction of the actual
decompressor, which the composition change already owns.

**Risk.** Collapsing gzip and deflate into one `translate` without a policy
flag will mislabel short gzip bodies. Collapsing the accelerator matchers
into one predicate list will mislabel the other format.

### R6. Timestamps: the shared helper, and the copies that missed it

`timestamps.py` says the FILETIME conversion is shared so it is not
copy-pasted, and that RAR will use it later. ZIP and 7z call
`filetime_to_datetime`. RAR does not. `rar_parser.py` `_load_windowstime`
(507–523) does the same epoch math with integer `divmod`, then keeps the
remainder as microseconds (`rem // 10`). `filetime_to_datetime` (44–49) uses
float division (`value / 10_000_000 - NTFS_EPOCH_OFFSET`). Float64 cannot
hold 100 ns ticks at modern dates, so ZIP and 7z NTFS times are coarser than
RAR Windows times, and a hostile value becomes a `TimestampIssue` on ZIP/7z
and a silent `None` on RAR.

The same `(ValueError, OverflowError, OSError)` guard around
`datetime.fromtimestamp` also appears for Unix seconds, with different
observability:

| Site | On failure |
|------|------------|
| `timestamps.filetime_to_datetime` | `(None, TimestampIssue)` |
| ZIP extended UT (`zip_reader.py` around 270) | append a `TimestampIssue` |
| TAR mtime (`tar_reader.py` around 413) | diagnostic, field cleared |
| TAR `_pax_time` (115–127) | silent `None` |
| `directory_reader._stat_datetime` (44–54) | silent `None` |
| RAR `_load_unixtime` (496–504) | silent `None` |

DOS time is two policies on purpose-looking code: ZIP (`_zip_timestamps`)
turns `ValueError` into an issue and `modified = None`. RAR `_parse_dos_time`
(472–493) clamps the fields and still returns a `datetime`.

RAR5 `_parse_rar5_xtime` (1476–1489) reads creation and access times and
discards them (`_, pos = ldr(...)`). `ArchiveMember` has `accessed` and
`created`. ZIP and 7z fill those fields. RAR listing never will, even though
the parser already walked the bytes.

**Merge.** Route RAR `_load_windowstime` through one integer FILETIME helper
that can preserve microseconds, and use that helper from `filetime_to_datetime`
so ZIP and 7z gain the same precision and the same out-of-range behavior.
Add `unix_seconds_to_datetime` that returns `(datetime | None, issue | None)`.
Callers that today stay silent can keep dropping the issue, in one line, until
someone decides the diagnostic is wanted everywhere.

**Stays per format.** ZIP's DOS-then-NTFS-then-extended precedence. ISO 9660
directory dates (`iso_reader.py` `_dr_date_to_datetime`), which are not Unix
seconds and not FILETIME. The DOS clamp-versus-issue choice, until there is
an explicit rule.

**Risk.** Making RAR emit `TimestampIssue` changes diagnostics for archives
that today list cleanly with a null time. Preserving microseconds changes
`modified` equality for ZIP and 7z NTFS entries. Both are behavior changes,
small ones, and they want a test that pins a known Windows timestamp.

### R7. Identical payload wrap, identical reader construction

`RarReader._wrap_payload_stream` (474–485) and `SevenZipReader._wrap_folder_member`
(924–935) are the same function: if `member.hashes`, wrap `VerifyingStream`
with the collector, the member, and the archive name; then
`_wrap_member_stream(..., size=member.size)`.

**Merge.** `BaseArchiveReader._wrap_verified_member_stream(inner, member)`.
ZIP stays on zipfile's own CRC at EOF. Do not put `VerifyingStream` on ZIP.

The seven `ReadBackend.open_read` methods are constructor forwarding with the
same signature. Pairwise similarity is about 0.95, except directory.
Differences that a helper must not erase: TAR passes `format` through;
directory rejects a non-`Path` and does not take passwords; RAR and 7z ignore
`format`. A `_reader_kwargs(...)` bundle used by each `open_read` is enough.
A registry that constructs readers by a class object will get TAR's argument
order wrong.

### R8. Smaller copies worth deleting when the file is already open

These are real duplicates. None of them is worth a change by itself. The
third column is the trap.

| Copy | Where | Leave in place |
|------|--------|----------------|
| `emit_member_name_normalized` five-line tail | zip, tar, rar, 7z, iso `_to_member` / `_make_member` | Directory skips it on purpose (`directory_reader.py` 207–210). Single-file has no stored name. |
| ZIP and 7z timestamp-issue emit loops | `zip_reader.py` ~523, `sevenzip_reader.py` ~754 | 7z hardcodes `source="ntfs"`; ZIP uses `issue.source`. The helper must pass `issue.source`. |
| Seekable-source rejection | zip ~338, rar ~199, 7z ~565 | TAR and single-file accept a non-seekable source. |
| `passwords or _PasswordCandidates()` | zip, rar, 7z `__init__` | The other four readers must not grow a password field. |
| CONCURRENT `_handle_lock = threading.Lock()` | zip ~334, iso ~274 | TAR also locks when `streaming` (`tar_reader.py` ~172). |
| Empty payload `BytesIO(b"")` then wrap | TAR `extractfile is None`; 7z folder index `None` | "Missing" is not always "empty". |
| `zlib.crc32(data) & 0xFFFFFFFF` | `rar_parser._crc32`, `sevenzip_parser._crc32`, plus inline sites in xz, lzip, 7z reader | Not ZipCrypto's keystream update. Not RAR3's 16-bit header CRC. |
| Strict short read | `read_exact` (soft), RAR `_require_exact` (`CorruptionError`), 7z `_read_exact` (`CorruptionError` plus a size cap), 7z folder decode (`TruncatedError`) | One helper with a caller-chosen exception. A single exception type changes the public error. |
| `readinto` via `memoryview` | `ReadOnlyIOStream`, `ArchiveStream`, `LockedStream`, `BinaryIOWrapper` | Keep one implementation in `streamtools`. |
| `LockedStream.close` and `CloseLockedStream.close` | `streamtools/locked.py` | Same skeleton, lock around `inner.close()`. |
| Path-or-stream source for a codec | `tar_reader.py` ~203, `single_file_reader.py` ~260, `core.py` ~341 | TAR still needs `ensure_bufferedio` around the result. |
| Non-consuming prefix peek | `detection.py` `_peek_prefix`, `single_file_reader.py` `_read_source_prefix` | Single-file returns `b""` when the source is not seekable and not peekable. Detection does not. |
| `1_048_576` member cap | `ListingLimits.max_members`, `ExtractionLimits` default, `rar_parser._MAX_ARCHIVE_MEMBERS` | The parser cap must stay a hard ceiling when the user sets listing limits to unlimited. Share the number, not the policy. |
| Extraction `except (ArchiveyError, OSError)` blocks | `extraction.py` ~364, ~701, ~810 | The main pass rewrites `OSError` `EILSEQ` into `ExtractionError`. A helper has to allow that. |
| `open_archive` / `open_stream` stream prep | `core.py` | Both wrap a non-seekable stream in `PeekableStream` and rewind a seekable one. `open_archive` also does volumes and directory. Share only the stream prep. |

## Same policy, different behavior

These are not line-for-line copies. They are the consistency problems the
corner cases created. Each one needs a decision before a mechanical merge.

**C1. Encrypted symlink target.** ZIP reports `SYMLINK_TARGET_UNAVAILABLE`.
7z and RAR leave `link_target` unset and say nothing. Following the link
later fails with `LinkTargetNotFoundError` in all three, so a caller who
only watches diagnostics cannot tell "this format has no target" from
"the target is in an encrypted payload". R3 is the place to make them match.

**C2. Timestamp failure.** Silent `None` versus `MEMBER_TIMESTAMP_INVALID`,
listed under R6. TAR is inconsistent with itself: a bad mtime is reported,
a bad PAX atime or ctime is not.

**C3. `size` on a non-file.** TAR, ISO, and directory set `size` only for
`FILE` (TAR also keeps it for `HARDLINK`) and use `None` otherwise. ZIP, RAR,
and 7z copy the header size, which is often 0 for a directory. Callers that
treat `None` and `0` as the same "no payload" are fine. Callers that treat
`None` as "unknown, do not trust it" are not. This wants a sentence in the
member contract, not a shared helper that picks one.

**C4. CRC on a directory.** ZIP attaches `crc32` only for `FILE` and `SYMLINK`,
and the comment says a directory CRC of 0 is meaningless. 7z attaches `crc32`
whenever the record has one, with no type filter. RAR drops a CRC that the
encryption header has tweaked.

**C5. Junctions.** Directory entries and RAR5 Windows redir set
`extra["is_junction"]`. 7z maps the Windows reparse-point attribute bit to
`MemberType.SYMLINK` (`sevenzip_reader.py` 783–784) and does not set the
flag. The bit is not a junction tag; mount points and symlinks share it.
Setting `is_junction` for every 7z reparse point would be wrong. The gap is
that 7z has no reparse tag, so `is_junction` cannot be filled from the
attribute word. Worth a comment on `_member_type`, not a forced flag.

**C6. bzip2 error class depends on the accelerator.** Stdlib `Bzip2Codec.translate`
(`codecs.py` 663–664) maps every `ValueError` to `TruncatedError`. The
rapidgzip path (`677–707`) maps Huffman, magic, and block `ValueError`s to
`CorruptionError`. The same damaged bzip2 stream can change exception type
when `use_indexed_bzip2` flips. The messages say `indexed_bzip2` while the
object is rapidgzip's `IndexedBzip2File` (the comment at `codecs.py` 102–106
explains the symbol collision; the user-facing string does not).

**C7. Mid-pass links versus `members()`.** Covered by R2. A test that builds
a three-symlink chain and reads `link_target_member` from `stream_members`
before EOF would show the gap. The existing contract tests check stream
bytes and id stamping, not this chain.

## Confusing

**K1. Comments that describe a library this tree has already left.**
`base_reader.py` still says native 7z/RAR readers "will need" the forward-pass
contract (around 205) and "the future native 7z/RAR readers" (around 471).
`timestamps.py` line 5 and `zip_reader.py` line 166 say RAR will use the
FILETIME helper later. RAR has its own converter. A reader who trusts the
comment will "fix" RAR by calling the float helper and drop microseconds.

**K2. Who closes a member stream.** Three places, described in R1. The base
comment, the RAR/7z `finally`, and `stream_members` do not agree on the page.
TAR streaming matches the comment (it does not close). RAR and 7z match
neither comment nor TAR.

**K3. ZIP opens one entry three ways.** `_open_zip_entry` chooses lazy,
stored-confirmed, or compressed-confirmed. Stored is a four-phase method
(check-byte survivors, one CRC pass, provider, reopen) that does not call
`attempt`. Compressed confirms with a short decompress, then opens again.
The second open is the stream the caller receives; the first exists only to
accept or reject a password. That is correct and easy to miss. The earlier
review's X4 note is still accurate.

**K4. RAR has two password stages for one candidate list.** `_parse_archive`
runs `attempt` so the native parser can decrypt headers. Member payload uses
`_unrar_password`, taken from the promoted candidate, and a wrong password
surfaces when `_UnrarOwnedStream` sees `unrar` exit 11 at close, not at open.
There is no per-member candidate loop. That is a real constraint of `unrar`.
The code does not say, at the payload open, that the header stage already
consumed the provider.

**K5. 7z verifies a folder, then decodes it again.** `_password_for_folder`
fully decodes the folder and CRC-checks it to accept a password. `_open_folder_stream`
decodes it again for the caller. `VerifyingStream` CRC-checks member slices a
third time on read. Header materialization (`decode_folder_to_bytes`) CRC-checks
with `CorruptionError`; the password confirm CRC-checks with `EncryptionError`.
The split is load-bearing (B1). It is not obvious from either function alone.

**K6. Single-file nests `ArchiveStream`.** `open_codec_stream` already returns
an `ArchiveStream` with the codec translator. `SingleFileReader._open_codec_stream`
(267–269) wraps it again and comments that nesting is fine. The outer
translator is the base default, which translates nothing. Errors still
translate on the inner stream. The outer wrapper exists to attach the
reader's collector and `size`. A reader of `_open_member` cannot see which
layer stamps.

**K7. `base_reader.py` is the two access modes, in one class.** 1400 lines.
Indexed snapshot (`_members_cache`, `_get_members_registered`) and forward
pass (`_pass_scanned`, `_progressive_gen`, `_stamp_progressive_member`,
`_finalize_pass_links`) both assign ids, both index names, both resolve
links. `get_members_if_available` is a third enumeration that stamps ids and
does not resolve links. The class docstring tells an implementer which hooks
are mandatory. It does not say which of the two pipelines their override
joins. R1 and R2 are the readable slices of that. S2 is the structural one.

## Bugs and likely bugs

Confirmed by reading both sides. Not executed against fixtures.

**B1. A wrong 7z header password can skip the rest of the candidate list.**

`_PasswordCandidates.attempt` retries only on `EncryptionError` (`password.py`
177–184).

Folder unlock wraps every `ArchiveyError` from the trial decode, including
`CorruptionError` from a CRC miss, into `EncryptionError("Wrong password or
corrupt 7z folder")` (`sevenzip_reader.py` 854–855). The next candidate runs.
The comment above that CRC (849–851) says a wrong LZMA key can still emit the
expected length, so the CRC is what rejects it.

Header unlock does not wrap. `_decode_header_folder` (611–615) passes
`decode_folder_to_bytes` straight to `attempt`. On CRC mismatch that function
raises `CorruptionError` (521–522), which is a sibling of `EncryptionError`,
not a subclass (`exceptions.py`). `attempt` lets it out. The
`_PasswordCandidatesExhausted` handler (617–618) never sees it. Later static
candidates and the provider are not tried.

A header whose first candidate is wrong, and whose decoder returns a
full-length buffer with a bad CRC, fails the open. The same archive would
keep trying if that folder were a member folder rather than the header.

**B2. Stdlib bzip2 maps every `ValueError` to truncation.** Cited under C6.
This is a misclassification risk, not a traced failure. Stdlib `bz2` reports
bad data as `OSError("Invalid data stream")`, which the method already maps
to `CorruptionError`. The `ValueError` arm is broader than any comment
justifies. The accelerator path shows that bzip2 `ValueError` is often
corruption.

**Not a bug, recorded so it is not "fixed" later.** RAR and 7z closing the
last stream in `finally` does not invalidate a stream the caller is still
reading. The generator is suspended at `yield` until `stream_members` pulls
the next item or closes. The double close happens at that moment. See K2.

**Not confirmed.** `Deflate64Codec.translate` catches `zlib.error` (`codecs.py`
1085). The stream is `inflate64.Inflater`, not zlib. The branch may be dead.
No test was run to see whether inflate64 leaks `zlib.error`.

## Leave separate

A second read threw these out. They matched at a glance because several
subclasses override the same method name.

- **`_to_member` / `_member_type`.** ZIP (directory, then Unix symlink, else
  file), TAR (including `HARDLINK` and `OTHER`), RAR (directory, then
  hardlink-or-copy, then symlink), 7z (anti, Unix mode, Windows reparse,
  directory), ISO (Rock Ridge symlink, then directory). One function would
  drop anti-items or TAR devices.
- **Compression tables.** ZIP's integer map, RAR's method byte as `UNKNOWN`
  plus a level, 7z's coder chain with AES removed. The tuple type is shared.
  The maps are not.
- **Password key schedules.** ZipCrypto, 7z AES-SHA256, RAR3 SHA1, RAR5.
  Named functions in one module would only help navigation. One function
  would be a bug.
- **Header integer encodings.** RAR5 vint, XZ multi-byte integer, 7z UINT64.
  Different widths, endians, and escape rules. The fuzz helper
  `_load_vint_at` repeats the RAR vint; that is the only copy, and it returns
  `None` instead of raising.
- **RAR and 7z parsers.** Share `read_exact` and a CRC helper (R8). Do not
  share the grammars.
- **XZ and lzip backward index walks, and `_XzState` / `_LzipState`.** Same
  `feed` / `flush` / `is_finished` protocol, different trailers. The
  composition change already deletes the stream subclass shell. The parsers
  stay.
- **Volume discovery.** `volumes.py` already holds 7z and RAR naming. ZIP
  split (`.z01`) is a reject, not a join. Concatenating RAR for the native
  parser and handing paths to `unrar` are both required.
- **Extraction link planning versus on-disk symlink checks.** `filters.py`,
  `naming.resolve_link_target_name`, and `ExtractionCoordinator._write_symlink`
  are three times on purpose: archive namespace, destination planning, and
  chained symlinks after create.
- **Directory listing.** Live `scandir`, junction reparse, no
  `normalize_member_name`. It is not an archive backend that forgot to call
  the helper.
- **ISO dates, Joliet versus Rock Ridge, pycdlib.** No second implementation
  to merge with.
- **Single-file `_build_member`.** The codec fills size and time through
  `extract_metadata`. It should stay thin.

## What to do first

1. **B1**, if a two-password encrypted 7z header confirms it. The fix is to
   treat a CRC miss on a header trial as `EncryptionError`, the way the
   folder trial already does, without changing the `CorruptionError` used
   once the password is known.
2. **C1 / R3**, one symlink-unavailable policy, then the shared decode.
3. **R6**, integer FILETIME in the shared helper, RAR calls it. Decide
   whether Unix-seconds failures are silent everywhere or reported everywhere.
4. **R1**, the pass driver. Do it alone. It is the override deduplication
   this sweep was aimed at.
5. **R2**, extract the duplicated finalize loops and document the mid-pass
   hop. Leave S2 (one pipeline) for its own change.
6. **R5**, codec defaults for rewind, EOF, optional imports, and the
   accelerator non-seekable tail. Leave the substring tables.

R7 and R8 can land inside whichever of those edits already has the file open.
