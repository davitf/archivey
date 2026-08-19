## 1. Parser start offset

- [x] 1.1 Give the 7z open path a signature origin S (not always 0). *Landed as the amended Decision 1: `start_offset` reaches `SevenZipReader`, which rebases `SharedSource` views through `_view`; the parser keeps its "`fp` begins at the signature" contract, since its seeks already run against a view.*
- [x] 1.2 Fast path unchanged when magic is at the open origin

## 2. Forced-format SFX scan

- [x] 2.1 When magic is missing at the open position, scan forward within the shared `SFX_MAX` for `MAGIC_7Z` (same constant as RAR / detection — do not introduce a second bound)
- [x] 2.2 On miss within bound, raise `CorruptionError` (no silent empty archive)

## 3. Reader / pipeline wiring

- [x] 3.1 Accept detection-supplied `payload_offset` / start offset on the 7z reader open path
- [x] 3.2 Exercise packed-stream + encoded-header archives opened behind a stub (not only empty/tiny fixtures)

## 4. Verify

- [x] 4.1 Forced `format=SEVEN_Z` on `MZ` + 7z payload opens real members
- [x] 4.2 Explicit start offset N with magic at N
- [x] 4.3 No magic within bound → `CorruptionError`
- [ ] 4.4 `openspec validate --strict sevenz-sfx-start-offset`
- [ ] 4.5 Archive this change in the finishing PR (`openspec archive sevenz-sfx-start-offset --yes`)
