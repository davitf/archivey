# `catalogue.md` — the problem catalogue (annotated)

Every non-trivial problem this project has had to consider, stated so that **someone who
has never seen archivey could design against them**. One entry per problem, N sources.

Read [`brief.md`](brief.md) first. This file is the **annotated** view — all four fields.
[`catalogue-neutral.md`](catalogue-neutral.md) is the same catalogue with field 4 stripped,
and is the artifact the [experiment](experiment.md) is run against.

## Entry schema

| # | Field | Rule |
|---|---|---|
| 1 | **Problem** | Solution-neutral. What the format, the library, the platform, the attacker or the user does — never what we built |
| 2 | **Symptom** | What someone actually observes |
| 3 | **Evidence** | Format spec section, upstream issue, a pinning test, a `file:line`. An entry without evidence is a belief, not a problem |
| 4 | **Answer today** | The mechanism plus the ADR / change / finding that decided it. **Strippable** |

Plus a stable id, a category, and every source that states it.

### The neutrality rule, and one place the schema needed sharpening

Field 1 may not name an archivey type, module or config field
([`brief.md`](brief.md) §The neutrality rule). Two consequences the brief does not spell
out, both adopted here:

1. **Fields 2 and 3 carry the same obligation as field 1**, because the experiment is
   handed all three. A symptom described as "`ArchiveReader.open()` raises
   `ConcurrentAccessError`" leaks the design as surely as a problem statement would.
   Symptoms are therefore written as what an operator or caller *sees*.
2. **Field 3 cites the demonstration, not the implementation.** A pinning test, an
   upstream issue, a format spec section, a measurement, or a register entry is evidence
   that the problem is real. The internal class that *answers* it is field 4 material, and
   citing it in field 3 would smuggle the solution into the redacted view. Where the only
   available proof is archivey's own test, the test path is cited (a path like
   `tests/test_iso.py:362` says a case exists; it does not describe an architecture).

Category prefixes: `FQ` format quirk · `UL` upstream library defect · `SEC` security /
hostile input · `PLAT` platform & filesystem · `PERF` performance & memory · `API` API and
usage pattern · `PKG` packaging & dependency · `CONC` concurrency & lifetime.

Ids are stable. A merged duplicate keeps the surviving id and gains the merged entry's
sources; the retired id is listed in §Retired ids rather than reused.

---

## Format quirk

### FQ-01 — A member's true size and checksum can only be known after its bytes have been read

**Problem.** Several container formats place a member's uncompressed length and integrity
digest *after* the member's data rather than in a header before it: a gzip stream's CRC32
and length live in an 8-byte trailer, and a ZIP entry whose general-purpose flag bit 3 is
set carries zeros in its local header and its real sizes in a data descriptor following
the compressed bytes. A producer streaming into such a format cannot know the values in
advance, which is exactly why the formats allow this. So a listing taken before the data
is read is structurally incomplete, and the completion arrives in the middle of a read.

**Symptom.** A member's reported size is absent, zero, or wrong at listing time and
correct after the bytes have been consumed. Code that captured the metadata up front and
compared it with the delivered bytes reports a mismatch that does not exist. A consumer
that needs the length in order to preallocate, report progress, or enforce a limit has
nothing to work from on exactly the archives that stream.

**Evidence.** ZIP APPNOTE §4.3.9 (data descriptor) and §4.4.4 general-purpose bit 3;
RFC 1952 §2.3.1 (gzip `CRC32`/`ISIZE` trailer). Stated at
`dev-docs/history/SPEC.md:341-347` and `dev-docs/history/ARCHITECTURE.md:73-88`.

**Answer today.** The member object is a mutable dataclass and the library fills late
fields in place on the object the caller already holds, so a caller under a single forward
pass sees the late values without re-fetching; callers are contractually read-only and
edit via a copy. Decided in ADR
[`0007-mutable-archive-member`](../../dev-docs/decisions/0007-mutable-archive-member.md);
consequences (unhashability, filters copy-on-edit) at `ARCHITECTURE.md:95-101` and
§2.10.

**Sources.** `history/SPEC.md` §4.4, §10.1; `history/ARCHITECTURE.md` §2.1, §2.10, §5.2;
`history/COMPARISON.md` §4.2; ADR 0007.

---

### FQ-02 — A gzip stream's stored length is the true length modulo 2³²

**Problem.** The gzip trailer records the uncompressed size in a 32-bit field, so for any
payload of 4 GiB or more the stored value is the real size taken mod 2³². The field is not
optional and not marked unreliable; it is simply too narrow, and nothing in the stream says
which of the two readings applies.

**Symptom.** A large single-file compressed stream reports a plausible but wrong size —
a 5 GiB payload advertises 1 GiB. A completeness check that compares bytes delivered
against the stored length fails on a correct decode, or passes on a truncated one.

**Evidence.** RFC 1952 §2.3.1 (`ISIZE`, "the size of the original input data modulo 2^32").
Recorded at `dev-docs/history/SPEC.md:949`.

**Answer today.** Size is reported as unknown rather than guessed for gzip members, and
the trailer length is used only as a single-member truncation backstop where the payload is
small enough for the comparison to be meaningful; multi-member summing is deferred.
`dev-docs/library-analysis.md` §gzip note 1; `dev-docs/known-issues.md` §"Soft EOF on
truncated gzip".

**Sources.** `history/SPEC.md` §10.3; `library-analysis.md`; `known-issues.md`;
`open-issues.md` (Irreducible).

---

### FQ-03 — A link's target is sometimes stored in the member's data, not its metadata

**Problem.** Formats disagree about where a symbolic link's target path lives. Some record
it as a header field; others store the link as an ordinary entry whose *content* is the
target string — which means the target is compressed, and if the archive is encrypted, the
target is encrypted too. Resolving such a link therefore requires decompressing (and
possibly a password), not just reading the index. Formats also disagree on whether a link
member has a data stream at all.

**Symptom.** An enumeration of the archive shows link entries whose targets are blank until
something reads their payloads. On an encrypted archive, merely resolving a link asks for a
password. A tool that resolves links during listing does far more work — and prompts far
more often — than the user expected from "list".

**Evidence.** `dev-docs/history/ARCHITECTURE.md:80-88` and `SPEC.md:386-390` (target "stored
in (or encrypted within) the member's data"); `SPEC.md:1000-1002` and
`ARCHITECTURE.md:1002-1011` for the per-format table. Measured per-format digest/data
comparison in `dev-docs/investigations/rar-corpus-sweep-diagnosis.md:66-76` (ZIP stores 9
bytes of target as data, 7z 45 bytes, TAR none, RAR5 none).

**Answer today.** Link targets are treated as late-bound like sizes (FQ-01) and filled in
place during the pass; a report peek is documented as possibly having unresolved targets,
while the resolved-list calls guarantee them.
`ARCHITECTURE.md:262-269`; `SPEC.md:168`.

**Sources.** `history/SPEC.md` §4.4, §3.2, §10.5; `history/ARCHITECTURE.md` §2.1, §2.4, §8;
`history/COMPARISON.md` §4.2; `investigations/rar-corpus-sweep-diagnosis.md`.

---

### FQ-04 — Some formats put their index at the end of the file

**Problem.** A ZIP file's authoritative table of contents is the central directory, located
at the end of the file and found by scanning backwards from the last bytes for the
end-of-central-directory record. Reading the archive's structure therefore requires
positioning at the end before anything else. This is the format working as designed —
appending to a ZIP means writing a new directory at the end — but it means the archive
cannot be understood from its first bytes.

**Symptom.** An archive arriving over a pipe, socket, or HTTP body cannot be listed at all
without first spooling the whole thing to memory or disk, however small the part of it the
caller wanted. The failure appears at open time, before any member is touched.

**Evidence.** ZIP APPNOTE §4.3.6 (overall `.ZIP` file format), §4.3.16 (end of central
directory record). Recorded at `dev-docs/history/SPEC.md:902,911`;
`dev-docs/open-issues.md` §Irreducible ("ZIP / ISO need seek — no pure-pipe path").

**Answer today.** A non-seekable source for a format that needs an index at the end is
refused at open time with an error advising the caller to buffer and reopen; the library
never buffers implicitly. Decided in ADR
[`0010-no-silent-buffer-nonseekable`](../../dev-docs/decisions/0010-no-silent-buffer-nonseekable.md).
A native streaming reader that could handle the pipe case is registered as future work
(`open-issues.md` §Longer-term, "Native streaming ZIP").

**Sources.** `history/SPEC.md` §10.1; `history/ARCHITECTURE.md` §2.12; `open-issues.md`
§Irreducible, §Longer-term; ADR 0010.

---

### FQ-05 — Some formats have no index at all, so enumerating members costs a full read

**Problem.** A tar archive is a bare concatenation of 512-byte headers each followed by its
file's bytes. There is no table of contents anywhere in the file, so the only way to learn
what the archive contains is to walk every header, skipping over every payload — the whole
file. Wrapping that tar in a single compression stream makes it worse: reaching the next
header means decompressing everything before it.

**Symptom.** Asking "what is in this archive?" takes time proportional to the archive's
size rather than its member count, and on a compressed archive costs a full decompression.
The same question is answered instantly for a format with an index, so the cost is
invisible to a caller who does not know which format they hold.

**Evidence.** POSIX.1-2017 `pax` Interchange Format / ustar header block layout. Recorded
at `dev-docs/history/SPEC.md:920` (listing cost "O(N) — no central directory") and
`dev-docs/history/ARCHITECTURE.md:526-533`.

**Answer today.** Listing cost is a declared, queryable property of the opened archive
(indexed / requires scanning / requires decompression) rather than something the caller has
to infer from the extension, and a member list is never materialized unless asked for.
`ARCHITECTURE.md` §2.12, §2.4.

**Sources.** `history/SPEC.md` §10.2; `history/ARCHITECTURE.md` §2.4, §2.12, §7.2;
`history/COMPARISON.md` §2.

---

### FQ-06 — Solid compression makes one member's bytes depend on every earlier member's

**Problem.** Several formats compress many members as a single continuous stream (7z
folders, solid RAR blocks, and any tar wrapped in one compressor). This is why they compress
well: the codec's window spans file boundaries. The consequence is that member *k*'s bytes
cannot be produced without decoding members 1..*k*−1 first. Reading *n* members in
arbitrary order therefore costs O(n²) decode work, while reading them in archive order
costs one pass.

**Symptom.** A loop that opens each member by name and reads it — the obvious way to write
the code — takes quadratically longer than the same loop over a non-solid archive of the
same size, with no error and no warning; the archive just appears slow. Extracting a single
small file from the end of a large solid archive costs as much as extracting all of it.

**Evidence.** 7z folder/substream layout (a folder's decompressed output is the
concatenation of its files, sizes from the header): `dev-docs/history/ARCHITECTURE.md:836-847`.
`dev-docs/history/SPEC.md:957,981`; `dev-docs/open-issues.md` §Irreducible ("Solid
archives: out-of-order `open()` can re-decode").

**Answer today.** Access cost (direct vs solid) and the solid block count are declared on
the opened archive; the in-order streaming pass is the documented way to read every member
once, and an out-of-order open re-decodes from the block start rather than holding a
growing cache. Whether an out-of-order open should also *warn* was decided as "no
diagnostic" and parked as an open question — `spec-drop-unimplemented-solid-warning`,
`open-issues.md` P9.

**Sources.** `history/SPEC.md` §10.4, §10.5; `history/ARCHITECTURE.md` §2.12, §5.6, §7.3,
§7.4; `history/COMPARISON.md` §4.4; `open-issues.md` §Irreducible, P9;
`investigations/parallel-reader.md` §5.

---

### FQ-07 — Member names are stored as bytes with no dependable statement of their encoding

**Problem.** Older archive formats predate Unicode and store names as raw bytes whose
encoding is either unstated or stated unreliably. ZIP has a general-purpose flag bit that
declares a name is UTF-8, and producers set it on names that are not UTF-8 and omit it on
names that are; unflagged names are nominally CP437 and in practice any local codepage. Some
RAR versions write filename fields that are malformed in their own declared encoding. There
is no in-band way to recover the intended characters, and a wrong guess is silent.

**Symptom.** Filenames appear as mojibake, or as replacement characters, or an archive that
other tools list fine cannot be listed at all because one name fails to decode. The same
archive shows different names under different tools.

**Evidence.** ZIP APPNOTE §4.4.4 (general purpose bit 11, "language encoding flag") and
APPNOTE Appendix D (CP437 default). `dev-docs/open-issues.md` P4 (a strict UTF-8 decode of
flagged names makes one bad name render the whole archive unlistable);
`dev-docs/history/COMPARISON.md:57,225` (RAR 2.9–4 UTF-16 filename corruption);
`history/SPEC.md:363-366,440-445` (the verbatim stored bytes are retained precisely so a
wrong guess can be undone).

**Answer today.** The decoded name and the verbatim stored bytes are both carried, so a
name can be re-decoded losslessly under another encoding; encoding is sniffed for ZIP
(`2026-07-14-zip-name-encoding-sniffing`) and normalization that changes a logical path
emits a diagnostic. The strict-UTF-8-flag failure mode remains open, tied to a native ZIP
parser (`open-issues.md` P4).

**Sources.** `history/SPEC.md` §4.4; `history/COMPARISON.md` §2, §4.12; `open-issues.md` P4;
`openspec/changes/archive/2026-07-14-zip-name-encoding-sniffing/`.

---

### FQ-08 — Two members of one archive can carry the same name

**Problem.** Nothing in tar, 7z or ZIP requires member names to be unique. Tar's append mode
produces a second entry with the same name by design — that is how the format expresses "a
newer version of this file" — and a crafted or merely sloppy archive can contain any number
of same-named entries. There is no defined winner: the format states an order, not a
resolution.

**Symptom.** A listing shows N entries and extracting them produces fewer than N files, with
later ones silently overwriting earlier ones. A lookup by name returns one entry with no
indication that others exist, so a tool that round-trips "list, then fetch each by name"
loses data without any error.

**Evidence.** POSIX.1-2017 `pax`/ustar (append semantics; no uniqueness constraint).
Recorded at `dev-docs/history/COMPARISON.md:144` ("duplicate filenames are real (TAR, 7z)
and CSP's name-keyed model mishandles them") and `dev-docs/open-issues.md:233`.

**Answer today.** Members carry a stable positional identity independent of their name, and
name lookup is documented as last-wins; hardlink resolution is defined against that
positional identity ("earlier member with that name") rather than the name alone.
`ARCHITECTURE.md:144`, `SPEC.md:415-417`; `open-issues.md:233-234`.

**Sources.** `history/COMPARISON.md` §4.2; `history/SPEC.md` §4.4; `open-issues.md` §Docs.

---

### FQ-09 — Formats disagree about timestamp epoch, precision, and timezone

**Problem.** Each format records modification time in its own terms. The DOS date/time
field used by ZIP has 2-second granularity and no timezone, so it means local wall-clock at
the producer. Tar records a Unix timestamp, understood as UTC, with optional extended
headers carrying higher precision. Some formats record local time without an offset; others
record UTC with sub-second precision; others carry a second, higher-precision timestamp in
an optional extra field that disagrees with the mandatory one. None of these is convertible
to another without information the archive does not contain.

**Symptom.** Converting an archive from one format to another shifts or rounds every
timestamp. The same file listed from two formats shows two different times. A comparison
that expects equality across formats fails on legitimate archives, and a naive
local-vs-UTC read is wrong by the producer's offset.

**Evidence.** ZIP APPNOTE §4.4.6 (MS-DOS date/time, 2-second resolution) and §4.5.5 (extra
field `0x5455` extended timestamp); POSIX.1-2017 `pax` extended header keywords
(`mtime` with sub-second precision). Recorded at `dev-docs/history/SPEC.md:906,927-928,
996-998` and `history/COMPARISON.md:57`.

**Answer today.** A timestamp is exposed as timezone-aware when the format records UTC or
an offset and naive when the format records local wall-clock, with the distinction
documented rather than papered over; the higher-precision extra field is preferred where
present. Cross-format equivalence testing compares only the fields a format can carry
(`ARCHITECTURE.md:396`).

**Sources.** `history/SPEC.md` §4.4, §10.1, §10.2, §10.5; `history/COMPARISON.md` §2, §4.12.

---

### FQ-10 — Permissions and ownership are optional or absent in most formats

**Problem.** Unix mode bits, uid/gid and user/group names are not part of every archive
format's data model. ZIP carries them only when the producer was a Unix-like system and
wrote them into an external-attributes field; when it was not, the field is zero and means
nothing. 7z keeps POSIX attributes in an optional block a writer may omit entirely. Some
formats record only a Windows attribute word. A reader therefore cannot distinguish "mode
0" from "no mode recorded" from the stored bytes alone.

**Symptom.** Extracted files get whatever the umask allows rather than the modes they had,
or a tool that copies modes across a conversion writes mode 0 files. A member that a
different tool shows with permissions shows none here, or vice versa, and there is no error
either way.

**Evidence.** ZIP APPNOTE §4.4.15 (external file attributes) and §4.4.2.2 (version made by
/ host system). Recorded at `dev-docs/history/SPEC.md:905,972` ("7z POSIX metadata lives in
an optional attribute block (absent → `mode`/`uid`/`gid` are `None`)").

**Answer today.** Unrecorded metadata is an explicit "unknown" value rather than a
substituted default — a design authority stated at `SPEC.md:13`: an unmappable format quirk
surfaces as a documented `None`/unknown, "never as a silent guess, default, or exception".

**Sources.** `history/SPEC.md` §1, §4.4, §10.1, §10.4; `history/COMPARISON.md` §4.2.

---

### FQ-11 — ISO 9660 images carry the same tree several times, at different fidelity

**Problem.** An ISO 9660 image can describe its contents in up to three parallel
namespaces: the base standard (names truncated to 8.3 and case-folded, no POSIX metadata),
Joliet (long UCS-2 names in a supplementary volume descriptor), and Rock Ridge (POSIX
metadata, long names, symlinks, as extensions to the base records). They are views of the
same extents, and an image may carry any combination. Nothing designates one as canonical.

**Symptom.** The same disc image lists as `README.TXT;1` under one tool and `ReadMe.txt`
under another, with symlinks and permissions present in one listing and absent in the
other. Which one a caller gets depends on a choice they did not know was being made.

**Evidence.** ECMA-119 (ISO 9660) §6.8/§8.4 (primary and supplementary volume descriptors);
the Rock Ridge Interchange Protocol and Joliet specifications. Recorded at
`dev-docs/history/SPEC.md:1016`.

**Answer today.** The richest available namespace is selected in a fixed priority order and
the choice is reported on the opened archive's metadata so a caller can see which view they
got. `SPEC.md:1016`.

**Sources.** `history/SPEC.md` §10.6; `history/COMPARISON.md` §2.

---

### FQ-12 — Some compressed formats have no magic bytes to detect them by

**Problem.** Identifying a compressed stream normally means matching a fixed byte signature
at a fixed offset. Brotli has no such signature: a raw brotli stream begins with the first
bits of its own compressed data. It is not merely undocumented — the format deliberately
spends no bytes on a header. Nor does it carry a length or checksum trailer.

**Symptom.** A file cannot be identified as brotli from its contents at all; identification
falls back to the filename, which may be absent (a stream) or wrong. And because there is
no trailer, a brotli stream cut short cannot be distinguished from one that ended.

**Evidence.** RFC 7932 (brotli; no container header, no trailing checksum). Recorded at
`dev-docs/library-analysis.md:57,309-315` and `history/COMPARISON.md:205`.

**Answer today.** Brotli is detected by trial-decoding a bounded prefix, and truncation is
reported only via "the decompressor never reported finished at end of input" — recorded as a
partial capability in the per-codec truncation column
(`library-analysis.md` §Summary, note 2).

**Sources.** `library-analysis.md` §Summary, §brotli; `history/COMPARISON.md` §4.9.

---

### FQ-13 — The same compressed bytes may be a single file or a whole archive

**Problem.** A gzip, bzip2, xz or zstd stream says nothing about what it contains. The
overwhelmingly common case is a tar archive, but the identical outer format is also used for
a single compressed file, and the outer header cannot distinguish them. Extensions
distinguish them by convention only, and only when a filename exists.

**Symptom.** The same bytes present as one member named after the file, or as hundreds of
members, depending on a guess. A caller iterating "the members of this archive" gets one
opaque blob where they expected a directory tree, or vice versa.

**Evidence.** RFC 1952 (gzip: `FNAME` is optional and carries no content type). Recorded at
`dev-docs/history/COMPARISON.md:205` ("decompress a sample to find tar inside gz/bz2/xz/zstd
— disambiguates `.tar.gz` from `.gz`") and `history/SPEC.md:936`.

**Answer today.** Detection decompresses a bounded sample and probes for a tar header
inside it, so the container/stream pair is a derived fact rather than an extension guess;
the format model is a composite of container and stream rather than a flat enum, so
"compressed tar" needs no separate enum member. `history/COMPARISON.md` §4.3;
`openspec/changes/archive/2026-07-04-inner-tar-probe-block-codecs/`.

**Sources.** `history/COMPARISON.md` §2, §4.3, §4.9; `history/SPEC.md` §10.2;
`openspec/changes/archive/2026-07-04-inner-tar-probe-block-codecs/`.

---

### FQ-14 — Identifying bytes are not always near the start of the file

**Problem.** Format signatures sit wherever the format put them. A tar archive's `ustar`
magic is at offset 257, inside the first header block. An ISO 9660 image's `CD001` is at
offset 32 769 — sector 16 — because the first 32 KiB is a reserved system area. A
self-extracting archive prepends an entire executable, so its archive signature is at an
offset nothing declares. A bounded read sized for the common case cannot see these.

**Symptom.** A format is not recognized, or recognition requires buffering far more of an
incoming stream than the caller expected; and a stream shorter than the signature's offset
is simply undecidable — it might be a valid short file of another format, or a truncated one
of this.

**Evidence.** ECMA-119 §6.2.1 (system area, first 16 logical sectors); POSIX ustar header
`magic` field at offset 257. Recorded at `dev-docs/history/SPEC.md:766-782` (magic table
with offsets, and the ISO caveat requiring a 32 774-byte peek) and `history/COMPARISON.md:205`
(SFX executables with embedded archives).

**Answer today.** The detection read is bounded by default and raised to the ISO
requirement only when that format is suspected; the peeked prefix is never discarded, so a
larger peek costs buffering but not data. `SPEC.md` §8.1–8.3.

**Sources.** `history/SPEC.md` §8.1, §8.2, §8.3; `history/ARCHITECTURE.md` §2.5;
`history/COMPARISON.md` §4.9.

---

### FQ-15 — Detection has to read bytes it is not allowed to consume

**Problem.** Deciding what a stream is requires reading its first bytes. If the stream
cannot be rewound — a pipe, a socket, an HTTP body — those bytes are gone once read, and the
consumer that needs them can no longer see them. The decision and the consumption are the
same act on a forward-only source.

**Symptom.** Either detection succeeds and the parser then fails on a stream missing its
first bytes, or detection is skipped and the caller must declare the format. A caller who
calls a standalone "what is this?" helper on a stream they intend to keep reading silently
damages it.

**Evidence.** `dev-docs/history/SPEC.md:764,784-804`; `history/ARCHITECTURE.md:274-299`.

**Answer today.** The opener wraps a non-seekable source in a read-ahead buffer *before*
detection and hands the same wrapper to detection and to the backend, so the peeked prefix
is replayed to the parser; detection itself consumes nothing and a caller invoking it
directly on a raw non-seekable stream must supply the wrapper.
`SPEC.md` §8.3; `ARCHITECTURE.md` §2.5.

**Sources.** `history/SPEC.md` §8.1, §8.3; `history/ARCHITECTURE.md` §2.5;
`history/COMPARISON.md` §4.9.

---

### FQ-16 — A codec chain can include a filter that is not one-in-one-out

**Problem.** Most compressed-member layouts are a linear pipeline: each stage takes one byte
stream and produces one. 7z's BCJ2 breaks that shape — it is a branch-conversion filter with
four input streams that must be consumed in a data-dependent interleaving. A decoder built
around a linear chain has no place to put it, and feeding BCJ2's streams as if they were one
produces plausible-looking output that is wrong.

**Symptom.** Either an archive using this filter cannot be read, or it is read and yields
corrupted bytes that no checksum was consulted to reject.

**Evidence.** 7-Zip's BCJ2 coder definition (four packed streams: main, call, jump, range
coder). Recorded at `dev-docs/history/ARCHITECTURE.md:854-855,961` and
`history/SPEC.md:963-964`.

**Answer today.** The filter is detected and refused with an unsupported-feature error —
explicitly "never garbage output and never a fallback to a third-party reader"
(`ARCHITECTURE.md:854-855`). Listed as irreducible in `open-issues.md` §Irreducible.

**Sources.** `history/ARCHITECTURE.md` §5.6, §7.3; `history/SPEC.md` §10.4;
`open-issues.md` §Irreducible.

---

### FQ-17 — One widely used archive format is proprietary and specified only by its own tool

**Problem.** The RAR compression algorithms are proprietary. There is no published
specification of the decompressor; the format is documented only through reverse engineering
and its reference implementation is the vendor's own extraction tool, whose license forbids
using it to build a competing compressor. Metadata layout has been reverse-engineered
successfully, but the entropy coder has not been independently reimplemented.

**Symptom.** Reading the bytes of a RAR member requires an external program that the user
must install separately and that is not redistributable, so the same code path works or
fails depending on the host. Listing an archive and reading it have different requirements.

**Evidence.** RARLAB `unrar` source license (permits decompression, forbids reverse
engineering to create a RAR compressor). Recorded at
`dev-docs/history/ARCHITECTURE.md:882-885` and `dev-docs/threat-model.md:347-359` (C1: the
decompressor matrix — `unrar` non-free, `unrar-free` handles little of RAR5, `7z`/`bsdtar`
coverage varies by build, `unar` on macOS).

**Answer today.** Metadata is parsed natively so listing needs no external tool, and member
data is delegated to the vendor binary only. Exactly one binary is accepted — no silent
fallback across the tool matrix — decided in ADR
[`0002-native-rar-metadata-unrar-data`](../../dev-docs/decisions/0002-native-rar-metadata-unrar-data.md)
and closed as won't-do in `threat-model.md` C1.

**Sources.** `history/ARCHITECTURE.md` §5.7, §7.4; `history/SPEC.md` §10.5;
`threat-model.md` C1; `open-issues.md` §Irreducible; ADR 0002.

---

### FQ-18 — Formats differ on whether a link member carries a data stream, and on what its digest covers

**Problem.** A format may express a symbolic or hard link as a redirect recorded in the
entry's metadata with no payload, or as an ordinary entry whose payload is the target path.
Both shapes coexist across formats and even across versions of one format. Where there is
no payload but the format still has a mandatory checksum field, the field is populated with
the checksum of zero bytes — a fixed value that describes nothing and distinguishes no link
from another. A reader keying on member *type* rather than on the storage shape gets it
wrong in one direction or the other.

**Symptom.** A link's advertised digest is a constant (the CRC32 of the empty string) that
does not match its target string, while its advertised size is the target's length — two
fields describing different things. Code that trusts stored digests to identify content
without decompressing sees every link in every archive as identical.

**Evidence.** Measured across four formats in
`dev-docs/investigations/rar-corpus-sweep-diagnosis.md:58-89`: ZIP symlink digest
`0x2d212004` = `crc32(target)`, 9 bytes stored; 7z `0x2b4106af` = `crc32(target)`, 45 bytes;
TAR no digest; RAR5 `0x00000000`, **0 bytes stored** (`unrar lt` reports `Packed size: 0`).
RAR3/4 stores the target as member data, so its CRC32 *is* a genuine digest of it.

**Answer today.** The digest is surfaced or withheld according to the storage shape (a
redirect surfaces no digest) rather than the member type, which is what keeps the RAR3/4
genuine digest while dropping the RAR5 constant. Fixed in the RAR reader, with the corpus
assertion restored (`rar-corpus-sweep-diagnosis.md:96-100`).

**Sources.** `investigations/rar-corpus-sweep-diagnosis.md`; `history/ARCHITECTURE.md` §8;
`history/SPEC.md` §10.5.

---

### FQ-19 — A hard link points backwards; a symbolic link may point anywhere

**Problem.** The two link kinds have different resolution rules. A hard link entry in a tar
archive names a target that must already have appeared earlier in the archive — that is what
makes single-pass extraction possible. A symbolic link stores an arbitrary path string,
which may name a later member, a member that is not in the archive at all, an absolute path,
a path outside the archive's tree, or another link forming a cycle. Nothing in the archive
validates it.

**Symptom.** Extraction has to create a link whose target does not exist yet, or does not
exist at all; a chain of links resolves indefinitely or loops; and a link may point at a
destination outside where extraction was asked to write.

**Evidence.** POSIX.1-2017 `pax` `LNKTYPE`/`SYMTYPE` semantics (link name refers to a
previously archived file). Recorded at `dev-docs/history/SPEC.md:168`,
`history/ARCHITECTURE.md:202-207,349,374`, and the adversarial-corpus obligations at
`history/SPEC.md:1140-1141`.

**Answer today.** Link chains are followed with cycle detection over visited members rather
than a depth cap, a missing target is a distinct typed error, and hard links are resolved in
one forward pass because the target precedes the link. A symlink whose target is a later
member is rejected in forward-only mode and deferred to a final check in random access.
`ARCHITECTURE.md` §2.6, §2.7, §8; `SPEC.md` §3.2.

**Sources.** `history/SPEC.md` §3.2, §7, §14.2; `history/ARCHITECTURE.md` §2.3, §2.6, §2.7,
§8; `history/COMPARISON.md` §4.5.

---

### FQ-20 — A run of zero bytes is a legal, complete, empty tar archive, at many different lengths

**Problem.** A tar archive ends with two 512-byte blocks of zeros, and writers pad beyond
that to a record boundary whose size is a documented, caller-settable option. An empty
archive therefore contains no non-zero byte anywhere, and its length varies by writer and
by flag. A file of nothing but zeros, at any block-aligned length, satisfies the format's
definition of a valid archive containing no members — and so does a sparse file, a
partially written file on a zero-filling filesystem, or a never-written disk region. The
format's identifying bytes are inside a *member header*, so an empty archive has no header
to carry them: there is nothing in the content to confirm or deny that this is an archive
at all. There is no predicate over the bytes that separates the cases, because there is no
difference between them.

**Symptom.** A file that is obviously not an archive is accepted and reported as an
archive with zero members. A verification pass over a corrupt or never-written region
reports success. Conversely, any rule that rejects it also rejects the most widely
distributed empty archive in existence.

**Evidence.** Measured in ADR
[`0015-zero-filled-files-are-valid-empty-tars`](../../dev-docs/decisions/0015-zero-filled-files-are-valid-empty-tars.md):
`tar -b 64 -cf e64.tar --files-from /dev/null` produces a file **byte-identical** to
`b"\x00" * 32768` (same sha256, and `tar -tvf` lists it with exit 0), and every documented
blocking factor from `-b 1` to `-b 128` yields another valid all-zero length. Python's
`tarfile` emits 10 240 bytes; Go's `archive/tar` emits 1 024 — and Go's empty archive
(sha256 `5f70bf18…`) **is the Docker/OCI empty-layer blob**, carried by every image with a
metadata-only layer. Surveyed on a development machine: 87 % of real archives are
10 240-aligned against 5 % of Go's test corpus. POSIX.1-2017 `pax` end-of-archive marker;
ustar `magic` at offset 257, inside a header.

**Answer today.** Zero members is never an error under any configuration; the observation
is *reported* instead — an empty-archive advisory, plus one saying the format came from the
extension and content detection would not have confirmed it. A canonical-size heuristic was
considered three times across two review rounds and rejected each time: it is unsound
(`tar -b 64` output is valid and the rule calls it suspect), it can only suppress an
advisory that is *true*, and it is quirk-driven architecture. The adjacent question —
appended junk after the trailer — stays a caller opt-in that deliberately does not fire
here, because zeros to end-of-file is exactly what an empty archive is. ADR 0015;
`2026-08-09-strict-archive-eof-trailing-bytes` (finding F20 / O8).

**Sources.** ADR 0015; `history/SPEC.md` §10.2; `known-issues.md` §tarfile;
`review/archive/2026-08-15-simplicity-consistency/` (F20, O8a/O8b);
`openspec/changes/archive/2026-08-09-strict-archive-eof-trailing-bytes/`.

---

### FQ-21 — Multi-file archives spread one logical archive over several files

**Problem.** Formats support splitting an archive across volumes: numbered parts that must
be concatenated, or addressed as separate "disks" by the index. A ZIP's central directory
records which disk each entry starts on; RAR volumes chain by naming convention. A member's
data may straddle a volume boundary. Opening the file the user names gives access to only
part of the archive, and the remaining parts may be absent.

**Symptom.** An archive opens and lists but reading a member fails partway, or the archive
does not open at all because the part naming the index is a different file than the one
opened. A member larger than a volume cannot be read from any single file.

**Evidence.** ZIP APPNOTE §8 (splitting and spanning). Recorded at `dev-docs/open-issues.md`
P2 ("Multi-volume / split ZIP (`.z01`…`.zip`) … detected and rejected") and
`history/SPEC.md:994` (RAR volume joining).

**Answer today.** RAR and 7z volumes are joined; split ZIP is detected and refused with an
unsupported-feature error advising the caller to rejoin first, with a native streaming ZIP
reader named as the place volume support would land (`open-issues.md` P2, §Longer-term).

**Sources.** `open-issues.md` P2, §Longer-term; `history/SPEC.md` §10.5.

---

### FQ-22 — A format may record file version history as ordinary members

**Problem.** Some formats can store several revisions of the same path in one archive, as
distinct entries distinguished by a version marker rather than by name. To a reader that
does not know the marker, they are duplicate names (FQ-08); to one that does, they are a
history the archive's own tool displays separately from the current contents.

**Symptom.** A listing shows several entries for one path, and extracting them all writes
each over the last, leaving whichever the archive happened to order last.

**Evidence.** RAR file-version records (`unrar` `-ver` switch). Recorded at
`dev-docs/open-issues.md:232` ("RAR5 `-ver` history rows in `members()`") and specified in
`openspec/changes/archive/2026-07-15-rar-file-version-members/`.

**Answer today.** Version rows are surfaced as members rather than hidden, and the behaviour
is documented as a user-facing gotcha (`open-issues.md:232`, closed).

**Sources.** `open-issues.md` §Docs; `openspec/changes/archive/2026-07-15-rar-file-version-members/`.

---

## Security / hostile input

### SEC-01 — A member name can name a destination outside where extraction was asked to write

**Problem.** An archive member's name is an arbitrary string chosen by whoever built the
archive. Joined to a destination directory it can escape it: `../` components walk upward,
a leading `/` or a drive letter makes it absolute, and a platform-specific separator or an
encoded form can slip past a check written for the other spelling. The archive format
imposes no constraint — a name is a name.

**Symptom.** Extracting an untrusted archive writes files outside the directory the user
chose, overwriting whatever the process has permission to overwrite.

**Evidence.** CWE-22; the adversarial-corpus obligations at `dev-docs/history/SPEC.md:1138-1139`
(`../evil`, `../../etc/passwd`, `./../../outside`, `/etc/passwd`,
`C:\Windows\System32\evil.dll`). Layered defence recorded at
`history/ARCHITECTURE.md:683-698`.

**Answer today.** Three independent layers: a string check on the member name before any
I/O, a resolved-path containment check against the destination before writing, and a
re-resolution after link creation. The name check is non-bypassable — it applies even under
the most permissive policy (`SPEC.md:698`).

**Sources.** `history/SPEC.md` §7.1, §14.2; `history/ARCHITECTURE.md` §4.1;
`history/COMPARISON.md` §4.5; `threat-model.md` (published half).

---

### SEC-02 — An archive can rewrite its own destination as it is being extracted

**Problem.** An extraction that writes members in archive order gives the archive control
over the filesystem it is writing into. An early member can create a symbolic link, or a
directory that is a link, and a later member's path then resolves *through* that link to
somewhere else. Every member's name may have been validated as relative and contained, and
the write can still land outside, because the path's meaning changed after the check. The
archive supplies both the link and the file that follows it.

**Symptom.** An extraction of an archive whose every member name looks harmless writes
outside the destination. Each individual check passes; the sequence defeats them.

**Evidence.** CWE-367 (time-of-check/time-of-use); chained-symlink cases in the
adversarial-corpus obligations at `dev-docs/history/SPEC.md:1140`. Layer 3 rationale at
`history/ARCHITECTURE.md:693,697-698`.

**Answer today.** Every link is re-resolved immediately after creation and removed with a
typed escape error if the resolved target leaves the destination, rather than checking only
the stored string before creation. `ARCHITECTURE.md` §2.7, §4.1.

**Sources.** `history/ARCHITECTURE.md` §2.7, §4.1; `history/SPEC.md` §7.1, §14.2.

---

### SEC-03 — Compressed data can expand without bound, and the archive declares the expansion

**Problem.** Compression ratios are unbounded in principle: a few kilobytes of highly
redundant input decompresses to gigabytes, and archives can nest so that each layer
multiplies. The declared uncompressed size in the header is attacker-controlled and need not
match what actually comes out. So neither the input size nor the declared output size bounds
the work an extraction will do.

**Symptom.** Extracting a small file fills the disk or exhausts memory. A process that
sized its buffers or its progress bar from the declared size is wrong by orders of
magnitude, in either direction.

**Evidence.** The `42.zip` family (outer layer ~391:1; nested layers multiply);
`dev-docs/history/ARCHITECTURE.md:814-824` and `SPEC.md:1136,1144` (adversarial corpus:
zip bombs, and "member claims 1 TiB size but archive is 1 KiB").

**Answer today.** A cumulative output-byte cap plus a per-member ratio cap, both enforced as
bytes are written rather than from declared sizes, with an entry-count cap alongside; the
caps live on one limits object with an explicit unlimited value for trusted input.
`ARCHITECTURE.md` §4.2, §5.5.

**Sources.** `history/ARCHITECTURE.md` §4.2, §5.5; `history/SPEC.md` §7.3, §14.2;
`history/COMPARISON.md` §4.5; `threat-model.md`.

---

### SEC-04 — Legitimate files reach compression ratios that look like an attack

**Problem.** A ratio threshold cannot separate hostile from benign input, because benign
input reaches extreme ratios routinely: ten bytes of a repeated character compress to a few
bytes and expand back at whatever ratio the sizes happen to give, and sparse or
zero-padded files compress arbitrarily well. The ratio of a small file is dominated by
per-member overhead and carries no information about intent.

**Symptom.** A guard tuned to catch bombs rejects ordinary archives — a 10-byte file
expanding to 15 KiB presents a 1500:1 ratio — so the guard is either loose enough to be
useless or tight enough to break real workloads.

**Evidence.** The false-positive obligation is pinned as a required adversarial-corpus case
at `dev-docs/history/SPEC.md:1137` ("a tiny but highly-compressible legitimate file (e.g.
10 bytes → 15 KiB, 1500:1) — verify it extracts **without** error"). Typical ratios for
comparison at `history/ARCHITECTURE.md:820`.

**Answer today.** The ratio check is armed only after a member's own output crosses an
absolute activation floor (5 MiB by default), so ratios on small members are never
evaluated; the cumulative byte cap covers what the ratio check declines to judge.
`ARCHITECTURE.md:740-747`, `SPEC.md:743`.

**Sources.** `history/SPEC.md` §7.3, §14.2; `history/ARCHITECTURE.md` §4.2, §5.5.

---

### SEC-05 — Enumerating an archive's metadata is itself unbounded work

**Problem.** The cost of *listing* an archive is attacker-controlled independently of its
data. A small file can declare an enormous number of entries, or entries with enormous
names, comments and extended attributes; an indexed format's header can claim a member count
that the reader will allocate for before reading any member. None of this requires
decompressing anything, so limits placed on extraction do not apply.

**Symptom.** Merely opening or listing an untrusted archive exhausts memory, with no member
ever read and no byte ever written to disk.

**Evidence.** `dev-docs/threat-model.md:18-32` (O1: `max_members`, `max_metadata_bytes`;
"indexed formats (7z/RAR) may still allocate up to those parser ceilings during
`open_archive()` before spine listing caps apply"). Specified in
`openspec/changes/archive/2026-07-12-listing-resource-limits/`.

**Answer today.** Caps on member count and on retained metadata bytes are enforced when a
member list is materialized, with format-local parser bounds as defence in depth; the
forward-only unbounded iteration path is left unguarded deliberately as the O(1) escape
hatch. Residual: indexed-format parser ceilings apply before the spine caps
(`threat-model.md` O1).

**Sources.** `threat-model.md` O1; `openspec/changes/archive/2026-07-12-listing-resource-limits/`;
`review/archive/2026-07-12-codebase-deep-review/`.

---

### SEC-06 — Two names that differ in the archive are one file on the filesystem

**Problem.** Archive formats compare names as byte strings; filesystems often do not. On a
default Windows or macOS volume, `README` and `readme` are the same file, and so are the
NFC and NFD spellings of `café` — two distinct, legal archive members that cannot both
exist on disk. The archive is not malformed and the filesystem is not wrong; they simply
disagree about identity.

**Symptom.** Extracting an archive produces fewer files than it has members, silently, on
some machines and not others. Under a fail-on-existing policy it instead reports a confusing
"already exists" for a file the extraction itself just wrote.

**Evidence.** `dev-docs/threat-model.md:34-58` (O2, with the pre-fix behaviour recorded);
Unicode Standard Annex #15 (normalization forms). Decided in ADR
[`0013-cross-platform-name-safety-policies`](../../dev-docs/decisions/0013-cross-platform-name-safety-policies.md).

**Answer today.** A casefolded, NFC-normalized key is tracked per written path and a
collision is treated as a first-class event on **every** platform, not only on the ones
where it manifests; the overwrite policy is then applied deliberately, a rename option
exists, and the outcome is recorded per member. Only content-bearing members are tracked:
directory entries recur structurally (auto-created parents, re-listed directory members) and
merge by design, so folding them in would break legitimate archives. Residual: a
file-versus-directory collision differing only by case stays OS-dependent, deferred rather
than risk regressing normal directory handling (ADR 0013, `threat-model.md` O2).

**Sources.** `threat-model.md` O2; ADR 0013;
`openspec/changes/archive/2026-07-16-cross-platform-name-safety/`.

---

### SEC-07 — A platform can silently rewrite the name it was asked to write

**Problem.** Win32 does not store every name it accepts. A trailing dot or space is trimmed
away, so `stuff_etc.` becomes `stuff_etc`. A name matching a reserved device name — `CON`,
`NUL`, `COM1` — refers to a device, not a file. A `:` in a name selects an NTFS alternate
data stream, producing a hidden stream on another file instead of a file. These are all
legitimate names on macOS and Linux, so an archive built there carries them innocently.

**Symptom.** The file that appears on disk has a different name than the one reported, or
two members collapse onto one name, or the write goes to a device or an invisible stream. No
error is raised, because the platform considers the operation successful. And because these
names appear in *path segments*, refusing one is not a per-member decision: a rejected
directory segment takes every member beneath it — reported as one failure if the run aborts,
or silently dropped along with its whole subtree if the run continues.

**Evidence.** `dev-docs/threat-model.md:60-84` (O3, O4). The subtree consequence is a
real-world report recorded in ADR 0013 decision 4: a macOS zip containing a `stuff_etc.`
folder failed an entire extraction, and under continue-on-error would instead "*silently
drop the folder and every file under it*, since they share the segment". Decided in ADR
[`0013-cross-platform-name-safety-policies`](../../dev-docs/decisions/0013-cross-platform-name-safety-policies.md).

**Answer today.** Reserved device names and `:` are rejected under the two safe policies on
every platform; a trailing dot or space is stripped to its portable spelling under the
strictest policy, deterministically and collision-tracked, with the pre-rewrite name
recorded per member so the archive name, a caller's rename, and the on-disk spelling stay
three distinguishable strings. ADR 0013, `threat-model.md` O3.

**Sources.** `threat-model.md` O3, O4; ADR 0013;
`openspec/changes/archive/2026-07-16-cross-platform-name-safety/`.

---

### SEC-08 — A name can be representable as bytes and still unwritable

**Problem.** A member name that decodes to a valid string, and that the operating system's
encoding layer will accept, can still be refused by the filesystem at the moment of writing:
a name carrying bytes that are not valid UTF-8 fails with an encoding error on a volume that
requires UTF-8, while the same name works on a volume that does not care. Separately, a name
containing a lone surrogate cannot be encoded to filesystem bytes at all. Representability is
a property of the target volume, not of the name.

**Symptom.** A per-member write failure that depends on which filesystem the destination is
on, surfacing as a low-level operating-system error partway through an otherwise successful
extraction.

**Evidence.** `dev-docs/threat-model.md:162-181` (O7, naming `caf\udce9.txt` → `EILSEQ` on
APFS); pinning test `tests/test_extraction.py:253`
(`test_unrepresentable_name_oserror_is_translated`). PEP 383 (surrogateescape).

**Answer today.** Non-UTF-8 bytes are percent-escaped to a deterministic, reversible
portable spelling under the two safe policies on every platform, and collision-tracked;
names that cannot be encoded at all are rejected; a write-time encoding error is translated
to a typed extraction error naming the member. ADR 0013, `threat-model.md` O7.

**Sources.** `threat-model.md` O7; ADR 0013.

---

### SEC-09 — Member names are attacker-controlled text that ends up on a terminal

**Problem.** Whoever builds an archive chooses its member names, and those names are what
every tool prints when reporting on the archive. A name may contain ANSI control sequences,
carriage returns, or line separators: `README\x1b[2K\rSUCCESS.txt` printed raw erases the
line it is being reported on and writes the attacker's text in its place. The operator sees
a report the archive authored. This is not specific to any one message — every path, error
string, log record and progress line derived from a member name has the same property, and
the failure paths report the name too, so refusing to write it does not remove the exposure.

**Symptom.** Listing or extracting a hostile archive produces terminal output that
misrepresents what happened — a blocked member reported as succeeded, a path that reads as a
different path — with no indication that anything was rewritten.

**Evidence.** `dev-docs/threat-model.md:240-343` (O9), including a reproduction on `main`
showing a library log record emitting the unescaped name one line before the fixed print
site, and the note that Windows refuses control bytes in filenames (`WinError 123`) yet the
failure path is itself a reporting path. GNU `ls`/`tar` quote output for the same reason.
Pinning tests: `tests/test_escaping.py`, `tests/test_cli.py::test_extract_escapes_*`.

**Answer today.** Archive-derived text is made inert where it *becomes* a message rather
than where a message is displayed: error and diagnostic messages escape at construction, so
every route to a terminal is covered including an uncaught traceback's final line.
`escape-cli-log-records` (`threat-model.md` O9). Two rules are guarded by static tests
because either failure is invisible except against a hostile archive: escape exactly once
(52 sites interpolating `{name!r}` were converted), and keep `%r` at logger call sites since
the handler no longer escapes.

**Sources.** `threat-model.md` O9; `openspec/changes/archive/2026-08-15-escape-cli-log-records/`;
`review/archive/2026-08-15-simplicity-consistency/`.

---

### SEC-10 — Escaping text for display is itself easy to get wrong

**Problem.** Rendering arbitrary text inertly requires deciding which code points to escape
and how. A hand-written escape table gets edge cases wrong in ways that are invisible on
ordinary input: an astral code point emitted as a five-hex-digit `\uXXXXX` escape reads back
as a different character; the range of code points that a filesystem-byte round-trip
actually produces is narrower than the full surrogate block, so escaping the wrong range
turns valid characters into bytes they never came from; and distinct inputs can render
identically, so a claim that the rendering is reversible is false.

**Symptom.** An escaped name is not the name — it reads back as different text — and two
different members render as the same string. Nothing detects this, because the affected code
points do not occur in test data drawn from real archives.

**Evidence.** `dev-docs/threat-model.md:332-339`: three bugs found by review in a
hand-rolled table — 955 086 code points affected by the five-hex-digit form; the
surrogateescape range stated as `U+DC00`–`U+DFFF` rather than the `U+DC80`–`U+DCFF` that
PEP 383 actually produces, reversing 768 code points; and `U+009B` colliding with an escaped
byte `0x9B`. Pinning tests in `tests/test_escaping.py`.

**Answer today.** Rendering delegates to the language's own `repr`, whose escape set was
verified across the whole code space to be exactly the non-printable characters, and the
guarantee is restated as inertness rather than unique recoverability.
`threat-model.md` O9 §Escaping correctness.

**Sources.** `threat-model.md` O9; `review/archive/2026-08-15-simplicity-consistency/`.

---

### SEC-11 — An archive can be a container for itself

**Problem.** Archives can contain archives, and nothing bounds the nesting. A file can be
constructed to contain itself — a quine — so any process that opens nested archives
recursively never terminates. The founding use case for a library like this (indexing
backups) does exactly that recursion, so the hazard is on the main path, not an exotic one.

**Symptom.** A tool that descends into nested archives loops forever, or exhausts memory, on
an archive that opens and lists perfectly at every individual level.

**Evidence.** `dev-docs/threat-model.md:154-160` (O6, naming `droste.zip`).

**Answer today.** Recursion is caller-driven, so nothing loops unless the caller loops; the
stance is documented with a bounded-recursion recipe as the remaining work
(`threat-model.md` O6, `open-issues.md` §Longer-term). **Partly unresolved** — the explicit
documented stance is recorded as still owed.

**Sources.** `threat-model.md` O6; `open-issues.md` §Longer-term, §Irreducible.

---

### SEC-12 — A corrupted header can make a member name the destination itself

**Problem.** A member whose name normalizes to the current directory — `.` — is not a
traversal: it stays inside the destination. But if such a member is typed as a *file* rather
than a directory, writing it means writing *through* the destination path, replacing the
directory being extracted into with a regular file. A single bit flip in a header produces
this, so it does not require a deliberate attacker.

**Symptom.** An extraction replaces its own output directory with a file; subsequent members
then fail, or the caller finds a file where it expected a tree.

**Evidence.** `dev-docs/threat-model.md:144-152`, found by the corpus mutation harness
(`bitflip@107:0x10` on `adversarial-tar.tar.gz`). Pinning tests
`tests/test_extraction.py:154` (`test_check_universal_rejects_root_named_file`) and
`test_extract_error_when_dest_is_a_file`.

**Answer today.** The universal name check rejects a non-directory member naming the
extraction root, and the parametrized fuzz loop asserts the destination is still a directory
after any successful extraction (`threat-model.md` O5).

**Sources.** `threat-model.md` O5; `openspec/changes/archive/2026-07-12-atheris-fuzz-harness/`.

---

### SEC-13 — A format with no password verifier cannot tell a wrong password from corrupt data

**Problem.** Some encrypted formats store a value that lets a reader check a candidate
password cheaply before decrypting anything — RAR5's password-check field, WinZip AES's
verifier bytes. 7z stores none. Decryption with a wrong key therefore succeeds at the
cipher level and yields garbage, and the only signal is whether the garbage happens to fail
the next stage: a decompressor on random input, or a header parser on random bytes. Garbage
sometimes parses. In particular, a header parser that stops at the first structural
terminator will accept random output whose first byte happens to be that terminator, and
report a well-formed archive containing nothing.

**Symptom.** Opening a header-encrypted archive with the wrong password returns an archive
with zero members and no error. A verification or indexing tool concludes "empty archive"
where the truth is "wrong password", and reports success. The rate depends on the random
salt chosen when the archive was written, so it is per-archive, not per-attempt, and it
looks like a flaky test rather than a systematic hole.

**Evidence.** `dev-docs/threat-model.md:183-238` (O8). Measured: **~0.3 % of salts slip both
checks** (2/300, 3/1110 across runs); of 8 observed slips, 7 were a leading `END` property
id and 1 was `HEADER`+`END`, matching the ≈1/256 chance that the first garbage byte is
`0x00`. Also the root cause of a long-standing intermittent test failure. Pinning test
`tests/test_sevenzip_reader.py:460`
(`test_header_encrypted_empty_decoded_header_rejected`).

**Answer today.** A decoded encrypted header that parses to zero file records is treated as
a rejected password, since legitimate writers never encrypt an empty header. The
folder-digest check remains the deterministic first line where the writer stored one.
Residual: garbage parsing into a *non-empty* plausible header is inherent to a format with
no check value (`threat-model.md` O8).

**Sources.** `threat-model.md` O8; `open-issues.md` §Irreducible;
`review/archive/2026-07-16-crypto/`.

---

### SEC-14 — A cheap password check has a false-accept rate

**Problem.** Where a format *does* provide a password verifier, it is often narrow — a
single byte — because it was designed to reject most wrong passwords quickly, not to be
authoritative. A one-byte check accepts a wrong password roughly once in 256 attempts. On an
archive whose members may use different passwords, confirming which password belongs to which
member therefore cannot rely on the verifier alone, and the fallback is to decrypt and check
the member's real checksum — which for a stored (uncompressed) member means reading it.

**Symptom.** A wrong password appears to be accepted, and the error surfaces later as
corrupt data. Determining the right password for each member of a multi-password archive
costs a full read of members rather than a header check.

**Evidence.** `dev-docs/open-issues.md` §Irreducible ("ZipCrypto multi-password + STORED
confirmation cost (~1/256 false open → CRC scan)"); ZIP APPNOTE §7 (traditional PKWARE
encryption, 12-byte header with a one-byte password check). Specified in
`openspec/changes/archive/2026-07-11-zip-multipassword-disambiguation/`.

**Answer today.** Password disambiguation falls back to a checksum scan when the cheap check
is inconclusive, with the cost documented rather than hidden
(`2026-07-11-zip-multipassword-disambiguation`, `open-issues.md` §Irreducible).

**Sources.** `open-issues.md` §Irreducible;
`openspec/changes/archive/2026-07-11-zip-multipassword-disambiguation/`;
`review/archive/2026-07-16-crypto/`.

---

### SEC-15 — An encrypted member's stored digest may not be a digest of its plaintext

**Problem.** A format that encrypts member data has to decide what its integrity field
covers. If it stored a plain checksum of the plaintext, that value would be an oracle: it
lets an attacker confirm a guess about the content without the key. Formats therefore
transform the field — keying it, tweaking it with the derived key — so the stored value is a
message authentication code rather than a checksum. It looks exactly like a checksum field
and is not comparable to one.

**Symptom.** A tool that reads stored digests to identify content without decompressing gets
values that match nothing for encrypted members. A verification that compares the stored
value against a computed checksum of the decrypted bytes reports corruption on a perfectly
good archive.

**Evidence.** Measured in `dev-docs/investigations/rar-corpus-sweep-diagnosis.md:90-96`:
with the RAR5 tweaked-encryption flag set, the stored CRC32 and BLAKE2sp are key-tweaked
MACs, so the corpus assertion demanding a plaintext digest "demanded a digest the format
does not expose". Also `dev-docs/open-issues.md:228` (RAR5 HASHMAC / tweaked digests) and
`:227` (7z CRC-less encrypted store).

**Answer today.** Tweaked values are kept out of the member's advertised digests and
verified by applying the same forward transform once a password is available; a 7z
encrypted store with no digest raises a diagnostic rather than silently verifying nothing.
`rar-corpus-sweep-diagnosis.md`; crypto review findings closed in
`review/archive/2026-07-16-crypto/`.

**Sources.** `investigations/rar-corpus-sweep-diagnosis.md`; `open-issues.md` §Docs;
`review/archive/2026-07-16-crypto/`;
`openspec/changes/archive/2026-07-14-rar-blake2sp-verification/`.

---

### SEC-16 — A password passed on a command line is visible to every process on the host

**Problem.** Delegating work to an external tool means passing it the password. Command-line
arguments are readable by any process on the machine through the process table, so a password
in an argument is disclosed for the lifetime of the subprocess. The tool's other channels —
standard input, an environment variable, a file — have different and not always documented
semantics.

**Symptom.** Reading an encrypted archive discloses its password to unrelated local
processes, with nothing in the calling code suggesting that happened.

**Evidence.** `dev-docs/open-issues.md:230` ("RAR password via stdin (`-p` + stdin)"), closed
in the crypto round (`review/archive/2026-07-16-crypto/`, PR #127).

**Answer today.** The password is written to the tool's standard input rather than placed in
an argument (`open-issues.md:230`, closed).

**Sources.** `open-issues.md` §Docs; `review/archive/2026-07-16-crypto/`.

---

### SEC-17 — A key-derivation cost parameter in the archive is attacker-controlled

**Problem.** Encrypted formats store the work factor used to derive the key from the
password — an iteration count, or an exponent for one. The reader must use the stored value
to derive the same key, so the archive dictates how much computation the reader performs
before it can even attempt a decryption. A crafted archive can name a work factor large
enough to hang the reader, and the format's own maximum may be far above anything a real
writer uses.

**Symptom.** Opening a small encrypted archive consumes unbounded CPU before any data is
read, and cannot be interrupted meaningfully because the work is one key derivation.

**Evidence.** `dev-docs/open-issues.md:229` ("7z `NumCyclesPower` ≤24 / `0x3F`"), closed in
the crypto round (`review/archive/2026-07-16-crypto/`, PR #127).

**Answer today.** The stored work factor is clamped to a sane ceiling, rejecting values
above it rather than honouring them (`open-issues.md:229`, closed).

**Sources.** `open-issues.md` §Docs; `review/archive/2026-07-16-crypto/`.

---

### SEC-18 — A native decompressor can loop forever on crafted input, and no host-language guard can stop it

**Problem.** Third-party native decoders can be driven into a busy loop by crafted input. A loop
inside a native thread cannot be converted into a host-language exception, cannot be interrupted
by a timer signal, and cannot be cleanly stopped by a test harness's timeout, because there is no
host-language frame to interrupt. Fuzzing that code from within the same process is therefore
impossible: the harness dies with it.

**Symptom.** Processing a hostile archive never returns and cannot be cancelled; the only remedy
available to the calling program is to have run it in a separate process it can kill.

**Evidence.** `dev-docs/threat-model.md:123-133`: the optional accelerators "can **busy-loop on
crafted input** — a hang no Python-level translator can convert into an `ArchiveyError`, and one
that SIGALRM/pytest-timeout cannot cleanly interrupt (the loop is in a C++ thread)". Found by the
corpus mutation harness. The ISO library's infinite tree walk (UL-02) is the same shape in pure
Python and *was* fixable in-process.

**Answer today.** The mutation and coverage-guided fuzz harnesses run with accelerators off, and
the accelerators are stated to be an opt-in performance path rather than part of the defended
parsing surface for untrusted input; callers under a hard latency budget are told to disable them
or enforce their own timeout. Fuzzing that native code is deferred to a resource-limited
subprocess sandbox. **Unresolved:** the sandbox is not built (`threat-model.md` O5,
`open-issues.md` §Longer-term).

**Sources.** `threat-model.md` O5; `open-issues.md` §Longer-term, §Irreducible;
`openspec/changes/archive/2026-07-12-atheris-fuzz-harness/`;
`openspec/changes/archive/2026-07-15-atheris-harness-depth/`.

---

---

### SEC-19 — A filename can display in a different order than it is stored

**Problem.** Unicode carries bidirectional formatting controls whose purpose is to reorder
the text that follows them. A name containing a right-to-left override displays, in every
listing a person reads, with its tail reversed: `evil\u202Egnp.exe` renders as
`evil<reversed>exe.png`, so an executable presents as an image. Nothing is escaped, nothing
is unwritable, and the bytes on disk are exactly the bytes in the archive — what is falsified
is the name a human reads back. The controls that do this are a specific set; three other
directional characters reorder nothing at all and occur in ordinary Arabic and Hebrew
filenames, so a blanket ban on "bidi characters" refuses legitimate names, and
right-to-left *script* carries no control character at all.

**Symptom.** A member's name reads as a harmless file type in every tool that lists it,
while being something else. A user who inspects an archive before extracting it is
inspecting a name the archive authored.

**Evidence.** Trojan Source, CVE-2021-42574. The set split is recorded in ADR
[`0017-bidi-override-rejection-is-policy-keyed`](../../dev-docs/decisions/0017-bidi-override-rejection-is-policy-keyed.md):
the **overrides and isolates** (U+202A–U+202E, U+2066–U+2069) reorder surrounding text and
are what the disguise needs; the **directional marks** (U+061C, U+200E, U+200F) reorder
nothing and appear in legitimate names. The ecosystem response is the same shape — `rustc`'s
`text_direction_codepoint_in_literal` and GCC's `-Wbidi-chars` are deny-by-default
diagnostics over the same ranges, not unconditional refusals.

**Answer today.** Rejected under the two safe extraction policies and extracted faithfully
under the explicitly-trusted one, because this is a presentation property rather than an
unsafe write: the member lands inside the destination under exactly its stored bytes. The
check runs on the *final* name after any caller rename, so renaming a deceptive name — the
natural remedy — is reachable. Listing and reading are unaffected and always were, with the
whole advisory set (overrides *and* marks) reported at listing time. ADR 0017 (review finding
F10 / O7), which moved the check off the non-bypassable layer after establishing that placing
it there left the member unextractable by any route — the same axis-coupling ADR 0013 had
already rejected. Guarded by a test asserting the check is absent from the universal layer.

**Sources.** ADR 0017; ADR 0013; `review/archive/2026-08-15-simplicity-consistency/` (F10/O7);
`openspec/changes/archive/2026-08-09-reject-bidi-overrides-in-safe-extraction/`;
`openspec/changes/archive/2026-07-14-adversarial-string-corpus-contract/`.

---

## Platform & filesystem

### PLAT-01 — A byte source's own report of whether it can be repositioned is unreliable

**Problem.** Deciding whether a stream supports random access by asking it is not sound, in
several independent ways. A buffered wrapper answers for itself, not for the raw stream
underneath, and reports that it can seek over a source that cannot. A memory-mapped region
can be repositioned but, before a recent language version, offered no method to say so. Most
seriously, an operating-system pipe on Windows *lies*: its read end reports that it is
seekable, a seek call returns a plausible new offset, and the stream never actually moves —
a subsequent read returns bytes from the real, unmoved position, and a seek to the end
returns a fabricated size. The equivalent POSIX call fails cleanly, so the same code is
correct on one platform and silently wrong on the other. Probing by actually seeking is not
an answer either: it mutates a stream the caller may be mid-read on, and as the pipe case
shows, a successful probe proves nothing.

**Symptom.** Random-access reads return the wrong bytes, and sizes are fabricated, on one
platform only, with no error anywhere. Or a source that could have been read efficiently is
treated as forward-only.

**Evidence.** `dev-docs/history/ARCHITECTURE.md:301-323`, with the Windows behaviour
confirmed on CI and pinned by `tests/test_stream_inputs.py:585`
(`test_windows_pipe_seek_characterization`). POSIX `lseek` on a FIFO fails with `ESPIPE`.
The resolution is a file-type check via `fstat`
(`src/archivey/internal/streams/streamtools/binaryio.py:96-107`).

**Answer today.** A single predicate answers seekability: the stream's own claim is
confirmed against the underlying file type, and a FIFO or character device overrides the
claim to false. The check runs only when the claim is `True` and only for objects with a
real file descriptor, so in-memory and network streams are untouched, and no seek probe is
ever performed. `ARCHITECTURE.md` §2.5.

**Sources.** `history/ARCHITECTURE.md` §2.5; `history/SPEC.md` §8.3;
`openspec/changes/archive/2026-08-09-decouple-member-metadata-from-declared-seekability/`.

---

### PLAT-02 — Resolving a path that loops raises an error the caller was not looking for

**Problem.** Asking the operating system to resolve a path that passes through a cycle of
symbolic links — `a` pointing at `b` pointing at `a` — fails, because it must: there is no
resolution. The failure surfaces as a low-level operating-system error on some platforms and
as a language-level runtime error on others, from a call whose documented job is "tell me
what this path really is". Code that treats resolution as infallible aborts.

**Symptom.** An archive containing a pair of mutually-referring links crashes the extraction
with an unhandled error rather than rejecting the member, taking down members that had
nothing to do with it.

**Evidence.** `dev-docs/history/ARCHITECTURE.md:365-372` (`ELOOP` as `OSError`, "or
`RuntimeError` on some platforms/versions"); required adversarial-corpus case at
`history/SPEC.md:1141` ("cyclic symlinks (`a → b`, `b → a`) — verify extraction fails safe …
no uncaught `OSError`/crash").

**Answer today.** An unresolvable link is treated as an escape: the resolution is guarded,
and both error kinds map to the same typed rejection, so the member is refused rather than
the run aborting. `ARCHITECTURE.md` §2.7.

**Sources.** `history/ARCHITECTURE.md` §2.7; `history/SPEC.md` §14.2.

---

### PLAT-03 — Hard links cannot always be created where the file is

**Problem.** A hard link and its target must live on the same filesystem; a destination that
spans a mount point, or a filesystem that does not implement links at all, refuses the
operation. Some filesystems refuse symbolic links too. The archive says "these two names are
one file", and the destination cannot express that.

**Symptom.** Extraction fails partway on an archive that is perfectly valid, on some
destinations and not others; or the two names end up as independent copies, which is a
different filesystem than the archive described.

**Evidence.** `dev-docs/history/SPEC.md:932` ("If hardlink creation fails (cross-device),
fall back to copying"); `history/COMPARISON.md:180` (cross-device fallback to copy);
`dev-docs/open-issues.md:236` ("Symlink-unsupported FS ≠ `tarfile` copy-through").

**Answer today.** Link creation falls back to copying the content, and the divergence from
the stdlib's own behaviour on symlink-hostile filesystems is documented as a user-facing
gotcha rather than silently matched (`open-issues.md:236`).

**Sources.** `history/SPEC.md` §10.2; `history/COMPARISON.md` §4.5; `open-issues.md` §Docs.

---

### PLAT-04 — Extraction writes into a directory other processes can change underneath it

**Problem.** The destination of an extraction is ordinary shared filesystem state. Between
the moment a path is checked and the moment it is written, another process can replace a
directory with a link, create the file first, or delete a parent. No sequence of checks
performed by the extractor closes this, because the checks and the writes are separate
system calls.

**Symptom.** An extraction that validated every path writes somewhere else, or fails
inexplicably, when something else is touching the destination concurrently.

**Evidence.** `dev-docs/open-issues.md:270` — recorded explicitly as out of scope:
"Concurrent hostile modification of the destination during extract".

**Answer today.** **Unresolved by design.** Declared out of scope in `open-issues.md`
§Irreducible; the in-archive variant of the same shape is defended (SEC-02).

**Sources.** `open-issues.md` §Irreducible.

---

### PLAT-05 — Extended file metadata has no portable representation

**Problem.** Beyond mode, owner and timestamps, filesystems carry metadata that archive
formats represent partially or not at all: extended attributes, access-control lists, macOS
resource forks, NTFS alternate data streams. A format may transport some of it in an
extension mechanism, and the destination filesystem may or may not have a place to put it.
Fidelity is therefore bounded by both ends independently.

**Symptom.** A round trip through an archive loses metadata silently — the files are there
and the attributes are not — and which attributes survive depends on the source format and
the destination filesystem.

**Evidence.** `dev-docs/threat-model.md:369-375` (C3: PAX extended attributes survive only
inside a format-specific extras mapping; ACLs, resource forks and NTFS alternate data
streams are untouched).

**Answer today.** **Unresolved / deliberately deferred.** No metadata-fidelity claim is made
on extraction; read-side promotion to first-class fields is noted as cheap and additive, and
true fidelity is deferred to when writing lands. `threat-model.md` C3; `open-issues.md`
§Irreducible.

**Sources.** `threat-model.md` C3; `open-issues.md` §Irreducible; `IDEAS.md`.

---

## Upstream library defect

### UL-01 — A standard tar reader treats a corrupt header as a clean end of archive

**Problem.** The language's own tar reader validates header blocks, but only re-raises the
resulting error when the bad header is the *first* one in the file. A corrupt header
anywhere later is swallowed and iteration simply stops. Since a tar archive legitimately
ends when the headers stop, a reader built on it cannot distinguish "the archive ended here"
from "the archive is damaged here" — the library has already discarded the difference.

**Symptom.** A damaged archive lists successfully with fewer members than it contains. There
is no error, no warning, and no way for the caller to know that the listing is a prefix of
the truth. A verification tool reports the archive as sound.

**Evidence.** `dev-docs/known-issues.md:7-33`, confirmed against both a corrupted-checksum
fixture and a corrupted compressed archive whose garbage decode parses as an invalid header
(deep review finding W1). The behaviour is in `tarfile.TarFile.next()`: the invalid-header
error is re-raised only at offset 0.

**Answer today.** An end-of-archive check backstops the library: when the stopped scan lands
on a rejected non-null header block, a corruption error is raised by default, while an
archive that merely ended without the two zero blocks is reported as a warning that a
stricter setting escalates. In random-access mode a probe inspects the final header attempt
so the case is caught even when the bad header is the archive's last block, without seeking
back. Decided in `2026-07-19-decide-strict-archive-eof-default` (Option F). **Residual:** in
forward-only mode the library hides its header reads, so a rejected *final* header is still
misclassified as a missing trailer; a native header walker is the named structural fix
(`open-issues.md` P3).

**Sources.** `known-issues.md` §tarfile; `open-issues.md` P3, §Longer-term;
`review/archive/2026-07-12-codebase-deep-review/` (W1);
`openspec/changes/archive/2026-07-19-decide-strict-archive-eof-default/`; ADR 0015.

---

### UL-02 — An ISO reader loops forever on directory records that form a cycle

**Problem.** A widely used ISO 9660 library walks the image's directory tree with a plain
queue and no record of what it has already visited. A valid tree never revisits an extent,
so this is correct on valid input — but a corrupt or crafted image whose directory records
form a back-edge, a child extent pointing at an ancestor, makes the walk enqueue forever. It
affects every namespace the library walks. A single bit flip is enough to produce it.

**Symptom.** Opening a damaged disc image never returns and consumes memory until the
process dies. No exception is raised, so no error handling in the calling code runs — and a
timeout cannot distinguish this from a slow image.

**Evidence.** `dev-docs/known-issues.md:35-50` and `dev-docs/threat-model.md:135-142`. Found
by the corpus mutation harness: a Joliet case at `bitflip@71746:0x01` on `basic-iso`, with
the same one-bit corruption in a subdirectory's extent reproducing on plain-only and
Rock-Ridge-only images. Pinning test `tests/test_iso.py:362`
(`test_pycdlib_directory_cycle_does_not_hang`), parametrized over all three namespaces.

**Answer today.** A guard is installed into the third-party library's own namespace at
import: a queue subclass drops a directory record whose extent was already scheduled. It is
confined to that library rather than swapping a global, installed once, and is a strict
superset of the library's behaviour on valid trees. The trade — a program that also uses that
library directly in the same process sees the guarded queue — is documented deliberately
(`known-issues.md`, `open-issues.md` §Irreducible).

**Sources.** `known-issues.md` §pycdlib; `threat-model.md` O5; `open-issues.md` §Irreducible;
`openspec/changes/archive/2026-07-12-atheris-fuzz-harness/`.

---

### UL-03 — A native decompressor's worker threads outlive what the language can see

**Problem.** A high-performance decompressor implemented in C++ spawns its own worker
threads, invisible to the host language's threading machinery. It installs a guard that
terminates the process if a worker is still running when the interpreter begins finalizing.
Crucially, joining its threads is *not* enough to stop them — only closing the object is —
so an object that is garbage-collected, or merely dropped and reclaimed at interpreter exit
without being explicitly closed, aborts the process. This happens *after* all the work has
completed successfully.

**Symptom.** A program that decompressed everything correctly dies with an abort signal at
exit, printing a message about finalization from a running thread, or a heap error. The
crash has no relationship to the code executing at the time.

**Evidence.** `dev-docs/known-issues.md:52-96` (Bug 1). Measured by
`tests/test_accelerator_shutdown.py` across cleanup strategies, each in its own subprocess:
explicitly closed → clean; reclaimed by the cyclic collector without closing → abort;
finalized at interpreter shutdown without closing → abort. The library's own message says to
close all objects.

**Answer today.** Every such object is wrapped, and the wrapper installs a finalizer that
*closes* the raw object exactly once — when collected cyclically or otherwise, or at
interpreter exit — holding a strong reference so the close always runs first. The test
doubles as a canary: if a future release stops aborting on a raw unclosed object, the
raw-case assertions fail, signalling that the wrapper could be simplified.
`known-issues.md` §The canary.

**Sources.** `known-issues.md` §Random-access accelerators (Bug 1);
`investigations/parallel-reader.md` §4; ADR 0008.

---

### UL-04 — Two independent extensions bundling the same C++ core corrupt each other's heap

**Problem.** Two separately distributed native extensions by the same author statically bundle
a large overlapping C++ core. On macOS the dynamic loader coalesces their duplicate weak C++
symbols across the two shared libraries, so one module's allocator can free the other
module's objects. Importing both is harmless; *using* both in one process corrupts the heap.
Neither library documents this as a constraint, and the author's own guidance is to depend on
only one of them.

**Symptom.** A process that decompresses through both libraries aborts with a memory
allocator error about freeing an unallocated pointer, on macOS, essentially always — while
using either library alone, even with both imported, never fails. The crash site is
unrelated to either library.

**Evidence.** `dev-docs/known-issues.md:98-129` (Bug 2). Isolated by
`scripts/dual_accelerator_repro.py` with no archivey and no pytest: ~100 % crash rate on
macOS with both, never with one. The upstream author's guidance is quoted verbatim from
`mxmlnkn/librapidarchive` ("if you need to use both, depend on rapidgzip for now").

**Answer today.** Exactly one accelerator library is used, for both codecs, because that
library bundles the other's specialized decoder; the standalone package is never imported.
Decided in ADR
[`0008-single-accelerator-rapidgzip`](../../dev-docs/decisions/0008-single-accelerator-rapidgzip.md);
guarded by `tests/test_accelerator_shutdown.py:203`
(`test_archivey_uses_single_accelerator_library`), which asserts in a subprocess that the
standalone package is never imported.

**Sources.** `known-issues.md` §Random-access accelerators (Bug 2); `library-analysis.md`
§bzip2, §Seekable zstd; ADR 0008.

---

### UL-05 — A native decompressor aborts the whole process when its Python callback raises

**Problem.** A native decompressor reading through a host-language file object calls back
into that object for bytes. If the callback raises — because the underlying stream was
closed, for instance — the C++ layer converts the failure into a C++ exception thrown across
a boundary that terminates on any exception, and the process aborts. This fires on read, on
close, and on garbage-collection-time finalization alike, so no host-language exception
handler anywhere can contain it: there is no frame to catch in.

**Symptom.** Closing a stream while decompression is still using it kills the process
outright, with no traceback and no chance for error translation. The equivalent
standard-library codec raises an ordinary catchable exception.

**Evidence.** `dev-docs/known-issues.md:131-154` (Bug 3), present in the current and floor
version; the C++ layer throws `std::invalid_argument` ("Cannot convert nullptr Python object
to the requested result type") through a `terminate()` boundary. Also
`dev-docs/investigations/rapidgzip-upstream-report.md:68-82` §2, which records that some
*path*-source truncations and checksum mismatches can terminate during worker finalization
too.

**Answer today.** The source is never killed underneath a live accelerator stream: teardown
of the shared source is deferred behind the streams that use it, so closing the reader with
a member stream still open cannot trigger the abort. **Residual, upstream-only:** a caller
who closes *their own* source stream while an accelerator-backed stream is still in use
remains exposed (`open-issues.md` P5, `known-issues.md` Bug 3).

**Sources.** `known-issues.md` Bug 3; `investigations/rapidgzip-upstream-report.md` §2;
`open-issues.md` P5; `openspec/changes/archive/2026-08-06-close-member-streams-on-reader-close/`.

---

### UL-06 — A parallel decompressor reports success on a truncated stream, by design

**Problem.** A decompressor optimized for random access decodes speculatively: it guesses
where compressed blocks begin and swallows the exceptions that guessing produces, because
they are expected. Reaching the end of the data is indistinguishable, inside that design,
from "no more decodable data here" — so a truncated stream yields either nothing or a
correct short prefix, and the read returns normally. The object's own completeness and size
attributes are set from what it decoded, so they agree with the short answer. There is no
flag that says the stream ended without a verified trailer, and this is deliberate rather
than a bug: the library's purpose is mid-stream random access, not integrity.

**Symptom.** Reading a truncated compressed file returns empty, or a plausible prefix,
without raising. A caller cannot distinguish that from a legitimately short file, and the
attributes it would consult to check agree with the wrong answer. The equivalent
standard-library decoder raises. The rate of silent-versus-raising varies by platform.

**Evidence.** `dev-docs/investigations/rapidgzip-upstream-report.md:30-64`, with the upstream
code paths named (`ParallelGzipReader::read` returns bytes already written on a missing
chunk; `GzipChunkFetcher::processNextChunk` finalizes and returns empty; `GzipChunk::tryToDecode`
swallows `std::exception` while guessing block starts) and the explicit finding that
`block_offsets_complete` and `size` must not be trusted for completeness. Deliberately **not
filed upstream** — an incompleteness flag would be a feature request, not a bug report.
Also `dev-docs/known-issues.md:156-165`.

**Answer today.** On any seekable source the accelerator's answer is backstopped: an empty
soft end-of-file switches the decode to the standard-library engine, and a non-empty one is
checked against the stream's declared trailer length for a single-member stream. Decided in
ADR
[`0014-integrity-verdicts-from-reads-not-close`](../../dev-docs/decisions/0014-integrity-verdicts-from-reads-not-close.md)
and `2026-07-24-rapidgzip-truncation-investigation` /
`2026-07-25-gzip-truncation-backstop-any-seekable`. **Residual:** multi-member trailer
summing is deferred; the honest statement to users is that bare-stream truncation detection
is best-effort and the accelerator can be turned off when certainty is required
(`open-issues.md` §Irreducible).

**Sources.** `investigations/rapidgzip-upstream-report.md`; `known-issues.md`;
`library-analysis.md` §gzip; `open-issues.md` §Irreducible, §Longer-term; ADR 0014;
`investigations/adr-0014-investigation.md`.

---

### UL-07 — A random-access index over a compressed stream does not record the stream's own boundaries

**Problem.** An index built for random access records seek points chosen for chunked
parallel decode. Those are not the boundaries of the compressed format's own members: a gzip
file may be several concatenated streams, and the index has no reason to record where one
ends. Nor does the library expose a count of them. So the index — the obvious place to look
for structure — cannot answer a structural question about the data it indexes.

**Symptom.** Determining whether a compressed file contains one member or several requires
scanning the bytes for another header, even though an index of the file is already in memory.

**Evidence.** `dev-docs/known-issues.md:167-179`. Measured empirically on 2- and 3-member
files with distinct member sizes, read to end: member boundaries never appear in the offsets
at any parallelism setting — serial records only the start and end, parallel adds mid-member
chunk points unrelated to member starts. The index-import entry points are inputs, not a
decode-time enumeration. A change proposal to use the index for this was **closed on this
finding**.

**Answer today.** The byte scan for a further member header stays; the deferred per-member
trailer sum cannot use the index either. Recorded as a confirmed limitation with no action
(`known-issues.md`).

**Sources.** `known-issues.md` §rapidgzip's index; `library-analysis.md` §gzip.

---

### UL-08 — A native codec's worker thread can decode past the end of its input and write into freed memory

**Problem.** A codec binding runs its decoder on a native worker thread. A rewrite in one
release removed the worker loop's stop condition for exhausted input, and the binding
translates an unbounded output request into an effectively infinite symbol budget. For a
codec variant with no end-of-stream marker, the worker therefore decodes past the true end
of the data and, when its input runs out, is left blocked inside the reader rather than
finishing. Meanwhile the call that started it completes and frees the output block the
blocked worker still holds a raw pointer into. Any later wake — the next call, or teardown —
resumes the worker to write into freed memory.

The decisive detail is that a *bounded* request is not automatically safe: a request that
overshoots the stream's true remaining output by enough (measured at ≳64 KiB) reproduces the
crash with no unbounded call anywhere. The exact bound is what matters.

**Symptom.** Intermittent process aborts — segmentation faults, allocator errors, heap
corruption reports — during or after decoding valid, non-adversarial data. Crash rates
depend on allocator layout, so exercising other codecs first changes them, and the crash
often lands in unrelated code long after the corrupting write. A green test run can abort
at teardown.

**Evidence.** `dev-docs/known-issues.md:199-419`, root-caused with valgrind: the
use-after-free is of the binding's own output buffer, freed at `_ppmdmodule.c:552`
(`OutputBuffer_Finish`) while the worker resumes at `ThreadDecoder.c:134`. Version matrix
under one identical stress scenario: 0/40 native crashes on 1.1.1 and 1.2.0, **12/40** on
1.3.1, pinning the regression to the threaded-decoder rewrite (upstream
`miurahr/pyppmd#126`). Minimal upstream-facing reproduction with no archivey
(`scripts/pyppmd_crash_repro.py`): ~40 % for one shape, 15–25 % for another, 0 % for the
controls. Overshoot A/B: +65 536 bytes over → 13/20 and 10/20; +64 and +4096 → 0/20 each.
Deterministic gate: `scripts/ppmd_uaf_valgrind.py`.

**Answer today.** Every decode is bounded by the container's declared size for that member
or block; an unbounded request after end-of-input is never made; at compressed end-of-input
at most one documented synthetic byte is injected, bounded, and anything still missing is
reported as truncation rather than pumped in a loop. The variant with no end marker is
*rejected at construction* when no size is declared, since there is then no safe request
size and no correct output boundary either. A parked worker is driven to completion before
the decoder is disposed so teardown cannot resume it. **Residual:** a crafted header that
inflates the declared size ≳64 KiB past the member's true content puts the one bounded
decode back into the crashy class, and cannot be detected before decoding
(`known-issues.md`).

**Sources.** `known-issues.md` §Intermittent `pyppmd` native aborts, §exit-after-green;
`investigations/ppmd-native-investigation-results.md`;
`investigations/ppmd-native-investigation-brief.md`;
`investigations/ppmd-exit-after-green-exploration.md`;
`investigations/pyppmd-upstream-report.md`; `open-issues.md` §Irreducible, §Longer-term.

---

### UL-09 — Older releases of the same codec binding returned wrong bytes instead of crashing

**Problem.** The releases preceding the crash described in UL-08 had the stop condition the
rewrite removed, so their worker halted when input ran out — which prevented the runaway but
also cut symbols short at chunk boundaries. On a chunked bounded decode they therefore
returned *wrong output* and reported success. Pinning to an older release to avoid the crash
trades a loud failure for a silent one.

**Symptom.** Reading a member of a solid archive yields data that fails the container's
checksum, on the second and later members, with no crash and no diagnostic from the codec
itself.

**Evidence.** `dev-docs/known-issues.md:278-311`: under one identical stress scenario,
1.1.1 and 1.2.0 produced 0/40 native crashes but 27/40 checksum mismatches on a solid
second member, while 1.3.1 produced 12/40 crashes and 0 mismatches.

**Answer today.** The version floor is raised to the release that crashes rather than
corrupts, because bounding the decode is an effective mitigation for the crash (0/80 in the
same soak) while wrong bytes have no mitigation; the older releases' recovery workarounds
were removed with the floor. `known-issues.md` §Version floor decision.

**Sources.** `known-issues.md` §Intermittent `pyppmd` native aborts;
`investigations/ppmd-native-investigation-results.md`;
`openspec/changes/archive/2026-07-30-consolidate-optional-extras/`.

---

### UL-10 — A codec can report end-of-stream early, making truncation indistinguishable from completion

**Problem.** A codec binding may flip its end-of-stream flag before the stream is actually
exhausted, when asked for a small amount of output over compressible data. A caller that
treats the flag as authoritative stops early and silently truncates valid data; a caller that
distrusts it and keeps draining to finish the tail can be pushed into an allocation failure
on the buggy release. Without knowing how many compressed bytes the member really has, the
two cases cannot be told apart at all.

**Symptom.** A chunked read of a valid member returns less than the member contains, or the
attempt to finish it exhausts memory. Which happens depends on the size of the reads the
caller chose.

**Evidence.** `dev-docs/known-issues.md:477-483`: draining to finish the tail produced a
memory error in **36/36** trials at 50–99 % compressed-length cuts on the affected release.

**Answer today.** The compressed length is required for the affected codec variant, so the
decoder can tell "input exhausted" from "flag flipped early" instead of choosing between
truncating a valid member and crashing; post-end drains run only when the compressed input is
known complete *and* a declared output size bounds them. The length is plumbed through the
7z pipeline, including the encrypted case where the codec's own input has no knowable length
and the preceding stage's output size supplies it. `known-issues.md` §Mitigation in archivey.

**Sources.** `known-issues.md` §exit-after-green;
`investigations/ppmd-exit-after-green-exploration.md`;
`investigations/ppmd-native-investigation-results.md`.

---

### UL-11 — A zstd binding short-reads a truncated stream silently and cannot seek backwards

**Problem.** One widely used zstd binding returns a short read on a truncated stream without
raising, while two other bindings of the same codec raise on the same input; and its reader
cannot be repositioned backwards at all, refusing the operation with an
operating-system-level error. Separately, and independently of any binding, zstd's default
frame carries no integrity check, so corruption inside a frame is undetectable by any
reader.

**Symptom.** A truncated compressed file decodes to a short result that looks like success.
A backward seek fails outright, so serving one requires closing and reopening the stream and
decoding forward from the start. Corrupt-but-complete data decodes to garbage with no error
from any binding.

**Evidence.** `dev-docs/library-analysis.md:102-116` — probed directly on one language
version with a 200 KB incompressible multi-block frame: truncation → silent short read for
one binding, `EOFError` for the two others; backward seek → refused for one, in-place rewind
for the two others; corruption with no frame checksum → silent for all three, "inherent to
zstd".

**Answer today.** The decode backend was migrated to the standard-library line of the same
API (a backport on older language versions), which fixes both behaviours at once and lets
the reopen-from-start workaround be deleted. Decided in ADR
[`0009-zstd-stdlib-backports`](../../dev-docs/decisions/0009-zstd-stdlib-backports.md) and
`2026-07-01-zstd-stdlib-backend-migration`.

**Sources.** `library-analysis.md` §zstd; ADR 0009;
`openspec/changes/archive/2026-07-01-zstd-stdlib-backend-migration/`;
`openspec/changes/archive/2026-06-30-compression-library-evaluation/`.

---

### UL-12 — Composing two raw filter stages in one library chain can silently drop the tail

**Problem.** A compression library that accepts a chain of raw filters can be asked to
combine an entropy coder with a branch-conversion filter. When the entropy coder's stream
lacks an end-of-stream marker — which is common from one widely used producer — combining
them in a single chain silently truncates the final look-ahead bytes the branch filter
needs. The library reports success; the last bytes of the member are simply wrong.

**Symptom.** A member decodes without error and its tail is corrupt. A checksum catches it
if the container stored one; nothing else does.

**Evidence.** `dev-docs/library-analysis.md:282-290`, citing upstream BPO-21872 and the
xz-devel discussion, and noting that the same staging workaround is what another Python 7z
implementation does.

**Answer today.** The two stages are run separately — the entropy coder through the standard
library, the branch filter through a dedicated package — rather than composed into one raw
chain. `library-analysis.md` §LZMA1 / LZMA2.

**Sources.** `library-analysis.md` §LZMA1/LZMA2 and filter stages;
`openspec/changes/archive/2026-07-12-support-lzma1-bcj/`.

---

### UL-13 — The standard xz reader reports the wrong size for a multi-stream file

**Problem.** An xz file is a sequence of independent streams, optionally separated by
padding, each ending with a footer pointing back at an index of its own blocks' sizes. No
single index describes the whole file. A metadata path that reads only the last stream's
index therefore reports only the last stream's uncompressed size as the file's size. The
standard-library reader also cannot seek efficiently at all: any backward seek, including
seeking to the end to learn the size, re-decompresses the entire file.

**Symptom.** A multi-stream compressed file reports a size much smaller than it decompresses
to. Learning the size at all costs a full decompression.

**Evidence.** `dev-docs/library-analysis.md:177-234` — the format's structural property
(footer → index → per-block compressed and uncompressed sizes) and the explicit record that
the previous standard-library-based metadata path "reported the wrong size for multi-stream
XZ files". The alternative third-party reader was rejected for requiring a seekable input
and doing an upfront full index scan.

**Answer today.** A native parser over the standard library's codec walks streams backwards
from the end, reading footers and indices without decompressing, so both size and block-level
random access come from the file's own structure; multi-stream handling is explicit in both
directions. The same framework serves lzip, whose member trailer carries the equivalent
information. `library-analysis.md` §xz, §lzip.

**Sources.** `library-analysis.md` §xz, §lzip;
`openspec/changes/archive/2026-06-30-phase-3-indexed-leaf-formats/`.

---

### UL-14 — An encrypted-header writer may omit the digest that would make a wrong password detectable

**Problem.** Where a format's header-encryption has no password verifier (SEC-13), the one
deterministic check available is the stored digest of the encrypted header's own decoded
block — if the writer stored one. The reference implementation does; another widely used
writer does not, leaving the digest undefined. The archives most likely to be exercised in
testing are the ones the available writer produced, so the gap is invisible in a test suite
built on it.

**Symptom.** Wrong-password detection is deterministic on archives from one writer and
heuristic on archives from another, with nothing distinguishing them to the reader.

**Evidence.** `dev-docs/threat-model.md:190-197` (O8, defence 1): the reference writer stores
the digest so detection is deterministic; the other writer reports the digest as undefined on
the encoded-header block, "and the only 7z archives *we* produce are test fixtures written
through" it.

**Answer today.** The digest is verified when present, and the zero-file-record heuristic
(SEC-13) covers the case where it is absent; upstream storing the digest is listed as an
optional hardening. `threat-model.md` O8.

**Sources.** `threat-model.md` O8; `review/archive/2026-07-16-crypto/`.

---

### UL-15 — A parallelism setting of zero means "all cores", not "sequential"

**Problem.** A native decompressor's parallelism parameter uses zero to mean "use every
available core" rather than "do not parallelize". A caller reading the parameter as a count
gets the opposite of what they intended, and the difference is invisible except in
throughput and thread count.

**Symptom.** Code that meant to disable parallel decode enables it maximally. Behaviour that
depends on parallelism — including which truncation cases go silent (UL-06) — differs from
what the caller reasoned about.

**Evidence.** `dev-docs/investigations/rapidgzip-upstream-report.md:88-93`: "`parallelization=0`
→ `availableCores()`. Archivey passes `0` **intentionally** (all-cores + benchmarks). Not
'sequential.'"

**Answer today.** The value is passed deliberately with the intent recorded next to it, and
the note is kept in the upstream report so the reading is not re-derived
(`rapidgzip-upstream-report.md` §3).

**Sources.** `investigations/rapidgzip-upstream-report.md` §3; `known-issues.md`.

---

### UL-16 — A finalizer that references its own subject can never run

**Problem.** A finalizer registered to clean up an object will not fire if its callback holds
a strong reference to that object: the registration keeps the callback alive, the callback
keeps the object alive, and the object therefore never becomes unreachable. The
registration succeeds, the code reads as a working safety net, and the cleanup simply never
happens. Nothing reports this.

**Symptom.** A resource the safety net was written to release leaks for the life of the
process. Collecting garbage explicitly does not help, because the object was never garbage.

**Evidence.** `dev-docs/open-issues.md:88-95` — measured as +1 file descriptor on every one
of seven backends when a stream was dropped without closing, unchanged after three explicit
collections; root-caused to exactly this pattern, and fixed by capturing the object's
identity rather than the object.

**Answer today.** The callback captures only the identity value it actually needed, so the
subject can become unreachable and the finalizer runs. Fixed in
`2026-08-06-close-member-streams-on-reader-close` (`open-issues.md` P7).

**Sources.** `open-issues.md` P7;
`openspec/changes/archive/2026-08-06-close-member-streams-on-reader-close/`.

---

### UL-17 — A library's tar and zip readers invalidate member streams when the archive is closed

**Problem.** The standard library's own archive readers invalidate any open member stream
when the archive object is closed. A library offering a different lifetime — streams remaining
usable after the archive closes — is more permissive, but contradicts what users migrating
from the standard library have learned, and makes an escaped stream survive silently where
they expect it to fail loudly.

**Symptom.** Code written against the standard library keeps working when it should have
failed, so a bug (a stream escaping its archive's scope) is not surfaced; or, if the
behaviour is matched, code that relied on the more permissive lifetime breaks with no notice
in a migration guide.

**Evidence.** `dev-docs/open-issues.md:136-142`: `zipfile.ZipFile.close()` and
`tarfile.TarFile.close()` both invalidate member streams; measured on all seven backends,
reading after archive close succeeded everywhere before the change.

**Answer today.** Member streams are closed when the reader closes, matching the standard
library, chosen by the maintainer over a diagnostic-plus-finalizer alternative after
establishing that the principle the alternative protected was about how *contention* is
resolved, not about lifetime. `2026-08-06-close-member-streams-on-reader-close`
(`open-issues.md` P7).

**Sources.** `open-issues.md` P7;
`openspec/changes/archive/2026-08-06-close-member-streams-on-reader-close/`.

---

### UL-18 — Many native extensions in one long-lived process corrupt each other's heap

**Problem.** A process that loads a dozen independent native extension modules — codecs,
accelerators, cryptography, a subprocess-spawning wrapper — accumulates a shared heap that any of
them can damage. When one does, the failure surfaces at an arbitrary later allocation, so the
reported crash site is whatever ran next, not the offender. A component that merely allocates a
lot becomes the apparent culprit.

**Symptom.** A long-running process dies intermittently with a segmentation fault or an allocator
abort, at a site with no relationship to any archive work — during garbage collection, while
starting a subprocess, inside an unrelated library. The same run passes elsewhere. Removing
components changes the rate but not the shape, so the offender cannot be identified from the
crash.

**Evidence.** `dev-docs/known-issues.md:530-709`, still open: crash sites listed with the
explicit note that the stack at death "is usually a **late symptom** (heap already corrupt), not
the corrupting call"; present with all optional extensions installed and clean with none;
typically absent on two other platforms on the same commits; and with no reliable single-command
reproduction. Also recorded: one combined test process aborted with every test green during
coverage flush.

**Answer today.** **Unresolved.** Process isolation as continuous-integration hygiene — the
suite is split so the heaviest native paths run in their own subprocesses and a corrupted heap in
one cannot take down another — explicitly labelled "CI hygiene, not a product fix", with a
bisection recipe and known red fingerprints recorded. `known-issues.md` §Intermittent Linux
full-suite heap corruption.

**Sources.** `known-issues.md` §Intermittent Linux full-suite heap corruption;
`investigations/ppmd-native-investigation-results.md`; `open-issues.md` §Irreducible.

---

---

## Performance & memory

### PERF-01 — Streaming a solid archive with bounded memory and reading it member-by-member are different algorithms

**Problem.** Given that a solid block must be decoded from its start (FQ-06), there are two
ways to serve *n* members from it, and they have different costs in different resources.
Decoding the block once and handing out each member's bytes as the decompressor produces them
costs one pass and memory bounded by the decompressor's working set — but the members must be
consumed in order, and each stream is only valid until the next begins. Buffering the block
to memory or a temporary file lets members be read in any order and more than once, but costs
memory or disk proportional to the block. Neither dominates; the right one depends on what
the caller is doing, which the library cannot know.

**Symptom.** A conversion or hashing pass that reads every member once is either
memory-bounded or spills the largest block to disk, depending on a choice made for it. A
caller who reads members out of order finds the bounded design re-decoding, or finds the
buffered design's temporary files filling the disk.

**Evidence.** `dev-docs/history/COMPARISON.md:159-173` compares the two designs directly:
one gives memory bounded by a queue "regardless of file/block size", the other O(largest
solid block) spilling to disk. The "two memory profiles" distinction — a monotonically
growing random-access cache versus a bounded sequential pass — is called out there as
something to state explicitly rather than leave implicit.

**Answer today.** The in-order streaming pass is the bounded-memory path and is what
conversion drives; a random open re-decodes from the block start rather than accumulating a
cache, and may cache at most one decoded block for repeated access to that block. The
prohibition is explicit: no growing cache of decoded data released only at close.
`ARCHITECTURE.md` §5.6, §7.3.

**Sources.** `history/COMPARISON.md` §2, §4.4; `history/ARCHITECTURE.md` §2.3, §5.6, §7.3;
`history/SPEC.md` §10.4.

---

### PERF-02 — A backward seek in a compressed stream costs a full re-decode unless the format has an index

**Problem.** A compressed stream can only be read forwards: the decoder's state at offset *n*
is the result of everything before it. Repositioning backwards therefore means starting over,
unless the format records enough per-block information to restart mid-stream — which xz and
lzip do in an index or trailer, gzip and bzip2 do not, and zstd does only at frame
granularity, which for a single-frame file is one useless seek point at the start.

**Symptom.** A backward seek that looks like an ordinary cheap operation re-reads and
re-decodes the whole file, so an access pattern that seeks repeatedly is quadratic with no
error and no signal. Which streams are cheap to seek is a property of the format the caller
may not know they have.

**Evidence.** `dev-docs/library-analysis.md:32-38` ("a codec with no native index services a
backward seek by re-decompressing from the start — O(n) per rewind … permitted but never
silent: the first rewinding seek logs a warning"), and the per-codec efficient-seek column at
`:46-60`. Frame-granularity limits of the zstd option at `:159-170`.

**Answer today.** Rewinding is permitted and never silent: the first rewinding seek warns,
naming the extra that would make it cheap; efficient seeking is provided natively where the
format carries the information (xz block index, lzip trailer, `.Z` clear-code boundaries) and
by an optional accelerator for gzip/bzip2/deflate. A seek-cost signal is part of the declared
cost of the opened archive. `library-analysis.md`; `ARCHITECTURE.md` §2.12.

**Sources.** `library-analysis.md` §Summary, §xz, §Seekable zstd; `history/ARCHITECTURE.md`
§2.12; `history/COMPARISON.md` §4.12; `open-issues.md` §Irreducible.

---

### PERF-03 — Verifying a member's declared checksum and delivering its bytes are the same read

**Problem.** A container that stores a digest per member lets a reader confirm that what it
delivered is what was stored. But the digest is over the whole member, so it can only be
checked once every byte has passed through — which is exactly the read the caller is already
performing. Treating verification as a separate step means reading the member twice, and on a
solid or compressed source the second read is a second decode.

**Symptom.** Verifying an archive costs twice what reading it costs, and on large members the
doubling is the dominant cost. Alternatively verification is skipped for speed, and
corruption is delivered silently.

**Evidence.** Named as the worked example of the neutrality rule in
[`brief.md`](brief.md):216 (§The neutrality rule). Verification is stated as a stage of the
uniform stream layer at `dev-docs/library-analysis.md:39-42`: the container-supplied digest
is checked over the decompressed bytes at clean end-of-file.

**Answer today.** Verification is fused into the delivering read as a stream stage rather
than a second pass; the verdict is produced by the reads themselves rather than at close, and
digests the container already stores are surfaced so callers need not recompute them. ADR
[`0014-integrity-verdicts-from-reads-not-close`](../../dev-docs/decisions/0014-integrity-verdicts-from-reads-not-close.md);
`2026-07-19-surface-stored-stream-digests`; stream-layering review F1/F2 (`#137`).

**Sources.** `library-analysis.md`; `review/archive/2026-07-19-stream-layering/`; ADR 0014;
`openspec/changes/archive/2026-07-19-surface-stored-stream-digests/`.

---

### PERF-04 — Recovering the readable prefix of a damaged stream and reporting the damage are in tension

**Problem.** When a compressed stream is cut short, the bytes before the cut are valid and
often wanted; the fact that it was cut is also wanted. A read that returns the prefix and
reports success is a lossy success — the caller cannot tell it got part of the data. A read
that raises and returns nothing is honest but throws away recoverable content. There is no
single answer, because the two callers (salvage a damaged backup; verify an archive) want
opposite things.

**Symptom.** Either a truncated file appears to read cleanly and short, or a truncated file
yields nothing at all even though most of it was intact. Both look like the library
misbehaving, from opposite directions.

**Evidence.** `dev-docs/library-analysis.md:62-69` (note 1) and `:255-264`: a bounded read
returns the recoverable prefix and then raises on the next empty read, while a
read-everything call "raises `TruncatedError` and returns nothing — a silent lossy success is
worse than not salvaging". The salvage use case is registered as unmet:
`dev-docs/open-issues.md:279` ("Salvage / best-effort read mode … all-or-error today").

**Answer today.** The chunked read path recovers the prefix and then reports truncation; the
read-everything path refuses to return a partial result. A general best-effort salvage mode
is **unresolved** and registered as longer-term work (`open-issues.md` §Longer-term).

**Sources.** `library-analysis.md` §Summary note 1, §gzip; `open-issues.md` §Longer-term;
`openspec/changes/archive/2026-07-24-gzip-zlib-truncation-recovery/`;
`openspec/changes/archive/2026-07-18-partial-members-and-errors/`.

---

### PERF-05 — Truncation is undetectable in formats that store no length or checksum

**Problem.** Detecting that a stream was cut short requires something to compare against: a
stored length, a checksum, or a defined terminator. Several compressed formats have none.
Brotli has no trailer at all. The classic Unix compress format has neither length nor
checksum, and its writers zero-pad after the last complete code — so a cut that happens to
leave only zero bits is byte-identical to a correctly terminated stream. A reader cannot
distinguish them because there is no difference in the bytes.

**Symptom.** Some truncations of the same file are reported and others are silent, with no
pattern the user can see. A verification pass gives a clean verdict on a damaged file.

**Evidence.** `dev-docs/library-analysis.md` notes 2 and 3 (`:70-78`): brotli detected only
by "never finished at EOF"; `.Z` truncation is best-effort via nonzero leftover bits, and
"cuts that leave only zero leftover bits stay silent". Also `dev-docs/open-issues.md:265`
("`.Z` truncation: only nonzero leftover bits are loud").

**Answer today.** Best-effort detection where the format allows any, and an explicit
statement of the limit rather than an implied guarantee; where the member sits inside a
container that stores a digest, the container's digest is the real net (PERF-03).
`library-analysis.md` §Summary; `open-issues.md` §Irreducible.

**Sources.** `library-analysis.md` §Summary, §brotli, §unix-compress; `open-issues.md`
§Irreducible; `openspec/changes/archive/2026-07-14-vendor-unix-compress-lzw/`.

---

### PERF-06 — Decoding is CPU-bound native work, so concurrency buys overlap and not throughput

**Problem.** Every decompressor available to this kind of library is native code that holds
the interpreter's global lock or releases it around a CPU-bound loop. Running more of them
concurrently in one process therefore does not multiply throughput the way independent I/O
would; what concurrency buys is overlapping a slow source with decode, and not blocking a
caller's event loop. Adding more concurrent decoders also adds file descriptors, memory and
native threads, so past a point it costs more than it returns.

**Symptom.** A parallelized read of an archive is no faster than the sequential one, or
slower, while using several times the memory and descriptors. Speedups measured on one shape
of archive do not appear on another.

**Evidence.** `dev-docs/history/ASYNC.md:70-77`: "async buys **no CPU concurrency** — decode
is CPU-bound under the GIL. The only win is **I/O overlap**."
`dev-docs/investigations/parallel-reader.md:131-137`: a consumer "must not assume more
accelerator objects ⇒ linear speedup (FD / memory / thread pressure)". Per-format
parallelizable units and their constraints at `parallel-reader.md:141-154`; workloads
including a deliberate negative control at `:83-97`.

**Answer today.** Correctness changes carry no speed threshold and no throughput claim;
parallel decode and extraction scheduling are deferred as a separate feature whose speed
claims require targeted before/after measurement at the format's real parallel unit (member
for independent-offset formats, folder or block for solid ones).
`parallel-reader.md` §3, §5; `threat-model.md` C4.

**Sources.** `history/ASYNC.md` §3; `investigations/parallel-reader.md` §3, §4, §5;
`threat-model.md` C4; `openspec/changes/archive/2026-07-10-parallel-reader-exploration/`.

---

## API and usage pattern

### API-01 — A caller who asks for random access over a forward-only source can be served silently or refused

**Problem.** Random access over a source that cannot be repositioned is only possible by
buffering the source — to memory or to disk — for as long as the archive is open. That cost
is proportional to the whole archive regardless of how little of it the caller wanted, and it
is invisible: the operation succeeds, and the resource consumption appears somewhere else.
The alternative is to refuse, which is honest but makes the caller handle a case they may not
have known existed.

**Symptom.** Either an unremarkable-looking open consumes memory or disk proportional to a
stream the caller was treating as streamed, or a program that works on a file fails on a pipe
with an error about seekability.

**Evidence.** `dev-docs/history/SPEC.md:911` (a non-seekable source for an
index-at-the-end format raises at open time, "the library does **not** implicitly buffer");
decided in ADR
[`0010-no-silent-buffer-nonseekable`](../../dev-docs/decisions/0010-no-silent-buffer-nonseekable.md);
`dev-docs/open-issues.md:252` (§Irreducible, "no silent buffer").

**Answer today.** Refuse, loudly, at open time, with the message naming the remedy (buffer
and reopen); transparent spooling would return only as an explicit opt-in argument. ADR 0010.

**Sources.** `history/SPEC.md` §10.1, §5.1; ADR 0010; `open-issues.md` §Irreducible.

---

### API-02 — "Unknown" and "empty" are different answers that formats force into one field

**Problem.** For most metadata a format either records a value or does not: no mode, no
timestamp, no size, no digest. Zero, the empty string and the epoch are all legal *values*
for those fields too. Collapsing the two cases — substituting a default for a missing value —
destroys information the caller needs, and raising instead of answering makes ordinary
listing fail on ordinary archives.

**Symptom.** Every member appears to have been modified at the epoch, or to be mode 0, or to
be size 0, and the caller cannot tell which of those are real. A conversion writes those
substituted values into the target archive as if they had been recorded.

**Evidence.** Stated as a design authority at `dev-docs/history/SPEC.md:13`: "when a format
quirk cannot be cleanly mapped to the unified model, the library surfaces the inconsistency
as an explicit, documented field value (`None` or an `Unknown` sentinel) — never as a silent
guess, default, or exception." Per-field consequences at `SPEC.md:368,905,972`.

**Answer today.** Every optional field is explicitly nullable and documented as such, and an
unrecognized codec maps to an unknown value rather than raising. `SPEC.md` §4.4, §4.3.

**Sources.** `history/SPEC.md` §1, §4.3, §4.4; `history/COMPARISON.md` §4.2.

---

### API-03 — Handing a caller a metadata object before the data is read forces a choice about mutation

**Problem.** Given that some fields are only knowable after the data is read (FQ-01, FQ-03),
a library that yields a metadata object first has three options and no fourth: mutate the
object the caller holds when the values arrive; hand back a replacement, which the caller
must know to re-fetch; or refuse to yield anything until the data is read, which defeats
streaming. Mutation makes the object unusable as a dictionary key or set member and means
two things can write to it. A replacement cannot be delivered at all in a single forward pass,
where there is nothing to re-fetch from.

**Symptom.** Either the object a caller stored changes underneath them, or it silently never
gains the late values, or they cannot put members in a set. A filter that edits a member
either loses the late values or corrupts the library's bookkeeping.

**Evidence.** `dev-docs/history/ARCHITECTURE.md:73-101` and `:229-243`, which state the
mechanism and the reason a transform cannot live in the yielding generator: a copy-returning
filter "would yield that copy while the backend went on updating the original — the caller's
object would never see the late values". Decided in ADR
[`0007-mutable-archive-member`](../../dev-docs/decisions/0007-mutable-archive-member.md).

**Answer today.** Mutate, under a contract: the library is the only writer, callers treat
members as read-only, and any caller edit goes through a copy-returning method. The
unhashability that follows is accepted and callers key by name or positional identity
instead. Transformation lives at the sinks that consume the stream, each applying it to a
transient copy while the original supplies accurate limits and metadata.
`ARCHITECTURE.md` §2.1, §2.10, §5.2; ADR 0007.

**Sources.** `history/ARCHITECTURE.md` §2.1, §2.3, §2.10, §5.2; `history/SPEC.md` §4.4;
`history/COMPARISON.md` §4.2; ADR 0007.

---

### API-04 — The cost of an operation varies by orders of magnitude across formats that share one interface

**Problem.** "List the members" is instant on a format with an index and a full decompression
on one without (FQ-05); "read this member" is direct on one layout and a re-decode of
everything before it on another (FQ-06); "seek backwards" is cheap with an index and a full
re-decode without (PERF-02). A uniform interface over these formats necessarily makes
operations with wildly different costs look identical at the call site. Worse, the cheap path
often exists but is reachable only by knowing which knob turns it on, which means the cost
model is expressed as configuration flags rather than as an answerable question.

**Symptom.** A caller writes the obvious loop and it is quadratic on one format and linear on
another, with nothing at the call site to suggest a difference. To get the cheap behaviour
they must already know it exists — the flag is the documentation.

**Evidence.** `dev-docs/history/COMPARISON.md:188-190`: the backend flags' "weakness … is
that backend flags … leak the cost model: you must know the trick to get cheap seeking on
`.tar.gz`." The three orthogonal cost axes and their per-format values at
`history/ARCHITECTURE.md:508-541`.

**Answer today.** Cost is a queryable property of the opened archive along three orthogonal
axes — enumeration cost, per-member access cost, and the source's own stream capability —
computed before any heavy I/O, with the solid block count alongside; backend flags become
tri-state and are resolved against the declared access mode rather than being the interface
to the cost model. `ARCHITECTURE.md` §2.12; `history/COMPARISON.md` §4.7;
`2026-07-30-member-stream-capability-booleans`.

**Sources.** `history/COMPARISON.md` §3, §4.7; `history/ARCHITECTURE.md` §2.12;
`history/SPEC.md` §4.6; `open-issues.md` P9.

---

### API-05 — Access mode is a real binary, but three-valued models of it are tempting and wrong

**Problem.** A caller either needs to reach any member at any time or is going to make one
forward pass. Those are different requirements with different feasible sources, so the choice
has to be made before the archive is opened. An "automatic" third value looks attractive but
cannot deliver: nothing at open time reveals what the caller will do next, so "auto" is
either a guess or just another mode. What *is* genuinely separate is a performance hint —
build seek points eagerly — which is not an access requirement at all.

**Symptom.** A three-valued setting where one value does not do what its name says, and
callers choosing it get one of the other two behaviours arbitrarily.

**Evidence.** `dev-docs/history/COMPARISON.md:9-20`: an enum was recommended and then
**reversed** during the build — "`AUTO` does not actually auto-select (it is just another
mode), and the model collapses to a real binary — random access vs. forward-only — plus a
deferred performance hint." Decided in ADR
[`0004-streaming-bool-not-intent-enum`](../../dev-docs/decisions/0004-streaming-bool-not-intent-enum.md).

**Answer today.** A boolean access mode, defaulting to random access and failing fast on a
non-seekable source; the eager seek-point hint may return later as an explicit opt-in.
ADR 0004.

**Sources.** `history/COMPARISON.md` §Decision update, §2, §3, §5; ADR 0004;
`history/SPEC.md` §5.1.

---

### API-06 — A forward-only pass can be run once, and callers will try to run it twice

**Problem.** A single forward pass over a stream consumes it. Anything that would need to see
a member again — listing after iterating, iterating twice, extracting after listing — is not
available, and the reason is the source, not the interface. Meanwhile an interrupted pass
leaves the source somewhere in the middle, which is neither "not started" nor "finished". A
caller who breaks out of a loop early and then asks a question about the archive is asking
about a stream that no longer starts where they think.

**Symptom.** The second iteration of an archive yields nothing, or a listing after a partial
read is silently short, and the same code works on a file and fails on a pipe.

**Evidence.** `dev-docs/history/ARCHITECTURE.md:250-273` (the one-pass rule, the operations it
covers, and the deliberate absence of replay from cache because link-resolution semantics
differ at yield time and after finalization); `dev-docs/open-issues.md:251` (§Irreducible,
"Streaming mode is one pass (including after early `break`)").

**Answer today.** Forward-only passes are explicitly once-only and a second attempt raises; a
dedicated scan call is the documented exception that may finish an interrupted pass or return
the completed result. Random-access iteration serves from the stored report once materialized.
`ARCHITECTURE.md` §2.4; `2026-07-07-scan-members`.

**Sources.** `history/ARCHITECTURE.md` §2.4; `history/SPEC.md` §3.2; `open-issues.md`
§Irreducible; `openspec/changes/archive/2026-07-07-scan-members/`.

---

### API-07 — A programming mistake and a bad archive need to be distinguishable by type

**Problem.** Two unrelated kinds of failure arise at the same call sites: the input is
damaged, encrypted, truncated or uses an unsupported feature — and the caller used the API
wrongly, by asking for random access on a forward-only reader, passing a member from a
different archive, or setting contradictory arguments. A caller writing a robust tool wants to
catch the first kind and let the second kind crash, because the second is a bug in their own
code. If both are the same type, `except` swallows their bugs.

**Symptom.** A broad exception handler around archive work hides the caller's own logic
errors, so a misuse presents as "this archive is bad" and is diagnosed as a data problem for
as long as it takes someone to read the traceback.

**Evidence.** Decided in ADR
[`0012-usage-errors-outside-archiveyerror`](../../dev-docs/decisions/0012-usage-errors-outside-archiveyerror.md);
the error-contract convention is restated in `review/README.md` §Conventions ("usage errors
sit deliberately outside the tree"). Taxonomy at `dev-docs/history/SPEC.md:642-694`.

**Answer today.** Usage errors are a separate hierarchy deliberately outside the
archive-error tree, so catching archive errors cannot catch a misuse; unrecognized exceptions
propagate raw with no catch-all, and only exceptions from a decoding library's own taxonomy
are translated. ADR 0012; `ARCHITECTURE.md` §2.11.

**Sources.** ADR 0012; `history/SPEC.md` §6; `history/ARCHITECTURE.md` §2.11;
`review/README.md`; `review/archive/2026-07-19-api-coherence/`.

---

### API-08 — Errors from a dozen libraries must be comparable without erasing their origin

**Problem.** Every codec and container library has its own exception taxonomy, and a library
that composes them presents callers with a dozen unrelated hierarchies for the same handful of
real conditions — corrupt data, truncated data, wrong password, unsupported feature.
Translating them into one taxonomy makes callers' code possible, but a translation that
catches broadly also swallows genuinely unrelated failures — filesystem errors, interrupts,
memory exhaustion — and one that discards the original loses the only precise diagnostic
information there was.

**Symptom.** Either callers must catch several libraries' exception types (and learn which
libraries are in use, which is a packaging detail), or an interrupt or a disk error is
reported as a corrupt archive and the original traceback is gone.

**Evidence.** `dev-docs/history/ARCHITECTURE.md:460-506`: translation is per-library, context
is stamped centrally, the original is attached as the cause, and "genuine non-decoding errors
propagate unchanged" — a filesystem error, an interrupt, and a memory error must not become
archive errors. `history/SPEC.md:692` ("Libraries must never swallow the original
exception").

**Answer today.** Two separable concerns: a small translator per underlying library maps that
library's exceptions to typed errors and sets no context, and the reader boundary stamps
archive/member/format context onto an error it is already re-raising. The cause chain is
preserved; no catch-all. `ARCHITECTURE.md` §2.11; `review/README.md` §Error contract.

**Sources.** `history/ARCHITECTURE.md` §2.11; `history/SPEC.md` §6; `history/COMPARISON.md`
§4.6; `review/README.md`.

---

### API-09 — Advisory conditions are numerous, and logging is the wrong channel for a library

**Problem.** Reading real archives produces a steady stream of things worth saying that are
not failures: a name was normalized, an extension disagreed with the content, a digest was
absent, a rewinding seek was expensive, a trailer was missing. A library cannot decide what to
do with these. Emitting them as log records makes them unavailable to a caller that wants to
act on them programmatically and configures a logger it does not own; raising them makes
ordinary archives fail; returning them as values requires deciding *when* — an advisory about
a member is meaningless before the member exists, and one about the whole archive is
meaningless after it is closed.

**Symptom.** A caller who wants to know whether anything was odd about an archive has to
parse log text, or install a log handler and correlate records with operations, or accept that
the information is only visible to a human reading a terminal.

**Evidence.** `dev-docs/threat-model.md:361-368` (C2, "Warnings that should be data"),
addressed by the lifecycle-aware diagnostics capability. The lifecycle constraint (which
surface an advisory can attach to) is what made this non-trivial, discussed at length in
`dev-docs/discussions/2026-08-diagnostics/`.

**Answer today.** Advisories are immutable values with stable codes attached to
lifecycle-appropriate surfaces (the detection result, the reader or a stream, a member, an
extraction report), with per-code policy (ignore / collect / raise) and a shared retention
budget; logging becomes the zero-configuration projection of the same data.
`diagnostics-warnings-as-data` (`threat-model.md` C2). Extraction results were later made the
sole authoritative record for extraction outcomes
(`2026-08-15-extraction-results-authoritative`).

**Sources.** `threat-model.md` C2; `dev-docs/discussions/2026-08-diagnostics/`;
`openspec/changes/archive/2026-07-11-diagnostics-warnings-as-data/`;
`openspec/changes/archive/2026-08-09-review-diagnostics-batch/`;
`openspec/changes/archive/2026-08-15-extraction-results-authoritative/`.

---

### API-10 — Asynchronous decoding is not available at all in this language, whatever the interface says

**Problem.** Every decoder is synchronous native code that pulls its input through a blocking
read callback the caller does not control. There is no hook to make a decompression call await
its next input chunk, and a native stack frame cannot be suspended on an await. So an
"asynchronous archive library" is not a thing that can exist here: the middle of the pipeline
is unavoidably synchronous, and the only places asynchrony can live are the two edges — bytes
arriving from a slow source, and bytes leaving to a slow consumer. An interface written
asynchronously all the way down would still run the decode on a worker thread, and would have
paid the cost of colouring every layer to reach a ceiling the edges already reach.

**Symptom.** An asynchronous interface over blocking decoders gives the appearance of
asynchrony with none of the benefit, and every function it touches becomes callable only from
other asynchronous functions — the parallel-universe duplication that makes the choice hard to
reverse.

**Evidence.** `dev-docs/history/ASYNC.md:50-82`: the named blocking call sites in each decode
path (`zipfile` reading `self.fp.read(n)` synchronously inside its member reader; `tarfile`
walking headers synchronously; the pull-driven standard-library codecs; a subprocess pipe read
synchronously) and the conclusion that "there is no 'async all the way down' available in
Python without rewriting the decoders". Cost comparison of the three options at `:115-154`.

**Answer today.** Synchronous only, with a documented recipe for running whole operations on a
worker thread; a leaf-level asynchronous facade is designed but deferred, and the sync-hygiene
seams that keep it cheap (inject the source as a narrow protocol, keep readers
thread-confinable, keep the byte interface pull-based and chunked, keep the event loop out of
error plumbing) are adopted now. Decided in ADR
[`0005-sync-only-v1`](../../dev-docs/decisions/0005-sync-only-v1.md); analysis in
`history/ASYNC.md` §6–§7 ("bake in the *seams*, not the *colour*").

**Sources.** `history/ASYNC.md`; `history/ARCHITECTURE.md` §5.3; `history/SPEC.md` §2,
Appendix A; ADR 0005.

---

### API-11 — Appending to an archive in place is possible for some formats and unsafe in all of them

**Problem.** Some formats technically permit appending: a ZIP can gain a new central
directory at the end, a tar can be concatenated. But an append interrupted partway leaves a
file that is neither the old archive nor the new one, and a concatenated tar is not a valid
single archive by every reader's reading. Other formats have no append mode at all. So the
operation is available unevenly, and where available it converts a crash into a corrupted
archive.

**Symptom.** A tool that appends produces archives that some readers accept and others
reject, and an interrupted append destroys data that existed before it started.

**Evidence.** `dev-docs/history/ARCHITECTURE.md:807-812`: ZIP append "is fragile and creates
corrupt archives if interrupted. 7z has no append mode. TAR can be appended to … but the
result is not a valid multi-stream archive."

**Answer today.** Writing is create-only; the supported workflow is read-old-write-new, which
the conversion path makes a single streaming pass. `ARCHITECTURE.md` §5.4.

**Sources.** `history/ARCHITECTURE.md` §5.4; `history/SPEC.md` §11.

---

### API-12 — Requiring pre-fetched members for a one-shot call forces the caller to open the archive twice

**Problem.** A convenience function that does the whole job in one call — open, read, extract,
close — cannot also accept a list of members to select, because obtaining that list requires
having opened the archive already. A caller with a selector in hand must open, list, close, and
then call the one-shot function, which opens again. The selection and the one-shot shape are
mutually exclusive by construction.

**Symptom.** The convenient function grows a parameter that can only be used by first doing
the thing the function exists to avoid, and callers write the open-list-close-reopen
anti-pattern because the signature invited it.

**Evidence.** `dev-docs/history/SPEC.md:55-59`: "There is deliberately no `members=` selector:
passing pre-fetched members to a one-shot function would force the caller to open the archive,
fetch the list, and reopen it here (an anti-pattern)."

**Answer today.** The one-shot call extracts everything and has no selector; selective work is
done on an already-open reader. `SPEC.md` §3.1.

**Sources.** `history/SPEC.md` §3.1; `review/archive/2026-07-19-api-coherence/`.

---

### API-13 — An explicit argument that is silently ignored is worse than one that is rejected

**Problem.** When a caller passes an explicit override, they are asserting something. If some
other property of the input takes precedence, the assertion is discarded — and the plausible
route to that situation is a variable holding what the caller *believes* is a path to an
archive, which is exactly the case where a quiet reinterpretation is least welcome.

**Symptom.** A call with an explicit format override behaves as though the argument were not
passed, with no error and no warning, only when the input is of the one kind that overrides
it.

**Evidence.** `dev-docs/open-issues.md:162-187` (P8): a directory path resolved its own format
before the caller's argument was ever consulted, "so the argument is discarded without a
diagnostic … every other way of being explicit about the format is honoured or rejected
loudly."

**Answer today.** The contradictory combination raises a usage error; passing the override
that agrees with the input stays valid. Fixed in `2026-08-06-reject-format-override-on-directory`
(`open-issues.md` P8, closed).

**Sources.** `open-issues.md` P8;
`openspec/changes/archive/2026-08-06-reject-format-override-on-directory/`.

---

### API-14 — Laziness has to be honoured all the way down or it is not laziness

**Problem.** An interface that yields members and opens each one's data only if the caller
asks promises that unselected members cost nothing. A backend that opens the underlying
resource at yield time, or starts the whole decode when the pass begins, satisfies the shape
of the interface and not the promise. The difference is invisible until the archive is
encrypted, at which point starting the decode demands a password for data nobody asked for —
and a wrong password then appears to be accepted, because the failure happens where no one is
looking.

**Symptom.** Iterating an encrypted archive without reading any member fails asking for a
password, or accepts a wrong one. Iterating an archive to look at names does the work of
reading it.

**Evidence.** `dev-docs/open-issues.md:239`: the requirement was already specified —
"unselected/unread members are not opened/decompressed **and do not request passwords**" — and
"7z opened each folder at yield time and solid RAR spawned `unrar` at pass start, so iterating
an encrypted archive without reading raised `EncryptionError` and made a wrong password look
right". Found by review; fixed in `#225`.

**Answer today.** Both solid backends defer to the first read. `open-issues.md:239` (closed);
found by review on `#224`.

**Sources.** `open-issues.md` §Docs; `review/archive/2026-08-15-simplicity-consistency/`;
`openspec/changes/archive/2026-08-06-close-member-streams-on-reader-close/`.

---

### API-15 — A capability that is cheap on one format and a trap on another cannot be on by default

**Problem.** Seeking inside a member and opening two members at once are both free on a
format with independent per-member offsets and both hazardous elsewhere: on a single
compressed stream a backward seek re-decodes (PERF-02), on a solid layout an out-of-order
open re-decodes a whole block (FQ-06), and on a shared handle two concurrent readers corrupt
each other (CONC-01). Offering them unconditionally means a developer can write and test
their code against the cheap format, see it work, and ship a footgun that fires on a format
they never tried. Publishing the cost as a queryable property (API-04) does not prevent this,
because nothing forces anyone to query it.

**Symptom.** Code validated against one archive format degrades to quadratic, or returns
wrong bytes, on another — in production, on a user's archive, with no error at development
time.

**Evidence.** Stated as the context of ADR
[`0003-member-streams-opt-in`](../../dev-docs/decisions/0003-member-streams-opt-in.md): "an
unconditional 'always seekable / always concurrent' API lets developers test on ZIP and ship
a footgun on TAR/7z. Cost receipts alone are too passive."

**Answer today.** Both capabilities are off by default on **every** format, including the
trivial ones, so the default path has no locks, no seek tables and no accelerators, and a
caller who needs either declares it at open time. The strict default is reversible before
1.0 while permissive-then-gate would be a breaking change. Solid open-*order* cost is
explicitly **not** erased by declaring concurrency — it stays the caller's algorithm.
ADR 0003 (amended before 0.2.0 to share one vocabulary with the single-stream entry point,
on the same reasoning as ADR 0004: prefer a boolean when the mode count is small).

**Sources.** ADR 0003; ADR 0004; `openspec/changes/archive/2026-07-11-concurrent-member-streams/`;
`openspec/changes/archive/2026-07-12-promote-concurrent-member-streams/`;
`openspec/changes/archive/2026-07-30-member-stream-capability-booleans/`.

---

### API-16 — A verdict delivered at close is delivered where nobody can act on it

**Problem.** An integrity check over a member can only conclude once every byte has passed
through (PERF-03), which makes the closing call the tempting place to report it. But closing
happens in scope-exit and cleanup blocks, where raising displaces whatever exception was
already propagating — so the report arrives by destroying the diagnostic it arrived with. And
a caller who deliberately stopped reading early never asked for a verdict at all; failing
their cleanup for a check they did not request is wrong. Meanwhile the read itself has an
ambiguity of its own: if a short return can mean either "healthy data, ask again" or "this is
the end", then no single read result is a conclusion, and a caller cannot tell a complete
short member from a truncated one.

**Symptom.** An integrity failure surfaces as an exception from a cleanup block, masking the
real error, or from a scope exit after the caller has already handled what they thought was
the outcome. Or a truncated member reads as a series of short-but-successful reads that never
resolve into a verdict.

**Evidence.** ADR
[`0014-integrity-verdicts-from-reads-not-close`](../../dev-docs/decisions/0014-integrity-verdicts-from-reads-not-close.md):
"`close()` runs in `__exit__` and in `finally` blocks, where raising masks the original
exception, and a caller who stops reading early has not asked for a verdict at all." The
trade-off analysis and rejected alternatives are in
`dev-docs/investigations/adr-0014-investigation.md`.

**Answer today.** Verdicts surface from reads, never from close. A read of *n* bytes returns
exactly *n* unless it hits a terminal boundary, so a short return is always terminal;
reaching the end of proven-wrong bytes raises and withholds the reaching chunk, while a
truncation-shaped end delivers the best-effort prefix and raises on the read past it.
Stopping early is not verification and is quiet; a caller who wants verification regardless
of access pattern opts into a strict mode. ADR 0014, settling the contract that
`2026-07-24-gzip-zlib-truncation-recovery` implements.

**Sources.** ADR 0014; `investigations/adr-0014-investigation.md`;
`openspec/changes/archive/2026-07-24-gzip-zlib-truncation-recovery/`;
`openspec/changes/archive/2026-07-18-partial-members-and-errors/`;
`review/archive/2026-07-19-stream-layering/`.

---

## Packaging & dependency

### PKG-01 — An optional dependency that is imported at module scope is not optional

**Problem.** Support for some formats and codecs requires third-party packages that will not
be present in every installation — native wheels that do not exist for every platform and
architecture, packages with build dependencies, packages a user declined. If the code
importing them runs at import time, an absent package makes the entire library unimportable,
so an installation missing an ISO reader cannot read ZIP files either.

**Symptom.** Importing the library raises an import error naming a package the user has never
heard of and does not need. Or a format silently appears to be supported and fails much later,
at the moment data is read.

**Evidence.** `dev-docs/history/ARCHITECTURE.md:543-582` and `history/SPEC.md:817`: the
registration guard pattern, and "an absent dependency simply makes the format unavailable (it
never appears in `list_formats()`)"; the error message names the extra to install.

**Answer today.** A library-backed backend registers itself only inside a successful-import
guard and declares its identifying bytes as data, so an absent package makes the format
absent from the availability list rather than breaking the import; requesting it raises an
error naming the extra. Native-parser backends register unconditionally and degrade at the
point the missing piece is actually needed — listing works, reading data raises an error
naming the missing tool. `ARCHITECTURE.md` §2.13;
`2026-08-09-format-availability-required-source`.

**Sources.** `history/ARCHITECTURE.md` §2.13; `history/SPEC.md` §9.1;
`openspec/changes/archive/2026-08-09-format-availability-required-source/`; ADR 0011.

---

### PKG-02 — A codec's best provider changes with the language version and over time

**Problem.** Which package should provide a codec is not a stable fact. A codec can arrive in
the standard library in a new language version, so the right answer differs across the
supported version range; several third-party bindings of one codec exist with different APIs
and materially different behaviour (UL-11); a binding can be deprecated, or acquire a
dependency on a backport of the standard-library module, changing which one is the smaller
surface. Depending on a specific package therefore encodes a decision that expires.

**Symptom.** The same code has different truncation and seeking behaviour depending on which
package the environment resolved, and a dependency chosen for good reasons becomes the wrong
one without anything in the project changing.

**Evidence.** `dev-docs/library-analysis.md:81-151`: five candidate providers for one codec,
with a measured behaviour table, and the decision to target the standard-library API and its
backport rather than any third-party binding — partly because a competing binding "depends on
`backports.zstd` for Python < 3.14" so targeting that API covers it too. Also `:55` (core on
3.14+, an extra below).

**Answer today.** Target the standard-library API and use a pure backport of it below the
version where it landed, so the extra disappears as the floor rises. ADR
[`0009-zstd-stdlib-backports`](../../dev-docs/decisions/0009-zstd-stdlib-backports.md);
`library-analysis.md` §zstd.

**Sources.** `library-analysis.md` §zstd; ADR 0009;
`openspec/changes/archive/2026-06-30-compression-library-evaluation/`.

---

### PKG-03 — Behaviour depends on both the presence and the version of optional packages

**Problem.** With optional providers for codecs and accelerators, the behaviour under test is
not one program but a family of them: with everything installed, with nothing installed, and
with the oldest permitted version of each. Truncation detection, seeking cost, error types and
crash exposure all differ across those. A test suite run in one configuration proves nothing
about the others, and a finding reported without naming its configuration is not reproducible.

**Symptom.** A bug reproduces on one developer's machine and not another's, or in one
continuous-integration leg and not the rest, with no difference in the code.

**Evidence.** `review/README.md` §Conventions ("Three dependency configs. Behaviour changes by
both presence and version of optional libs … Say which config a finding reproduces in").
Measured version-dependent behaviour differences at `dev-docs/known-issues.md:278-311` and
`dev-docs/library-analysis.md:102-116`.

**Answer today.** Three dependency configurations are a standing gate before pushing —
everything, everything at lowest permitted versions, and zero-dependency core — and every
review finding must name the configuration it reproduces in. `CONTRIBUTING.md` §Before
pushing; `review/README.md` §Conventions.

**Sources.** `review/README.md`; `known-issues.md`; `library-analysis.md`;
`openspec/changes/archive/2026-07-30-consolidate-optional-extras/`.

---

### PKG-04 — A required external tool may be absent, or present as a different program with the same name

**Problem.** Delegating to an external binary makes the host's contents part of the library's
behaviour. The tool may not be installed. Worse, several different programs answer to the same
name with different capabilities: one handles little of the modern format version, another's
coverage varies by build, a third exists only on one platform. A fallback matrix across them
produces divergent behaviour on solid archives and passwords, per machine, presented as one
feature.

**Symptom.** Reading an archive works on one machine and fails on another with the same
package versions installed; or it appears to work and handles a subset of the format, failing
on archives that use the rest.

**Evidence.** `dev-docs/threat-model.md:347-359` (C1: `unrar` is non-free freeware,
`unrar-free` "handles little of RAR5", `7z`/`bsdtar` coverage varies by build, `unar` exists on
macOS; a multi-tool fallback matrix "would otherwise degrade into 'works on my machine' plus
divergent solid/password behavior").

**Answer today.** Exactly one vendor binary is accepted; any other program of that name raises
an error naming the required one, with no silent fallback, and listing is designed to work
without the tool at all. ADR 0002; `threat-model.md` C1 (closed as won't-do).

**Sources.** `threat-model.md` C1; `history/ARCHITECTURE.md` §5.7; ADR 0002;
`open-issues.md` §Irreducible.

---

### PKG-05 — Producing test archives for a format can require a tool that cannot be shipped

**Problem.** Testing that a reader handles a format correctly means having archives in that
format. Generating them requires the format's *writer*, and for a proprietary format the
writer may be trialware — installable, but a licensing decision rather than a technical one.
Committing pre-built archives instead trades that for binary artifacts in the repository. So
the format's entire test column can end up running nowhere, and — because the suite still
reports green — nobody notices.

**Symptom.** A test suite passes with a whole format's coverage silently skipped. The gap is
invisible in the pass count and only appears if someone counts skips.

**Evidence.** `dev-docs/investigations/rar-corpus-sweep-diagnosis.md`: the corpus's RAR column
"runs on **no CI leg and in no provisioned dev environment**". Once the writer was installed,
**four of eight** entries failed, one of them a genuine reader bug (FQ-18) hidden by the gap;
enabling the column added **42 tests that previously ran nowhere**. Licensing is named as the
reason and explicitly left as a maintainer decision. Pinning test
`tests/test_review_simplicity_consistency.py:520`
(`test_rar_column_is_unmeasured_without_the_rar_writer`), which documents the gap and skips
when the writer is present.

**Answer today.** **Partly unresolved.** The gap is pinned by a test that fails to skip only
when the writer is present, so it cannot be forgotten; the diagnosis recommends a third route
(install the writer on one continuous-integration leg) that needs no digest rework and commits
no binaries, and leaves the licensing call to the maintainer. Committed fixtures with sidecars
are the accepted route where generation is genuinely impossible — ADR
[`0016-committed-rar-corpus-fixtures`](../../dev-docs/decisions/0016-committed-rar-corpus-fixtures.md).

**Sources.** `investigations/rar-corpus-sweep-diagnosis.md`; ADR 0016;
`review/archive/2026-08-15-simplicity-consistency/`; `history/ARCHITECTURE.md` §2.8.

---

### PKG-06 — Every hard dependency is imposed on every user, including those who never use it

**Problem.** A dependency in the base install is paid by everyone: it must resolve on every
supported platform and version, it can conflict with a consumer's own pins, and it is a
supply-chain surface. Convenience dependencies — a progress-bar library, typing backports for
older language versions, a compatibility shim — are the easiest to add and the least likely to
be needed by a given user, and they are hardest to remove later because they appear in the
public surface.

**Symptom.** A library used for one archive format drags in unrelated packages; a consumer
with a version conflict cannot install it at all.

**Evidence.** `dev-docs/history/COMPARISON.md:59,190,217` (hard dependencies on a progress-bar
library and two backports, dropped for a zero-dependency core, with the progress bar becoming a
callback used only by the command-line tool). Decided in ADR
[`0011-zero-dependency-core`](../../dev-docs/decisions/0011-zero-dependency-core.md).

**Answer today.** Zero hard dependencies in the core; everything optional sits behind a named
extra, progress is a callback the caller supplies, and a guard test asserts that every package
pinned in a user-facing extra is actually imported by some source path so a dead or test-only
dependency cannot re-enter an extra. ADR 0011; `library-analysis.md` §Test-only libraries.

**Sources.** `history/COMPARISON.md` §2, §4.7, §4.11; ADR 0011; `library-analysis.md`;
`openspec/changes/archive/2026-07-30-consolidate-optional-extras/`.

---

### PKG-07 — The supported language-version range is a moving constraint at both ends

**Problem.** The oldest supported version determines which backports are needed and which
standard-library capabilities are unavailable; it also has an end-of-life date, after which
supporting it costs dependencies for a shrinking audience. The newest version brings both new
standard-library modules that replace third-party dependencies (PKG-02) and new interpreter
builds with different concurrency semantics. Neither end holds still.

**Symptom.** Dependencies exist solely to paper over the oldest supported version, and code
carries version-conditional paths for capabilities that are unconditional a version later.

**Evidence.** `dev-docs/history/COMPARISON.md:215-218`: raising the floor "drops two backport
deps; 3.10 is EOL October 2026 anyway", against a matrix spanning four newer versions.
Version-dependent codec availability at `dev-docs/library-analysis.md:55`.

**Answer today.** A floor chosen so the backports disappear, with a continuous-integration
matrix across the supported range, and a codec's provider expressed as "standard library where
available, backport below" so the extra retires itself as the floor rises.
`history/COMPARISON.md` §4.11; ADR 0009.

**Sources.** `history/COMPARISON.md` §4.11; `library-analysis.md`; ADR 0009.

---

### PKG-08 — A guard installed into another library's namespace is visible to everyone else using it

**Problem.** Working around a defect inside a third-party library (UL-02) sometimes requires
changing that library's behaviour rather than one's own. Doing so at import time modifies
process-global state: any other code in the same process that uses that library sees the
modification, whether or not it wanted the fix. The alternative is to leave the defect in
place for callers who reach the library through this one.

**Symptom.** A program that uses both this library and the patched one directly gets subtly
different behaviour from the latter, and nothing in its own code explains why.

**Evidence.** `dev-docs/known-issues.md:35-50`: the guard is described as installed
"permanently" into the third-party library's own namespace, confined to it rather than a global
swap, with the trade named explicitly — "hang-safety on hostile input over leaving another
library's pycdlib untouched" — and the mitigating argument that the guard is a strict superset
of the library's behaviour on valid trees.

**Answer today.** The narrowest possible patch (that library's namespace, not a global),
installed once, documented as an irreducible visible consequence.
`known-issues.md`; `open-issues.md` §Irreducible.

**Sources.** `known-issues.md` §pycdlib; `open-issues.md` §Irreducible; `threat-model.md` O5.

---

### PKG-09 — An archive rebuilt from identical inputs is not byte-identical to the last build

**Problem.** Archive formats embed the moment of writing — creation timestamps in headers,
sometimes in the container metadata as well. Building the same content twice therefore
produces two different files. Any check of the form "rebuild it and compare the bytes" is
permanently unavailable, and a checksum over a stored archive proves only that the file was
not damaged in transit — something version control already guarantees. So the failure mode of
a stored test archive is *silent staleness*: the definition it was built from is edited, the
archive still sits there testing the old shape, and no hash of the bytes can notice.

**Symptom.** A test suite is green while testing something the project no longer describes.
Alternatively, a continuous-integration check that compares rebuilt bytes is red forever, for
a reason unrelated to any defect.

**Evidence.** ADR
[`0016-committed-rar-corpus-fixtures`](../../dev-docs/decisions/0016-committed-rar-corpus-fixtures.md)
§"Why the manifest, and not a hash of the bytes": "A regenerated RAR is also **never**
byte-identical to its predecessor, because archives embed timestamps. So 'rebuild and
compare' is not available as a CI check, and any scheme that assumed it would be perpetually
red."

**Answer today.** Each stored archive is pinned to a hash of its *definition* — the same
content key the on-demand builder caches on — rather than to a hash of its bytes, so the
check answers "were these bytes built from what the definition currently says?". A mismatch
is detected and never absorbed: rebuilt in place where the writer is available, and a hard
error naming the regeneration command anywhere else. Which formats are stored rather than
built is an explicit one-line set, so the precedent cannot drift. ADR 0016 (review finding
F16 / Q11 / O6).

**Sources.** ADR 0016; `investigations/rar-corpus-sweep-diagnosis.md`;
`review/archive/2026-08-15-simplicity-consistency/` (F16); `history/ARCHITECTURE.md` §2.8.

---

## Concurrency & lifetime

### CONC-01 — Most archives are one byte source, so two concurrent readers of different members contend for one position

**Problem.** An archive is usually a single file read through a single handle. Serving two
members at once means two consumers issuing seeks and reads against one position; each read
must be preceded by a seek to that member's own offset, and if the two interleave, each
receives bytes from the other's position. This is not a defect in any component — it is what a
single shared cursor means. Third-party readers frequently do exactly this, re-seeking on every
read with no synchronization of their own.

**Symptom.** Reading two members of one archive from two threads returns bytes belonging to
the wrong member, intermittently, with no error. The corruption is data-dependent and does not
reproduce under a debugger.

**Evidence.** `dev-docs/investigations/parallel-reader.md:44-60`: the standard tar reader's
member file object "re-seeks on each `read()`, **no lock**", and the ISO library's stream
object has "the same shape". The standard zip reader does coordinate seek and read internally,
but its reference-count updates race without the interpreter lock. Per-format parallelizable
units and their constraints at `:141-154`.

**Answer today.** Concurrency is a declared capability rather than an ambient promise: by
default one live member stream, no locks, and a second overlapping open fails fast as a usage
error, so accidental cross-thread sharing cannot race. Once declared, first-touch
materialization is coordinated (one builder, waiters share the published result) and the
shared-handle backends take one comprehensive per-reader lock covering open, listing reads,
member creation, reads, positioning and close — correctness guaranteed, handle operations
serialized. Reader-wide passes stay single-owner.
`concurrent-member-streams`, `reader-concurrency-coordination`, `tar-concurrent-open`;
`parallel-reader.md` §1, §4.

**Sources.** `investigations/parallel-reader.md`; `threat-model.md` C4; ADR 0003;
`openspec/changes/archive/2026-07-11-concurrent-member-streams/`;
`openspec/changes/archive/2026-07-11-tar-concurrent-open/`;
`openspec/changes/archive/2026-07-12-reader-concurrency-coordination/`;
`openspec/changes/archive/2026-07-12-shared-source-streams/`.

---

### CONC-02 — A single subprocess pipe carries every member's bytes in one undelimited stream

**Problem.** When member data comes from an external tool, the efficient invocation asks it for
everything at once and reads one pipe: one process for the whole archive rather than one per
member, which for a solid archive is the difference between one decode and *n*. But the pipe is
a single undelimited byte stream. Splitting it back into members relies entirely on the sizes
declared in the archive's own metadata, and on knowing exactly which members the tool emits
bytes for at all — link members may produce zero bytes, and whether they do depends on the
format version, because one version stores the target in the header and another stores it as
member data. Get the emission model wrong for one member kind and every subsequent member's
bytes are offset.

**Symptom.** Members after some particular kind of entry contain another member's data,
shifted, with checksums failing from that point on. Adding support for a new member kind
breaks members unrelated to it.

**Evidence.** `dev-docs/open-issues.md:207-216` (P6): "Solid ALL-pipe demux must match what
`unrar` actually emits (RAR5 symlink targets in header → 0 stdout bytes; RAR3 symlink targets in
LZ data → also 0 after decode). Easy to desync on new member kinds." The storage-shape
difference is measured in `investigations/rar-corpus-sweep-diagnosis.md:58-89` (FQ-18).

**Answer today.** The pipe is demultiplexed by header-declared sizes with each member's
checksum validated incrementally as it is read, so a desynchronization surfaces as a checksum
failure rather than silent wrong data. A shared emission table is named as the hardening;
**partly unresolved** (`open-issues.md` P6). `ARCHITECTURE.md` §7.4.

**Sources.** `open-issues.md` P6; `history/ARCHITECTURE.md` §5.7, §7.4;
`history/COMPARISON.md` §4.4; `investigations/rar-corpus-sweep-diagnosis.md`.

---

### CONC-03 — A stream handed to a caller can outlive the thing that produced it

**Problem.** A reader that returns a stream over a member has given away a reference to its own
internal state. The caller may hold it after closing the archive, drop it without closing it, or
close the underlying source themselves while it is live. Each has a different bad outcome:
tearing down resources under a live stream can be the exact operation that aborts the process
with some decoders (UL-05); leaving them alive indefinitely leaks descriptors; and invalidating
the stream contradicts a caller who was told it stays usable. The lifetime question has to be
answered for all three at once.

**Symptom.** A file descriptor per forgotten stream leaks for the life of the process; or
closing an archive crashes it; or a stream that worked yesterday raises today.

**Evidence.** `dev-docs/open-issues.md:98-160` (P7), measured on all seven backends: reading
after archive close succeeded everywhere and dropping a stream without closing leaked one
descriptor per stream, unchanged after three explicit collections. The competing considerations
are laid out there — a general principle against silently invalidating a held stream, the
decoder abort hazard running the other way, and the standard library doing the opposite
(UL-17).

**Answer today.** Backend teardown is deferred behind lifecycle leases until the last member
stream closes, so the source is never closed under a live stream; member streams are then closed
when the reader closes, matching the standard library; and the finalizer safety net was fixed
(UL-16) so a dropped stream is reclaimed. `2026-08-06-close-member-streams-on-reader-close`
(`open-issues.md` P7); `threat-model.md` C4.

**Sources.** `open-issues.md` P7; `threat-model.md` C4;
`investigations/parallel-reader.md` §4;
`openspec/changes/archive/2026-08-06-close-member-streams-on-reader-close/`;
`openspec/changes/archive/2026-07-30-member-stream-capability-booleans/`.

---

### CONC-04 — Archive-wide limits cannot be enforced from parallel workers

**Problem.** Guards that bound a whole operation — total bytes written, total entries, an
overall ratio — are cumulative counters by definition. Distributing the work that increments them
across workers makes every increment a contended update, and a limit that is checked after the
fact on each worker's local total does not bound the aggregate. The guard's correctness depends
on a property that parallelism removes.

**Symptom.** A parallelized extraction exceeds its own configured output limit, by a margin that
scales with the number of workers, without reporting that any limit was crossed.

**Evidence.** `dev-docs/investigations/parallel-reader.md:170-173`: "archive-wide limits
(`max_entries`, `max_extracted_bytes`, ratio guard) must stay on a single coordinator thread (or
a locked counter); workers only produce bytes / paths."

**Answer today.** The limits stay on a single coordinator; parallel extraction scheduling is
deferred, and the constraint is recorded as a design obligation for whenever it lands.
`parallel-reader.md` §6.

**Sources.** `investigations/parallel-reader.md` §6; `history/ARCHITECTURE.md` §4.2;
`threat-model.md` C4.

---

### CONC-05 — A native decompressor's threads are invisible to the language's own concurrency model

**Problem.** A decompressor implemented in C++ starts its own operating-system threads. The host
language's threading module does not know about them, cannot enumerate them, and cannot join or
interrupt them. Nothing that reasons about concurrency in host-language terms — a timeout, a
signal handler, a thread-count assertion, a test harness's cleanup — reaches them. A busy loop
inside one of those threads (SEC-18) cannot be interrupted at all.

**Symptom.** A process appears idle or hung with no host-language stack to inspect; timeouts do
not fire; and the interpreter's own shutdown sequence collides with work it does not know is
running (UL-03).

**Evidence.** `dev-docs/known-issues.md:73-79` ("spawn **C++ worker threads** (`std::thread`s,
invisible to Python's `threading` module)"); `dev-docs/investigations/parallel-reader.md:131-137`
(free-threading "does not remove the close-before-finalize requirement").

**Answer today.** Such objects are always explicitly closed via a finalizer guard (UL-03), never
have their source removed underneath them (UL-05), and are excluded from the defended parsing
surface for untrusted input (SEC-18); the free-threading position is that this constraint is
unchanged by the interpreter's own concurrency model. `known-issues.md`;
`parallel-reader.md` §4; `threat-model.md` C4.

**Sources.** `known-issues.md`; `investigations/parallel-reader.md` §4; `threat-model.md` O5, C4;
ADR 0008.

---

## Retired ids

None yet. When two entries merge, the surviving id keeps its number and the retired one is
listed here with a pointer, so a citation of the retired id still resolves.
