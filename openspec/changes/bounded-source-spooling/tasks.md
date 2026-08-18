# Tasks — bounded source spooling

> **Specs-first proposal. Nothing here is implemented.** These tasks describe the
> implementation for when the change is accepted and scheduled. Run tools through `uv`
> (`uv run pytest`, `uv run pyrefly check`, `uv run ty check`, `uv run ruff`).
> `design.md` §Open questions must be answered before task 1 — the default limit and the
> packaging are inputs, not implementation choices.

## 1. The setting

- [ ] 1.1 Add the spool limit to `ArchiveyConfig` beside `listing_limits`, in the shape
      chosen for `design.md` Q2, with the three values: byte count, unlimited sentinel,
      none. Match the `ExtractionLimits.UNLIMITED` / `ListingLimits.UNLIMITED` pattern.
- [ ] 1.2 Add the spool directory alongside it.
- [ ] 1.3 Export any new public name from `archivey.__all__` and give it an `api.md` entry.
- [ ] 1.4 Docstrings on the field and any new type — `api.md` renders from docstrings, and
      a `#` comment reaches no reader (`review/docs-content/scope.md` §Precondition).

## 2. The spool primitive

- [ ] 2.1 One internal helper performing a bounded spool: takes the limit, the directory, a
      source and an optional known size; returns a path; raises `ResourceLimitError` on the
      limit; registers cleanup with the reader's close.
- [ ] 2.2 Check the size **before** writing when it is known; enforce during the write when
      it is not, removing the partial file on the way out.
- [ ] 2.3 Best-effort free-space pre-flight per `design.md` Decision 5 — a fast-fail, never
      a promise, and not reachable as a guarantee from any public docstring.
- [ ] 2.4 Record every spool in `CostReceipt.notes` with its byte count. No diagnostic.
- [ ] 2.5 Refusal path for the "none" setting, raising whichever error `design.md` Q3
      settles on. Keep `StreamNotSeekableError` for the non-seekable-source case, extending
      its message to name the setting.

## 3. Route the existing RAR materialization through it

- [ ] 3.1 `RarReader._ensure_archive_path()` uses the primitive.
- [ ] 3.2 `RarReader._materialize_stream_volumes()` likewise, with the limit measured across
      the whole volume set rather than per volume.
- [ ] 3.3 Confirm no behaviour change for path sources, for listing a stream source, and for
      stored-member reads from a stream — none of those may spool.

## 4. Spooling a non-seekable source

- [ ] 4.1 At open, when the format requires seekability, the source is not seekable, and the
      limit permits it: spool, then proceed as for a seekable source.
- [ ] 4.2 When the limit is none, keep raising `StreamNotSeekableError`, with the message
      naming the setting so the error teaches the fix.
- [ ] 4.3 Settle `design.md` Q4 — does `streaming=True` over a spooled source stream from
      the spooled file or switch to random access? — and implement it explicitly.

## 5. Tests

- [ ] 5.1 Red-green for the P11 case: a compressed RAR member read from a `BytesIO` records
      a `CostReceipt.notes` entry, and exceeds a low limit with `ResourceLimitError`. Verify
      by reverting the fix and watching each fail.
- [ ] 5.2 Every row of the four delta scenario matrices — spool limit, reporting, timing,
      pre-flight/directory — plus the RAR matrix.
- [ ] 5.3 Timing specifically: listing a RAR from a stream does **not** spool; the first
      compressed member does; the second does not spool again.
- [ ] 5.4 Cleanup: temporary file or directory gone after `close()`, and after an exception
      raised mid-spool.
- [ ] 5.5 Non-seekable source for ZIP, 7z and ISO — the formats `StreamNotSeekableError`
      refuses today — under a permitting limit and under none.
- [ ] 5.6 Cross-platform: Windows temp-file semantics differ (an open file cannot always be
      replaced or removed). Name the spool file and close handles so cleanup works there —
      CI matrixes Windows and macOS, and the container is Linux.

## 6. Registers and docs

- [ ] 6.1 Close `dev-docs/open-issues.md` **P11**, recording the limit that shipped and the
      `CostReceipt.notes` choice.
- [ ] 6.2 Threat-model pass: untrusted bytes at a predictable path, spool-directory
      permissions, cleanup after a hard kill. Match `docs/extracting.md`'s existing
      `.archivey-tmp-*` treatment rather than inventing a second convention.
- [ ] 6.3 Update `review/docs-content/claims.md` **E-71** — the row records that no page
      states the spill; when this lands the page states the limit instead. Do **not** edit
      pages under `docs/` from this change; Topic 8 owns the guide.
- [ ] 6.4 Add the payload-cache idea to `dev-docs/IDEAS.md` §Performance, recorded as a
      **caller-side wrapper-stream** concern archivey might ship or recommend later — not
      as a deferred version of this change.
- [ ] 6.5 `openspec archive` this change in the implementing PR — CI checks it on PRs.
