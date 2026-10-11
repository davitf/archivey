# Tasks — native TAR reader

One PR per section. `design.md` §"PR plan" says why the split falls where it does.

## 1. Design (this PR)

- [ ] 1.1 Proposal, design, tasks, brief and the `format-tar` delta; `openspec validate`.

## 2. Parser (`internal/backends/tar_parser.py`)

- [ ] 2.1 `parse_header_block`: zero block, signed and unsigned checksum, v7 / ustar /
      old GNU layouts, octal and base-256 numbers, `RejectedBlock` with a reason.
- [ ] 2.2 `parse_pax_records` with strict length validation and `hdrcharset` scope.
- [ ] 2.3 Sparse parsers for old GNU (slots and extension blocks), PAX 0.0, 0.1 and 1.0,
      each weighing entries before allocating and each run during the walk (1.0 from
      the data area); `validate_sparse_map` with PR 716's rules and the exact stored
      size.
- [ ] 2.4 `TarWalker`: skip, read, extended headers through `read_within_reach` under
      one per-member budget, global-record snapshots, `TarEnd` (a chain that ends in
      no member header, or PAX records that do not parse, is a rejected end, as
      tarfile reports it); member resolution
      (override order, the old-style directory rule on the final name, link names only
      on link types). The `REGTYPE d/` rule moves in with the switch, as `main` has it.
- [ ] 2.5 Unit tests per encoding and per rejection reason, from GNU tar / bsdtar
      fixtures where a tool writes the shape.
- [ ] 2.6 Differential listing test against `tarfile` over the TAR corpus (oracle in
      tests only), with the `design.md` §"Behaviour that changes" differences named.
- [ ] 2.7 `tests/fuzz_tar_parser.py`.

## 3. Sparse stream (`streams/streamtools/sparse.py`)

- [ ] 3.1 `SparseStream`: reads, `readinto`, seek within and past the end, holes as
      zeros, forward-only over a forward-only source.
- [ ] 3.2 Byte-compare against `tarfile.extractfile` for every sparse fixture, including
      the ones GNU tar writes with `tar -cS --format=gnu|oldgnu` and `tar -cS
      --format=posix --sparse-version=0.0|0.1|1.0`.

## 4. Switch (`internal/backends/tar_reader.py`)

Starts once PRs 704, 706 and 716 are on `main`.

- [ ] 4.1 Build the reader's byte stream (source or codec stream) and hand it to
      `TarWalker`; member streams as `SharedView` / `open_data` slices.
- [ ] 4.2 Rewire `__init__`, `_iter_members`, `_iter_members_progressive`,
      `_iter_with_data`, `_open_member`, `_verify_tar_eof`, `_close_archive`.
- [ ] 4.3 `_to_member` from `TarEntry`: names from bytes and source; PAX times parsed
      once.
- [ ] 4.4 Delete every workaround `design.md` §"Which workarounds this deletes" lists,
      including the stdlib-source hash test and the `is_seekable` catch in
      `binaryio.py`.
- [ ] 4.5 Full suite and `--all-configs` green; update only the tests §"Behaviour that
      changes" names, each with its reason in the PR.
- [ ] 4.6 Streaming-retention test: a pass over many members holds one member list.
- [ ] 4.7 Benchmarks against the baseline; restore header batching only if the
      100 000-member listing is slower. The numbers feed the TAR row of
      `docs/access-and-cost.md` in 5.1.
- [ ] 4.8 Point the atheris TAR target at the new reader.

## 5. Docs and archive

- [ ] 5.1 Handbook `formats/tar.md` §2.2, §2.3, §5, §6, §7 (including the contiguous-file
      row), `known-issues.md`, `threat-model.md`, `docs/formats.md`,
      `docs/access-and-cost.md` (the member-seek claim and the "wraps stdlib" row),
      `code-map.md`, `backends/__init__.py` docstring, the `format-tar` spec purpose.
- [ ] 5.2 Re-copy any requirement in the delta that a merge has changed on `main` since
      (the open TAR PRs touch `format-tar`). Dry-run the archive on a scratch tree and
      diff `openspec/specs/` to check that every MODIFIED block landed on the
      requirement it names; then sync the delta specs and archive this change.
