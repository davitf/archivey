# Spike findings — does rapidgzip's index expose gzip *member* boundaries?

**Verdict: NO (rapidgzip 0.16.0).** The public index accessors expose random-access *seek
points*, not gzip member/stream boundaries, at any parallelization. Per this change's own
task 1.3 stop-condition, the index swap is **infeasible as specified** — keep the byte scan.

## Method

`rapidgzip==0.16.0` (the floor version). Concatenated multi-member gzip with **distinct** member
sizes so a member boundary is identifiable in the decompressed-offset space, read fully to EOF
(so the index is complete), then inspected. Both highly-compressible and incompressible
(`os.urandom`) payloads, across `parallelization ∈ {0, 1, 2, 4}`. Scripts:
`scratchpad/probe_index*.py`.

## Evidence

Decompressed member starts vs. what `block_offsets()` / `available_block_offsets()` report:

| Input (decompressed member starts) | parallelization | `block_offsets()` decompressed offsets | member starts present? |
| --- | --- | --- | --- |
| 3 members @ {0, 400000, 550000}, incompressible | 0 | `{0, 640000}` | only 0 (start) |
| 3 members @ {0, 400000, 550000}, incompressible | 1 / 2 / 4 | `{0, 531122, 640000}` | only 0 (start) |
| 2 members @ {0, 300000}, compressible | 0 | `{0, 420000}` | only 0 (start) |

- **Serial mode (`parallelization=0`, what archivey uses)** records only `{first_block, EOS}` —
  no intermediate offsets at all, even for incompressible data with many deflate blocks.
- **Parallel mode** adds seek points chosen by decompressed-size chunking (`531122` — a chunk
  boundary *mid-member*), which do **not** coincide with member starts (`400000`, `550000`).
- `block_offsets_complete()` is `True` and `file_type()` is `GZIP` in every case — "complete"
  refers to the seek index, and carries no member-boundary information.
- No accessor reports a member/stream **count**. `add_deflate_stream_crc32` /
  `set_deflate_stream_crc32s` are *inputs* for index import/verification (you supply per-stream
  CRCs), not a decode-time member enumeration. `export_index(fileobj)` serializes the same
  seek-point index (INDEXED_GZIP/GZTOOL); parsing it would not recover member boundaries the
  in-memory index does not hold.

## Consequence

- **This change (`gzip-multimember-detect-via-index`) cannot land as written.** The index does
  not answer "≥2 gzip members?"; the byte scan (`gzip_has_additional_member`) stays.
- The delta spec ("SHALL prefer … the index") **must not** be synced into `openspec/specs/`.
- **Knock-on:** the deferred *per-member ISIZE sum* (`rapidgzip-truncation-investigation`) was to
  "walk members" using this same boundary data — it is **equally blocked** by this finding. A
  per-member walk needs a byte scan for member starts, not the index.
- Therefore the sibling `gzip-truncation-backstop-any-seekable` **cannot** rely on this change to
  remove its concurrent multi-member scan; it must keep the scan (via an independent
  `SharedSource` view — see that change's FINDINGS).

## Open follow-ups (maintainer decision)

- **Close or shelve this change?** As a documented no-op it carries no code; recommend closing it
  (or moving the reasoning to `known-issues.md`) rather than leaving an unimplementable proposal
  open. Re-open only if a future rapidgzip exposes member starts (watch its changelog for a
  member/stream boundary accessor).
- If member detection ever becomes a hot cost in practice, the cheaper win is a *bounded* scan
  from the last known member offset rather than re-reading the whole file — independent of
  rapidgzip's index.
