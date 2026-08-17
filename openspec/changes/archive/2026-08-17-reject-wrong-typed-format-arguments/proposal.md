# A wrong-typed `format=` argument is a usage error, not an invented answer

## Why

`format_availability()` takes an `ArchiveFormat` and both project type checkers enforce
it, so every caller inside this repo is protected. Given the wrong type anyway — an
untyped project, or a caller holding the `StreamFormat` that `open_stream(format=…)`
legitimately accepts — it **invented a record instead of refusing**:

```
format_availability(StreamFormat.ZSTD)
-> FormatAvailability(format=StreamFormat.ZSTD, support=NONE, missing=(),
                      required_source=SEEKABLE)
```

`support=NONE` with an empty `missing` is indistinguishable from a legitimate
"unsupported, and nothing to install about it"; `required_source=SEEKABLE` contradicts
the real record for `ArchiveFormat.ZST` (`FORWARD_ONLY`) on the field callers are taught
to branch on; and the returned object's `format` field violates the type this very spec
declares for it. A public query that invents an answer is the shape `VISION.md` rules
out, even when the caller was wrong to ask that way.

Sweeping the other three entry points that take a format found the same defect in two
more shapes, so this is one cause with four call sites rather than one bug:

| Call | Before |
| --- | --- |
| `format_availability(StreamFormat.ZSTD)` | fabricated record |
| `open_archive(path, format=StreamFormat.ZSTD)` | `AttributeError: 'StreamFormat' object has no attribute 'container'` |
| `extract(path, dest, format=StreamFormat.ZSTD)` | same `AttributeError` |
| `open_stream(src, format="zst")` | argument silently discarded; auto-detected instead |

The middle two put a raw `AttributeError` naming a private attribute across the public
boundary, which the error contract's no-internal-leakage rule forbids as squarely as
ADR 0012 does. The fourth is the same dishonesty as the first: an assertion the caller
made, answered with something else.

Filed as `dev-docs/open-issues.md` **P10**; the maintainer decided the resolution
(restrict to `ArchiveFormat`, raise on anything else) before this change was written.

## What Changes

- **One validation helper at the boundary** (`internal/format_args.py`), called from
  `format_availability()`, `open_archive()`, `extract()` and `open_stream()`. Each
  refuses a value outside the types its own signature declares, with
  `ArchiveyUsageError` — outside `ArchiveyError` (ADR 0012), so `except ArchiveyError`
  cannot swallow a caller bug.
- **The message ends the mistake.** It names what was passed and what was expected, and
  for a `StreamFormat` it names the `ArchiveFormat` pairs built on that codec, read off
  the predefined names rather than a second table:

  ```
  format_availability() takes an ArchiveFormat, but got StreamFormat.ZSTD. A
  StreamFormat is only the codec half of an ArchiveFormat's (container, stream) pair,
  so pass the pair instead: ArchiveFormat.ZST (a raw .zst stream) or
  ArchiveFormat.TAR_ZST (a tar compressed with it).
  ```

- **`open_stream` keeps its wider argument.** `StreamFormat | ArchiveFormat` is the
  design, not an inconsistency: a raw compressed stream genuinely has no container, so
  the codec alone identifies it. What changes there is only the fall-through — a value
  of neither type is refused rather than ignored.

Not changing: which formats are supported, what a real `ArchiveFormat` answers, or the
signatures themselves. `ArchiveFormat.UNKNOWN` still answers `NONE` with an empty
`missing` — a hintless NONE is a legitimate verdict, and the validation keys on the
argument's *type*, never on the shape of the verdict.

## Impact

- Specs: `backend-registry` (the `format_availability()` contract, plus the boundary
  rule the other three entry points share).
- Code: `src/archivey/internal/format_args.py` (new), `src/archivey/internal/registry.py`,
  `src/archivey/core.py`.
- Tests: `tests/test_format_arguments.py` (new) — red-green per call site, plus the
  guards that a real `ArchiveFormat`, `None` where it is allowed, and `open_stream`'s
  two accepted types all keep working.
- Public API: behaviour-only. Every call this now refuses was already a type error that
  `pyrefly` and `ty` reject; a typed caller sees no change.
- Docs: none in this change. `docs/install.md`'s inbound `format_availability()` section
  is Topic 8's to write; the claim is recorded in `review/docs-content/claims.md` Part 1
  instead of edited into a page here.
