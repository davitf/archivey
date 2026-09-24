# Code map

A **signpost, not an inventory.** Every module in `src/archivey/` already carries a
docstring that says what it is; this file answers the question those docstrings cannot —
*where do I start for the change I have been asked to make?*

Deliberately coarse, so it does not rot: directory shape, the call path through a read,
and a task→files index. If you find yourself wanting to add a per-file row here, add the
detail to that file's docstring instead.

Normative behavior lives in `openspec/specs/`; this describes the tree, not the contract.

---

## The shape

```
src/archivey/
├── __init__.py          the export surface — the frozen public API
├── core.py              open_archive(), extract()
├── reader.py            ArchiveReader — the caller-facing read interface
├── types.py             Member, ArchiveInfo, ArchiveFormat, MemberType, …
├── config.py cost.py measurement.py diagnostics.py exceptions.py escaping.py
│                       public value types: config, CostReceipt, IoStats,
│                       Diagnostic, the error hierarchy, terminal-safe escaping
├── cli/                 the CLI — a *consumer* of the public API, not a peer of it
└── internal/            everything else; not importable contract
    ├── base_reader.py   BaseArchiveReader ABC + the ReadBackend/WriteBackend ABCs
    ├── extraction.py    extraction coordinator + decompression-bomb tracker
    ├── filters.py       path-safety checks and policy permission transforms
    ├── detection.py     detect_format() and FormatInfo
    ├── registry.py      ArchiveFormat → backend; format_availability()
    ├── reader_state.py  operation ownership, live-stream gate, lifecycle leases
    ├── backends/        one self-registering reader module per format
    └── streams/         the codec and stream-plumbing layer
        └── streamtools/ generic BinaryIO plumbing (slice, lock, share, count)
```

Two boundaries carry most of the design weight:

- **`archivey/` vs `archivey/internal/`.** The former is frozen surface; the latter is
  not. **The CLI importing from `internal/` is a smell** — it usually means the public API
  has a gap, and that is a finding, not a shortcut (`review/api-coherence/`).
- **`backends/` vs `streams/`.** A backend parses a *container* — headers, member
  metadata, layout. It does not call codec libraries; it composes the uniform pull-based
  decoder layer in `streams/`. A change that makes a backend import `lzma` directly is
  almost always in the wrong file.

---

## The path through a read

```
open_archive(path)                                   core.py
  └─ detect_format(source)                           internal/detection.py
       └─ magic table, non-seekable peek/replay
  └─ registry lookup: ArchiveFormat → backend        internal/registry.py
  └─ backend opens                                   internal/backends/<fmt>_reader.py
       └─ BaseArchiveReader machinery                internal/base_reader.py
            member registration, ids, listing cost, diagnostics emission

reader.members() / .open(m)                          reader.py → base_reader.py
  └─ backend yields member metadata
  └─ member stream: codec pipeline                   internal/streams/codecs.py
       ├─ decoder strategies                         internal/streams/decompress.py
       ├─ seekable decode engine                     internal/streams/decompressor_stream.py
       ├─ AES stage ([recommended])                  internal/streams/crypto.py
       └─ digest / length verification               internal/streams/verify.py
  └─ ArchiveStream wraps + translates exceptions     internal/streams/archive_stream.py

extract(path, dest)                                  core.py → internal/extraction.py
  └─ member selection                                internal/selection.py
  └─ path safety + policy                            internal/filters.py
  └─ bomb accounting                                 internal/extraction.py
```

Three things about this path are worth knowing before you debug it:

- **A reader lists its members once, and every method hands out the same objects.**
  `BaseArchiveReader` owns one member list (`_listed`) filled by one pull over the
  backend's `_iter_members()`. The peek, `members()`, `scan_members()`, `get()`,
  `stream_members()` and `extract_all()` all read that list; `_pull_member` stamps,
  checks and accounts each member exactly once, so per-member work needs no dedupe. The
  walk runs once when it completes. A random-access walk that fails without terminal
  damage is discarded and may be retried, which is why `_iter_members()` must still
  yield in a stable order; a streaming one poisons the reader. Last-entry-wins
  `is_current` is stamped once, when the walk ends (`_end_walk`). A streaming pass keeps
  its own cursor into the list and finalizes links only when that cursor passes the end,
  so a peek that drains the walk does not finalize a pass the caller then abandons.
  `ArchiveMember` is mutable and filled in place (ADR 0007), and there is now only one
  object per member to fill.
- **"Backend" means archivey's class, not the third-party code.** `ReadBackend` /
  `ZipReadBackend` are ours; the library a backend wraps (stdlib `zipfile`, `pycdlib`, the
  `unrar` binary) is **the library**. Handbook pages tag a limitation **format** (inherent),
  **library** (upstream's — fixable only there or by replacing it) or **archivey** (ours),
  and that middle word is why it is not "backend".
- **Exceptions are translated per backend**, through that backend's translator, into
  `ArchiveyError` subclasses. Unknown exceptions return `None` from the translator and
  propagate; there is no catch-all.
- **`ArchiveyUsageError` is deliberately outside the `ArchiveyError` tree** (ADR 0012), so
  a caller-misuse fault cannot be produced by a translator that can only return archive
  errors.

---

## Task → files

| If you are changing… | Start here |
|---|---|
| A format's parsing or metadata | `internal/backends/<fmt>_{reader,parser}.py`; spec `openspec/specs/format-<fmt>/` |
| ZIP internals | `zip_reader.py` (stdlib central directory + archivey member data) · `internal/zip_detect.py` (scan-hit validator) · `internal/zipcrypto.py` · `internal/zip_aes.py`; handbook [`formats/zip.md`](formats/zip.md) |
| 7z internals | `sevenzip_parser.py` (headers) · `sevenzip_pipeline.py` (coder graph) · `sevenzip_reader.py` · `sevenzip_methods.py` · `internal/sevenzip_detect.py` (scan-hit validator); handbook [`formats/7z.md`](formats/7z.md) |
| RAR internals | `rar_parser.py` (native RAR3/RAR5 metadata) · `rar_reader.py` · `rar_unrar.py` (the external binary, data only) · `internal/rar_detect.py` (scan-hit validator); handbook [`formats/rar.md`](formats/rar.md) |
| A codec, or adding one | `streams/codecs.py` + `streams/decompress.py`; `xz.py` / `lzip.py` / `unix_compress.py` for the hand-written ones |
| Seeking inside a compressed stream | `streams/decompressor_stream.py`; spec `seekable-decompressor-streams` |
| Stream wrapping / slicing / locking | `streams/streamtools/`; handbook [`topics/stream-ownership.md`](topics/stream-ownership.md); archived review `review/archive/2026-07-19-stream-layering/` |
| Extraction safety, path traversal, symlinks | `internal/filters.py` + `internal/extraction.py`; `dev-docs/threat-model.md` |
| Decompression-bomb limits | `internal/extraction.py`; listing-side caps in `internal/listing_limits.py` |
| Member names, encoding, bidi, cross-platform safety | `internal/naming.py`; ADRs 0013, 0017 |
| The error hierarchy or a translation | `exceptions.py` + the backend's translator; spec `error-handling` |
| Warnings-as-data | `diagnostics.py` (public types) + `internal/diagnostics_collector.py` (emission) |
| Cost or IO accounting | `cost.py` · `measurement.py` · `internal/measurement.py` · `streams/counting.py` |
| Concurrency, locking, `MemberStreams` | `internal/reader_state.py` + `streams/streamtools/locked.py`; spec `reader-concurrency` |
| Format detection or a magic number | `internal/detection.py`; prefixed/SFX payloads: `internal/sfx.py` + `<fmt>_detect.py` validators, handbook [`topics/prefixed-archives.md`](topics/prefixed-archives.md) |
| Adding a backend | `internal/registry.py` + a self-registering module in `backends/` |
| The CLI | `cli/main.py` dispatches; one module per subcommand |
| Terminal-safe output of hostile text | `escaping.py`; threat-model O9 |
| Test-suite leak oracle (live children, owning streams; pipe fds as context) | `tests/leak_oracle.py` (autouse plugin); `tests/test_leak_oracle.py`; the shutdown-only accelerator diagnostic is still `scripts/accel_leak_trace.py` |

---

## Where the answers live

Most "why is it like this?" questions have a written answer already. In rough order of how
often they turn out to be the right place:

| Question | Look in |
|---|---|
| How should we work this change? | `dev-docs/pair-workflow.md` |
| Why is a review round running, or not running, on this PR? | `dev-docs/review-loop.md` |
| What is true *here* for a format / topic? | `dev-docs/formats/<format>.md` / `dev-docs/topics/<topic>.md` when present; else code-map + threat model + ADRs/investigations — create the handbook page in the PR that needs it ([`pair-workflow.md`](pair-workflow.md)) |
| What is the authoritative agent/CI contract? | `openspec/specs/<capability>/spec.md` — capability map in `openspec/project.md` (not the primary human reading surface) |
| Why was this chosen? (legacy / repo-wide) | `dev-docs/decisions/` (ADR log, `index.md` first); new answers prefer handbook pages |
| Is this a known defect / upstream bug? | `dev-docs/known-issues.md`, `dev-docs/investigations/` |
| Is this a known unfixed gap? | `dev-docs/threat-model.md` (`O*` register), `dev-docs/open-issues.md` |
| Has this already been reviewed? | `review/STATUS.md`, then the archive tables under `review/archive/` |
| Has this *file* been swept? | The `SWEPT` markers on [#315](https://github.com/davitf/archivey/pull/315) — one per file read end to end, findings or not. `scripts/sweep_coverage.py` counts them; [`review/sweep/`](../review/sweep/README.md) is the brief shape |
| Was this deliberately deferred? | `review/backlog.md`, `dev-docs/IDEAS.md` |
| What is open right now, and in what order? | `dev-docs/open-work-inventory.md` — a dated snapshot across every register; the registers themselves stay authoritative |
| Was this discussed but not settled? | `dev-docs/discussions/` |
| What does the user-facing story say? | `docs/` (published guide only) |

**Check before deriving.** Several questions here have been re-answered from scratch more
than once — the empty-TAR question was raised three times across two review rounds before
ADR 0015 was written. If you find yourself measuring something that feels like it should
already be known, search these first; if it genuinely is not written down, write it down
where the table above says it belongs.
