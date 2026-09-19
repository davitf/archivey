## Context

Member data already goes through `unrar p`. The finder refused RARLAB `rar` because
`_parse_unrar_banner` required the substring `UNRAR`. Handbook §10 #20; measurements
are in `dev-docs/formats/rar.md` §3.

## Goals / Non-Goals

**Goals:**

- Accept RARLAB `rar` 6.0+ when `unrar` is missing or not a usable RARLAB binary.
- Prefer `unrar` when both are usable.
- Keep refusing `unar` / `7z` / `unrar-free`.
- Spawn only `p`.

**Non-Goals:**

- Amortizing solid random reads via `unrar x` (handbook #8; rejected).
- Signalling the stream-source copy (handbook #6).
- A second decompressor engine (`unar` remains an open candidate, not this change).

## Decisions

- Banner: vendor string plus a standalone `UNRAR` or `RAR` token. Version from
  `UNRAR x.yy` first, else `RAR x.yy`. Lookbehind so `RAR` does not match inside
  `UNRAR`.
- One-entry probe cache stays; a `which` miss is still not cached.
- Docs name both binaries. Windows `Rar.exe` banner unmeasured.

## Open Questions

None for this change. Windows `Rar.exe` banner remains unmeasured (recorded in
`docs/install.md` and the handbook).
