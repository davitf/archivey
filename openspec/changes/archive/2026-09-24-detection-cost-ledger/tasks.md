# Tasks — the detection cost ledger charges what detection does

## 1. Code

- [x] 1.1 Far tier: a signature past a positive `max_far_bytes` records `far_magic` as
      budget exhausted and is not searched.
- [x] 1.2 `read_at`'s buffered fallback is capped by the budget's prefix/far/scan ceiling
      as well as the 1 MiB constant.
- [x] 1.3 `detect_format` threads one receipt through the stub pass and the
      sibling-volume pass.
- [x] 1.4 Inner-TAR probe: input capped by `max_decode_input`, charged on failure, and a
      probe cut short records `inner_tar` as budget exhausted.
- [x] 1.5 `within_budget` compares `far_bytes` with `max_far_bytes`.
- [x] 1.6 The receipt carries `passes`; `within_budget` scales every limit by it, and a
      repeated skip is kept once.

## 2. Proof

- [x] 2.1 Red-green tests for each, plus the invariant test: a receipt that fails
      `within_budget` carries a skip naming the tier.
- [x] 2.2 `openspec validate --strict detection-cost-ledger`, then archive this change.
