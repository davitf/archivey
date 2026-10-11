# Device-flag entries with data, and directory entries with data (2026-10-10)

Investigation for two questions the 2026-10-10 code sweep left open: a device, FIFO or
socket entry that carries data, and a directory entry that declares data. Research only;
the sweep's pull requests (#683, #698, #706) kept the earlier behaviour until the maintainer
ruled on this report (2026-10-10, both recommendations accepted; see
[`design-rules.md`](../design-rules.md) DR-25, applied by PR 730 and PR 731). The raw tool
output and the scripts are kept with the project's working files, not in the repo. Every
crafted fixture named below is rebuilt by the script under "Reproduce"; the four real-tool
fixtures by the two command lines there. Every `file:line` is as of archivey `a5aba3e`,
the commit measured; `design-rules.md` is cited by rule, since this report's own pull
request moves its lines.

Tools measured here: Info-ZIP zip 3.0 / unzip 6.00, 7-Zip 23.01 (Linux), bsdtar 3.7.2
(libarchive), GNU tar 1.35, rar / unrar 7.00, Python 3.13.16 (`zipfile`, `tarfile`),
py7zr 1.1.3, archivey main at a5aba3e. Source read where a tool could not be run on a shape:
7-Zip `7zIn.cpp`, `ZipItem.cpp`, `TarIn.cpp`, `ArchiveExtractCallback.cpp`; libarchive
`archive_read_support_format_zip.c`, `archive_read_support_format_tar.c`,
`archive_write_disk_posix.c`; unrar `arcread.cpp`, `extract.cpp`; Go `archive/zip`.

## Recommendations in short

**Q1 (device, FIFO or socket mode on an entry that carries data).** Take option B, with
the rule stated as a structural one, not a size threshold: in ZIP, 7z, RAR and ISO the
Unix file-type bits are an *attribute*; the entry's *data stream* is structure. An entry
with a data stream is `FILE`; a special-mode entry with no stream is `OTHER` (the 2026-10-06
ruling, whose reason "useless as files" only ever applied to the stream-less case). TAR
is different and needs no change: its typeflag is structure, GNU tar and libarchive ignore
the size of a device entry, so a device header followed by data is damage there, and
archivey already raises `CorruptionError` like GNU tar exits 2. Surface the stored type in
every format with one cross-format `extra` key, say `extra["special_file_type"]`
(`"fifo"`, `"char_device"`, `"block_device"`, `"socket"`, `"unknown"`), set on *every*
member whose stored type is special, whether it ended up `OTHER` or `FILE`. Add one
advisory diagnostic for the data-bearing case so the CLI and `on_diagnostic` show it;
I would not put it in the strict refusal set, because a real producer writes this shape
(Info-ZIP `zip -FI`) and nothing is hidden by delivering the bytes.

**Q2 (directory entry that declares data).** Take option B, extended: the member stays a
`DIRECTORY` (every official tool, and PR #683's "type follows the name"); `size` keeps the
declared byte count (7-Zip and unzip list it too); a per-member diagnostic, say
`MEMBER_DIRECTORY_DATA_IGNORED`, fires in ZIP, TAR and RAR, is a warning by default and is
refused under `DiagnosticPolicy.STRICT` (the ZIP spec forbids the shape, APPNOTE 4.3.8);
and the bytes stay reachable: `open()`/`read()` stop refusing by *type* and refuse only a
member that has *no data* (an anti item, a data-less `OTHER`, a directory with size 0 or
`None`). Extraction keeps creating the directory. This puts the case at the top of DR-1's
ranking (nothing lost, everything reported) instead of "silent loss", and gives the user who
needs the bytes a way in without a new API name. Fire only on declared uncompressed size
greater than 0: a deflated empty directory body (compressed size 2, uncompressed 0) is a
shape Go's `archive/zip` had to learn to tolerate, attributed in its source to the Java
`jar` tool (the `jar` 21.0.12 in this container stores directories with both sizes 0, so
the attribution is Go's, not a measurement here; §Sources). Also re-type the
TAR `REGTYPE` entry named `d/` with data to `DIRECTORY` under the same rule (GNU tar and
7-Zip do; today archivey makes it the `FILE` `d`, the inconsistency sweep note zip-10 found).
7z has no "directory with data" shape at all (a record with a stream is never a directory
in the format; PR #698 is right), and ISO directories' extents *are* their data, so neither
needs this.

Both answers come from one sentence that could go into `design-rules.md`: **type comes
from the format's structure (name marker, typeflag, directory flag, stream presence); a
mode attribute never overrides structure; when structure is silent, the data decides; and
what the archive said about the type is always visible in `extra`, while bytes extraction
does not deliver are reported with a diagnostic and reachable through `open()`.**

## Q1. Special-mode entry with data

### Is it real or crafted?

**Real, in ZIP.** Info-ZIP `zip` has `-FI` ("Zip now can read Unix FIFO (named pipes). Off
by default to prevent zip from stopping unexpectedly on unfed pipe, use -FI to enable",
`zip -h2`). It reads the pipe's content *and* stores the pipe's own `st_mode`:

```
$ mkfifo pipe1; (echo "hello from fifo" > pipe1 &); zip -FI zip_fifo_FI.zip pipe1
$ zipinfo -v zip_fifo_FI.zip | grep -E "attributes|uncompressed"
  uncompressed size:                              16 bytes
  Unix file attributes (010644 octal):            prw-r--r--
```

libarchive knows this shape well enough to carry a dedicated workaround in
`zip_read_local_file_header`:

```c
/* Work around a bug in Info-Zip: When reading from a pipe, it
 * stats the pipe instead of synthesizing a file entry. */
if ((zip_entry->mode & AE_IFMT) == AE_IFIFO) {
    zip_entry->mode &= ~ AE_IFMT;
    zip_entry->mode |= AE_IFREG;
}
```

So "FIFO mode plus data" is a shape a mainstream writer produces on purpose, from a
documented option, and the user's intent is unambiguous: they wanted the bytes that came
through the pipe. Without `-FI`, `zip` skips FIFOs and devices ("Nothing to do!", rc 12);
`rar a` skips them (rc 10, "no files to add"); GNU tar, bsdtar and 7-Zip store a device or
FIFO entry with size 0 (verified on `/dev/null` and a FIFO; 7-Zip lists `Attributes = A
prw-r--r--`, `Size = 0`). Device or socket mode *with* data has no known producer in any
format; only a hand-built header carries it.

### What readers do with it

Fixtures: `zip_fifo_FI.zip` (real), `zip_chr_data.zip` / `zip_fifo_data.zip` (stdlib
`ZipInfo.external_attr`, the same construction as `tests/test_zip.py:1870-1883`),
`tar_chr_data.tar` / `tar_fifo_data.tar` (stdlib `TarInfo` with `size > 0`). Each crafted
archive has a normal `after.txt` behind the odd entry.

| Reader | ZIP: FIFO or char device with data | TAR: device or FIFO typeflag with data |
| --- | --- | --- |
| unzip 6.00 | Regular file with the bytes, rc 0 | — |
| 7-Zip 23.01 | Regular file with the bytes (`7z l`: `Attributes = crw-r--r--`, `Size = 28`) | Regular file with the bytes, rc 0 (keeps the size for typeflags 3/4/6; `TarIn.cpp` zeroes it only for `5` and `1`) |
| bsdtar 3.7.2 | FIFO: regular file with bytes (workaround above). Char device: **`mknod` as root**, data dropped (`archive_write_disk_posix.c`: `fd < 0` so size set to 0) | Creates the node, ignores the size (`header_common` zeroes size for `2`,`3`,`4`,`5`,`6`), then "Damaged tar archive, Retrying", finds `after.txt` |
| GNU tar 1.35 | — | Creates the node, ignores the size, "Skipping to next header", lists `after.txt`, exit 2 |
| Python `zipfile` | `read()` returns the bytes; `extractall` writes a regular file | — |
| Python `tarfile` | — | Lists size 28; `extractfile` → `None`; `data` filter raises `SpecialFileError`; the data blocks are not skipped, so the next header is misread |
| archivey main | `OTHER`, `size=16`, `mode=0o644`; `read()` → `ArchiveyUsageError` "type is 'other'"; `extract_all` → `BLOCKED` "Special file (device/FIFO/socket) not allowed" | Lists the device, then `CorruptionError` at the end (tarfile desync; the same failure GNU tar reports) |

7z and RAR could not be measured on a crafted shape in this thread (see "Not done"). From
source: 7-Zip's `ZipItem.cpp` `IsDir()` consults the Unix high word only for `S_ISDIR`,
and `ArchiveExtractCallback.cpp` has no `mknod`/`mkfifo` path, so a 7z or ZIP entry with a
device mode and a stream is written as a regular file, exactly as PR #698 observed for the
`S_IFDIR` shape. unrar's `extract.cpp` has no device-creation path either; its only
`IsDevice()` check is on the destination *name* (Windows reserved names). py7zr extracts a
stream-less FIFO entry as an empty regular file.

### What archivey does today, and where

Paths are under `src/archivey/`; lines as of `a5aba3e`.

- Type: `internal/backends/zip_reader.py:999-1002`, `internal/backends/sevenzip_reader.py:849-861`,
  `internal/backends/rar_reader.py:2850-2857`, `internal/backends/tar_reader.py:455-465`,
  `internal/backends/iso_reader.py:1626-1632`; shared test `internal/unix_mode.py:16-28`
  ("`OTHER`, whatever bytes it stores").
- `open()` refuses by type: `internal/base_reader.py:2756-2760`. Extraction refuses `OTHER`
  in the universal filter: `internal/filters.py:320-324`; the writer treats it as
  unreachable: `internal/extraction.py:1826-1831`.
- The file-type bits are dropped: `ArchiveMember.mode` is `S_IMODE` only
  (`types.py:677-678`; `internal/backends/zip_reader.py:975`,
  `internal/backends/sevenzip_reader.py:735`, `internal/backends/rar_reader.py:2602`,
  `internal/backends/tar_reader.py:1525`, `internal/backends/iso_reader.py:1656`). The
  only format that keeps *which* special type is TAR, through `extra["tar.type"]`,
  `tar.devmajor`, `tar.devminor` (`types.py:563-569`). `size` is kept for ZIP/7z/RAR
  `OTHER` (spec `openspec/specs/archive-data-model/spec.md:109`) but `None` for TAR
  `OTHER` (`internal/backends/tar_reader.py:1520`), a small DR-5 gap of its own.
- Rulings: `design-rules.md`, the "Is the tool's result useful?" factor under "When
  consistency and the official tool disagree", and the DR-5 ruling list ("useless as
  files", 2026-10-06, PR 610).

### Weighing it

- **Is the tool's result useful?** For a stream-less FIFO, no: unzip's empty file hides
  what the archive said, which is why the 2026-10-06 ruling chose `OTHER`. For an entry
  with data the tool's result *is the content*; archivey's `BLOCKED` is the useless
  outcome, and on a real `zip -FI` archive it is the odd one out against unzip, 7-Zip,
  bsdtar and `zipfile`.
- **Would it break a promise?** No. Writing a regular file is what every policy level
  already does for a `FILE`; nothing special is created on disk (archivey never calls
  `mknod`, and the bsdtar-as-root result above is a reminder of why).
- **Could the lenient behaviour hide bytes?** The opposite: `OTHER` is what hides them.
  DR-1 ranks refusal above silent loss, but delivering checked bytes beats both.
- **Only crafted archives?** No for ZIP FIFOs. Yes for devices and sockets, and for 7z,
  RAR and ISO; there the least-code answer is to apply the same rule rather than a
  per-format exception (DR-0, DR-5).
- **Plumbing across layers?** Small: one condition in each `_member_type`, one `extra`
  key, one diagnostic.

On the worry that the rule "relies on the size": the test is *does the entry have a data
stream*, which is a structural fact in each container (ZIP: declared uncompressed size;
7z: `HasStream`, which is how 7-Zip itself decides "not a directory"; RAR: the header's
data size; ISO: the extent length). It is the same fact the 2026-10-06 ruling turned on,
made explicit. A `zip -FI` of a pipe that delivered nothing gives a 0-byte FIFO entry and
stays `OTHER`, which is right: there is nothing to deliver.

### How to show the device flag

1. **`extra["special_file_type"]`** (typed overload in `types.py`, next to
   `tar.type`): `"fifo" | "char_device" | "block_device" | "socket" | "unknown"`. Set in
   all five formats on every member whose stored type is special: ZIP/7z/RAR/ISO from the
   mode's `S_IFMT` bits, TAR from the typeflag (`3`, `4`, `6`; everything else `OTHER`
   is `"unknown"`). Present on `OTHER` members (so callers stop needing `tar.type` to
   learn *which* kind) and on the data-bearing `FILE` members. Keep `tar.type`,
   `tar.devmajor`, `tar.devminor` as they are. This is the durable, machine-readable
   answer and costs no new top-level name (DR-13).
2. **A diagnostic**, say `MEMBER_SPECIAL_FILE_HAS_DATA`, one per member typed `FILE` this
   way: "listed as a file: the archive marks it as a FIFO and stores 16 bytes". Context:
   member name, stored type, size. The CLI listing prints diagnostics, `on_diagnostic`
   sees it, strict can be configured to refuse it, but I would leave it out of the
   default strict set: a documented writer option produces it and the bytes are
   delivered and checked.
3. The CLI type mark stays `f` for these members (`cli/format.py:13-20`); the diagnostic
   carries the rest. A new mark would be a third place to explain.

Alternative considered and not recommended: keep `OTHER` but let `open()` read it and let
extraction write a regular file when it has data. That makes `is_file` false for something
extraction writes as a file, which is harder to explain than `FILE` plus an `extra` key.

## Q2. Directory entry that declares data

### Is it real or crafted?

- **ZIP.** APPNOTE 4.3.8: "Zero-byte files, directories, and other file types that contain
  no content MUST NOT include file data." The only known producer that puts *anything*
  behind a directory entry writes a Deflate stream of nothing (compressed size 2,
  uncompressed 0); Go's `archive/zip` names the Java `jar` tool and documents the lesson:

  > We previously tried failing here if f.CompressedSize64 != 0, but it turns out that a
  > number of implementations (namely, the Java jar tool) don't properly set the storage
  > method on directories resulting in a file with compressed size > 0 but uncompressed
  > size == 0. We still want to fail when a directory has associated uncompressed data …

  and returns `ErrFormat` from `Open()` on a directory whose uncompressed size is not 0.
  Measured here: `jar` 21.0.12 (`jar cf`) stores its directory entries with both sizes 0,
  so the deflated shape is an older `jar` or a repackager (Debian bug 654899, §Sources);
  Python's `zipfile` writes it for any deflated empty entry. A directory with real
  uncompressed bytes is a malformed or hand-built archive.
- **TAR.** A `DIRTYPE` (`5`) header with a size is damage to every tar reader (below).
  A regular-file typeflag whose name ends in `/` is the old V7 directory spelling; with
  data behind it, it is hand-built: `tar` never writes one.
- **RAR.** `rar` writes directories with no data. unrar's `ReadHeader50` sets `Dir` from
  `FHFL_DIRECTORY` and computes `NextBlockPos` from `DataSize` with no consistency check
  between the two, so a hand-built header is accepted and its data skipped.
- **7z.** Not a shape in the format: a record with a stream is a file (`7zIn.cpp`:
  `file.HasStream = true; file.IsDir = false;`), the directory attribute bit 0x10 is never
  consulted for `IsDir`, and `S_IFDIR` in the high word is an attribute. PR #698 adopts
  exactly this.
- **ISO.** Directories always have an extent; it holds their directory records. Not a
  shape.

### What readers do with it

Fixtures: `zip_dir_data_stored.zip`, `zip_dir_data_deflate.zip`, `zip_dir_data_dos.zip`
(FAT host, attribute 0x10), `zip_dir_empty_deflate.zip` (the deflated empty directory),
`tar_dirtype_data.tar` / `tar_dirtype_data_ustar.tar`, `tar_regtype_slash_data.tar`,
`tar_aregtype_slash_data.tar`.

| Reader | ZIP `d/` with 28 bytes | TAR `5` with data | TAR `0` or NUL typeflag, name `d/`, with data |
| --- | --- | --- | --- |
| unzip | "creating: d/", data dropped, rc 0 | — | — |
| 7-Zip | Lists `Folder = +`, `Size = 28`; `x` creates the directory, data dropped, rc 0 | `7z l` lists it; `7z x` → "Headers Error", rc 2, stops | Directory, data skipped silently, rc 0 |
| bsdtar | Directory, data dropped, rc 0 | Directory, "Damaged tar archive, Retrying", recovers `after.txt`, rc 0 | Directory, same "Damaged" warning (zeroes the remaining count without skipping the body) |
| GNU tar | — | Directory, "Skipping to next header", exit 2 | Directory, data skipped **silently**, exit 0 |
| Python `zipfile` | `read("d/")` returns the 28 bytes; `extractall` creates the directory and drops them | — | — |
| Python `tarfile` | — | Lists `isdir` with size 28; does not skip the data | `0`: `FILE` `d/`, data readable; NUL: converted to `DIRTYPE`, data not skipped |
| Go `archive/zip` | `Open()` → `ErrFormat` (tolerates the deflated empty directory) | — | — |
| archivey main | `DIRECTORY`, `size=28`; `read()` refused by type; `extract_all` → `EXTRACTED`, no diagnostic | `CorruptionError` (matches GNU tar's failure) | `0`: `FILE` `d` with data plus `MEMBER_NAME_NORMALIZED`; NUL: `CorruptionError` (PR #706 makes it a directory and skips the data) |

The deflated empty directory (`zip_dir_empty_deflate.zip`) is handled silently by every tool, archivey
included; any new check must keep it that way.

### Weighing it

- DR-6 is unanimous: every official tool creates the directory and does not deliver the
  bytes. Matching that on disk is right, and users compare extraction output far more than
  diagnostics.
- But every tool is *silent* about it, and DR-1 ranks silent loss below a reported one.
  The sweep's option B (a diagnostic) is the minimum. The factor "could the lenient
  behaviour hide bytes" says the stricter side wins where the tools disagree, and here the
  "stricter side" for archivey is: say it, and let strict refuse it, as it already does
  for `ARCHIVE_TRAILING_DATA` (DR-3's "outside a member" weight: nothing is misread, so a
  warning, not an error). I would not raise by default: the shape is a spec violation,
  but the directory is created correctly and the only data at stake is bytes no reader
  treats as member content.
- Option C (type it `FILE`) matches 7-Zip's 7z behaviour but nothing else: unzip, GNU tar,
  bsdtar, unrar and `zipfile` all say directory; a `FILE` named `d/` contradicts its own
  name (PR #683's reasoning), and `d/x` members behind it would then collide with a file at
  `d`.
- The "user who really needs the data" is served by `open()`, which already exists:
  `zipfile.read("d/")` is what a Python user would reach for, and archivey can give the
  same without a new name (DR-14: one mechanism per job). The refusal in
  `base_reader.py:2756` is by *type*; make it by *data*: refuse `ANTI`, a data-less
  `OTHER` and a directory with no declared bytes with the current message, and open
  anything that has a stream. `stream_members()` would then also yield the stream for
  such a directory instead of `None`. Extraction is unchanged apart from the diagnostic.

Per-format feasibility of that accessor: ZIP reads member data through its own codec layer
from the header offset, so the directory's stream opens like a file's. TAR: for a `0`/NUL
entry `d/` the data is at `offset_data` and tarfile's `fileobject` can be built on it
(`extractfile` itself returns `None` for `DIRTYPE`, so this needs the small bypass PR #706
already has the hooks for). RAR: a *stored* member is sliced natively, so a stored
directory's bytes open; a *compressed* one would need `unrar p`, which "emits nothing for
a directory" (`extract.cpp`: the `IsArcDir()` branch returns before any data), so that
case raises `UnsupportedFeatureError` with a message saying why. Both RAR cases are
hand-built archives, so this is acceptable (DR-5a within DR-1).

### Diagnostic shape

`MEMBER_DIRECTORY_DATA_IGNORED` (the name PR 731 uses), context: member name, declared size,
compressed size, format. Emitted at listing time (the sizes are in the header), once per
member, in ZIP, TAR (`0`/NUL `d/` after the retype, and PR #706's NUL case) and RAR. Default
`collect`; in `DiagnosticPolicy.STRICT`'s raise set. `extract_all` keeps status
`EXTRACTED` for the directory; the diagnostic is the record. The handbook rows for ZIP,
TAR and RAR and `docs/errors-and-diagnostics.md` get one row each.

## Other questions worth deciding

Dispositions as of 2026-10-10: items 1, 2, 4 and 5 are settled by DR-25 as recommended
here (1: advisory; 2: the key on `OTHER` too; 4: `d/` with data is a directory; 5: a
non-empty extent under a special mode is a `FILE`); item 7 is DR-25 itself. Items 3 and 6
are open and listed in `design-rules.md` §Open gaps.

1. **Strict policy on Q1's diagnostic.** My recommendation is advisory only (real producer,
   bytes delivered). If you prefer parity with Q2 and `ARCHIVE_TRAILING_DATA`, it goes in
   the strict set and `zip -FI` archives fail under strict.
2. **`extra["special_file_type"]` on stream-less `OTHER` members too?** Recommended yes
   (one key, five formats, no more reading `tar.type` to learn the kind). The alternative
   is to set it only when the type was decided by data, which makes the key's presence
   mean two things.
3. **TAR `OTHER` `size` is `None`, ZIP/7z/RAR keep the stored value**
   (`tar_reader.py:1520` vs spec row `archive-data-model/spec.md:109`). Pick one; `None`
   is the better fit for TAR, where the size of a device entry is not data (GNU tar and
   libarchive ignore it), and the stored value is right where it is data.
4. **TAR `REGTYPE` `d/` with data becomes a directory** (GNU tar, 7-Zip; today a `FILE`).
   PR #706 left it for this decision and notes the change is a one-line extension of
   `_mark_old_style_directory`.
5. **ISO special mode with a non-empty extent.** Under the Q1 rule it becomes `FILE` with
   the `extra` key; today `OTHER` "whatever bytes sit at the extent"
   (`iso_reader.py:1629-1632`, handbook `iso.md:213`). genisoimage and xorriso write a FIFO
   with an empty extent, so only a hand-built image differs.
6. **Symlink and hard-link entries with trailing data in TAR** are the same structural
   case as a device with data (libarchive zeroes size for typeflags `1` and `2` too) and
   were not measured here. Worth one check in the TAR area for the same `CorruptionError`.
7. **Design-rules entry.** If you take both recommendations, the one-sentence rule in the
   summary could become a DR under "Consistency", with these two rulings and PR #698's as
   its examples, so the next format question of this kind does not come back here.

## Not done, and why

- 7z and RAR fixtures with a device mode or directory flag on a data-bearing entry were
  not built in this thread; the measurements above for those two formats come from the 7-Zip
  and unrar sources and from PR #698's own 7-Zip runs. A sweep-thread PR that adds such a
  fixture to the test suite (as `tests/test_sevenzip_parser_hardening.py` already does for
  the `S_IFDIR` shape) is the right place to pin them.
- ISO fixtures: no `xorriso`/`genisoimage` in the container.
- `unar` is not usable here (the installed build fails archivey's RAR5 check).

## Reproduce

Real-tool fixtures: `zip -FI` on a fed named pipe, and `tar -cf`, `bsdtar -cf`, `7z a` on
a FIFO and on `/dev/null`:

```
mkfifo pipe1; (echo "hello from fifo" > pipe1 &); zip -FI zip_fifo_FI.zip pipe1
mkfifo pipe2; tar -cf tar_fifo.tar pipe2; bsdtar -cf bsdtar_fifo.tar pipe2; 7z a sz_fifo.7z pipe2
```

Crafted shapes, with the standard library (the same construction as
`tests/test_zip.py::test_unix_special_file_is_other`). This builds every crafted fixture the
tables above name:

```python
import io, stat, tarfile, zipfile
DATA = b"directory or device payload\n"
AFTER = b"after\n"

def zip_with(out, name, attr, data, compress=zipfile.ZIP_STORED, create_system=3):
    with zipfile.ZipFile(out, "w") as z:
        zi = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
        zi.create_system = create_system
        zi.external_attr = attr << 16 if create_system == 3 else attr
        zi.compress_type = compress
        z.writestr(zi, data)
        z.writestr("after.txt", AFTER)

zip_with("zip_chr_data.zip", "chrdev", stat.S_IFCHR | 0o644, DATA)
zip_with("zip_fifo_data.zip", "fifo", stat.S_IFIFO | 0o644, DATA)
zip_with("zip_dir_data_stored.zip", "d/", stat.S_IFDIR | 0o755, DATA)
zip_with("zip_dir_data_deflate.zip", "d/", stat.S_IFDIR | 0o755, DATA, zipfile.ZIP_DEFLATED)
zip_with("zip_dir_empty_deflate.zip", "d/", stat.S_IFDIR | 0o755, b"", zipfile.ZIP_DEFLATED)
zip_with("zip_dir_data_dos.zip", "d/", 0x10, DATA, create_system=0)  # FAT host, attribute 0x10

def tar_with(out, name, typ, data, fmt=tarfile.GNU_FORMAT):
    with tarfile.open(out, "w", format=fmt) as t:
        ti = tarfile.TarInfo(name); ti.type = typ; ti.size = len(data); ti.mtime = 0
        ti.mode = 0o755 if typ == tarfile.DIRTYPE else 0o644
        if typ in (tarfile.CHRTYPE, tarfile.BLKTYPE): ti.devmajor, ti.devminor = 1, 3
        t.addfile(ti, io.BytesIO(data))
        ti = tarfile.TarInfo("after.txt"); ti.size = len(AFTER); ti.mtime = 0; ti.mode = 0o644
        t.addfile(ti, io.BytesIO(AFTER))

tar_with("tar_chr_data.tar", "chrdev", tarfile.CHRTYPE, DATA)
tar_with("tar_fifo_data.tar", "fifo", tarfile.FIFOTYPE, DATA)
tar_with("tar_dirtype_data.tar", "d/", tarfile.DIRTYPE, DATA)
tar_with("tar_dirtype_data_ustar.tar", "d/", tarfile.DIRTYPE, DATA, tarfile.USTAR_FORMAT)
tar_with("tar_regtype_slash_data.tar", "d/", tarfile.REGTYPE, DATA)
tar_with("tar_aregtype_slash_data.tar", "d/", tarfile.AREGTYPE, DATA)
```

Then list and extract each with `unzip`, `7z l -slt` / `7z x`, `bsdtar -tvf` / `-xvf`,
`tar -tvf` / `-xvf`, and `zipfile` / `tarfile` / `archivey`.

## Sources

- Go `archive/zip` reader, directory handling in `File.Open`:
  <https://github.com/golang/go/blob/master/src/archive/zip/reader.go>
- libarchive ZIP reader (Info-ZIP FIFO workaround), TAR reader (`header_common` zeroes
  size for types 2–6), disk writer (`fd < 0` drops data for non-regular entries):
  <https://github.com/libarchive/libarchive/tree/master/libarchive>
- 7-Zip 7z reader (`HasStream` ⇒ not a directory), ZIP item (`IsDir()`), TAR reader
  (size zeroed only for `5` and `1`), extraction callback (no device creation):
  <https://github.com/ip7z/7zip>
- unrar `arcread.cpp` (`Dir` from `FHFL_DIRECTORY`, no size check), `extract.cpp`
  (directory branch returns before data): <https://github.com/aawc/unrar>
- CPython `zipfile` (`_extract_member` returns before opening a directory's data) and
  `tarfile` (`_proc_builtin` skips data only for regular types; `data_filter` rejects
  special files): <https://github.com/python/cpython/tree/main/Lib>
- PKWARE APPNOTE 4.3.8 and 4.4.15: <https://pkware.cachefly.net/webdocs/casestudies/APPNOTE.TXT>
- Debian bug 654899 (javahelper's jh_manifest turned a jar's `META-INF/` directory entry into
  compressed size 2, uncompressed 0, and `unzip -t` flagged it: "ucsize 0 <> csize 2 for STORED
  entry"), the deflated-empty-directory shape in the wild, from a repackager rather than
  `jar` itself (`jar` 21.0.12 measured here stores directories with both sizes 0):
  <https://alioth-lists.debian.net/pipermail/pkg-java-maintainers/2012-January/036676.html>
