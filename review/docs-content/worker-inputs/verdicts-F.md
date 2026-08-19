# Verdicts — Worker F (Cost, accelerators, measurement)

Config for this pass: **`[all]`** (`uv sync --group dev --extra all`), unless a row notes
otherwise. Spec citations preferred over `src/` when both speak (O-26). `[code]` rows
were executed with `uv run --no-sync` (harness via `uv run --extra all` as documented).

| # | V | Evidence |
|---|---|---|
| F-1 | verified | `testing-contract` structural invariants gate PRs; full wall-time ratios are change-guarded nightly / not PR-required. `benchmarks/harness.py` docstring + `RESULTS.md`: PR gate = structural only; VISION bands informational. Matches `access-and-cost.md:8-9` and `philosophy.md:70-72`. |
| F-2 | verified | `[code]` `uv run --extra all python -m benchmarks.harness --mode full --scale realistic` completed successfully (exit 0); CLI help lists the same flags. |
| F-3 | verified | Stale `davitf/archivey-2` nightly URL on `access-and-cost.md:17-18`. Already **O-4** / S-1 — do not re-file. `gh run view 29992136861 --repo davitf/archivey` resolves (redirect); rename still leaves the wrong name on a user page. |
| F-4 | verified | Four aspirational bands on `access-and-cost.md:21-26` match VISION / harness Q1 prints (≤1.3× read, ≤~2× extract, ≤2–3× open+list, ≈1.25× 7z/RAR open+list). |
| F-5 | verified | Downloaded artifact `benchmark-wall-realistic` from run `29992136861`: `measured_at` 2026-07-23, `source_sha` `89720de…`, scale 64×256 KiB / 32 MiB gzip. Ratios match the table (ZIP read 1.87×, extract 2.38×, open+list 4.44×, TAR list 1.41× / read 1.90×, 7z 2.13×, RAR 2.39×, gzip/tar.bz2/tar.gz accel figures). **L5** lazy derivation named in `dev-docs/IDEAS.md` (perf L5, deferred past 0.2.0). |
| F-6 | verified | `access-mode-and-cost` Exposing a CostReceipt: fields `listing_cost`, `access_cost`, `stream_capability`, `solid_block_count` (+ `notes`). Spot-check: open RAR → all four present on `reader.cost`. |
| F-7 | verified | Spec + `CostReceipt` include public `notes: tuple[str, ...]`; four-row guide table omits it. True completeness gap. |
| F-8 | verified | Spec enum + `ListingCost`: `INDEXED` / `REQUIRES_SCANNING` / `REQUIRES_DECOMPRESSION`. |
| F-9 | verified | Spec + `AccessCost`: `DIRECT` / `SOLID` with the guide’s meanings. |
| F-10 | verified | Spec: `solid_block_count: int \| None` — distinct solid blocks or None when unknown. Guide “when known” matches. Spot-check solid RAR → `None`. |
| F-11 | verified | `access-mode-and-cost` CostReceipt immutable open-time description; Concurrent-stream cost is informational; `archive-reading` capability gate: cost never determines legality. Matches `access-and-cost.md:46` / `philosophy.md:66-67`. |
| F-12 | verified | Spec: only `StreamCapability` is ordered; `ListingCost`/`AccessCost` are kinds of work. Same wording in `cost.py` StreamCapability docstring. |
| F-13 | verified | Spec `format-rar` listing cost O(1) / member table at open; `ListingCost.INDEXED` docstring names RAR. Spot-check: `basic_solid__.rar` → `listing_cost=INDEXED`. Canonical home is the cost page (formats matrix says “native metadata”, not the enum). |
| F-14 | verified | Same open-time walk; once open, `members()` / `get()` serve the in-memory table. Spot-check `get(name)` after open. |
| F-15 | verified | Guide Quick Open claim: RAR5 SERVICE blocks (format home for QO) are walked in `rar_parser` and not appended as members; FILE headers build the table — every header traversed, QO not the primary listing source. Spec does not name “Quick Open”; Settles-it matched by native header walk + up-front table. |
| F-16 | wrong | `[code]` solid “do this” block as published fails on directory members: `stream_members()` yields `stream is None` for non-files (`archive-reading` Non-file stream_members yield None). `consume(stream)` → `AttributeError` on `tests/fixtures/rar/basic_solid__.rar`. None-guard works. **Prose/code-sample wrong.** |
| F-17 | verified | `[code]` out-of-order `open(name)` loop runs on solid RAR file members. Comment “may restart” matches `access-mode-and-cost` / `archive-reading` solid open-order (no diagnostic). (RAR via `unrar`: byte re-decode not always visible in `io_stats` — harness P9.) |
| F-18 | verified | `reader-concurrency` + `access-mode-and-cost` Concurrent-stream cost is informational: `concurrent_members` gates correctness, not solid open-order cost. Matches `access-and-cost.md:85-86` / `gotchas.md:22-23`. |
| F-19 | verified | `seekable-decompressor-streams` XZ and lzip use format-native indexes. |
| F-20 | verified | Spec: rapidgzip for gzip/bzip2 + DEFLATE-family (zlib/raw deflate). **cfg `[all]`**: `rapidgzip` installed. Absence → stdlib rewind path (`seekable-decompressor-streams` accelerator matrix). |
| F-21 | verified | Spec: without accelerator / index, backward seek re-decompresses from start. Matches guide + gotchas + philosophy. |
| F-22 | verified | Spec: `STREAM_REWIND_REDECOMPRESSES` predicate is redecode distance vs `REWIND_REDECODE_WARN_BYTES` (1 MiB), not codec name. Spot-check: large gzip rewind under `use_rapidgzip=OFF` raises/records that code. Verifier trap true: guide names `RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE` nearby, not `REWIND_REDECODE_WARN_BYTES` (same numeric value, different quantity — `config.py`). |
| F-23 | verified | Spec slow-rewind matrix: single-block `.xz` / sparse rapidgzip index same event; small rewinds under threshold quiet on every codec. |
| F-24 | verified | `diagnostics` / `seekable-decompressor-streams`: RAISE evaluates every qualifying seek; recording once-per-stream. Spot-check: three EOF→`seek(0)` rewinds → three `DiagnosticRaisedError`; `diagnostics.retained` length 1 for `STREAM_REWIND_REDECOMPRESSES`. |
| F-25 | verified | Spec: native xz/lzip indexes from any seekable source; `seekable_members` is stream seek capability. Spot-check `.xz`: `member.size` identical with/without `seekable_members=True` (hashes empty both ways for plain xz). |
| F-26 | wrong | Guide/claim: AUTO selects “only when” declared seekability **and** known size ≥ 1 MiB. Spec (`seekable-decompressor-streams` demand matrix + AUTO threshold prose): when size is **unknown**, AUTO still selects when otherwise eligible; DEFLATE AUTO also needs a verifiable decompressed size (`config.py` / codecs). Absolute “only when A∧B” overstates. |
| F-27 | verified | Spec: known size &lt; threshold → stdlib; design intent for many tiny members. Matches guide. |
| F-28 | verified | Spec: `ON` ignores threshold; `OFF` never selects. `AcceleratorMode` docstring. Spot-check modes. |
| F-29 | verified | Silence claim true: no guide page states it. **cfg**: monkeypatch `_rapidgzip=None` — `AUTO` opens seekable gzip silently on stdlib; `ON` → `PackageNotInstalledError` naming `rapidgzip`. Matches `AcceleratorMode` + codecs raise path. (`compressed-streams` Missing optional backends is the broader PackageNotInstalledError home.) |
| F-30 | verified | Spec: seek indexes/accelerators only under declared seek demand. Matches `philosophy.md:40` / cost-page advice. |
| F-31 | verified | `archive-reading` weak-check confirmation; `format-zip` Confirm multi-candidate ZipCrypto — STORED does a shared full-ciphertext CRC pass (expensive niche). Matches guide + `formats.md:55-56`. |
| F-32 | verified | Spec rapidgzip-only accelerator; terminate-on-raising-source hazard documented. Matches guide + acknowledgements. |
| F-33 | verified | Spec: terminate-on-raising Python source SHALL be contained (`_TrappingSource` / benign EOF → Python exception). Gotchas + cost page. Spot-check: `tests/test_accelerator_bug3_trap.py` (see F-34). Pair with F-35. |
| F-34 | verified | `tests/test_accelerator_bug3_trap.py` asserts untrapped abort vs trapped clean exit. **cfg `[all]`**: both tests passed. |
| F-35 | verified | Residual path-source truncations/CRC → `std::terminate` during worker finalization: `dev-docs/known-issues.md` Bug 3 + `rapidgzip-upstream-report.md` §2. Stated on `access-and-cost.md:174-177`. Spec lifecycle § does not name this residual; known-issues does. **Pair with F-33:** both halves present on the cost page; gotchas states only the positive half (harvest). |
| F-36 | verified | `extracting.md` Hardening notes + `gotchas.md:49-52`: `AcceleratorMode.OFF` / own timeout for untrusted + hard latency budget; C++ busy-loop. Matches testing stance (accelerators off in fuzz). |
| F-37 | verified | Same hardening notes; `testing-contract` Atheris/mutation force accelerators off. Performance path, not defended fuzz surface. |
| F-38 | verified | Checklist six rows match declared APIs: `stream_members`/`__iter__`; solid → reorder/stream; `seekable_members`; `concurrent_members` after `members()`; `streaming=True`; `archivey.extract` (`safe-extraction` one-shot). Consistent with access-mode / concurrency specs. |
| F-39 | verified | Silence claim true: no guide states opt-in. `measurement.py`: `io_stats()` is `None` unless opened inside `enable_measurement()`. Spot-check: without → `None`; with → `IoStats(...)`. |
| F-40 | verified | Guide has no field-by-field `ArchiveyConfig` defaults table; checklist is the actionable half. Defaults live in `archive-reading` Explicit configuration object + `ArchiveyConfig` / `docs/api.md` mkdocstrings (→ DS / docstring home). |
| F-41 | verified | Spec: one accelerator library — rapidgzip for gzip+bzip2; MUST NOT import standalone `indexed_bzip2` (macOS heap). Matches `acknowledgements.md:43-48`, `:54`. |

## Notes for coordinator

### Wrong rows
- **F-16** — solid `stream_members` sample must skip `stream is None` (directories)
- **F-26** — AUTO “only when seek + size ≥ 1 MiB” omits unknown-size exemption (and verifiable-size gate)

### Config notes (`cfg`)
- Everyday verification: **`[all]`** (rapidgzip present).
- F-20 / F-29 absence path: monkeypatch `_rapidgzip = None` (same mechanism `[core-only]` / no `[seekable]` would hit).
- F-2 harness: `uv run --extra all` as the guide writes (not `--no-sync` alone).

### Cross-cluster / process
- **F-33 + F-35 pair:** both verified on `access-and-cost`. Gotchas (:45-48) states containment without the path residual — incomplete pair (round-2 class), not a false F-33/F-35.
- **known-issues.md Bug 3** mitigation prose still says caller-close “can only be fixed upstream”; code + F-34 tests show `_TrappingSource` containment — maintainer-doc drift (harvest).
- **F-3 / O-4:** cite only; do not re-file.
- A-16 seek-list understatement on `access-and-cost.md:145-146` is outside F-1…F-41 but still live on the same page.
- Spec line numbers in Settles-it have drifted; requirements matched by title/text.

### Counts
- **verified:** 39
- **wrong:** 2 (F-16, F-26)
- **unverifiable:** 0
- **left for TM:** 0
