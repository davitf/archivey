# Sample page — Safe extraction

*Probe page: depth and voice. Claims below are checked against code/tests in this
pass; citations omitted in the user-facing voice but recoverable from
`must-explain.md`.*

---

Archivey’s extraction API is safe-by-default for **untrusted** archives. The
defaults are intentional and easy to defeat if you copy patterns from `tarfile`
or `zipfile` without reading this page.

## The one-shot path

```python
import archivey

report = archivey.extract("upload.zip", "outdir")
for result in report:
    if result.status is not archivey.ExtractionStatus.EXTRACTED:
        print(result.status, result.member.name, result.error)
```

Defaults:

| Knob | Default | Meaning |
|---|---|---|
| `policy` | `STRICT` | Portable names; strip execute/setuid; drop ownership |
| `overwrite` | `ERROR` | Existing dest paths are failures (then `on_error`) |
| `on_error` | `STOP` | First **data/write** failure aborts |
| bomb limits | 2 GiB / ratio 1000 after 5 MiB / ~1M entries | `ResourceLimitError` |

There is **no** `members=` on `extract()`. Filtering requires an open reader:

```python
with archivey.open_archive("upload.zip") as ar:
    ar.extract_all("outdir", members=["README", "src/main.py"])
```

## What is never optional

These checks run under **every** policy, including `TRUSTED`:

- Absolute paths, `..` segments, NUL bytes, unencodable names
- Symlink / hardlink targets that resolve outside the destination root
- Special files (devices, FIFOs, sockets)
- A member name that is the extraction root itself (non-directories)

`TRUSTED` means “apply stored modes and ownership (uid/gid as root)”, **not**
“disable path safety”. If you needed “abort on first hostile path”, `OnError.STOP`
does **not** do that — blocked members are recorded as `BLOCKED` and extraction
continues. Escalate diagnostics with `DiagnosticPolicy` / `RAISE`, or pre-scan
and refuse the archive yourself.

## Policies (what actually changes)

**`STRICT`** (default) — treat the archive as hostile and the destination OS as
unknown. Modes become `0644`/`0755`; execute and setuid bits go away; Windows
reserved device names and `:` in a path segment are rejected **even on Linux**;
trailing dots/spaces are rewritten to a portable spelling; NFC/case folding
applies to collision detection so `README` and `readme` collide everywhere.

**`STANDARD`** — keep execute bits; still reject reserved names / `:`; do not
strip trailing dots/spaces.

**`TRUSTED`** — faithful names and modes; collision keys are exact. Path escape
checks still apply.

## Overwrite and symlinks

`ERROR` / `SKIP` / `REPLACE` / `RENAME` decide collisions with paths that already
exist under the destination.

`REPLACE` **unlinks** the existing entry and creates a new one. It will not open
a pre-existing symlink and write through it to an escape target. Treat that as a
feature.

The destination directory itself may be a symlink to a real directory — that is
followed (you chose the dest). A destination that is a plain file or a dangling
symlink is never deleted to make room for extraction.

## Duplicate names inside the archive

Two members named `file.txt` are not an overwrite error. The earlier row is
marked non-current (`is_current=False`) and recorded as `SUPERSEDED`; the last
row’s content is what lands on disk. `reader.get("file.txt")` returns that last
row.

## Bombs

Limits are cumulative. Hitting them raises `ResourceLimitError` and **stops the
run even if `on_error=CONTINUE`**. Members you skipped with a selector or filter,
and members blocked by safety checks, do not consume the entry-count budget —
only things that would create archive entries on disk.

Tiny highly-compressible files will not trip the ratio guard until output passes
`ratio_activation_threshold` (default 5 MiB). That avoids false positives; it is
not a license to disable limits.

## Hardlinks

Hardlinks share an inode when the source was extracted in the same run. If you
select a hardlink but exclude its source, a **seekable** archive can recover by
re-reading the source bytes in a second pass and writing them at the **link’s**
path (the source name is not created). A pipe cannot rewind — recovery fails per
`on_error`.

## CLI note

`archivey x archive.zip` is not the same defaults as `archivey.extract`. The CLI
defaults overwrite to **rename** and picks a **smart destination** (cwd vs a
wrapping directory) to avoid tarbombs. Library code that copies CLI habits will
mis-handle collisions; CLI users who expect “splat into cwd” must pass `-d .`.

## Failure shape you should handle

```python
from archivey import extract, ExtractionStatus, ResourceLimitError, ArchiveyError

try:
    report = extract(src, dest, on_error=archivey.OnError.CONTINUE)
except ResourceLimitError:
    ...  # bomb — partial tree may exist
except ArchiveyError:
    ...  # open/detect/read problems

blocked = [r for r in report if r.status is ExtractionStatus.BLOCKED]
failed = [r for r in report if r.status is ExtractionStatus.FAILED]
```

Do not catch `Exception` hoping to cover misuse: `ArchiveyUsageError` is
deliberately outside `ArchiveyError`, but extraction itself rarely raises it —
mis-opened readers do.
