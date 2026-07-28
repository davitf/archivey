# Test-strategy holes

Original refs: `main` @ `7bb862b`. **Status refresh 2026-07-25** @ `3793646`.

## Paid since the original ledger

- Randomized seek (XZ), both-idioms truncation (`.Z`), Atheris gate, etc.
- **T1 solid-RAR mutation — DONE (#184).**

## T1 — **DONE (#184)**

Static solid RAR4/RAR5 under mutation. Encrypted-header / multi-volume still
outside mutation → **T7**.

## T2 — seek-interleaving stops at XZ — **DONE**

`test_seek_interleaving_matches_plaintext` parametrized over XZ / lzip / `.Z`
(unix-compress; `ncompress` gated).

## T3 — benchmark-gate RAR / encrypted / accelerator data — **DONE**

Structural gate cases for RAR solid/encrypted (committed fixtures + `unrar`),
WinZip AES ZIP (`[crypto]`), ZIP LZMA, and in-ZIP accelerated deflate
(`[seekable]`). Omit cleanly when optional deps/binaries are absent. Perf P6
remainder closed.

## T4 — free-threaded core-only; `*_if_available` untested under threads — **half DONE**

KEEP scope (the `3.13t` job stays core-only — that half is a documented support-matrix
statement, not a test gap). **Paid:** two `members_report_if_available` tests in
`test_concurrent_multithread.py` — a foreign-thread peek held deterministically inside
a live materialization (all-or-nothing: `None` or the *complete* report, never the
scan's partial working list, and never a scan of its own), plus a barrier test of the
`_MEMBER_LIST_UPFRONT` branch, which builds a fresh report per call while another
thread materializes. The first was verified against an injected partial-publication
mutant (it fails `4 == 8`), so it is a real net, not a smoke test.

## T5 / T6 — KEEP (opportunistic / past 0.2.0)

## T7 — corpus matrix thin spots — **DONE**

Audited in [`corpus-matrix.md`](corpus-matrix.md): full shape×format matrix, the
extensions, the deliberate exclusions, and four recorded residuals. Closed: ISO into
`encoding`/`symlinks` + a Joliet-only builder variant (the reader's Joliet branch was
corpus-dead); header-encrypted 7z as a new shape in **both** sweep and mutation;
encrypted-header + volume RAR fixtures into the static mutation intake.

Audit finding worth carrying forward: **11 of 71 rows never run in CI**, and only the
8 `rar`-writer rows are a deliberate decision — the 3 encrypted-ZIP rows depend on an
ambient `7z` CLI that no workflow installs, so that coverage is *unpinned* rather than
absent. Recorded as residual 1 in the audit doc; it is a CI-workflow call.
