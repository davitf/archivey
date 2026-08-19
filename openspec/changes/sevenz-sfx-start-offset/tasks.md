## 1. Parser start offset

- [ ] 1.1 Thread a start-offset parameter into `read_signature_and_next_header` and absolute seeks (signature origin = S, not always 0)
- [ ] 1.2 Fast path unchanged when magic is at the open origin

## 2. Forced-format SFX scan

- [ ] 2.1 When magic is missing at the open position, scan forward within the shared `SFX_MAX` for `MAGIC_7Z` (same constant as RAR / detection — do not introduce a second bound)
- [ ] 2.2 On miss within bound, raise `CorruptionError` (no silent empty archive)

## 3. Reader / pipeline wiring

- [ ] 3.1 Accept detection-supplied `payload_offset` / start offset on the 7z reader open path
- [ ] 3.2 Exercise packed-stream + encoded-header archives opened behind a stub (not only empty/tiny fixtures)

## 4. Verify

- [ ] 4.1 Forced `format=SEVEN_Z` on `MZ` + 7z payload opens real members
- [ ] 4.2 Explicit start offset N with magic at N
- [ ] 4.3 No magic within bound → `CorruptionError`
- [ ] 4.4 `openspec validate --strict sevenz-sfx-start-offset`
- [ ] 4.5 Archive this change in the finishing PR (`openspec archive sevenz-sfx-start-offset --yes`)
