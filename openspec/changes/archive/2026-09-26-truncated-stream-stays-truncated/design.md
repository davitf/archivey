# Design

The stream keeps the raised exception instance as its record. Two details keep the
record cheap:

- `readall` releases the decoded prefix it is about to drop before it raises, because
  the exception's traceback keeps the `readall` frame and its locals alive.
- Each re-raise uses `with_traceback(None)`, so the traceback holds only the frames of
  the current raise and does not grow with each retry.

`tell()` stays at the bytes the caller received. Counting the dropped prefix in the
position would make the recovery `seek(0)` look like an expensive rewind and report
`STREAM_REWIND_REDECOMPRESSES` for progress the caller never had.
