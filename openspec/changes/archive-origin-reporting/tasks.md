## 0. Order

- [ ] 0.0 **Implement after `prefixed-archive-detection`**, which defines `PrefixKind`.
      This change reuses that enum rather than adding a second, coarser spelling of the
      same property on `ArchiveInfo`. `MagicHit` — the other dependency — landed with
      `detection-prefix-workspace` (#273).

  **If the order inverts**, ship the resolver unification and `payload_offset: int | None`
  alone and add `prefix_kind` with the other change (`design.md` §Sequencing). That is a
  fallback: it costs two public-shape changes where one would do.

  **It blocks nothing.** `detection-result-surface` touches the reader's detection field,
  not `ArchiveInfo`, and neither change reads the other's field.

## 1. Pin the current behaviour, then change it

- [ ] 1.1 Characterisation test recording today's asymmetry: an SFX 7z / RAR / ZIP opened
      auto-detected and with `format=`, asserting what the caller can learn about the
      origin in each case (today: nothing, on either path)
- [ ] 1.2 Failing test: the two open paths report the same `payload_offset` for the same
      SFX 7z and SFX RAR. `prefix_kind` is classified on the detected path and `None` on the
      forced one — assert that too, so the asymmetry is pinned deliberately rather than
      discovered later
- [ ] 1.3 Failing test: forced `format=ZIP` on a prefixed ZIP reports the real origin —
      `zipapp` (`concat == 0`, so the naive derivation would say `0`), shebang, `MZ` and
      JPEG prefixes. Plus an empty ZIP behind a prefix reporting `None` / `None`
- [ ] 1.4 Failing test: the `(prefix_kind is NONE) == (payload_offset == 0)` invariant
      holds for every format in the declarative corpus, including the ones that cannot
      carry a prefix

## 2. The shared resolver

- [ ] 2.1 `resolve_payload_origin(fp, needles, *, limit=SFX_MAX) -> MagicHit` in
      `internal/sfx.py`: fast-path read at the open position, bounded forward scan on a
      miss, `CorruptionError` past the bound, `fp` restored either way
- [ ] 2.2 Port `sevenzip_parser.find_signature_offset` onto it; keep the exported name as
      a thin wrapper only if callers outside the reader still need it
- [ ] 2.3 Port `rar_parser._find_sfx_header` onto it, taking the version from
      `hit.needle` rather than a second code path; delete the `tuple[int, int]` signature
- [ ] 2.4 Verify the detected path still performs **no** scan (instrument
      `scan_for_magic`; the fast path must hit when detection supplied the offset)
- [ ] 2.5 Decide `find_signature_offset`'s fate on the evidence of what still imports it
      (`tests/test_sfx.py`, the fuzz harness) — keep as a wrapper or delete with callers
      updated, not left as an unexplained alias

## 3. Reporting the origin back

- [ ] 3.1 Add the reader-side channel (`design.md` option A: a base-class attribute the
      `ArchiveInfo` builder reads, defaulted so backends that never receive a start offset
      are unaffected). Derive that set from `reject_start_offset` **at implementation
      time**, not from today's census — `prefixed-archive-detection` moves formats into the
      prefix-capable set (see 3.6)
- [ ] 3.2 7z reader reports its resolved origin on both paths
- [ ] 3.3 RAR reader reports its resolved origin on both paths
- [ ] 3.4 ZIP reader reports the slice origin when given one, and otherwise
      `min(header_offset)` over the central directory it already parsed — **not** `concat`,
      which is `0` for a `zipapp`. No extra scan or tail probe. `None` only when there are
      no members to measure from
- [ ] 3.5 Formats that cannot carry a prefix report `NONE` / `0` without per-backend code
- [ ] 3.6 **Cover every format `prefixed-archive-detection` can hand a `start_offset`**, not
      only ZIP / 7z / RAR. That change makes a makeself `.run` (`#!` + tar.gz) detect as
      `TAR_GZ` with a non-zero `payload_offset`, so TAR and the single-file codecs stop
      being start-offset-rejecting. Reporting `NONE` / `0` for those would recreate the
      exact "detection knew, `ArchiveInfo` says byte zero" hole this change exists to
      close. Add the makeself fixture alongside PAD's

## 4. The public fields

- [ ] 4.1 `ArchiveInfo.prefix_kind: PrefixKind | None` and `ArchiveInfo.payload_offset:
      int | None`, both defaulted so existing backend construction sites stay valid
- [ ] 4.2 Assert the state table as a real invariant (an `assert` or `__post_init__`
      check), not a convention: `prefix_kind is NONE` ⟺ `payload_offset == 0`, and
      `payload_offset is None` ⟹ `prefix_kind is None`. The check MUST accept two values the
      naive version would reject: `UNKNOWN` with a positive offset (an exhaustive-scan
      prefix from `prefixed-archive-detection` — rejecting it would fail that change's own
      fixtures), and `None` kind with a positive offset (a forced-format open)
- [ ] 4.3 Guard against the tempting shortcut: nothing may infer `prefix_kind` from
      `payload_offset > 0`. A red–green test that a forced-format open on a JPEG+ZIP reports
      `None`, not `EXECUTABLE` or `OTHER_FORMAT`
- [ ] 4.4 Extend the conformance sweep so every corpus archive is checked, rather than
      only the SFX fixtures

## 5. Second consumer

- [ ] 5.1 `cli/info_cmd.py` prints the origin from `reader.info` instead of from its own
      `detect_format(archive)` result. **Keep that call** — it also supplies `format`,
      `confidence` and `detected_by`, which have no reader-side home until
      `detection-result-surface` lands. Removing it here would trade one gap for three
- [ ] 5.2 Decide whether the printed key stays `sfx_offset` or follows the field names
      (`payload_offset` / `prefix_kind`) — a CLI output change either way, so it is a
      deliberate choice rather than a rename that falls out of the refactor
- [ ] 5.3 Cover the forced-format path as a **library** test, not a CLI one: the CLI has no
      `--format` flag, so `open_archive(..., format=...)` is the only way to reach that
      door

## 6. Docs

- [ ] 6.1 `docs/formats.md` self-extracting prose: what the two fields mean, and that
      `None` means not-established rather than zero
- [ ] 6.2 Nothing to record: the forced-ZIP origin question is resolved in `design.md`
      (`min(header_offset)`, no tail probe). Drop this task if it is still open at
      implementation time

## 7. Verify

- [ ] 7.1 `uv run --no-sync pytest tests/test_sfx.py tests/test_detection.py` plus the
      backend suites for zip / 7z / rar
- [ ] 7.2 Fuzz harness still imports what it needs from `sevenzip_parser` after task 2.5
- [ ] 7.3 `./scripts/check.sh --fix`
- [ ] 7.4 `./scripts/test.sh --all-configs`
- [ ] 7.5 `openspec validate --strict archive-origin-reporting`
- [ ] 7.6 Dry-run archive on a scratch tree and diff `openspec/specs/` — `--strict` does
      not check that a `MODIFIED` header names an existing requirement, and three of the
      five deltas here are `MODIFIED`. Diff **scenario rows**, not just requirement counts:
      a `MODIFIED` requirement is replaced whole, so a row omitted from the delta is
      silently deleted from the live spec and the totals line still reads `- 0`
- [ ] 7.7 `openspec archive archive-origin-reporting --yes` and commit the
      `openspec/specs/` diff
