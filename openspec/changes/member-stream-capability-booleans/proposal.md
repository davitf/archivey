## Why

One concept is spelled two ways at the two entry points:

```python
open_archive(p, member_streams=MemberStreams.SEEKABLE | MemberStreams.CONCURRENT)
open_stream(p, seekable=True)
```

Every user learns it twice. Worse, `member_streams=` does not read like a flag set — it
reads like it takes *streams*, or a count, or a mode — and you must import
`MemberStreams` before you can pass anything at all, so the option is invisible in
autocomplete until you have already found the enum.

Two ADRs are directly on point and both point **toward** this change rather than against
it:

- **ADR 0004** rejected an `Intent` enum in favour of `streaming: bool`, reasoning "two
  real modes, not three labels for two behaviors". The recorded taste is: prefer a bool
  when the mode count is small.
- **ADR 0003** describes the member-stream defaults and then says, of
  `open_stream(..., seekable=False)`, "**same rule**" — the ADR already treats these as
  one concept.

Neither ADR defends `member_streams` as a *name* or the flag/bool split between entry
points. That split is accretion, not a decision. It has now been independently flagged
twice (the archived api-coherence review, then the code-derived documentation pass).

`0.2.0` is the first public release, so there are no external callers. This is breaking
after the tag and free before it.

## What Changes

- `open_archive` takes two keyword-only booleans instead of a flag enum:

  ```python
  open_archive(p, seekable_members=True, concurrent_members=True)
  open_stream(p, seekable=True)
  ```

- `member_streams=` is **removed**, not deprecated. Nothing external depends on it, and
  carrying both spellings would double exactly the surface this change exists to halve.
- `MemberStreams` **stays exported**. It remains meaningful as the declared-capability
  value on `CostReceipt` and in diagnostics, and removing a public name buys nothing.
  Internal plumbing may keep using the flags; only the public entry point changes.
- The existing rejection of `streaming=True` combined with concurrency keeps its
  behaviour, now phrased against `concurrent_members=True`.

## Impact

- **Breaking**: one public signature (`core.py:101`). ~52 internal references, ~38 in
  tests, 8 in docs — mechanical, with the tests carrying most of the work.
- Behaviour is unchanged: same defaults (both `False`), same errors, same capability
  semantics. This is a spelling change, deliberately kept free of behavioural drift so
  the diff stays reviewable.
- ADR 0003 needs a short amendment noting the new spelling; ADR 0004's reasoning is
  reinforced rather than altered.
