## 0. Order

- [ ] 0.0 **Implement this change last, after `detection-evidence-ledger`.** It exposes what
      that change produces; there is nothing to expose before it, and the derived `confidence`
      and `detected_by` properties are derivations over its evidence types.

  **The `detection=` handoff may ship separately, after the field.** It additionally depends on
  `detection-prefix-workspace` for the replay buffer and spool policy that make it work on a
  non-seekable source. Being a new keyword argument with no change to existing behaviour, it
  needs no migration row and can follow.

  **Two public-value migrations land here** — the `sfx_scan` rename and four added
  `detected_by` values — so they happen once, while the redesign is already changing what
  callers observe, rather than as a second break later.

  **Inherited from `detection-prefix-workspace` review:** public exposure of
  `FormatInfo.cost_receipt` / `unavailable_tiers`, and attaching a receipt to
  `FormatDetectionError` on the miss path (review F14), belong here — not in the
  plumbing change. Until then those fields stay private (`compare=False`, `repr=False`)
  for tests and the fuzz harness.

  **Export surface (Decision 3A):** `archivey.detection_cost` is deliberately **not** in
  `archivey.__all__`. When exposing detection results publicly, decide deliberately:
  (1) what belongs at the package root, (2) what is niche enough for a documented
  subpackage (`archivey.detection_cost` or similar), and (3) what stays internal. Do not
  re-freeze seven budget/receipt names into the root by default.

## 1. Red tests first

- [ ] 1.1 Failing test: `open_archive(path).detection` (final name per design §Open Questions)
      carries the winning evidence — today the object is dropped at `core.py:386`
- [ ] 1.2 Failing test: the field is present and non-`None` for a `format=` open, carrying one
      `DECLARED_BY_CALLER` record
- [ ] 1.3 Failing test: `open_stream(path)` exposes the detected **container**, not only the
      stream codec
- [ ] 1.4 Failing test: `detect_format` then `open_archive(source, detection=result)` runs
      detection once, and the reader's ledger equals the standalone result
- [ ] 1.5 Failing test: the same result routed through `format=` is **not** stamped on a read
      failure, while through `detection=` it is — the two parameters must differ here
- [ ] 1.6 Failing test: `archivey info <archive>` calls `detect_format` zero times separately
      from the open

## 2. Retain the result

- [ ] 2.1 Stop discarding the `FormatInfo` at `core.py:386`; carry it onto the reader
- [ ] 2.2 Same for `open_stream`, which today returns `detected.format.stream` and loses the
      container
- [ ] 2.3 Subsume `internal/format_provenance.py` into the ledger — its
      `chosen_by` / `probe_only` pair is the interim version of what the ledger carries
- [ ] 2.4 The field is always present: never `None`, on any path

## 3. Declared evidence

- [ ] 3.1 `DECLARED_BY_CALLER` for `format=`, at class `ASSERTED`
- [ ] 3.2 `DECLARED_BY_CONTAINER` for a member stream's codec read from container metadata,
      **inheriting the container's achieved class** — not a class of its own
- [ ] 3.3 Verify a member of a checksum-validated 7z reports the container's class, not a guess

## 4. Derived properties and value migration

- [ ] 4.1 `confidence` becomes a property over the winning record's class
- [ ] 4.2 `detected_by` becomes a property over the winning record's kind
- [ ] 4.3 Rename `sfx_scan` → `prefixed_scan`
- [ ] 4.4 Reserve `zip_tail_probe` and `exhaustive_scan` for the revised
      `prefixed-archive-detection` to supply
- [ ] 4.5 Add `declared_by_caller` and `declared_by_container`
- [ ] 4.6 Render the full ledger in `__str__` / `__repr__` — kind, class, validation state per
      record, so "bounded probe **and** a matching name" is legible to a human
- [ ] 4.7 Remove any test that constructs a `FormatInfo` with an explicit confidence; that
      breakage is the intended forcing function

## 5. The `detection=` handoff

- [ ] 5.1 Add `detection=` to `open_archive` and `open_stream`; skip detection when given
- [ ] 5.2 `format=` and `detection=` together is a usage error
- [ ] 5.3 The result records an opaque source token; a mismatch raises. Document in the
      docstring that this is a typo-catcher, **not** an integrity check — it inherits the
      time-of-check-to-time-of-use window detect-then-open already has
- [ ] 5.4 Non-seekable sources: the replay buffer travels with the result, so the result is
      not a pure value object there and its lifetime is tied to the source's
- [ ] 5.5 A result whose buffer was released raises rather than re-reading bytes that are gone
- [ ] 5.6 Confirm `format=` still performs **no** detection I/O of any kind

## 6. Errors and the CLI

- [ ] 6.1 An error marked `format_unconfirmed` carries the evidence, or a stable reference to
      it, so the flag explains itself without a second detection
- [ ] 6.2 `cli/info_cmd.py:run_info` drops its `detect_format(archive)` call and reads the
      reader's field
- [ ] 6.3 `archivey info -v` renders the evidence ledger, not only the two derived scalars
- [ ] 6.4 `archivey info` over an ambiguous source names the tied candidates

## 7. End-to-end pin

- [ ] 7.1 `archivey info` over each golden fixture from `detection-evidence-ledger`, output
      compared against a committed expectation — this asserts the ledger survives the whole
      path from detection to public rendering, which is where `main` currently drops it
- [ ] 7.2 Include a fixture whose ledger holds both a probe hit and a matching name, since
      that composition is what a single scalar cannot carry

## 8. Docs and migration

- [ ] 8.1 `docs/opening-and-listing.md`: the detection field and how to read it
- [ ] 8.2 `docs/errors-and-diagnostics.md`: how an unconfirmed error explains itself
- [ ] 8.3 `docs/cli.md` if `info` output changes shape
- [ ] 8.4 Migration prose: `sfx_scan` → `prefixed_scan`, the four added values, and that an
      exhaustive match over `detected_by` breaks
- [ ] 8.5 Close the `FormatInfo.corroborated` entry in `dev-docs/IDEAS.md` if
      `detection-evidence-ledger` has not already
- [ ] 8.6 Settle the field and type names left open in design §Open Questions, and record the
      choice there

## 9. Verify

- [ ] 9.1 `uv run --no-sync pytest tests/test_detection.py tests/test_cli.py tests/test_streams.py`
- [ ] 9.2 The end-to-end `archivey info` pins (group 7) pass
- [ ] 9.3 `./scripts/check.sh --fix`
- [ ] 9.4 `./scripts/test.sh --all-configs`
- [ ] 9.5 `openspec validate --strict detection-result-surface`
