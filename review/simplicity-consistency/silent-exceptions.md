# Silent exceptions / ignored knobs

Evidence for seeds B (silent argument discard, error translation, spec fiction).

## F1 — `volumes.py` ValueError crosses `open_archive` (CONFIRMED)

**Sites:**

- `src/archivey/internal/volumes.py:145` — `ConcatenatedFile`: empty sources
- `src/archivey/internal/volumes.py:167` — non-seekable volume stream
- `src/archivey/internal/volumes.py:269` — `join_volumes` empty paths
- `src/archivey/internal/volumes.py:314` — `resolve_source([])`

**Public path:** `core.open_archive` → `resolve_source` **before** any backend
translator (`core.py:194`).

**Repro (`[all]`):**

```python
from archivey import open_archive
open_archive([])  # builtins.ValueError: source sequence must not be empty

class Pipe:  # non-seekable
    ...
open_archive([Pipe(b"..."), Pipe(b"...")])
# builtins.ValueError: all volume streams must be seekable
```

Observed 2026-08-07: both raise **raw** `builtins.ValueError`, not
`ArchiveyUsageError` / `StreamNotSeekableError`.

**Why it matters:** CONTRIBUTING error contract — caller misuse / capability
refusal should be typed. Empty sequence and non-seekable volumes are API misuse,
parallel to pipe refusal elsewhere.

**Fix vehicle:** bug fix PR — map at `resolve_source` / `ConcatenatedFile` to
`ArchiveyUsageError` (empty) and `StreamNotSeekableError` (non-seekable), or wrap
in `open_archive` before backend construction.

---

## F2 — `encoding=` silently discarded on most backends (CONFIRMED)

**Contrast:** `password=` on a non-encrypting format raises
`UnsupportedOperationError` (`core.py:244–250`). `encoding=` has no analogous gate.

| Backend | Behaviour |
|---|---|
| ZIP / TAR | Honored |
| 7z | `del encoding` (`sevenzip_reader.py:190`) — UTF-16LE names |
| RAR | `del encoding` (`rar_reader.py:362`) — native parser |
| Directory | Param accepted on factory; never passed to reader (`directory_reader.py:315–334`) |
| ISO | Param accepted; never stored/used (`iso_reader.py:260`) |
| Single-file | Param accepted; unused (`single_file_reader.py:108`) |

**Repro:**

```python
open_archive("tests/fixtures/sevenzip/lz4.7z", encoding="cp932")  # OK, silent
open_archive(tmpdir, encoding="cp932")  # directory: OK, silent
```

**Fix vehicle:** product choice in QUESTIONS Q-encoding —
(a) reject static encoding on formats that ignore it (password-parallel), or
(b) document as ZIP/TAR-only and accept silence, or
(c) warn/diagnostic once.

---

## F3 — ZIP "already closed" → CorruptionError (CONFIRMED)

ZIP maps **all** `ValueError` to `CorruptionError` (`zip_reader.py:511–515`):

```text
Corrupt ZIP member offset/structure: ValueError('Attempt to use ZIP archive that was already closed')
```

Raise sites: `zip_reader.py:758,779,908,961,965` (message
`"Attempt to use ZIP archive that was already closed"`).

`ArchiveStream._fail` only special-cases `"closed file"` in the message
(`archive_stream.py:354–365`) — **"already closed" does not match**.

**Repro:**

```python
with open_archive(io.BytesIO(zip_bytes)) as r:
    r._archive.close()          # underlying ZipFile.fp = None
    r.open("a.txt")             # CorruptionError (misclassified)
```

Normal `reader.close()` then `open` is a different path (usage/closed-reader).
This finding is **close-underneath while reader live** / internal fp None.

**Compare translators:**

| Backend | `_translate_exception` breadth |
|---|---|
| ZIP | BadZipFile, RuntimeError(pw), UnsupportedOperation, NotImplementedError, zlib/lzma, **all ValueError**, OSError(bz2), UnicodeDecodeError, EOFError |
| TAR | ReadError, EOFError |
| 7z / RAR | EOFError only |
| ISO | PyCdlib + IndexError/struct/ValueError/… as Corruption |

**Fix vehicle:** bug fix — before the generic ValueError→Corruption arm, map
"already closed" (and ideally closed-file) to `ArchiveyUsageError`. Optionally
narrow ZIP ValueError mapping to known corruption substrings only.

---

## F10 — RAR `RuntimeError("unrar produced no stdout pipe")` (PLAUSIBLE)

`rar_unrar.py:155–157` raises raw `RuntimeError` when `proc.stdout is None`.
Call sites (`rar_reader.py:682`, `:883`) are **outside** `_translated_errors`.
RAR translator returns `None` for anything but `EOFError` (`rar_reader.py:741–744`).

Hard to hit with real `subprocess.Popen(..., stdout=PIPE)`; still a raw leak if it
fires. **Fix:** translate to `ArchiveCorruptError`-adjacent or
`PackageNotInstalledError` / `ArchiveyError` subclass in RAR translator or at spawn.

---

## F4 — Spec fiction: RTL "warns or rejects" (CONFIRMED)

`openspec/specs/testing-contract/spec.md:55` — "RTL warns or rejects".
Scenario `:76–79` — "rejected **or** exactly one warning".

**Code:** `naming.py:38–53` — `_warn_for_bidirectional_controls` →
`logger.warning` only. No reject path for RTL alone. Tests assert warn-once
(`tests/test_directory.py`, `tests/test_single_file.py`).

Null-byte rejection is separate (traversal). The "**or rejects**" clause is
aspirational for landed testing-contract capability.

**Fix vehicle:** spec change — "RTL warns once (logger); null bytes reject as
traversal." Optional follow-up: promote bidi to a `DiagnosticCode` (aligns with
VISION "queryable data over ambient warnings").

---

## Other silent / reserved CLI knobs (not defects)

| Knob | Behaviour |
|---|---|
| `--salvage` | Loud `CliError` "not implemented" (`common.py:29–31`) |
| Reserved verbs hash/create/… | Loud usage |
| `--track-io` on readers without counters | Prints "unavailable" (`common.py:57–58`) — honest |
| Config `zip_unflagged_fallback_encoding` on non-ZIP | No-op (F16) |
| Config `strict_archive_eof` on non-TAR | No-op |

Directory `format=` silent discard: **fixed** in `#225` (not reopened).
