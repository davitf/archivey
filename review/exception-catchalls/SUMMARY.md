# Summary — catch-all `except` clauses in `src/`

Run 2026-09-25 against `main` @ `5bbbfdc`, per [`brief.md`](brief.md). The fixes and this
record land in one pull request.

## Headline

The population is in the shape the brief predicted: of 67 blind handlers, 61 were right
as written or needed only a better comment. Six were wrong, and the brief's instinct
about where to look first was correct twice. Two unmarked handlers "re-raised" in the
sense ruff checks (a `raise` in the handler) while converting every exception to one
`ArchiveyError` type, which is the catch-all the error contract forbids. One C-boundary
trap was missing entirely from a backend, and that one aborted the interpreter. The four
reviewed after the password-confirmation PR merged (two deferred, two it added) were right
too.

Baseline: `[all]` leg 5174 passed, 39 skipped, 5 xfailed; `./scripts/check.sh` green.

## Findings

| # | Severity | Where | What a caller saw | Status |
|---|---|---|---|---|
| F1 | high | `codecs.py` bzip2 accelerator open | `open_stream(fileobj, seekable=True)` on a `.bz2` with rapidgzip installed: an `OSError` from the caller's stream crossed into rapidgzip's C++ callback and **aborted the process** (`std::invalid_argument`). The gzip/zlib/deflate path had the `_TrappingSource` shim; the bzip2 path never did | Fixed: every rapidgzip decoder opens through `_open_accelerator`, which traps a caller-owned source |
| F2 | medium | `verify.py` `MemberVerifier._read_sized_all` | A corrupt deflate member read with `read()` raised `TruncatedError`; the same member read with `read(n)` raised `CorruptionError`. The handler relabelled every raw decoder error as truncation (only `OSError` / `MemoryError` escaped) | Fixed: the raw error propagates to the `ArchiveStream` translator, as on the bounded path |
| F3 | medium | `rar_parser.py` RAR3 and RAR5 encrypted-header walks | A header-encrypted RAR cut inside a header's salt (RAR3) or IV (RAR5) raised `EncryptionError` "Failed to decrypt … headers", even with the right password, after the reader tried every candidate. Also any `OSError` or bug in that span | Fixed: the handlers are gone; nothing in that span depends on the password, so its errors propagate as they are (`CorruptionError` for the short read). The one password-dependent step in front, normalizing a `bytes` candidate, now raises a wrong-password `EncryptionError` for bytes with no Unicode form (and, on RAR5, for a password whose 127-unit cut splits a surrogate pair), so the candidate loop moves on (on the RAR5 walk that also fixes a raw `UnicodeDecodeError` the base already let out) |
| F4 | low | `verify.py` over-run probe (two sites, now `_probe_past_declared`) | An `OSError` or `MemoryError` on the read one byte past a member's declared size was taken as "no trailing data" | Tightened: those propagate; an opaque decoder error there still reads as end of data, with the reason written down |
| F5 | low | `codecs.py` `_AcceleratorStream` read / readinto / seek, and accelerator open | When the trap's EOF-shaped answer made rapidgzip raise its own error, that error propagated and the real source fault stayed parked. A fault parked while the decoder opened waited for the first read | Fixed: the parked fault wins (the accelerator's error is its `__context__`), and an open-time fault raises at open |
| F6 | low | `base_reader.py` `_maybe_teardown` | A `KeyboardInterrupt` in the backend's close left the lifecycle at `TEARDOWN_RUNNING`, against the docstring's "marked complete even when `_close_archive` fails". Nothing reads the state today | Fixed: `complete_teardown` runs in a `finally` |

Each fix has a red-then-green test in `tests/test_exception_handlers.py` (F1, F2, F4,
F5, F6) or `tests/test_rar_parser.py` (F3).

### What callers now see differently

- F2: a corrupt member read through `read()` raises `CorruptionError` where it raised
  `TruncatedError`. Where rapidgzip raises its opaque `std::exception` / "Unknown
  exception" while still short of the declared size, the translator's mapping
  (`CorruptionError`) now applies on this path too; that platform wrinkle is already
  documented at `_translate_rapidgzip`, and the tests accept either type there.
- F3: `CorruptionError` instead of `EncryptionError` for a header-encrypted RAR cut inside
  a salt or IV. A non-UTF-8 `bytes` password on a header-encrypted RAR5 raises
  `EncryptionError` where it raised `UnicodeDecodeError`.
- F1, F4, F5: the caller's own `OSError` / `MemoryError` / interrupt, where they got a
  process abort, silence, or a translated accelerator error.

The error contract (`CONTRIBUTING.md` §Exception translation) settles every one of these,
so none went to the maintainer as a decision.

## Census

67 handlers on `5bbbfdc`, counted from the AST (the brief counted 55 on `8e88e4f`; the
difference is code that landed since): 37 catch `BaseException`, 30 catch `Exception`, 36
carry `# noqa: BLE001`. Five sat in files two open pull requests were changing. The three
in `extraction.py` were reviewed once the streaming-extraction PR merged, and the two in
`sevenzip_reader.py` once the password-confirmation PR merged (`c599fc5`). That PR also
added two handlers in `password_confirm.py`, reviewed with them, which makes 69. Pattern
names are the ones in
[`dev-docs/topics/exception-handlers.md`](../../dev-docs/topics/exception-handlers.md).

Locations are by function, because line numbers drift.

| File | Function | Catches | Pattern | Disposition |
|---|---|---|---|---|
| `core.py` | `open_archive` | Base | cleanup and re-raise | keep |
| `core.py` | `_open_resolved` | Base | cleanup and re-raise | keep |
| `core.py` | `open_stream` | Base | cleanup and re-raise | keep |
| `reader_state.py` | `ReaderState.mark_reader_closed` | Base | cleanup and re-raise | keep |
| `base_reader.py` | `_maybe_teardown` | Exc | error combination | fixed (F6) |
| `base_reader.py` | `_pull_member` | Base | cleanup and re-raise | keep |
| `base_reader.py` | `_materialize_members` | Base | cleanup and re-raise | keep (reason already in code) |
| `base_reader.py` | `open` (unbound stream close) | Exc | primary error wins | keep |
| `base_reader.py` | `_close_public_streams` | Base | error combination | keep |
| `base_reader.py` | `_ProgressivePassIterator.__next__` ×3 | Base | cleanup and re-raise | keep |
| `base_reader.py` | `_TranslatedErrorBoundary.__exit__` | Base | translator handoff | keep |
| `volumes.py` | `ConcatenatedFile.read` | Base | cleanup and re-raise | keep |
| `diagnostics_collector.py` | `DiagnosticCollector.emit` | Exc | error combination (deferred raise) | keep |
| `backends/rar_parser.py` | RAR3 header walk | Exc | none: converting | removed (F3) |
| `backends/rar_parser.py` | RAR5 header walk | Exc | none: converting | removed (F3) |
| `backends/rar_reader.py` | `_UnrarOwnedStream.close` ×2 | Base | error combination | keep |
| `backends/rar_reader.py` | `_UnrarRespawnStream.close` | Base | error combination | keep |
| `backends/rar_reader.py` | `_bounded_member_pipe` | Base | cleanup and re-raise | keep |
| `backends/rar_reader.py` | `_materialize_stream_volumes` | Base | cleanup and re-raise | keep |
| `backends/rar_reader.py` | `_ensure_archive_path` | Base | cleanup and re-raise | keep |
| `backends/rar_reader.py` | `_iter_with_data._pipe` ×2 | Base | cleanup and re-raise | keep |
| `backends/rar_reader.py` | `_open_member._spawn` ×2, `_open_member` | Base | cleanup and re-raise | keep |
| `backends/iso_reader.py` | `IsoReader.__init__` | Base | cleanup and re-raise | keep (reason already in code) |
| `backends/tar_reader.py` | `TarReader.__init__` | Base | cleanup and re-raise | keep |
| `backends/sevenzip_reader.py` | `_open_member` ×2 | Base | cleanup and re-raise | keep (reviewed after the password-confirmation PR merged): the slice and the password watch own what they wrap, and the watch stays silent when closed before any read |
| `password_confirm.py` | `UnverifiedPasswordReadWatch.read`, `.seek` | Base | cleanup and re-raise | keep (added by the password-confirmation PR): drops the close-time report, since a failed read or seek already told the caller; the original propagates |
| `extraction.py` | `_write_file_atomic`, `_place_link` | Base | cleanup and re-raise | keep (reviewed after the streaming-extraction PR merged) |
| `extraction.py` | `_close` | Exc | teardown hygiene | keep; reason sharpened: both callers close after the member's result is recorded |
| `streams/archive_stream.py` | `_attach_finalizer._finalize` ×2 | Exc | teardown hygiene | keep |
| `streams/archive_stream.py` | `_ensure_open` (open) | Exc | translator handoff | keep |
| `streams/archive_stream.py` | `_ensure_open` (close raced) | Exc | primary error wins | keep |
| `streams/archive_stream.py` | `read`, `seek` | Exc | translator handoff | keep |
| `streams/archive_stream.py` | `_note_raised_seek` ×2 | Exc | primary error wins | keep |
| `streams/archive_stream.py` | `close` ×3 | Exc | error combination | keep |
| `streams/codecs.py` | `_AcceleratorStream._close_inner` | Exc | teardown hygiene | keep |
| `streams/codecs.py` | `_AcceleratorStream.nearest_resume_offset` | Exc | diagnostic probe | keep |
| `streams/codecs.py` | `_TrappingSource` ×5 | Base | C-boundary trap | keep; bzip2 gap fixed (F1) |
| `streams/codecs.py` | `_GzipTruncationCheckStream._begin_stdlib_fallback` | Exc | teardown hygiene | keep |
| `streams/codecs.py` | `_Bzip2EmptyStreamCheck._begin_stdlib_fallback` | Exc | teardown hygiene | keep |
| `streams/decompress.py` | `PpmdDecoder._quiesce_worker` | Exc | teardown hygiene | keep |
| `streams/decompressor_stream.py` | `DecompressorStream.__init__` | Base | cleanup and re-raise | keep |
| `streams/decompressor_stream.py` | `DecompressorStream.__init__` (inner close) | Exc | primary error wins | keep |
| `streams/verify.py` | over-run probe ×2 | Exc | diagnostic probe | tightened (F4) |
| `streams/verify.py` | `_verify_reaches_declared` | Base | cleanup and re-raise | keep |
| `streams/verify.py` | `_read_sized_all` | Exc | none: converting | fixed (F2) |
| `streams/verify.py` | `MemberVerifier.read` ×3 | Exc | cleanup and re-raise | keep; bare marker given its reason |
| `streams/verify.py` | `VerifyingStream.__init__` | Base | cleanup and re-raise | keep |
| `streams/verify.py` | `VerifyingStream.__init__` (inner close) | Exc | primary error wins | keep |

The fixes add five handlers, all re-raising: `_AcceleratorStream` read / readinto / seek
and the decoder open in `_open_accelerator` catch `Exception` and raise the parked fault in
its place (an interrupt passes through untouched), and `_open_accelerator` closes the new
stream on `BaseException` before re-raising. `_read_sized_all`'s handler widened from a
converting `except Exception` to `except BaseException: abandon; raise`, the same shape as
`_verify_reaches_declared`.

## Answers to the brief's questions

- **Unmarked handlers (step 2).** 31 unmarked. 27 clean up and re-raise the original, and
  `DiagnosticCollector.emit` holds a callback's error for its caller to raise. The other
  three are the ones ruff's exemption let through: the two RAR walks and
  `_read_sized_all` each end in `raise SomethingElse(...) from exc`, which ruff counts as a
  re-raise. That is exactly the case the contract calls a catch-all. The marker's absence
  is a claim about the *raise statement*, not about the exception that leaves.
- **Pattern 5, `verify.py` "≈ no trailing data".** It cannot turn a content failure into
  a pass on a sequential read: the digests are checked after the probe, on bytes already
  delivered. It could swallow an `OSError` / `MemoryError`, which is F4. After a seek the
  checksum was already forfeited, so the probe was, and remains, the only over-run check
  there.
- **Pattern 1, teardown.** No site can lose an integrity verdict: every content verdict
  fires from a read (ADR 0014), and the teardown sites run after that or on objects
  already being replaced.
- **Pattern 3, `_fail`.** It raises on every path: an `ArchiveyError` is stamped and
  re-raised, a closed-source `ValueError` becomes `ArchiveyUsageError`, a translated one
  is raised `from` the original, and an unrecognized one is re-raised unchanged. Its
  return type is `NoReturn`, so a path that fell through would be a type error.
- **Pattern 2, combination.** Every path reaches a `raise`; no early return.
- **Pattern 4, "always precedes data reaching the caller".** True for read / seek, but
  only after F5: before it, an accelerator error raised on the shim's EOF answer
  propagated in place of the parked fault. On a **trapped `KeyboardInterrupt` on a handle
  closed without reading**: after F5 it cannot happen. Python delivers interrupts to the
  main thread, so the only fault parked outside a caller's call is a background worker's
  prefetch, which is never an interrupt. A fault parked during the open raises at the
  open. The reasoning is now in the `_reraise_trapped` comment.
- **`BaseException` reach.** All 37 existing `BaseException` handlers either re-raise
  after cleanup (so an interrupt still propagates, and the cleanup is exactly what an
  interrupt must not skip) or are the C-boundary trap (an interrupt must not unwind
  through C++). None swallows one.

## What is actually fine

Most of it, and for reasons worth keeping:

- **Cleanup and re-raise is the house default, and it is applied consistently.** Every
  site that builds something another object will own (a reader, a subprocess, a temp
  file, a decoder) guards the window before ownership passes with `except BaseException:
  release; raise`. `BaseException` is right there: an interrupt that skips the release
  leaks an `unrar` process or pins a file.
- **The `noqa: BLE001` reasons were accurate.** Of 36 markers, every reason still
  described its handler, apart from the two over-run probes (F4). The only marker without a reason (`MemberVerifier.read`, the
  abandoned/verified branch) now has one.
- **Error combination is careful.** `ArchiveStream.close`, `_maybe_teardown` and
  `_close_public_streams` always mark the object closed, then raise one error or an
  `ExceptionGroup`, never neither. `_UnrarOwnedStream.close` reaps `unrar` even on an
  interrupt.
- **The translator handoff never converts.** `_fail` and `_TranslatedErrorBoundary`
  re-raise an unrecognized exception unchanged, as the contract asks.
- **The C-boundary trap is sound where it exists**, and its comment already argued the
  error case correctly; F1 is a site that never used it, not a flaw in it.

## Observations, not findings

- The cleanup-and-re-raise shape lets a failing cleanup replace the original error (it
  becomes the `__context__`). `base_reader.open` attaches the cleanup failure as a note
  instead; the other sites do not. No site found where cleanup is likely to fail after
  the primary error, so this is recorded rather than changed.
- While building F3's repro: a RAR (encrypted headers or not) cut exactly at a header
  boundary lists the members before the cut with no diagnostic, and a header-encrypted
  RAR3 cut mid-header reports a wrong password with the right one. Neither is a
  catch-all; both are tracked internally for a separate look (a missing end-of-archive
  block is legal in old RAR versions, so the first needs care).
