# Tasks — opt-in source spooling

> **Specs-first proposal. Nothing here is implemented.** These tasks describe the
> implementation for when the change is accepted and scheduled. Run tools through `uv`
> (`uv run pytest`, `uv run pyrefly check`, `uv run ty check`, `uv run ruff`).
> `design.md` §Open questions must be answered before task 1 — the policy's shape and its
> default limit are inputs, not implementation choices.

## 1. The policy object

- [ ] 1.1 Add a frozen policy dataclass (name per `design.md` Q2) carrying: whether each
      spool kind is permitted, a maximum byte count, and an optional spool directory.
- [ ] 1.2 Named constructors for the two common cases, so callers do not assemble fields.
- [ ] 1.3 Add it to `ArchiveyConfig` beside `listing_limits`, with the defaults from
      `design.md` Q1. Export from `archivey.__all__` and add an `api.md` entry.
- [ ] 1.4 Docstrings on the class and every field — `api.md` renders from docstrings, and a
      `#` comment reaches no reader (`review/docs-content/scope.md` §Precondition).

## 2. The spool primitive

- [ ] 2.1 One internal helper that performs a bounded spool: takes the policy, the kind, a
      source and an optional known size; returns a path; raises `ResourceLimitError` on the
      limit; registers cleanup with the reader's close.
- [ ] 2.2 Check the size **before** writing when it is known, and enforce during the write
      when it is not, removing the partial file on the way out.
- [ ] 2.3 Best-effort free-space pre-flight per `design.md` Decision 4 — a fast-fail, never
      a promise. It must not be reachable as a guarantee from any public docstring.
- [ ] 2.4 Record the spool in `CostReceipt.notes`: kind and byte count. No diagnostic.

## 3. Route the existing RAR materialization through it

- [ ] 3.1 `RarReader._ensure_archive_path()` uses the primitive as a **tool-tax** spool.
- [ ] 3.2 `RarReader._materialize_stream_volumes()` likewise, with the limit measured across
      the whole volume set rather than per volume.
- [ ] 3.3 Confirm no behaviour change for path sources and for stored-member reads from a
      stream — those must not spool at all.

## 4. The capability spool

- [ ] 4.1 At open, when the format requires seekability, the source is not seekable, and the
      policy permits a capability spool: spool, then proceed as for a seekable source.
- [ ] 4.2 When it is not permitted, keep raising `StreamNotSeekableError` — with the message
      extended to name the policy option, so the error teaches the fix.
- [ ] 4.3 Settle `design.md` Q4 (does `streaming=True` plus a capability spool stream from
      the spooled file, or switch to random access?) and implement it explicitly, not by
      accident.

## 5. Tests

- [ ] 5.1 Red-green for the P11 case: a compressed RAR member read from a `BytesIO` records
      a `CostReceipt.notes` entry, and exceeds a low limit with `ResourceLimitError`. Verify
      by reverting the fix and watching each fail.
- [ ] 5.2 Every row of the three delta scenario matrices — spooling policy, pre-flight and
      directory, RAR materialization.
- [ ] 5.3 Stored-member-from-stream does **not** spool; second compressed member does not
      spool twice.
- [ ] 5.4 Cleanup: temp file or directory gone after `close()`, and after an exception
      raised mid-spool.
- [ ] 5.5 Capability spool from a genuine pipe for ZIP, 7z and ISO — the formats
      `StreamNotSeekableError` refuses today.
- [ ] 5.6 Cross-platform: Windows temp-file semantics differ (an open file cannot always be
      replaced or removed). Name the spool file and close handles so cleanup works there —
      CI matrixes Windows and macOS, and the container is Linux.

## 6. Registers and docs

- [ ] 6.1 Close `dev-docs/open-issues.md` **P11**, recording what shipped and the signal
      choice.
- [ ] 6.2 Threat-model pass: untrusted bytes at a predictable path, spool-directory
      permissions, cleanup after a hard kill. Match `docs/extracting.md`'s existing
      `.archivey-tmp-*` treatment rather than inventing a second convention.
- [ ] 6.3 Update `review/docs-content/claims.md` **E-71** — the row records that no page
      states the spill; when this lands the page states the policy instead. Do **not** edit
      pages under `docs/` from this change; Topic 8 owns the guide.
- [ ] 6.4 Add the payload-cache idea to `dev-docs/IDEAS.md` §Performance with the reason it
      is not here (needs measurement; overlaps Topic 6 and stream-layering Q4).
- [ ] 6.5 `openspec archive` this change in the implementing PR — CI checks it on PRs.
