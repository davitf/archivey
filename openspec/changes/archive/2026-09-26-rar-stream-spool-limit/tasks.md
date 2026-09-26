# Tasks — bound the RAR stream-source copy

## 1. Setting

- [x] 1.1 `SpoolLimits` (frozen, `max_bytes: int | None = 2**30`, `UNLIMITED`), validated
      through `_check_limit`; `ArchiveyConfig.spool_limits`, type-checked at construction
- [x] 1.2 Export from `archivey.__all__`; `api.md` entry; docstrings on the class and field
- [x] 1.3 Widen `ResourceLimitError`'s docstring to name `SpoolLimits`; add
      `SpoolLimitExceededError(ResourceLimitError)`, exported, with an `api.md` entry

## 2. Bound the copy

- [x] 2.1 `archivey.internal.spool.SpoolBudget`: up-front `check_total`, per-chunk `copy`,
      one per reader, and a refusal that sticks
- [x] 2.2 `_ensure_archive_path()` checks the known size before `mkstemp`, copies through
      the budget, and removes the partial file on refusal
- [x] 2.3 `_materialize_stream_volumes()` checks the set's total before `mkdtemp`, copies
      every volume (file volumes included) through one budget, and removes the directory
      on refusal
- [x] 2.4 The open-time caveat names the limit in force, and says "refused" when the limit
      already rules the copy out at open

## 3. Tests (`tests/test_rar_spool_limit.py`)

- [x] 3.1 Within the limit reads; over it raises before any temp file and before `unrar`
- [x] 3.2 Unknown size: the copy stops at the limit and the temp file is removed
- [x] 3.3 `None` / `UNLIMITED` never refuse; `0` keeps direct reads and refuses the copy
- [x] 3.4 Volume set: total weighed, not each volume; mid-copy refusal removes the
      directory, for stream volumes and for a mixed set's file volume
- [x] 3.5 Path sources (single and volume set) are never copied under `max_bytes=0`
- [x] 3.6 The 1 GiB default, driven by a sparse file one byte over it
- [x] 3.6a A refused copy of an unknown-size source is not retried by later reads
- [x] 3.7 Each guard shown to fail under a mutation of the code it guards

## 4. Registers, docs, specs

- [x] 4.1 Close `dev-docs/open-issues.md` P11; `docs/access-and-cost.md`,
      `docs/formats.md` §RAR, `docs/extracting.md` §Limits,
      `docs/errors-and-diagnostics.md`; CHANGELOG
- [x] 4.2 Trim `bounded-source-spooling` to what remains after this change
- [x] 4.3 `openspec archive rar-stream-spool-limit --yes`
