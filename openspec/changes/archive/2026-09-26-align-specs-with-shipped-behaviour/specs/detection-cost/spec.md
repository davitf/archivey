## ADDED Requirements

### Requirement: FormatInfo reports the receipt and the tiers that did not run

`detect_format` SHALL return its receipt on `FormatInfo.cost_receipt` and the tiers it
did not run on `FormatInfo.unavailable_tiers`. Both are public:

```python
class TierSkipReason(Enum):
    NOT_ENABLED_BY_POLICY = "not_enabled_by_policy"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    BUDGET_EXHAUSTED = "budget_exhausted"

@dataclass(frozen=True)
class TierSkip:
    tier: str
    reason: TierSkipReason
```

`cost_receipt` SHALL be set on every `FormatInfo` that `detect_format` returns (the zero
receipt for a directory) and SHALL be `None` only on a `FormatInfo` that other code built.
`unavailable_tiers` SHALL list each skipped tier once, in the order it was first recorded,
and SHALL be empty when every tier ran. `NOT_ENABLED_BY_POLICY` SHALL NOT mean that the
search was incomplete; `CAPABILITY_UNAVAILABLE` and `BUDGET_EXHAUSTED` SHALL mean that it
was. Neither field SHALL take part in `FormatInfo` equality or `repr`, so two results that
found the same format at a different cost compare equal. `tier` names an open set: a later
release MAY add a tier.

#### Scenario: receipt and skips on FormatInfo

| Case | Expected |
| --- | --- |
| `detect_format` on a file | `cost_receipt` is a `DetectionCostReceipt`, not `None` |
| Directory path | `cost_receipt` is the zero receipt (`passes` 1); `unavailable_tiers` is empty |
| ZIP under the default budget | `unavailable_tiers` holds `TierSkip("zip_tail", NOT_ENABLED_BY_POLICY)` |
| Two results that differ only in receipt or skips | Compare equal |
