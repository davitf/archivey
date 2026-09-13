## 1. Public surface

- [x] 1.1 `open_archive`: replace `member_streams: MemberStreams` with keyword-only
      `seekable_members: bool = False` and `concurrent_members: bool = False`
      (`core.py:101`).
- [x] 1.2 Map to the internal `MemberStreams` value at the boundary; leave internal
      plumbing on flags so the diff stays shallow.
- [x] 1.3 Re-express the `streaming` + concurrency rejection against
      `concurrent_members` (`core.py:168`), keeping the error type and message shape.
- [x] 1.4 Update the docstring: state the two traps each flag opts into, and that
      `open_stream` uses the same `seekable` vocabulary. *(Both docstrings — the
      `open_stream` one now says `seekable` is the same capability `open_archive`
      spells `seekable_members`, rather than contrasting a bool with a flag enum.)*

## 2. Call sites

- [x] 2.1 Update internal callers (~52 refs), including `extract`/`extract_all` paths.
      *(No internal caller needed changing: the mapping happens at the entry point and
      every layer below `open_archive` already spoke `MemberStreams`. The one internal
      change is the `ConcurrentAccessError` message — see 2.4.)*
- [x] 2.2 Update tests (~38 refs). Behaviour assertions must not change — only the call
      spelling. A test whose *expectations* change is a red flag for scope creep.
      *(Two parametrizations changed shape rather than spelling —
      `test_iterate_and_open_inside_loop` now takes `concurrent: bool` instead of a
      `MemberStreams` value, and `_fan_out_read` passes its `seekable` argument
      straight through. Same cases, same assertions.)*
- [x] 2.3 Confirm `MemberStreams` is still exported and still used by `CostReceipt` /
      diagnostics; `test_public_api.py` must stay green.
- [x] 2.4 **Added:** the `ConcurrentAccessError` message told callers to reopen with
      `member_streams=MemberStreams.CONCURRENT` — a user-visible string naming a
      parameter that no longer exists. It now names `concurrent_members=True`, and the
      `error-handling` delta makes that a requirement rather than an incidental fix.
      `benchmarks/harness.py` also had its own `member_streams` plumbing, converted to
      `seekable_members`.

## 3. Docs and decision record

- [x] 3.1 Published user pages — measured **24 refs**, not the 8 first estimated:
      `support-matrix.md` (5), `costs.md` (5), `philosophy.md` (4), `usage.md` (3),
      `formats.md` (3), `gotchas.md` (2), `migrating.md` (1), `api.md` (1). The
      support-matrix concurrency example is the load-bearing one; `philosophy.md:52-53`
      is a two-row "how do I ask for it" table that is the change in miniature.
      `api.md:22` is the `::: archivey.MemberStreams` mkdocstrings entry — it **stays**
      (the enum remains exported for `CostReceipt` and diagnostics).
- [x] 3.2 Amend ADR 0003 with the new spelling; note ADR 0004's reasoning applies.
- [x] 3.3 Check `docs/migrating.md`'s "one live member stream by default" note still
      reads correctly. *(Reworded from "declare `MemberStreams.CONCURRENT`" to "pass
      `concurrent_members=True`"; the surrounding claim is unchanged.)*
- [x] 3.4 **Added:** `MemberStreams`' own class docstring described itself as the input
      to `open_archive` and explained combining flags with `|`. Rewritten to say what it
      now is — the *reported* capability value on `CostReceipt` and in diagnostics —
      since it is still the first thing a reader of `api.md` sees.

## 4. Verification

- [x] 4.1 Three-config suite green (`[all]`, `[all-lowest]`, core-only).
      *(1995 passed / 58 skipped, 1995 / 58, 1590 / 406.)*
- [x] 4.2 Free-threaded `concurrent_reader` job green — it exercises the concurrency
      capability directly. *(46 `concurrent_reader` tests pass locally; the 3.13t job
      runs the same suite in CI.)*
- [x] 4.3 `pyrefly` + `ty` clean; `mkdocs build --strict` clean.
- [x] 4.4 Grep for `member_streams=` across the tree: no survivors outside archived
      history. *(Survivors are all internal plumbing — `ReaderState`, the backend
      constructors — which the proposal keeps on flags deliberately. `docs/grab-bag/`
      is untouched: declared non-normative historical prose.)*

## 5. Spec deltas — restructured during implementation

The change originally shipped one delta, against `access-mode-and-cost`, introducing a
requirement (`Member stream capabilities are declared with booleans`) that did not exist
in that spec. That was wrong twice over: a `MODIFIED` requirement must match a real
requirement header, and the requirement that actually defines this surface lives in
`archive-reading`. Corrected to three deltas, each modifying a requirement that exists:

- [x] 5.1 `archive-reading` — `Opening an archive for reading` (the verbatim signature
      block) and `Declared member-stream capabilities` (the flags themselves). This is
      the spec that was silently left stale.
- [x] 5.2 `access-mode-and-cost` — `Declared capabilities compose with the two access
      modes` and `Concurrent-stream cost is informational`, both of which describe the
      capability in `member_streams` terms.
- [x] 5.3 `error-handling` — `Caller misuse remains outside ArchiveyError`, which names
      both the old parameter and the old flag in its `ConcurrentAccessError` wording.
- [x] 5.4 `openspec validate --strict` passes.

## 6. Review round (PR #213)

- [x] 6.1 **The `CostReceipt` / diagnostics claim was false.** The proposal asserted that
      `MemberStreams` "remains the declared-capability value on `CostReceipt` and in
      diagnostics", and that was carried into the class docstring and into the
      `archive-reading` delta as a `SHALL`. `CostReceipt` has `listing_cost` /
      `access_cost` / `stream_capability` / `solid_block_count` / `notes`; no diagnostic
      carries a `MemberStreams`. The claim was never true — it was invented in the
      proposal and never checked. Corrected in all three places, and the delta now says
      explicitly that nothing requires it to appear there. The honest reason it stays
      exported is that it is the internal representation the booleans map to; concrete
      readers expose `reader.member_streams`, but that property is **not** on the
      `ArchiveReader` ABC, so it is runtime-reachable and not part of the typed contract.
      Whether to change that is left open — see §7.
- [x] 6.2 **Sibling specs prescribed the old input.** Three specs beyond the original
      three carried normative "on `open_archive()`" / "opened with `MemberStreams.…`"
      text that would have survived `openspec archive` as contradictory `SHALL`s:
      `seekable-decompressor-streams`, `reader-concurrency`, `testing-contract`. Deltas
      added for all three. The delta bodies were **extracted programmatically from the
      live specs** and then substituted, rather than retyped, so a transcription slip
      cannot corrupt a requirement on sync.
      Where the flag names denote the reader's *capability state* rather than the call
      that produced it, they are deliberately kept — `reader-concurrency` is the
      implementer contract and the flags are the internal representation. A comment in
      that delta records the distinction so a later pass does not "finish" the rename.
- [x] 6.3 Non-requirement prose (`reader-concurrency` Purpose, two Related-specs rows)
      edited directly in the main specs. Deltas only carry `### Requirement:` blocks, so
      `openspec sync` would never have applied these.
- [x] 6.4 `ConcurrentAccessError`'s **class docstring** still taught
      `MemberStreams.CONCURRENT` while the raised message said `concurrent_members=True`.
      It is the mkdocstrings surface, so the two disagreed exactly where a user looks.
- [x] 6.5 Half-updated published prose: `docs/gotchas.md:19` ("With `SEEKABLE`…") and
      `docs/costs.md:87,114`, each sitting next to a line already converted.
      (`costs.md:43`'s `SEEKABLE` is `StreamCapability.SEEKABLE`, a different enum —
      correctly left alone.)
- [x] 6.6 Near-public docstrings still teaching flag declaration: `AcceleratorMode`
      (`config.py`), and `BaseArchiveReader.open` / `.close`, which are what `help()` on
      a live reader shows. `_open_member` / `_wrap_member_stream` keep the flag names —
      private, and there they genuinely mean the internal gate.
- [x] 6.7 Deleted the always-true assertion
      (`assert "concurrent-member-streams" not in str(...) or True`) in a test this
      change already touched.
- [x] 6.8 Added `test_member_streams_kwarg_is_gone` — the delta has a
      `member_streams=… → TypeError` scenario with no test behind it. Python enforces it
      anyway; the test exists so a well-meant "accept both spellings" patch has to delete
      a test rather than slip through.

## 7. Open for the maintainer — what is `MemberStreams` publicly for?

Now that it is not an input, its only public presence is `__all__` + the
`::: archivey.MemberStreams` block in `docs/api.md`, and `reader.member_streams` exists
only on the concrete base class. Options:

| | Approach | Note |
|---|---|---|
| **A** | Leave as-is; docs now describe it honestly as the internal/reported form | Zero cost. The export is decorative for anyone holding an `ArchiveReader`-typed handle |
| **B** *(recommended)* | Promote `member_streams` onto the `ArchiveReader` ABC | ~4 lines, purely additive, gives the export a real job ("what did I open this with?") |
| **C** | Put declared capabilities on `CostReceipt` | What the false claim described. A real API expansion, outside this change's no-behaviour-change scope |

**Recommendation: B, as a follow-up rather than here** — adding a property to the ABC
later is non-breaking, so it carries no `0.2.0` deadline, whereas *removing* the export
would be breaking. Keep it exported either way.
