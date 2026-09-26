# Tasks — listing and seek contract fixes

## 1. Code

- [x] 1.1 `max_metadata_bytes` weighs the keys of a dict nested in `extra` (TAR PAX keywords); top-level keys stay unweighed.
- [x] 1.2 A relative seek before a member's start clamps to 0; a negative `SEEK_SET` or unknown `whence` raises `ValueError` in `ArchiveStream.seek`. The RAR `unrar` stream clamps too.

## 2. Proof and documents

- [x] 2.1 Red-green tests in `tests/test_listing_limits.py` and `tests/test_member_stream_contract.py`.
- [x] 2.2 `CHANGELOG.md`, `docs/access-and-cost.md`, `dev-docs/known-issues.md`.
- [x] 2.3 `openspec validate --strict listing-and-seek-contract-fixes`, then archive this change.
