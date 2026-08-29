## 1. Pin the current shape, then break it

- [ ] 1.1 Add an instrumented source that counts reads, forward seeks, backward seeks and
      unique bytes fetched, usable from tests over paths, seekable streams and
      `PeekableStream`
- [ ] 1.2 Characterisation test recording today's numbers (gzip 5 backward seeks, ISO 2 with
      a 32 774-byte re-read from zero) so the improvement is visible in the diff rather than
      asserted
- [ ] 1.3 Failing test: zero backward seeks and at most one seek towards the end, for every
      source kind and every tier that runs at `BALANCED`
- [ ] 1.4 Failing test: growing 4 KiB → 32 KiB → 2 MiB fetches each byte exactly once

## 2. The prefix workspace

- [ ] 2.1 New workspace type owning the source handle and a monotonically growing buffer,
      serving range requests as buffer slices or delta reads
- [ ] 2.2 Path sources keep **one** detection handle for the whole detection, replacing the
      reopen in `_peek_prefix` and the second handle in `_make_probe_read_at`
- [ ] 2.3 Seekable caller streams record the entry position, read forward once, restore once
      in an exception-safe exit path — with a test for the error path, not only the happy one
- [ ] 2.4 Non-seekable sources use the same replay buffer the backend will consume; no
      second buffering layer
- [ ] 2.5 Retire `_peek_prefix`, the `peek_more` closures and `_make_probe_read_at` in favour
      of workspace views; keep the inner-TAR probe's bounded-reader semantics intact

## 3. Candidate-relative views

- [ ] 3.1 Add a candidate-relative range view; a read of length N at candidate origin O
      returns the same bytes as an absolute read at O, without a second fetch
- [ ] 3.2 Give needle declarations a candidate-internal offset (TAR `ustar` → 257, gzip → 0)
      and compute candidate origin as `hit - declared_offset`
- [ ] 3.3 Discard a hit whose computed candidate origin would be negative
- [ ] 3.4 Route the existing SFX scan (`sfx.py: find_magic_in_prefix`) through it, so hits
      carry candidate origins rather than raw needle positions

## 4. Budget, capabilities, receipt

- [ ] 4.1 `DetectionBudget` with the eleven fields in the delta spec, including the separate
      `max_far_bytes` that today's "4 096 near; ISO far" configuration contradicts
- [ ] 4.2 `DetectionCostReceipt`, charged by the workspace as reads happen — not reconstructed
      afterwards
- [ ] 4.3 `DetectionCapability` and `source.capabilities(budget)`; verify a zero-seek budget
      withdraws `SEEK` from a file and a spool policy grants `TAIL` to a pipe
- [ ] 4.4 `detect_format` accepts a budget; default `BALANCED`
- [ ] 4.5 `REMAINING_KNOWN` is measured from the caller's entry position, and an overestimated
      total size never proves a later offset reachable

## 5. Presets and the spool policy

- [ ] 5.1 `BALANCED`, `FAST`, `THOROUGH` as specified; ZIP tail tier outside `BALANCED`
- [ ] 5.2 Bounded spool-to-temporary-file policy, off by default, sharing the spooled object
      with the backend; abandons within the bound when the source exceeds it
- [ ] 5.3 A tier that cannot run is recorded as unavailable rather than silently skipped —
      the record shape is consumed by `detection-evidence-ledger`, so keep it minimal here

## 6. Cost-model boundaries

- [ ] 6.1 Assert the detection receipt is not merged into `CostReceipt` / `ArchiveInfo.cost`
- [ ] 6.2 Align capability and work-kind naming with `access-mode-and-cost` so the two
      receipts read as one cost model

## 7. Bounding detection's decode work

- [ ] 7.1 Add a decoy-dense seed family to `tests/atheris_fuzz`'s `detect_format` target:
      back-to-back near-miss headers for each scan-tier format
- [ ] 7.2 Assert the aggregate cost receipt stays inside the budget's limits — this pins the
      invariant regardless of how the aggregate-versus-per-candidate scope resolves, and is
      what lets that question stay open
- [ ] 7.3 Register the unbounded-detection-decode gap in `dev-docs/threat-model.md` (O1
      scopes to listing-time metadata bombs; `ExtractionLimits` scopes to `extract`; nothing
      covers detection), noting the measured 683-fold amplification and which change closes it

## 8. Sequencing notes

- [ ] 8.1 Record in `prefixed-archive-detection` that its makeself and TAR self-extracting
      needles depend on the candidate-relative view landing here
- [ ] 8.2 Record the two open questions (budget scope; pricing a source in round trips) in
      `dev-docs/IDEAS.md` or the design's Open Questions, whichever the maintainer prefers as
      the durable home

## 9. Verify

- [ ] 9.1 `uv run --no-sync pytest tests/test_detection.py tests/test_streams.py`
- [ ] 9.2 The access-shape assertions from group 1 pass for paths, seekable streams and pipes
- [ ] 9.3 `uv sync --group fuzz --group dev --extra all && uv run --no-sync python -m tests.atheris_fuzz --smoke`
- [ ] 9.4 `./scripts/check.sh --fix`
- [ ] 9.5 `./scripts/test.sh --all-configs`
- [ ] 9.6 `openspec validate --strict detection-prefix-workspace`
