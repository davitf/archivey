# A deferring stream operation raises once

## Why

Decompressing streams hold an escalated diagnostic until their state is consistent
(`codec-stream-diagnostics`). One read can meet the same condition twice, such as a seek
table thinned twice inside one `read(-1)`. It can raise only once, so the second
escalation is not raised. The diagnostics spec still says the policy raises on every
occurrence, with no qualification for an operation that defers.

## What Changes

- The diagnostics policy requirement states that a deferring stream operation raises
  at most once, with the first error it held; each occurrence is still evaluated and
  delivered.
- The seek-index matrix row for a table thinned more than once says it raises once per
  call that thins it.

## Impact

- `diagnostics` and `seekable-decompressor-streams` specs.
- Docstrings of `DiagnosticCollector.deferring_raises` and `escalate_only`.
