# detection-prefix-workspace — read the source once, and know what it cost

**Status:** Ready to implement. Depends on nothing. Blocks the evidence-ledger change, and unblocks the prefixed-archive change's installer support. Not breaking. Effort: medium.

**Why it matters:** Detection's byte budget is bounded but its access shape is not, and on sources where seeking is expensive the shape is what costs. Measured on a seekable stream, detecting a gzip file does five backward seeks, fetching the same thirty bytes five times. Detecting an ISO reads four thousand bytes, rewinds, then re-reads thirty-two thousand from zero. The path that cannot seek already behaves correctly, so the discipline exists for pipes and is missing where seeking is most expensive. An HTTP range reader and a member stream from a solid archive both look like a local file to every check in the system.

**What it does:** One detection-owned prefix buffer that grows monotonically, so extending the window reads only the difference. A stated access shape: one forward pass, then at most one seek towards the end, then one read to end, with no backward seeks ever. A candidate-relative view, so a validator can be handed bytes positioned at a payload that does not start at zero — which self-extracting installer support needs and cannot currently get. And a budget, a capability set and a cost receipt, so detection can be told what it may spend and can report what it did.

**Decided:** A flat shape rule rather than a source cost model, because that shape is affordable everywhere and nothing can currently tell an expensive seek from a cheap one. The ZIP tail probe stays out of the default budget until measured, in seeks as well as bytes.

**Your call later:** Whether the budget's limits are per-detection totals or per-candidate. A fuzz assertion pins the invariant either way, which is what lets the question stay open. Note the default preset now also runs a full decode for sources up to sixty-four kilobytes, so a genuine small compressed stream can be confirmed outright rather than guessed at.

**Bottom line:** Plumbing, but two later changes are blocked without it.
