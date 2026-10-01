# Give up an accelerated stream whose position is lost

## Why

After a read that raised, rapidgzip's in-process bzip2 decoder reported a `tell()` past
the bytes it had delivered (1 799 957 against 1 048 576 on a damaged stream), and a
later read would start there. The rapidgzip decoder process has moved back to the start
of a failed read since PR 510, and given the stream up when it could not. Neither rule
was written down, and the `compressed-streams` paragraph on a raised verdict says a seek
restarts the decode and `tell()` is not gated, which is false for a stream given up.

## What changes

Both accelerated engines move the decoder back after a read that raised, so `tell()`
stays at the bytes delivered. When that is not possible, or the caller's source faulted
during a read or seek, the stream is given up: the call raises the fault it met, and
every later `read`, `readinto`, `seek` and `tell()` raises `ReadError` naming the cause.
`close()` still succeeds.

## Impact

- `compressed-streams`: the "Content faults raise from read" requirement gains the
  given-up state, narrows the "not gated" sentence for it, and the matrix gains three
  rows.
- Code: `_AcceleratorStream` in `internal/streams/codecs.py`; the rapidgzip child
  already behaves this way.
