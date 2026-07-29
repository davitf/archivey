## 1. Public surface

- [ ] 1.1 `open_archive`: replace `member_streams: MemberStreams` with keyword-only
      `seekable_members: bool = False` and `concurrent_members: bool = False`
      (`core.py:101`).
- [ ] 1.2 Map to the internal `MemberStreams` value at the boundary; leave internal
      plumbing on flags so the diff stays shallow.
- [ ] 1.3 Re-express the `streaming` + concurrency rejection against
      `concurrent_members` (`core.py:168`), keeping the error type and message shape.
- [ ] 1.4 Update the docstring: state the two traps each flag opts into, and that
      `open_stream` uses the same `seekable` vocabulary.

## 2. Call sites

- [ ] 2.1 Update internal callers (~52 refs), including `extract`/`extract_all` paths.
- [ ] 2.2 Update tests (~38 refs). Behaviour assertions must not change — only the call
      spelling. A test whose *expectations* change is a red flag for scope creep.
- [ ] 2.3 Confirm `MemberStreams` is still exported and still used by `CostReceipt` /
      diagnostics; `test_public_api.py` must stay green.

## 3. Docs and decision record

- [ ] 3.1 Published user pages — measured **24 refs**, not the 8 first estimated:
      `support-matrix.md` (5), `costs.md` (5), `philosophy.md` (4), `usage.md` (3),
      `formats.md` (3), `gotchas.md` (2), `migrating.md` (1), `api.md` (1). The
      support-matrix concurrency example is the load-bearing one; `philosophy.md:52-53`
      is a two-row "how do I ask for it" table that is the change in miniature.
      `api.md:22` is the `::: archivey.MemberStreams` mkdocstrings entry — it **stays**
      (the enum remains exported for `CostReceipt` and diagnostics).
- [ ] 3.2 Amend ADR 0003 with the new spelling; note ADR 0004's reasoning applies.
- [ ] 3.3 Check `docs/migrating.md`'s "one live member stream by default" note still
      reads correctly.

## 4. Verification

- [ ] 4.1 Three-config suite green (`[all]`, `[all-lowest]`, core-only).
- [ ] 4.2 Free-threaded `concurrent_reader` job green — it exercises the concurrency
      capability directly.
- [ ] 4.3 `pyrefly` + `ty` clean; `mkdocs build --strict` clean.
- [ ] 4.4 Grep for `member_streams=` across the tree: no survivors outside archived
      history.
