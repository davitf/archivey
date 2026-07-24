# Tasks — gzip truncation backstop for any seekable source

> Investigation + specs + implementation. Read `design.md` first (up-front ISIZE capture with the
> tri-state preserved, `SharedSource` views for non-owning + position-isolated access, Bug-3 trap
> shim + open decision). Run tooling through `uv` (`uv run pytest`, `uv run pyrefly check`,
> `uv run ty check`, `uv run ruff`). Test in all three dependency configs before pushing.

## 1. Confirm the mechanism generalizes

- [x] 1.1 Verify `_gzip_isize_from_source` returns the trailer for a seekable `BinaryIO`
      (`BytesIO`, caller file object) with position restored; confirm `_config_with_gzip_isize`
      currently discards the value.
- [x] 1.2 Confirm `_begin_stdlib_fallback` only runs after `old.close()` (no live rapidgzip when
      the fallback re-decodes), so a source rewind cannot race the accelerator.

## 2. Source lifetime + position isolation (Obstacle 1)

- [x] 2.1 Route a caller-owned source through `SharedSource` (`streamtools/shared.py`): open the
      accelerator over a view, and take separate views for the empty→stdlib rewind and the
      multi-member scan — non-owning (so `old.close()` leaves the caller source open) and
      position-isolated (so the scan does not race a live accelerator).
- [x] 2.2 "Does the accelerator have to read through a view?" — **resolved (FINDINGS.md)**: stream
      sources already go through a fileno-less `SharedSource.view(0)`, so rapidgzip is already on
      its Python path (no fd fast path to lose); option (i) — accelerator + scan each take a
      `view(0)` — is the path. (ii) is dead: the index-based sibling change was found
      infeasible. New sub-task: plumb a view factory to `_GzipTruncationCheckStream` (it gets only
      the one view today) so the scan can mint its own `view(0)`.
- [x] 2.3 Test: caller source is still open/readable after the archivey stream closes (parity
      with `test_ensure_bufferedio_does_not_close_raw_source` / `SharedSource` non-owning tests).

## 3. Bug-3 boundary (Obstacle 2)

- [x] 3.1 Spike (FINDINGS.md): truncated **non-raising** bytes do **not** trip Bug 3 (soft EOF,
      clean exit); a source **closed underneath** a live accelerator reproduces the documented
      SIGABRT. So the backstop's own target case is safe; the trap is hardening for racing/hostile
      sources.
- [x] 3.2 Trap **implemented**: `_TrappingSource` + `_AcceleratorStream` re-raise wiring, covered
      by the subprocess test `tests/test_accelerator_bug3_trap.py` (untrapped raw path aborts;
      archivey's trapped path exits cleanly and re-raises on the read path). Remaining follow-up:
      the wall-clock-timeout sweep to bound the separate finalizer-race trigger (`known-issues.md`).
- [x] 3.3 **Maintainer decision recorded: (a) trap-then-enable** ("do the trap at once to be
      safe"). Land the trap shim with the backstop; (c) config axis deferred as optional later
      refinement. Implement §4 on top of the trap.

## 4. Generalize the backstop

- [x] 4.1 Capture ISIZE up front (on `StreamConfig` or `_GzipTruncationCheckStream`) and drop the
      EOF path re-open in `_verify_not_truncated`. **Preserve the tri-state**: too-short
      (`< 18`) ⇒ raise on non-empty EOF; value ⇒ compare; unreadable ⇒ return — do not collapse
      "too short" and "unreadable" into one `None` (would silently drop the `size < 18`
      truncation). Capture the source length alongside the int.
- [x] 4.2 Make `_begin_stdlib_fallback` rewind an independent view of the seekable source instead
      of re-opening a path; keep the path branch unchanged.
- [x] 4.3 Remove the `isinstance(source, (str, os.PathLike))` gate in `GzipCodec.open` so the
      check stream wraps any declared-seekable source (subject to §3.3).
- [x] 4.4 Multi-member disambiguation on a non-path source: scan an independent `SharedSource`
      view (never seek/consume the live accelerator's source). The index-based alternative
      (closed as infeasible — `docs/internal/known-issues.md`) cannot remove it, so the scan stays — it
      just moves onto a locked view.

## 5. Tests + docs

- [x] 5.1 Truncated single-member gzip from `BytesIO` / file object → `TruncatedError`
      (mirror the path-source cases in `tests/test_accelerator_corruption.py`).
- [x] 5.2 Empty→stdlib fallback over a rewound source recovers the same prefix as a path source.
- [x] 5.3 Update `docs/gotchas.md` — remove "non-path sources" from the residual-holes row for
      bare `.gz` once §4 lands.
- [x] 5.4 `uv run pyrefly check` + `uv run ty check` clean; `uv run ruff format`; full suite in
      `[all]`, `[all-lowest]`, `[core-only]`.

## 6. OpenSpec

- [x] 6.1 `openspec validate --strict gzip-truncation-backstop-any-seekable` green.
- [ ] 6.2 Sync into main `seekable-decompressor-streams` when landing. The would-be conflicting
      sibling `gzip-multimember-detect-via-index` was closed as infeasible (`docs/internal/known-issues.md`) and will
      **not** sync, so this is the only `MODIFY` of "Accelerator errors translate uniformly" — no
      merge coordination needed. (If that sibling is ever revived, hand-author one merged
      requirement text rather than applying two independent MODIFYs.)
