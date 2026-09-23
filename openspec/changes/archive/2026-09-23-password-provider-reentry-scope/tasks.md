## 1. Provider turn

- [x] 1.1 `_PasswordCandidates._call_provider`: a worker waits for another worker's
      provider call instead of raising; reentry is recognized by thread or by a
      context variable the provider call sets.
- [x] 1.2 Tests: two workers share the provider; reentry from a context-carrying
      helper thread raises; the turn is released when the provider raises.

## 2. Archive

- [x] 2.1 `openspec validate --strict password-provider-reentry-scope`, then archive.
