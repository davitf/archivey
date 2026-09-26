# rapidgzip upstream report — soft EOF and related defects

Status: **documentation only — not filed upstream** (2026-07-20). Soft empty/short
success on truncated gzip is **by design** in rapidgzip’s parallel reader (trial-and-error
mid-stream decode for ratarmount / random access). Filing it as a “bug” would be wrong;
an `is_stream_complete()`-style API would be a **feature request** we are not opening
now. This note records the contract Archivey must work around, plus adjacent abort
defects that *are* bug-class.

Deep dive (code citations, issue table, repros):
`openspec/changes/rapidgzip-truncation-investigation/UPSTREAM_TRUNCATION_REPORT.md`.

Archivey product mitigation (empty→stdlib fallback + single-member ISIZE backstop):
**implemented** in `_GzipTruncationCheckStream` (OpenSpec change
`rapidgzip-truncation-investigation`). Accelerator shutdown / dual-load /
Python-source `terminate()` / truncated-DEFLATE abort: `dev-docs/known-issues.md` (Bugs 1–4).

Pinned: **rapidgzip 0.16.0** ≡ librapidarchive `1221a30` (`[version] Bump rapidgzip
version to 0.16.0`). Soft-EOF paths unchanged on inspected HEAD.

---

## Classification

| Topic | Class |
| --- | --- |
| Soft EOF on truncated gzip / empty-short success | **by design** (not a bug) — Archivey limitation; mitigate with empty→stdlib + ISIZE. macOS raises more often than Linux/Windows but still silent at cut=10. |
| `std::terminate` on a truncated DEFLATE stream | **bug-class** — contained by a child process; known-issues Bug 4 + §2 below |

## 1. Soft EOF on truncated input (by design — Archivey limitation)

### Behaviour

With path sources and `parallelization=0` (**all cores** in upstream’s API — intentional
in Archivey):

- Mid-body truncations of ordinary single-block gzip often make `RapidgzipFile.read()`
  return `b""` **without raising** (**Linux / Windows**; macOS mostly raises after cut=10).
- Multi-block / large streams often return a **correct short prefix** (or full payload if
  only the trailer is missing) **without raising** on Linux/Windows.
- Objects frequently report `block_offsets_complete=True` and `size == len(returned)`, so
  callers cannot tell a valid short member from a truncated stream via those APIs.
- Stdlib `gzip` sized-reads still yield a prefix then raise `EOFError`.

### Why (upstream)

- `ParallelGzipReader::read`: missing chunk → soft EOF, return bytes already written.
- `GzipChunkFetcher::processNextChunk`: `encodedSizeInBits == 0` → finalize block map,
  return empty.
- `GzipChunk::tryToDecode`: **swallows** `std::exception` while guessing block starts
  (expected during speculative decode).

CHANGELOG / empty-gzip handling also prefer not throwing when there is “no more
decodable data.” There is **no** Python docstring that truncated files must raise.
GitHub issues do not treat silent-empty `read()` as a user-facing bug.

### Archivey stance

- Do **not** file this as an upstream bug.
- Do **not** parse stderr (`Unexpected end of file when getting block…` is a **rethrow**
  path near the trailer, not a silent-success channel).
- Do **not** trust `block_offsets_complete` / `size` for completeness.
- Mitigate in Archivey: empty→stdlib fallback + ISIZE for non-empty silent EOF
  (see OpenSpec change).

---

## 2. Abort / `std::terminate` on truncated input (bug-class — contained)

A gzip, zlib or raw DEFLATE stream that ends early makes rapidgzip throw from a destructor
(`GzipChunk::determineUsedWindowSymbolsForLastSubchunk` → `BitReader::tell()`,
`std::logic_error` "The bit buffer should not contain more data than have been read from
the file!"), and `std::terminate` aborts the process. It fires for path, file-object and
`BytesIO` sources alike, from about 380 KB up; on an 8 MB gzip, 27 of 30 random cuts aborted
on Linux. The macOS build raises "Unexpected end of file when getting block ..." instead.
`IndexedBzip2File` never aborted in 110 tries.

A CRC mismatch is a complete stream with the wrong content, not a short one. For the DEFLATE
family it goes through the child like any other input, so an abort on it would be contained
the same way; gzip CRC32 damage raised `CorruptionError` in 8 runs of 8 without one. The
bzip2 decoder still runs in the caller's process: its stream-CRC damage, 12 bit flips and
4 cuts of a 3 MB stream, each read from a path and from a file object, raised
`CorruptionError` or read clean, with no abort in 40 runs. That is testing, not proof; an
input that aborts the bzip2 decoder would still end the caller's process.

Archivey runs the DEFLATE-family decoders in a child process, so the abort costs the member:
the parent reports `TruncatedError` when the abort message names this truncation, else
`CorruptionError` (known-issues Bug 4). This is the report worth filing upstream: a
destructor must not throw, and the input is only short, not hostile.

| Related Archivey notes | |
| --- | --- |
| Bug 1 — must `close()` accelerators | `known-issues.md` |
| Bug 3 — Python source raises → terminate | `known-issues.md` |
| Bug 4 — truncated DEFLATE → terminate | `known-issues.md` |

Soft EOF (§1) is separate from this abort class.

---

## 3. API notes useful to Archivey

| Fact | Implication |
| --- | --- |
| `parallelization=0` → `availableCores()` | Archivey passes `0` **intentionally** (all-cores + benchmarks). Not “sequential.” |
| No `eof()` / `is_complete` / last-error | No first-class incompleteness flag today |
| `verbose=` | Stats/profile only — does not harden EOF |
| CLI maps EOF to exit 1 with a clear message | Python API has no equivalent status |

---

## 4. If we ever request an upstream feature

Only if product needs it later — **feature request**, not a bug report:

> Expose `is_stream_complete()` / similar set when decode stops without a verified
> gzip footer (CRC/ISIZE), without relying on stderr.

Draft body: `UPSTREAM_TRUNCATION_REPORT.md` §7. **Not filing now.**

---

## 5. bzip2 (`IndexedBzip2File`)

Shares soft-EOF shape for very short prefixes; mid-stream more often raises than gzip.
No ISIZE twin; container CRC covers archive members. Document only unless bare `.bz2`
parity is required.
