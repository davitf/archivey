## Two booleans vs. keeping the flags

**Chosen: two booleans.**

- Consistent with ADR 0004's recorded preference in the analogous case, so this is the
  codebase's existing taste rather than a new direction.
- Closes the vocabulary split: `seekable` means the same thing in `open_archive` and
  `open_stream`.
- Self-documenting where it matters. `seekable_members: bool = False` in the signature
  tells the whole story — no import, no enum lookup, visible in autocomplete. That is the
  point: this change came out of a "make the code say it so the docs need not" pass.
- There are exactly two capabilities, and ADR 0003 frames them as two specific traps
  (seek cost, overlapping opens) rather than an open-ended set.

**The counter-argument, recorded because it is real:** flags are more extensible (a third
capability is a new flag, not a new parameter) and composable (a caller can compute a
capability set and pass it). If a third capability were expected, the cheaper fix would be
to keep the enum and rename the parameter — `require=MemberStreams.SEEKABLE` reads well
and fixes the flag-ness complaint alone. This change bets that a third is not coming; if
that bet is wrong, adding `parallel_members=` later is still additive and non-breaking.

## Naming

`seekable_members` / `concurrent_members` over `seekable` / `concurrent` because
`open_archive`'s subject is the archive, not a stream: unqualified `seekable=True` there
would read as "the *source* is seekable", which is a different (and already inferred)
property. The `_members` suffix keeps the subject unambiguous while keeping the shared
`seekable` root that makes the two entry points rhyme.

## Why remove rather than deprecate

Deprecation exists to protect callers. There are none — `0.2.0` is the first public
release and only `0.2.0.dev0` reached TestPyPI. Accepting both spellings would leave the
library shipping the exact ambiguity this change removes, and every doc page would have to
explain which to prefer.

## Non-goals

- No behavioural change. Same defaults, same `ConcurrentAccessError`, same
  `io.UnsupportedOperation` on undeclared seek, same `streaming` + concurrency rejection.
  Keeping behaviour fixed is what makes the large mechanical diff reviewable.
- `MemberStreams` is not removed, and `CostReceipt` is untouched.
