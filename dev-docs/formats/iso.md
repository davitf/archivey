# ISO 9660

Current maintainer truth for the ISO backend. An ISO image is not an archive of members;
it is a filesystem laid out in 2048-byte sectors, with one data area and up to three
directory trees pointing into it. Most of what is peculiar here follows from that, and
from reading it through `pycdlib`, a library written to *author* images. Registers keep
the status — this page states the behaviour and links the row.

## At a glance

| | |
| --- | --- |
| Read | Yes, through `pycdlib` |
| Write | **Not shipped**, for any format — no `archivey.create`, no writer module (`PLAN.md` phase 9) |
| Source | Seekable only, in both access modes. `start_offset` is refused: nothing precedes an image |
| Listing cost | `INDEXED`. The whole tree is parsed inside `open_archive()`, for every tree the image has; listing after that reads nothing, except to confirm a multi-extent file (§2.2) |
| Access cost | `DIRECT` — every file is one extent (or one run of extents) at an absolute sector |
| Stream capability | `SEEKABLE` |
| Core dependencies | None can read it: ISO needs `pycdlib`, which is in `[recommended]` |
| Refuses | Non-seekable sources · raw CD sector images (`.bin`), by name, before `pycdlib` is consulted · a multi-extent file whose extents are not back to back · writing |
| Accepts and ignores | `password=` (`PASSWORD_ARGUMENT_UNUSED`) · `encoding=` (`ENCODING_ARGUMENT_UNUSED`, §2.2) |

**Five things a reader might expect and will not find.** There is no integrity check
anywhere in the format, so a damaged image reads damaged bytes without an error. A
*truncated* one is caught only where the data runs out: sizes list as declared, and a
file cut by the end of the image reads what is there, then raises `TruncatedError` (§4).
UDF is
never read: a DVD or Blu-ray image lists through its ISO 9660 tree when it has one, and a
UDF-only image is not detected at all (§3). zisofs, the Rock Ridge transparent compression,
is refused as `CorruptionError` rather than as an unsupported feature (§5). `encoding=`
cannot fix a Rock Ridge name written in Latin-1, even when the Joliet tree beside it has
the right one (§2.2). And the namespace archivey picks decides which *files* exist, not
only how they are named, because the trees are independent (§1).

## 1. Shape

Four properties generate most of this page.

```
sector 0-15    system area (32 KiB): zeros, or boot code / a hybrid MBR
sector 16      Primary Volume Descriptor  "\x01CD001"  ── root record ──┐  8.3 names
sector 17+     Supplementary VD (Joliet)  "\x02CD001"  ── root record ──┤  UTF-16BE names
               Boot record (El Torito), UDF descriptors, terminator      │
path tables, directory extents                                           │
file data  ◀──── each directory record: extent (sector no.) + 32-bit length
```

**Every file is an extent at an absolute sector, and nothing checks it.** A directory
record names a starting sector and a byte length; the data is those bytes, stored, with
no header, no compression and no checksum. So random access is one seek (`DIRECT`), and
`compressed_size == size` for every file; a source must be seekable, since the tree is
read by jumping to extents; an image cannot start part-way through a file, because every
offset counts from byte 0 (`reject_start_offset`); and there is nothing to verify, so
`member.hashes` is empty and corruption in the data area is invisible. The length is a
32-bit field: a file of 4 GiB or more is stored as several records with one name, each
flagged *multi-extent* but the last, and a reader that stops at the first record reads
4 GiB and stops without an error (§2.3). Nothing stops two records from naming one
extent either — that is how writers store hardlinks — so two members can share bytes.

**One data area, up to three independent directory trees.** The Primary Volume
Descriptor carries a tree of plain ISO 9660 names; Rock Ridge, when present, lives inside
that same tree's records. A Joliet Supplementary Volume Descriptor carries a second tree
with UTF-16BE names. UDF, on DVD-era images, is a third. The trees share file data but not
entries: `genisoimage -hide` drops a file from the first tree and `-hide-joliet` from the
second. So choosing a namespace (§2.2) chooses a file set. Measured on one genisoimage
image built with both flags: the Rock Ridge listing had `nojoliet.txt` and not `nopvd.txt`,
and the Joliet-only build of the same tree had the reverse.

**Directory records form a graph that only a correct writer makes a tree.** A
subdirectory is a record whose extent holds more records, including `.` and `..`. Nothing
in the format prevents a record from pointing back at an ancestor, and ISO 9660 caps depth
at eight levels, so Rock Ridge relocates deeper subtrees into a root-level `rr_moved`
directory and relinks them with `CL`/`PL`/`RE` records. Both show up in a reader: a cyclic
image hangs a naive walk (§4), and a listing that follows the physical tree shows the
relocation scaffolding instead of the tree someone archived (§2.2).

**Everything a POSIX user cares about is an optional extension record.** Plain ISO 9660
names are upper-case d-characters with a `;N` version suffix, and a record holds one
7-byte date. Rock Ridge adds, in the record's System Use area (with a `CE` continuation
when it overflows), the real name (`NM`), mode and owner (`PX`), times (`TF`), symlink
target (`SL`), relocation links, and compression (`ZF`). The name is bytes with no
charset. So metadata fidelity depends on the namespace; a record in a Rock Ridge image may
still lack its entries; and the name encoding is whatever the writer's locale was.

## 2. The pipeline here

Each stage: who does the work, what is ISO-specific rather than general, what is refused.

### 2.1 Identify

Two magics and one extension. `CD001` at offset 32 769 — the PVD's identifier, one byte
into sector 16 — is *far* magic: it ends past the default 4 KiB detection window, so the
detector takes an extended peek on demand. It runs before the content probes, because the
32 KiB in front of it is where a bootable or hybrid image keeps boot code, which is the
kind of data a probe accepts; before that order was fixed, a Brotli probe hit on a
bootable image opened a whole filesystem as one fabricated member. The peek is skipped for
a source known to be shorter than the span, and the far tier has its own byte budget. See
the module docstring of `internal/detection.py`.

The second magic is the 12-byte CD sync pattern at offset 0. It is claimed as ISO only so
that `open_archive` can refuse a raw sector image by name — Mode 1, Mode 2 Form 1 or 2,
2352- or 2448-byte sectors — with `UnsupportedFeatureError`, before the availability check,
so a caller without `pycdlib` is not told to install it first
(`refuse_raw_sector_image`). The extension is `.iso`.

Neither magic is validated past the match; there is no SFX scan and no validator, because
an image cannot sit behind a prefix. A UDF-only image has `BEA01`/`NSR02` at 32 769 rather
than `CD001`, and High Sierra has `CDROM` at 32 777, so neither is detected.

### 2.2 Open and list

**`pycdlib` parses everything at open.** `IsoReader.__init__` hands `open_fp` the
`ArchiveSource` itself — a path included, never `PyCdlib.open(path)` — so every read
`pycdlib` sizes from a header field lands on archivey's bounded read (threat-model O16).
`open_fp` then reads every volume descriptor, checks that the little- and big-endian path
tables agree, and walks every tree the image has: the PVD tree, the Joliet tree, and UDF
descriptors when present. That is where the cost is. After it, listing touches only
records already in memory (`test_listing_reads_nothing_from_the_image`), which is what
lets the member walk run without the handle lock. The one exception is a directory
holding a repeated identifier: its extent is read once more, under the handle lock, to
check the multi-extent flags as written (§2.3).

**The namespace is picked once for the image: Rock Ridge, then Joliet, then plain.**
`ArchiveInfo.extra["iso.namespace"]` reports which. Rock Ridge counts as present when
`pycdlib` saw Rock Ridge entries in the PVD tree and an `ER` record naming RRIP; entries
without the `ER` are treated as a false positive. From then on every name comes from that namespace and that tree
(§1). 7-Zip prefers Joliet over Rock Ridge, so the two tools can list different names —
and, on an image built with `-hide` or `-hide-joliet`, different files.

**The walk follows records, not names** (`_walk_records`). `PyCdlib.walk()` yields names,
and turning a name back into a record fails when a Rock Ridge name holds `/` or two
entries share one, costing the whole listing. So the walk enumerates each directory's
children through `pycdlib`'s private `_yield_children` — the one place that skips a
multi-extent file's extra records and follows Rock Ridge relocation — and renders a path
from the record. Each directory extent is entered once. Within a directory, subdirectories
come first, then files, in record order, except that plain ISO 9660 files sort by
(name, version).

What is ISO-specific in turning a record into a member:

- **Names.** Rock Ridge `NM` bytes and plain identifiers decode as UTF-8 with
  `surrogateescape`; Joliet decodes as UTF-16BE with U+FFFD for anything invalid. Decoding
  never raises. `encoding=` is registered as unused for ISO, so it cannot correct a Rock
  Ridge name written in another charset (§5). Backslash is an ordinary character.
  `raw_name` is the namespace path re-encoded as UTF-8, not the bytes on disc.
- **Versions.** In the plain namespace the `;N` suffix and the `.` of an empty extension
  are removed (`FOO.;1` is `FOO`) and the number goes to `extra["iso.version"]`. When a
  directory holds several versions, the highest takes the bare name; older ones are
  listed under their stored identifier with `is_current=False`, the RAR file-history
  shape, so extraction writes only the newest.
- **Rock Ridge gaps.** A record with no Rock Ridge entries in a Rock Ridge image is kept
  under its ISO 9660 name, with `MEMBER_HEADER_RECORD_SKIPPED` attached to it. The
  `rr_moved` directory is not listed: it is recognised by its contents (every child is a
  relocated directory whose `..` carries `PL`), not by its name, and its subtrees appear
  where they belong.
- **Type.** An `SL` record makes a symlink, the directory flag a directory, and a `PX`
  mode naming a device, FIFO or socket makes `OTHER`, whatever bytes sit at the extent.
  Everything else is a `FILE`. There is no `HARDLINK`: records sharing an extent are
  independent files. Records with the hidden flag are listed like any other.
- **Size.** The sum of the lengths of every extent record of the file. `compression` is
  one `STORED` entry. Directories and links have `size=None`.
- **Times.** `modified` is the Rock Ridge `TF` modification time when there is one, else
  the record's 7-byte date; `TF` also supplies `accessed`. `TF` long-form dates (17 bytes,
  hundredths of a second) are read; MagicISO's out-of-range hundredths become 0. A date
  that is all zeros or invalid is `None` rather than an error. `created` is set only from
  a `TF` creation time, which few writers record; the `TF` attribute-change time (POSIX
  `st_ctime`) goes to `extra["iso.ctime"]` and never to `created`. That is the rule after
  PR #470; before it, `created` fell back to the attribute-change time.
- **POSIX fields.** `mode` (permission bits only), `uid` and `gid` come from `PX`, and
  are `None` outside Rock Ridge. `link_target` is the `SL` path.
- **Archive info.** `comment` is the volume identifier. `format_version` is `None`:
  ISO 9660 stores no interchange level, and `pycdlib`'s inferred one read 3 on nearly
  every image because it counts the `.`/`..` identifiers as non-d-characters.
  `member_count` is `None`.

A bootable image's **El Torito boot catalog** (`boot.catalog`, `BOOT.CAT`) is listed as
an ordinary 2048-byte file, as 7-Zip lists it. archivey does not add 7-Zip's synthetic
`[BOOT]/…` entries for the boot images themselves; a boot image that is also a file in the
tree is listed there.

### 2.3 Member data

Reading a file is a `PyCdlibIO` over an inode, wrapped as `_PyCdlibStream` and entered at
open. There is no decoding, no password and nothing to verify. Seeking is supported when
`seekable_members=True`.

The inode is `pycdlib`'s own when it covers the file. Two kinds of record need one built
here, over the extents read straight from the image (`_data_inode`):

- **A multi-extent file.** In parse mode `pycdlib` gives each extent its own inode and
  links the records through `data_continuation`, so the first record's inode covers only
  the first extent. archivey sums the chain and reads it as one run. Writers put the
  extents back to back — xorriso and libarchive's fixture both do — and the ISO 9660
  standard does not require it, so a chain with a gap is refused with
  `UnsupportedFeatureError` rather than read as one run.

  `pycdlib` builds that chain for *any* record whose identifier repeats the previous one
  in its directory, and sets the multi-extent flag on the earlier record in memory while
  doing it. So the in-memory flags cannot tell a real multi-extent file from two
  unrelated files that share a name. archivey re-reads the directory's extent, only when
  a chain exists, and keeps the chain only if every record but the last carries the flag
  as written (`_layout`). Otherwise the member is its first record alone, and the
  duplicate stays hidden, as it always was.
- **The boot catalog.** `pycdlib` keeps it in memory and gives its record no inode. Its
  extent still holds the bytes, which is what a mounted image shows. Because it has no
  inode, `pycdlib` never clamped its length to the image either; the inode built here
  stops at the end of the image, like any other (§4).
- **A file cut by the end of the image.** `pycdlib` clamps a file whose data runs past
  the end of the image to end there, and overwrites the declared length with the clamped
  one on every record sharing the inode: zero when the extent starts at the end, negative
  when it starts past it. So a clamped record is one whose data ends exactly at the end
  of the image. For those alone, archivey re-reads the directory's extent and takes the
  declared length from the record on disc (`_parse_raw_directory`, keyed by extent and
  identifier). A record not found there keeps length 0 if it has it (an empty file whose
  extent sits at the image end); otherwise `size` is `None`. Reading such a file returns the bytes the image holds and then
  raises `TruncatedError`.

`MemberStreams.CONCURRENT` puts one per-reader lock around everything that moves
`pycdlib`'s shared image handle: `PyCdlibIO` construction and entry, every read and seek,
close. Correctness only: the lock serialises parallel reads of one image.

### 2.4 Extract

Nothing here is ISO-specific. Path safety, link handling, collisions and limits are the
shared extraction machinery
([`safe-extraction`](../../openspec/specs/safe-extraction/spec.md)). Three ISO facts reach
it through member fields rather than code: `OTHER` members (device nodes, FIFOs, sockets)
are skipped; superseded plain-ISO versions are not current and are not written; and
records that share an extent are written once each, as separate files.

### 2.5 Write

Not shipped, for any format. `pycdlib` can author images and the test suite uses it to
build every ISO fixture, but nothing in archivey writes one.

## 3. In the wild

**The corpus sees only `pycdlib`'s own output.** Every ISO in `tests/` is written by
`pycdlib` (`sample_archives._iso_build` and the builders in `tests/test_iso.py`), so the
suite exercises the library reading what it writes. Real producers differ. Measured on
genisoimage 1.1.11 and xorriso 1.5.6, and on the ISO images in libarchive's test
directory:

| Image | archivey |
| --- | --- |
| genisoimage and xorriso, plain / Joliet / Rock Ridge / both, with a symlink, a hardlink, a FIFO, a hidden file, a 10-level tree | Reads. The FIFO lists as `OTHER` under Rock Ridge and as an empty `FILE` elsewhere; the plain and Joliet trees stop where genisoimage stopped writing them (depth 8 without `-D`) |
| Bootable (`-b … -no-emul-boot`), either tool | Reads, boot catalog included |
| A 4 400 MiB file, `xorriso -as mkisofs -iso-level 3` | Reads all of it, in two extents (§2.3) |
| libarchive `test_read_format_iso_multi_extent` | Reads 262 280 bytes from three extents |
| libarchive Joliet, Rock Ridge, `rr_moved`, `CE`, Nero Joliet, xorriso images | Read |
| zisofs: `mkzftree` + `genisoimage -R -z`, libarchive `test_read_format_iso_zisofs` | `CorruptionError: … Unknown SUSP record` — `pycdlib` has no `ZF` support |
| genisoimage `-R`, a symlink target of 12 × 30-character components | `CorruptionError: … Invalid RR version 118!` for the whole image. `isoinfo` and `xorriso` both open it and show the target cut to four components, so genisoimage likely wrote the continuation wrongly |
| libarchive's crafted `ce_loop`, `ce_overflow`, `cl_re_*`, `utf16be_overflow`, `zf_overflow` images | `CorruptionError`, each at open |

**Every installer image is bootable**, which is why the boot catalog is not a corner: until
it was read from its extent, opening it raised `CorruptionError` and `extract_all()`
stopped on it.

**DVD-era images often carry UDF next to ISO 9660** (`genisoimage -udf`, and DVD-Video
discs), and archivey reads only the ISO 9660 side. On an image whose ISO tree carries only
8.3 names and whose long names live in UDF, the listing shows the 8.3 names. A Blu-ray
style UDF-only image has no `CD001` and is not recognised at all.

**A `.bin`/`.cue` pair is a raw sector dump**, not an ISO, and is refused by name (§2.1).
Reading one means stripping every sector to its payload first; it is planned for after
0.2.0.

**Rock Ridge names follow the writer's locale.** `genisoimage -input-charset iso8859-1`
stores `café.txt` as `caf\xe9.txt` in the Rock Ridge `NM` record and as proper UTF-16 in
Joliet; archivey lists `caf\udce9.txt` (§5).

## 4. Threat surface

ISO-specific only. General extraction and name hazards are §2.4.

- **The directory graph can close a cycle.** `pycdlib`'s own parse walk (inside
  `open_fp`, over every tree) has no visit tracking and loops forever on a record that
  points back at an ancestor — the mutation harness found it with one flipped bit. Two
  guards cover it: `_install_pycdlib_directory_cycle_guard` replaces `collections` inside
  `pycdlib.pycdlib` with a proxy whose `deque` drops an already-scheduled extent, and
  archivey's own `_walk_records` enters each extent once. The first is process-global
  within `pycdlib` and is written up in [`known-issues.md`](../known-issues.md); the
  mutation-harness finding is in [`threat-model.md`](../threat-model.md).
- **A directory's length sizes `pycdlib`'s read.** `pycdlib` clamps a file's length to the
  image but not a directory's, so a root record declaring 4 GiB asked for 4 GiB inside
  `open_archive()`. Closed by routing every read through the source's bound (O16).
- **`pycdlib` is not hardened against crafted input.** Beyond its own exception type it
  raises bare `IndexError`, `struct.error`, `UnicodeDecodeError`, `AttributeError`,
  `KeyError` and `ValueError` from its parsers. All of them are `CorruptionError` at the
  boundary; an `OSError` from the handle is not, and propagates unchanged.
- **Records that share an extent multiply output.** N records naming one extent extract
  N copies, and the per-member ratio stays 1 because each is `STORED`. The bound is
  `ExtractionLimits.max_extracted_bytes`, not the ratio guard.
- **Nothing authenticates the data; truncation shows only where the data runs out.**
  With no checksum there is no way to tell a damaged file from an intact one. A cut image
  opens as long as its directories survive, lists the sizes its records declare, and
  raises `TruncatedError` when a read reaches the cut (§2.3). A file wholly before the
  cut reads normally, which is what a partial download can still offer. The image-level
  signal, a volume space size larger than the source, is not used.
- **Rock Ridge names and link targets are attacker-chosen bytes.** A name can hold `/`,
  `..` or control characters, and a target can be absolute. The record walk keeps a `/`
  in a name from costing the listing; everything else is the shared name normalisation and
  link policy.

## 5. Sharp edges

*Where it lives*: **format** — inherent, no implementation fixes it · **library** — an
upstream library's behaviour, fixable only there or by replacing it · **archivey** — ours.

| What you see | Where it lives | More |
| --- | --- | --- |
| A truncated image (an interrupted download) opens and lists, and only the files the cut reaches fail, with `TruncatedError` after the bytes that survive | **format** | Nothing in the image says it is complete except the volume space size, which is not checked; files before the cut read normally (§4) |
| A damaged image returns damaged bytes | **format** | No checksum exists anywhere in ISO 9660 (§4) |
| A zisofs image fails with `CorruptionError: … Unknown SUSP record` | **library** / **archivey** | `pycdlib` does not know the `ZF` entry and refuses the image. The image is valid, so the error should at least name zisofs as unsupported. Tracked internally |
| One malformed Rock Ridge record fails the whole image | **library** | `pycdlib` parses every record at open, and any parse error is fatal. Seen on genisoimage's long symlinks (§3). Tracked internally |
| A Rock Ridge name written in Latin-1 lists with `\udcXX` escapes, and `encoding=` is declared unused | **archivey** | Rock Ridge names have no charset field; archivey assumes UTF-8 and does not take the hint, nor fall back to the Joliet name. Tracked internally |
| A file exists in the listing of one tool and not another | **format** | The trees are independent (§1). archivey picks Rock Ridge first, 7-Zip Joliet first |
| A DVD image lists 8.3 names while the disc shows long ones | **archivey** | Long names are in UDF, which is not read (§3) |
| A multi-extent file with non-contiguous extents raises `UnsupportedFeatureError` on read | **archivey** | Its `size` is right; reading it would need a chained stream rather than one run. No writer seen does this (§2.3) |
| Two members share their bytes, and extraction writes both | **format** | Hardlinks are records sharing an extent; there is no hardlink record to map to `HARDLINK` |
| Opening a large image is slow and listing is instant | **library** | `open_fp` parses every tree up front (§2.2). The cost class `INDEXED` is still right: nothing is decoded |
| A program that also uses `pycdlib` sees archivey's guarded `deque` inside it | **archivey** | The cycle guard is installed once, at import, in `pycdlib`'s namespace ([`known-issues.md`](../known-issues.md)) |
| `password=` or `encoding=` is accepted and has no effect | **archivey** | Dropped with `PASSWORD_ARGUMENT_UNUSED` / `ENCODING_ARGUMENT_UNUSED` — shared behaviour, not ISO's own |

## 6. Decisions

| Choice | Why | Rejected |
| --- | --- | --- |
| Read through `pycdlib`, an optional dependency | ISO 9660 plus Rock Ridge, Joliet and El Torito is a lot of parser for a format that stores no compressed data; `pycdlib` is pure Python and installs everywhere | A native parser (reconsider if the §5 library rows accumulate); `libarchive` bindings, which are native code outside the defended surface |
| Pick one namespace per image, Rock Ridge then Joliet then plain | Rock Ridge carries the most: long names, POSIX metadata, links. Reporting the choice lets a caller reason about fidelity | Merging the trees, which have different file sets; Joliet first, as 7-Zip does, which loses modes and links |
| Walk records, not names | A name that does not round-trip through `pycdlib`'s path lookup cost the whole listing, and a duplicated directory name re-walked its subtree | `PyCdlib.walk()` plus `get_record()` per name |
| Call `pycdlib`'s private `_yield_children` | It is the one place that skips extra multi-extent records and follows relocation; the tests run it on every supported `pycdlib` | Reimplementing both, which duplicates the fragile part |
| Hide `rr_moved` by its contents | The name is a writer convention, and a real directory may carry it | Hiding by name |
| List old plain-ISO versions under their stored identifier, not current | Same shape as RAR file history, so extraction writes the newest and nothing is lost from the listing | Dropping old versions; listing all under one name |
| Read a multi-extent file as one run, and refuse a gap | Every writer seen writes the extents back to back, so one inode covers them; a gap is refused rather than read wrong | A chained stream over per-extent inodes, not needed by any image seen |
| List the boot catalog as an ordinary file, read from its extent | It is a file in the tree, and 7-Zip lists it the same way; its bytes are on disc | Hiding it; synthesising 7-Zip's `[BOOT]` entries |
| `format_version` is `None` | ISO 9660 stores no level; `pycdlib`'s inference read 3 on nearly every image | Passing the inference through |
| Patch `pycdlib`'s `collections` once, at import | A crafted image otherwise hangs `open_fp` forever, and the patch is confined to `pycdlib`'s namespace and inert on valid trees | A per-open swap, which races between threads; a watchdog timeout |
| `created` holds only a `TF` creation time | `created` never holds `st_ctime`; the attribute-change time goes to `extra["iso.ctime"]` | Falling back to the attribute-change time, as before PR #470 |
| A clamped file lists its declared length, read back from the directory record, and fails its read at the cut | A partial download keeps every file before the cut readable, and the listing says what the file should hold; the lookup runs only for records ending exactly at the image end | `size=None` for clamped files; refusing at open when the volume space size exceeds the source, which also refuses the files that survived |

## 7. Open questions

- **Whether to take `encoding=` for Rock Ridge names, or fall back to Joliet.** Both
  change the names a caller sees on images that list today. What would answer it: how
  common non-UTF-8 Rock Ridge images are in practice. Tracked internally.
- **Whether UDF should be read.** `pycdlib` parses UDF already, so listing from it is
  reachable; the question is whether DVD and Blu-ray images are in scope for 0.2.x.

## 8. Verify

```bash
./scripts/test.sh tests/test_iso.py tests/test_iso_raw_sectors.py \
    tests/test_corpus_sweep.py -k "iso or ISO"
```

| Claim | Pinned by |
| --- | --- |
| Detection by far magic; a bootable image is not taken by a probe; a short source falls through | `tests/test_detection.py::test_iso_detected_via_extended_window`, `::test_bootable_iso_is_not_claimed_by_the_content_probe`, `::test_zeroed_system_area_iso_still_detected`, `::test_stream_too_short_for_iso_falls_through`; `tests/test_iso.py::test_iso_detected_by_extended_window` |
| Raw sector images refused by name, without `pycdlib` | `tests/test_iso_raw_sectors.py` |
| Cost, seekable-only, write refused, password unused | `tests/test_iso.py::test_iso_cost`, `::test_non_seekable_iso_rejected`, `::test_write_rejected`, `::test_password_is_accepted_and_recorded` |
| Namespace selection and metadata per namespace | `::test_rock_ridge_namespace_and_fidelity`, `::test_joliet_namespace_and_fidelity`, `::test_plain_iso_namespace_and_fidelity` |
| Record walk: `/` in a name, duplicate names, cycles, `rr_moved`, a record without Rock Ridge | `::test_a_rock_ridge_name_holding_a_slash_costs_no_sibling`, `::test_duplicate_rock_ridge_names_all_list`, `::test_the_record_walk_descends_each_directory_extent_once`, `::test_rock_ridge_relocation_directory_is_not_listed`, `::test_a_rock_ridge_record_without_entries_lists_under_its_iso_name` |
| Device node is `OTHER`; plain versions keep the newest current | `::test_a_rock_ridge_device_node_is_other_not_file`, `::test_plain_iso_versions_keep_the_newest_current` |
| `TF` long-form dates; `TF` wins over the record date | `::test_rock_ridge_long_form_tf_time_is_read`, `::test_rock_ridge_tf_modification_time_wins_over_record_date` |
| Boot catalog reads and extracts, and one declared past the image end reads short | `::test_the_el_torito_boot_catalog_reads_and_extracts`, `::test_a_boot_catalog_declared_past_the_image_end_reads_short` |
| Multi-extent size and data; a gap refused; a repeated identifier without the on-disc flag is not a chain; the raw directory walk crosses sector padding | `::test_a_multi_extent_file_lists_and_reads_every_extent`, `::test_a_multi_extent_file_with_a_gap_is_refused`, `::test_a_repeated_identifier_without_the_flag_is_not_one_file`, `::test_the_raw_directory_walk_crosses_sector_padding` |
| No interchange-level guess | `::test_format_version_is_not_pycdlibs_guess` |
| Cycle guard in `pycdlib`'s own walk, in all three trees | `::test_pycdlib_directory_cycle_does_not_hang` |
| Directory length bound; path sources go through the source; handles released on failure | `::test_directory_data_length_does_not_drive_the_allocation`, `::test_a_path_source_is_read_through_the_archive_source`, `::test_a_refused_path_source_does_not_hold_its_handle`, `::test_a_failure_after_open_fp_is_translated_and_releases` |
| Corrupt input is `CorruptionError`; handle `OSError` is not | `::test_corrupt_iso_raises`, `::test_filesystem_oserror_propagates_unwrapped` |
| Listing reads nothing after open, on an image with no repeated identifier and no file ending at the image end | `::test_listing_reads_nothing_from_the_image` |
| Concurrent reads under the lock | `tests/test_concurrent_multithread.py::test_multithread_iso_open_read`, `tests/test_locked_stream.py::test_tar_iso_concurrent_open_uses_lock` |
| Cross-format equivalence (`basic`, `encoding`, `symlinks`, Rock Ridge and Joliet-only) | `tests/test_corpus_sweep.py` |
| A truncated image lists declared sizes and raises `TruncatedError` at the cut, in the ISO 9660 and Joliet trees | `::test_a_truncated_image_lists_declared_sizes_and_reads_to_the_cut` |

**Building fixtures.** The suite builds every image with `pycdlib` (`PyCdlib.new`,
`add_fp`, `add_directory`, `add_symlink`, `add_eltorito`), and shapes `pycdlib` will not
write are made by patching bytes: the multi-extent tests split one directory record in two
(`_split_into_two_extents`). To check against real producers, `genisoimage` and `xorriso`
install from the distribution (`apt-get install genisoimage xorriso`), and `mkzftree`
comes with genisoimage for zisofs. A file over 4 GiB needs no disk: make it sparse with
`truncate`, pipe `xorriso -as mkisofs -iso-level 3 -o -` into a writer that seeks over
zero blocks, and the image is sparse too. libarchive's ISO fixtures live in
`libarchive/test/*iso*.uu` (uuencoded, most also `.Z`-compressed);
`tests/test_libarchive_corpus.py` reads them only as nested `.Z` streams, not as ISO.

## 9. References

- ECMA-119 (ISO 9660), 4th edition: §8.4 Primary Volume Descriptor, §9.1 directory
  record (§9.1.6 file flags, bit 7 multi-extent), §7.5 file identifier and version, and
  the eight-level directory depth limit
- IEEE P1281 (SUSP) and P1282 (Rock Ridge, RRIP 1.12): `CE`, `NM`, `PX`, `TF`, `SL`, `CL`,
  `PL`, `RE`
- Joliet specification (Microsoft, 1995): the Supplementary Volume Descriptor escape
  sequences and UCS-2 names
- El Torito Bootable CD-ROM Format Specification 1.0: the boot catalog and its
  validation entry
- zisofs: the `ZF` entry, documented in the zisofs-tools and libisofs sources
- [`pycdlib`](https://github.com/clalancette/pycdlib)
- Specs: [`format-iso`](../../openspec/specs/format-iso/spec.md) ·
  [`format-detection`](../../openspec/specs/format-detection/spec.md)
- Code: `internal/backends/iso_reader.py` (reader, raw-sector refusal, cycle guard) ·
  `internal/detection.py` (far magic)
- Registers: [`threat-model.md`](../threat-model.md) O16 and the cycle finding ·
  [`known-issues.md`](../known-issues.md) (the process-global `pycdlib` patch)
- Handbook: [`zip.md`](zip.md) · [`7z.md`](7z.md) · [`rar.md`](rar.md) (the file-history
  shape plain ISO versions borrow)
- User-facing: [`docs/formats.md`](../../docs/formats.md#iso-9660)
