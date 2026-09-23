# archive-reading — password provider reentry scope

## MODIFIED Requirements

### Requirement: Password candidates and provider

`password` SHALL accept a single `str | bytes`, an **ordered sequence**, and/or a
**provider** `PasswordProvider = Callable[[PasswordRequest], str | bytes | None]`:

```python
@dataclass(frozen=True)
class PasswordRequest:
    member: ArchiveMember | None  # None for archive-level (header) decryption
    attempt: int                  # 1 on first ask for this unit; increments on failure
```

Per encrypted unit (member / 7z folder / archive header), try in order: per-archive
**known-good** list (successes this open, most recent first), then remaining sequence
candidates, then provider repeatedly until `None`. Successful passwords SHALL join
known-good for the rest of the operation so a provider is consulted once per *new*
password rather than once per member. Exhaustion (or provider `None`) →
`EncryptionError`. No per-call password on `open()`/`read()`.

**Concurrent use (observable):** After materialization, workers MAY open
differently encrypted members concurrently; known-good promotions are shared;
provider callbacks are serialized, and a worker that needs the provider while
another worker's call runs waits for it. Reentry from inside a provider into a
password-requiring operation on the same reader raises `ArchiveyUsageError` where
it can be recognized; which reentry that is, and what a provider that blocks on a
helper thread gets, is in `reader-concurrency`.

#### Scenario: password matrix

| Case | Expected |
| --- | --- |
| `password=[pw_a, pw_b]`, members use different passwords, one streaming pass | Each unit matches; pass completes without RA |
| Provider + unknown password needed | Called with that member's `PasswordRequest`; success → known-good; later same-pw members skip provider |
| Provider password fails, consulted again | New request has incremented `attempt` |
| Provider returns `None` | `EncryptionError` for that unit |
| Header-encrypted archive, provider only | Request with `member is None` |
| Concurrent opens of different encrypted units (post-materialization) | Each decrypts correctly; promotions shared without races |
| Two workers need the provider at once | Second waits for the first call; neither raises |
| Provider starts another password op on same reader, same thread | Nested op → `ArchiveyUsageError` |
