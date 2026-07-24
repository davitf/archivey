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
| multi-member magic scan opens a fresh handle | Scan an independent **`SharedSource` view** of the same source (see Obstacle 1) — its per-view position + shared lock make the scan safe even while the accelerator is still live. (The index-based alternative was found infeasible and its change closed — rapidgzip exposes no member boundaries; see `docs/internal/known-issues.md`.) |

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

**Does the accelerator have to read through a view? — RESOLVED by the spike (`FINDINGS.md`).**
For the scan's view to coordinate with the accelerator, the accelerator must *also* read through
a `SharedSource` view (sharing the same lock); a raw-handle accelerator plus a separate
`SharedSource` do **not** share a lock. The spike found this is already how archivey runs: the
single-file reader wraps a seekable stream in `SharedSource` and hands the codec
`self._shared.view(0)`, and rapidgzip's `_hasValidFileno` is **False** on that view (`SlicingStream.fileno()`
raises), so rapidgzip is **already** on its Python read path for stream sources today — there is
no `mmap`/fd fast path to forfeit (that path is reached only by *path* sources, where archivey
passes the path string and rapidgzip opens its own fd). So option **(i)** — the accelerator and
the scan each take a `SharedSource.view(0)` — costs nothing new for stream sources, and is the
path to take.

Two consequences the spike nailed down:
- The earlier fallback "(ii) defer the scan to an index-based sibling change" is **dead**:
  that sibling's index spike found rapidgzip 0.16.0 does **not** expose member boundaries, so it
  cannot remove the scan. This change must keep the scan, on an independent view.
- Even a *real-fileno* source is not safe to share raw: the spike showed the fd path moves the
  caller's file position (rapidgzip `dup`s the description, shared offset), so a concurrent scan on
  the same raw handle would clobber. Locked `SharedSource` views are required regardless.

The one piece of new work is **layering**: the codec-level `_GzipTruncationCheckStream` currently
receives only the single `view(0)` as its `source` and has no view factory. Give it a way to mint
a second `view(0)` (pass the `SharedSource`, or a `Callable[[], BinaryIO]` view-factory, down to
the check stream) so the scan gets its own position-isolated view.

## Obstacle 2 — rapidgzip Bug 3 (`terminate()` on a raising Python source)

`docs/internal/known-issues.md` Bug 3: rapidgzip can `terminate()` the **process** when a
*Python* source object raises during decode (undefined finalizer ordering across the C++/Python
boundary). This is why the truncation sweep deliberately used path sources only.

**It is not introduced by this change** — production already opens rapidgzip over non-path
seekable sources (`return stream` in `GzipCodec.open`), so the exposure exists today with *less*
safety, not more. But generalizing the backstop means we deliberately drive more file-object
traffic through the accelerator, so we should harden the boundary first.

**Mitigation — an exception-trapping source shim (spike-validated, `FINDINGS.md`).** Wrap the
caller source in an inner adapter whose `read`/`seek`/`readinto` **never raise into rapidgzip**:
on an underlying error it stores the exception, returns a benign EOF-shaped result (`b""` / `0`)
to the C++ layer, and the `_AcceleratorStream` outer wrapper checks for a stored exception after
each accelerator call and re-raises it (translated). The spike reproduced the exact documented
abort (`std::invalid_argument: Cannot convert nullptr Python object …`, SIGABRT) by closing a
source underneath a live accelerator, and confirmed the trap turns it into a **clean exit** with
the `ValueError` captured for re-raise. So the shim works for the source-raises/closed trigger.

Residual questions the spike answered or bounded (finish in the sweep before committing):

- **Is the trap needed for the change's own target case?** The spike showed **no** — a truncated
  single-member gzip of plain **non-raising** bytes soft-EOFs without any abort. Truncated bytes
  just end; nothing raises into rapidgzip. So the trap is *hardening for hostile/racing sources*,
  not a prerequisite for the backstop's own correctness.
- **Does the trap cover every abort path?** It covers faults surfacing inside a wrapped Python
  call (`readinto`/`seek`/`tell`). It does **not** obviously cover the separate finalizer-race
  trigger `known-issues.md` notes ("path-source truncations/CRC mismatches can `std::terminate`
  during worker finalization after a Python exception"). Bound that corner with the same
  wall-clock-timeout sweep (`scripts/rapidgzip_truncation_sweep.py` shape) before calling the trap
  complete.

## Open decision (for the maintainer)

**Do we gate rapidgzip-over-file-objects, and how?** Options:

- **(a)** Land the trap shim, then enable the backstop on any seekable source unconditionally.
- **(b)** Keep using rapidgzip on file objects as today but add the backstop, treating the trap
  as a separate hardening change (accept the pre-existing Bug-3 exposure unchanged in the
  interim).
- **(c)** Add a config axis "prefer speed vs. absolute robustness" that decides whether
  file-object sources use rapidgzip at all (the maintainer's framing), with this backstop active
  whenever rapidgzip is chosen.

**Maintainer lean (2026-07): (a) trap-then-enable — "do the trap at once to be safe".** The
spike makes this cheap: the trap is validated against the documented abort (`FINDINGS.md`), and
landing it first removes the honest caveat that (b) carried — namely that the backstop adds extra
C++/Python crossings on file-object sources (`old.close()`, the rewind re-decode, the scan), each
a place Bug 3 can fire (`known-issues.md`: it fires on `read()`, `close()`, and the finalize
guard alike). With the trap in place those crossings surface as translated errors instead of a
process abort, so (a) enables the any-seekable backstop without widening the abort surface.
Option (c) (a speed-vs-robustness config axis) remains a reasonable *later* refinement but is not
required to land the backstop safely. Remaining gate before "done": the wall-clock-timeout sweep
to bound the finalizer-race corner the read/seek trap may not cover (above).

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
