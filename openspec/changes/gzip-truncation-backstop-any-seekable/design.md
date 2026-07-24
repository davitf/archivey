# Design — gzip truncation backstop for any seekable source

## Scope

Generalize `_GzipTruncationCheckStream` from **path-only** to **any declared-seekable source**.
No new public API; no format/detection change. The end state is that a bare seekable
`BinaryIO` of a truncated single-member gzip, read through rapidgzip with no container-declared
size, raises `TruncatedError` the same way a path source does today.

## Why the path restriction is incidental (code facts)

| Path use today | Generalization |
| --- | --- |
| `_verify_not_truncated` re-opens the path at EOF to read ISIZE | Capture ISIZE up front (already read by `_gzip_isize_from_source`; value currently discarded — keep it). **Preserve the tri-state** — see the ISIZE-capture note below |
| `_begin_stdlib_fallback` builds `GzipDecompressorStream(self._source_path)` from offset 0 | Rewind an independent view of the source (`seek(0)`) and hand it to the same stdlib engine — fallback only runs **after** `old.close()`, so no cursor conflict with the (now-closed) accelerator |
| multi-member magic scan opens a fresh handle | Scan an independent **`SharedSource` view** of the same source (see Obstacle 1) — its per-view position + shared lock make the scan safe even while the accelerator is still live — or, preferably, the sibling change `gzip-multimember-detect-via-index` removes this scan entirely |

`_config_with_gzip_isize` already calls `_gzip_isize_from_source` for any seekable source but
only stores `gzip_isize_backstop=True`. Plumbing the **int** (onto `StreamConfig` or the check
stream) is the core of the mechanism change.

**The multi-member scan is *not* guaranteed to run after the accelerator is closed.** The
empty-EOF `_begin_stdlib_fallback` (the branch that closes `old`) is the only place rapidgzip is
retired; the **non-empty** soft-EOF path calls `_verify_not_truncated` — and therefore the
multi-member scan — while `self._inner` is *still the live rapidgzip stream*, and the caller may
keep seeking it afterward. So the scan cannot consume, reposition, or close the accelerator's
source. For a path source today this is dodged by opening a fresh independent OS handle
(`gzip_has_additional_member`'s docstring assumes exactly that); a non-path source has no second
handle, so the scan must use an independent, position-isolated view — which is what
`SharedSource` provides (Obstacle 1).

### ISIZE-capture note — preserve the tri-state (do not regress `size < 18`)

`_gzip_isize_from_source` returns `None` for **two distinct** conditions: the source is *too
short* for a complete member (`< 18` bytes) **and** the source is unreadable / non-seekable /
errored. `_verify_not_truncated` today splits them at EOF: `size < 18` **raises**
`TruncatedError` on a non-empty delivery, while an `OSError` **returns** (no raise). This is
reachable now — a `< 18`-byte truncated `.gz` under `use_rapidgzip=ON` wraps in
`_GzipTruncationCheckStream` and raises. If the generalization plumbs a single `int | None` and
treats `None` as "no backstop", that truncation is **silently missed** — a safety regression, the
exact class this change exists to close. Capture must keep the tri-state: *(too-short ⇒ raise on
non-empty EOF)* / *(value ⇒ compare)* / *(unreadable ⇒ return)* — e.g. capture the source length
alongside the ISIZE int, not just the int.

## Obstacle 1 — caller's source: never close it, never clobber its position

Two problems, one root cause — multiple consumers (the live accelerator, the empty→stdlib
rewind, the multi-member scan) touching one caller-owned source with one file position:

1. **Never close it.** `_begin_stdlib_fallback` does `old.close()`, and
   `_AcceleratorStream.close()` closes the raw rapidgzip object, which may close the file object
   rapidgzip was opened over. archivey must never close a caller-owned source.
2. **Never clobber its position.** The multi-member scan (and, in principle, the up-front ISIZE
   read) must seek the source while the accelerator is still live and the caller may seek it
   afterward — a naive `seek`+restore on the shared handle races the accelerator.

**Reuse `SharedSource` (`streamtools/shared.py`).** It already solves both: it mints
independent, **non-owning**, per-view-position seekable views over one seekable source, each
re-seeking the underlying under a shared lock so seek+read is atomic (the streamtools analogue of
stdlib `zipfile._SharedFile`; the single-file reader already routes accelerator member streams
through it — see `known-issues.md` Bug 3 mitigation). Concretely:

- open the accelerator over a `SharedSource` view of the caller's source (not the raw source);
- the empty→stdlib fallback and the multi-member scan each take their **own** view — independent
  position, coordinated by the shared lock, so neither disturbs the live accelerator;
- `SharedSource.close()` never closes a caller-owned handle, so `old.close()` stops rapidgzip's
  C++ worker (still required — lifecycle requirement) without taking the caller's stream down.

For a **path** source nothing changes — archivey owns the fd and can keep opening fresh
independent handles (`SharedSource` for a path owns and closes its handle).

**Open question — does the accelerator have to read through a view?** For the scan's view to
coordinate with the accelerator, the accelerator must *also* read through a `SharedSource` view
(sharing the same lock); a raw-handle accelerator plus a separate `SharedSource` do **not** share
a lock. But feeding rapidgzip a Python view (a `SlicingStream`, no `fileno()`) forces it onto its
Python-object read path, losing the `mmap`/parallel fast path it gets from a real fd and keeping
it on the Bug-3-prone boundary (Obstacle 2). Two ways out, to decide during the spike: (i) accept
the view for non-path sources (simplicity, at a perf/Bug-3 cost the caller opted into by passing a
file object under `ON`); or (ii) prefer the sibling `gzip-multimember-detect-via-index` — its
index answers "≥2 members?" with **no** source re-read, so no independent scan view is needed and
the accelerator can read the source however it likes. (ii) makes the two changes complementary:
the index removes the only consumer that needed a concurrent scan view.

## Obstacle 2 — rapidgzip Bug 3 (`terminate()` on a raising Python source)

`docs/internal/known-issues.md` Bug 3: rapidgzip can `terminate()` the **process** when a
*Python* source object raises during decode (undefined finalizer ordering across the C++/Python
boundary). This is why the truncation sweep deliberately used path sources only.

**It is not introduced by this change** — production already opens rapidgzip over non-path
seekable sources (`return stream` in `GzipCodec.open`), so the exposure exists today with *less*
safety, not more. But generalizing the backstop means we deliberately drive more file-object
traffic through the accelerator, so we should harden the boundary first.

**Proposed mitigation (to validate): an exception-trapping source shim.** Wrap the caller source
in an inner adapter whose `read`/`seek`/`readinto` **never raise into rapidgzip**: on an
underlying error it stores the exception, returns a benign EOF-shaped result (`b""` / short) to
the C++ layer, and the `_AcceleratorStream` outer wrapper checks for a stored exception after
each accelerator call and re-raises it (translated). This converts a process-abort into a normal
Python exception on the archivey side.

Open questions this raises (measure before committing):

- Does returning `b""` to rapidgzip on a trapped inner error reliably produce a clean stop
  (soft-EOF), or can it still wedge a worker thread? Needs the same wall-clock-timeout sweep the
  investigation used.
- Is the trap needed at all for *seekable file* sources, or is Bug 3 specific to sources that
  themselves raise (vs. plain truncated bytes, which do not raise — they just end)? If a
  `BytesIO`/file of truncated bytes never raises, Bug 3 may not fire for the exact case this
  change targets, and the trap becomes belt-and-suspenders rather than a prerequisite.

## Open decision (for the maintainer)

**Do we gate rapidgzip-over-file-objects, and how?** Options:

- **(a)** Land the trap shim, then enable the backstop on any seekable source unconditionally.
- **(b)** Keep using rapidgzip on file objects as today but add the backstop, treating the trap
  as a separate hardening change (accept the pre-existing Bug-3 exposure unchanged in the
  interim).
- **(c)** Add a config axis "prefer speed vs. absolute robustness" that decides whether
  file-object sources use rapidgzip at all (the maintainer's framing), with this backstop active
  whenever rapidgzip is chosen.

Leaning **(b)** for the backstop itself, with the trap shim and/or (c) as a **follow-up** — but
this is the maintainer's call and the reason this change ships as investigation + specs first.
Note the honest caveat: (b) does **not** leave Bug-3 exposure *identical* to today. Today a bare
non-path source hits `return stream` and the wrapper does none of `old.close()`, a rewind
re-decode, or a scan; the backstop adds those extra crossings of the C++/Python boundary on
file-object sources, each a place Bug 3 can fire (`known-issues.md`: it fires on `read()`,
`close()`, and the finalize guard alike). So (b) trades *strictly more safety against truncation*
for *bounded new Bug-3 surface* — a real, if small, widening, not a no-op. The spike (task 3.1)
should measure whether truncated non-raising bytes actually trip Bug 3 before (b) is accepted as
"safe enough for now".

## Testing

- Truncated single-member gzip from a `BytesIO` / caller file object, `use_rapidgzip=ON`, no
  declared size → `TruncatedError` (parity with the existing path-source tests in
  `tests/test_accelerator_corruption.py`).
- Caller-owned source is **still open and readable** after the archivey stream closes
  (non-owning `SharedSource`) — mirror `test_ensure_bufferedio_does_not_close_raw_source` /
  the `SharedSource` non-owning tests.
- Multi-member disambiguation on a non-path source runs concurrently with a **live** accelerator
  (non-empty soft-EOF path): a valid concatenated `BytesIO` gzip read to EOF under
  `use_rapidgzip=ON` does not raise, and the scan's view does not disturb a subsequent caller
  seek on the accelerator stream.
- `size < 18` truncated `.gz` from a `BytesIO`/file object under `ON` → `TruncatedError` (the
  tri-state must survive up-front capture — see the ISIZE-capture note).
- Empty→stdlib fallback over a rewound `BytesIO` recovers the same prefix a path source does.
- Bug-3 sweep with a raising file object behind rapidgzip under a wall-clock timeout (no process
  abort) — reuse `scripts/rapidgzip_truncation_sweep.py` shape.
- Three dependency configs (`[all]`, `[all-lowest]`, `[core-only]`); rapidgzip-gated tests skip
  cleanly on core-only.
