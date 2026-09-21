# archive-reading — the RAR example is the source copy, not `unrar x`

## MODIFIED Requirements

### Requirement: Bounded implicit temporary storage

Reader ops SHALL NOT consume memory or temp storage proportional to member/archive
size as an implicit side effect of open/read/validate/password-confirm. Silently
spooling plaintext to a temp file is forbidden. A per-format strategy that
inherently needs proportional temp storage (e.g. `format-rar`'s documented copy
of a non-path archive source to disk, so `unrar` can seek it) is allowed only when
declared in that format's capability spec. Caller's own buffering of a returned stream is unrestricted.

#### Scenario: bounded storage matrix

| Case | Expected |
| --- | --- |
| Encrypted member, many candidates | Confirmation temp use bounded by a constant |
| Backend can only serve via materialization | Strategy declared in format spec, not adopted silently |
