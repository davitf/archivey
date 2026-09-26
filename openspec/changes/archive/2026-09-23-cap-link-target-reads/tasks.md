# Tasks — cap symlink targets read from member data

## 1. The cap

- [x] 1.1 `MAX_LINK_TARGET_BYTES` and a shared `_read_link_target_data` in `base_reader.py`:
      refuse a declared size over the cap without opening, otherwise read at most cap + 1.
- [x] 1.2 ZIP, 7z and RAR4 `_ensure_link_target` read through it (RAR4 checks the stored
      size, which is its read).
- [x] 1.3 Reparse buffers: read the header, then the payload length it declares, never
      refused for size;
      the parsed target is held to the cap; the fallback diagnostic reports the member's
      real size.

## 2. Listing accounting

- [x] 2.1 `ListingLimitTracker.account_link_target`, called from `_finalize_links` for a
      target the hook produced, under the caller's enforcement flag.

## 3. Proof

- [x] 3.1 `tests/test_link_target_cap.py`: ZIP at-cap and one-over, the compressed bomb,
      7z at-cap / one-over / bomb, RAR4 over-cap, the bounded read with no declared size,
      strict refusal, extraction outcome, and `max_metadata_bytes` over resolved targets —
      in random-access and streaming modes where the mode reaches the read.
- [x] 3.2 Reparse cases in `tests/test_windows_reparse.py`: over-cap target, and a large
      non-link member that keeps its content.

## 4. Documents and gates

- [x] 4.1 User docs name the cap and the reason.
- [x] 4.2 `openspec validate cap-link-target-reads --strict`; `./scripts/check.sh`.
- [x] 4.3 Archive this change.
