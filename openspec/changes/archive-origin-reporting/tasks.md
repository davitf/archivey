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
- [ ] 1.2 Failing test: the two open paths report the same `prefix_kind` and
      `payload_offset` for the same SFX 7z and SFX RAR
- [ ] 1.3 Failing test: forced `format=ZIP` on a prefixed ZIP reports `UNKNOWN` / `None`
      — explicitly *not* `NONE` / `0`
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
      `ArchiveInfo` builder reads, defaulted so the four `reject_start_offset` backends are
      unaffected)
- [ ] 3.2 7z reader reports its resolved origin on both paths
- [ ] 3.3 RAR reader reports its resolved origin on both paths
- [ ] 3.4 ZIP reader reports the slice origin when given one, `UNKNOWN` / `None` otherwise
      — and performs no extra scan or tail probe to fill the field
- [ ] 3.5 Formats that cannot carry a prefix report `NONE` / `0` without per-backend code

## 4. The public fields

- [ ] 4.1 `ArchiveInfo.prefix_kind: PrefixKind` and `ArchiveInfo.payload_offset:
      int | None`, both defaulted so existing backend construction sites stay valid
- [ ] 4.2 Assert the state pairing from the spec table as a real invariant (an `assert` or
      `__post_init__` check), not a convention — `UNKNOWN` ⟺ `None` is the one a caller
      will get wrong
- [ ] 4.3 Extend the conformance sweep so every corpus archive is checked, rather than
      only the SFX fixtures

## 5. Second consumer

- [ ] 5.1 `cli/info_cmd.py` reads the origin from `reader.info` and drops its second
      `detect_format(archive)` call — the API gap `VISION.md` says the CLI exists to expose
- [ ] 5.2 Confirm the CLI prints the origin on a forced-format open too, which it cannot
      do today at all

## 6. Docs

- [ ] 6.1 `docs/formats.md` self-extracting prose: what the two fields mean, and that
      `None` means not-established rather than zero
- [ ] 6.2 Record the forced-ZIP tail-probe question in `dev-docs/IDEAS.md` if the
      maintainer leaves it open (`design.md` §Open question)

## 7. Verify

- [ ] 7.1 `uv run --no-sync pytest tests/test_sfx.py tests/test_detection.py` plus the
      backend suites for zip / 7z / rar
- [ ] 7.2 Fuzz harness still imports what it needs from `sevenzip_parser` after task 2.5
- [ ] 7.3 `./scripts/check.sh --fix`
- [ ] 7.4 `./scripts/test.sh --all-configs`
- [ ] 7.5 `openspec validate --strict archive-origin-reporting`
- [ ] 7.6 Dry-run archive on a scratch tree and diff `openspec/specs/` — `--strict` does
      not check that a `MODIFIED` header names an existing requirement, and three of the
      five deltas here are `MODIFIED`
- [ ] 7.7 `openspec archive archive-origin-reporting --yes` and commit the
      `openspec/specs/` diff
