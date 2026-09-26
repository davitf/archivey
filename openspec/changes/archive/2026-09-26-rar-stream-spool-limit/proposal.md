# Bound the disk copy a RAR stream source needs for `unrar`

## Why

`unrar` reads only files. So a RAR opened from a `BytesIO` or another file object is
copied whole to a temp file (a volume set, to a temp directory) the first time a member
has to go through `unrar` (`RarReader._ensure_archive_path()`,
`_materialize_stream_volumes()`). `format-rar` already requires an open-time
`CostReceipt.notes` caveat for that copy, but nothing bounds it: a caller who hands over a
4 GiB `BytesIO` reads the caveat and then gets a 4 GiB temp file. This was the remaining
half of `dev-docs/open-issues.md` **P11**.

Maintainer ruling, 2026-09-26: *"let's add a cap, it would be a config field"*, shipped in
`0.2.0` so that adding it later is not a behaviour break.

## What Changes

This change carves the bound on the **existing** copy out of `bounded-source-spooling`,
which designs a wider feature (spooling a non-seekable source so ZIP, 7z, RAR and ISO can
read a pipe). It uses the shape and default that change settled with the maintainer on
2026-09-17 (its `design.md` Q1, Q2 and Q3), and nothing else from it:

```python
@dataclass(frozen=True)
class SpoolLimits:
    max_bytes: int | None = 2**30      # 1 GiB; None disables the guard
    UNLIMITED: ClassVar["SpoolLimits"]  # SpoolLimits(max_bytes=None)

ArchiveyConfig.spool_limits: SpoolLimits = SpoolLimits()
```

- Over the limit raises `SpoolLimitExceededError`, a new `ResourceLimitError` subclass,
  naming `SpoolLimits.max_bytes`. When the size is known up front, the refusal comes
  before anything is written and before `unrar` is spawned. When it is not, the copy stops
  before it passes the limit and the partial copy is removed. The limit holds for the
  reader: a refused copy is not retried by the next read.
- A volume set is one copy: the limit weighs the total across its volumes, file volumes of
  a mixed set included.
- The existing open-time caveat stays and names the limit in force; when the limit
  already rules the copy out at open, it says the read will be refused instead.
- Path sources are never copied and never refused.

**Left in `bounded-source-spooling`:** spooling a non-seekable source, a caller-named
spool directory, and the free-space pre-flight. Each is additive on top of this: a new
`SpoolLimits` field, or a new raise site of `SpoolLimitExceededError`.

## Specs

- **`archive-reading`** — MODIFIED: `SpoolLimits` joins the frozen config schema, and the
  temp-storage requirement names the bound on RAR's declared strategy.
- **`format-rar`** — MODIFIED: the stream-source copy is bounded by the spool limit, and
  the open-time caveat names it.
- **`error-handling`** — MODIFIED: `SpoolLimitExceededError` joins the hierarchy under
  `ResourceLimitError`.

## Impact

- **Public surface:** `archivey.SpoolLimits`, `ArchiveyConfig.spool_limits` and
  `archivey.SpoolLimitExceededError`. Additive.
- **Behaviour change, pre-tag and deliberate:** a RAR-from-stream read of a member that
  needs `unrar`, on an archive over 1 GiB, now raises where it used to copy. No corpus
  archive comes near that size, so a test drives the default with a sparse file.
