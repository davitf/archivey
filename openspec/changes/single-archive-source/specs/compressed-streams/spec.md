## MODIFIED Requirements

### Requirement: Content faults raise from read, never from close

This requirement scopes to the streams this layer owns: `DecompressorStream`
(and every codec `Decoder` behind it) and `VerifyingStream` / the fused
`MemberVerifier`. Other backends (the rapidgzip accelerator and its
`_GzipTruncationCheckStream`, and any third-party wrapper) are **out of scope**
here; they already surface content faults from `read` rather than `close`, and
retargeting them is deferred (see the rapidgzip follow-up). The wording below is
a standing rule for the in-scope streams, not a claim that every stream type in
the library has been audited to it.

Decode and verify streams SHALL raise content `TruncatedError` and
`CorruptionError` from `read` / `readall` (and from size/seek paths that would
otherwise report a false clean completion). `close()` MUST NOT raise those
content faults. `close()` MAY still propagate teardown failures (`OSError`,
translated inner-close errors).

Public bounded `read(n)` for `n ≥ 1` on `ArchiveStream` and
`VerifyingStream` / `MemberVerifier` SHALL be **full-count**: return exactly `n`
bytes unless a terminal boundary is reached (clean EOF, truncation-shaped short,
or a raised content error). Implementations SHALL issue one `inner.read(n)` and
forward a short non-empty return as terminal — they SHALL NOT retry it, which would
pull a decoder's deferred truncation into the call. The `n`-or-terminal guarantee
therefore rests on inners being fill-or-EOF (`DecompressorStream`, `ZipExtFile`,
`BytesIO`); an inner that may short mid-stream SHALL be read through a full-count
reader in front (at the source boundary, the `ArchiveSource`) rather than a gather loop
at the public surface. `read(0)` is a no-op, never EOF.

`VerifyingStream` / fused `MemberVerifier` SHALL verify digests (CRC and other
expected hashes) when a read **reaches the member's end**:

- **Size-declared** (`expected_size` set): the read that consumes the declared
  size is a verifying event (checksum and over-run). On digest mismatch or
  over-run it SHALL raise `CorruptionError` and return **no bytes** for that call
  (withhold the final chunk). On truncation-shaped EOF before the declared size,
  the first read that asks past available output returns the remaining prefix
  (short return); the next empty `read` raises `TruncatedError`.
- **Size-unknown**: every data chunk MAY be returned first; `CorruptionError`
  SHALL raise on the read that observes end-of-stream (typically the terminal
  empty `read`) — no mandatory one-chunk delayed-release lookahead.

On `readall` / `read(-1)`, the complete-stream read SHALL include the EOF verdict
and SHALL raise `CorruptionError` on mismatch (and `TruncatedError` on hash-less
short) so `read(); close()` cannot silently accept bad content. `finish_on_close`
SHALL close the inner and MUST NOT introduce a first content `TruncatedError` /
`CorruptionError` solely because the caller is closing.

A seek off the sequential frontier SHALL forfeit digest verification for the rest
of the handle's life. Length / truncation / over-run checks SHALL remain active and
SHALL key off bytes actually read (not a seek-updated logical position alone). When a
seek jumps the logical position to/past the declared size without reading the
intervening bytes, concluding SHALL read that skipped gap (bounded by the declared
size) **and probe one byte past the declared size**, reproducing the same length +
over-run verdict a sequential reaching read runs, rather than returning `b""` blind.
So a past-EOF `seek(declared_size)` on a **truncated** member MUST NOT silence
`TruncatedError`, and on an **over-long** member (one that decodes past its declared
size) MUST NOT silence `CorruptionError`. Symmetrically, the same jump on a
**complete** member MUST NOT fabricate either fault: a seek to/past the declared size
followed by `read` returns `b""` (standard `BinaryIO` past-EOF semantics), and the
`seek(member.size); read(1)` completeness idiom works.
A member already read to its declared size is length-verified, so a later seek past
the end concludes with no extra reads.

Deliberate partial read then close before clean EOF remains quiet for
digest/length verification (abandon before verdict), modulo the length checks
that only fire when a read reaches EOF / asks past available.

On the complete-stream (`readall` / `read(-1)`) path the verifier SHALL drain
the inner to genuine EOF (`inner.read` returning `b""`) in **bounded** steps.
It MUST NOT assume a single `inner.read` returns the whole body: `inner` is an
arbitrary `BinaryIO` and MAY return fewer bytes than requested without being at
EOF (a short read). A single `inner.read(remaining)` therefore under-returns on
any short-reading inner and skips the EOF verdict — the drain loop fixes both.
When a decompressed size is declared, each step SHALL stay capped by the
remaining declared byte count so a corrupt/adversarial **over-long** stream is
stopped at the declared size (raising `CorruptionError`) and never slurped
unbounded into memory. This is why the sized path MUST NOT delegate to
`inner.read(-1)`; the size cap is a decompression-bomb bound, and the code
carrying it SHALL say so inline. The unsized path (no declared size, no cap)
MAY delegate to `inner.read(-1)` and then run the EOF verdict.

#### Scenario: close vs read matrix

| Case | Expected |
| --- | --- |
| Truncated `DecompressorStream`; catch on empty `read`; then `close()` | `close()` succeeds |
| Truncated gzip stdlib path; error already observed on `read`; then `close()` | `close()` succeeds |
| Size-declared digest/CRC mismatch; `read(expected_size)` or chunk reaching size | Raises `CorruptionError`; that call returns no bytes (withholds) |
| Size-unknown digest/CRC mismatch; chunked `read(n)` | All content bytes delivered; terminal empty `read` raises `CorruptionError`; `close()` quiet |
| Digest/CRC mismatch; `read()` / `read(-1)` | Raises `CorruptionError` (complete-stream verdict); `close()` alone does not raise the digest fault |
| `read(); close()` with bad CRC | `read()` raises — must not succeed quietly |
| Hash-less short member; `read(-1)` | Raises `TruncatedError` |
| Hash-less short; chunked until empty | Available prefix delivered; terminal empty `read` raises `TruncatedError` |
| Exact-available `read(k)` then `close` (k == decompressed length < declared) | Quiet — did not ask past available |
| `read(-1)` over a short-reading inner (returns `< n`, not EOF) | Full body gathered via bounded drain; EOF verdict fires in that call |
| Bounded `read(n)` over a short-reading inner | Full-count: returns `n` or short only at terminal boundary |
| `read(-1)` over an over-long inner with a declared size | Stopped at the declared size; `CorruptionError`; inner not read unbounded past the cap |
| Seek off frontier then short of declared size | Checksum forfeited; `TruncatedError` still raises on completing/empty read |
| Seek to/past declared size on a **complete** member, then `read` (incl. `seek(size); read(1)`) | Returns `b""`; no fabricated `TruncatedError` (checksum forfeited by the seek) |
| Seek to/past declared size on a **truncated** member, then `read` | Concluding reads the skipped gap; `TruncatedError` with the true recoverable length |
| Seek to/past declared size on an **over-long** member, then `read` | Concluding reads the gap and probes past the declared size; `CorruptionError` (over-run), not a silent `b""` |
| Partial read then `close` before clean EOF (verify) | No digest/length verdict |
| Inner teardown fails on `close` | Teardown error may propagate |
