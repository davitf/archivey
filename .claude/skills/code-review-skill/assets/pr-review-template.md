# PR Review Template

Copy and use this template for code reviews in this repo.
Matches [addendum §0 output shape](../reference/archivey-review-addendum.md).

Brevity fence: keep blocks 1 and 3 skim-friendly; put full evidence and fix direction in
block 2. Do not drop real pause-and-ask items to leave section 3 empty.

---

## 1. Maintainer briefing (read this first)

**What this change is** — *once per PR, not once per reviewer: if any review on this PR
already carries one, delete this heading and open with the status table over the existing
IDs instead (addendum §0, §10)*

[2–4 sentences: intent, areas touched, behaviour delta — readable without the diff]

**Snapshot**

- **PR size:** [Small/Medium/Large] (~X lines)
- **Review scope:** [`main...HEAD` on a first round; `<last-reviewed-sha>..HEAD` from
  round 2 on — addendum §10 "Re-reviews"]
- **Gates (CI status, not re-run):** [ruff / pyrefly / ty / pytest — or `CI pending`]
- **Verdict:** [✅ Approve / ✅ Approve conditional on <your-prefix><n>, … / 💬 Comment / 🔄 Request Changes]
  - “Only nits left” is not an approval — addendum §0 Verdicts. Nits get fixed on this PR,
    or the verdict is the conditional approval, naming the IDs it is conditioned on.
  - Round 3 or later with only 🟢 nits open → the conditional approval, and stop reviewing;
    do not open another round to confirm wording (addendum §0 Round budget).
  - Posting through the maintainer's account? Submit as `COMMENT`; GitHub blocks
    self-approval (addendum §10). The verdict line *is* the verdict.

**Main points**

- 🔴/🟡 [one-sentence gist]
- …

**What’s fine** (optional)

- [Load-bearing thing that looked correct]

---

## 2. Implementor handoff (goes on the PR)

**Context:** [PR # / branch / scope — so this block stands alone once §10 splits it]

Finding IDs carry your own initial (`K1`, `K2`, … from Claude Code; `C1`, `C2`, … from
Cursor) and keep counting up across your rounds on this PR — addendum §10.

### Required changes

🔴 **[blocking]** `CONFIRMED|PLAUSIBLE` — [Title]

**Location:** `path/to/file.py:123`

[What’s wrong and why it matters]

**Suggested fix:** [concrete direction]

**Trigger:** [input/state → failure], or needs-repro

### Important suggestions

🟡 **[important]** `CONFIRMED|PLAUSIBLE` — [Title]

**Location:** `path/to/file.py:123`

[Why this matters]

**Consider:**
- Option A: [description]
- Option B: [description]

### Minor / suggestions

Nits are small, not optional: they are fixed on this PR (addendum §0 Verdicts).

🟢 **[nit]** [Suggestion — small, fix before merge]

💡 **[suggestion]** [Alternative approach]

📚 **[learning]** [Educational note — no action needed]

🎉 **[praise]** [Specific strength worth keeping]

**Verdict:** [✅ Approve / ✅ Approve conditional on <your-prefix><n>, … / 💬 Comment / 🔄 Request Changes]

### Measured this round

One line per command you ran, with its result, so the next round inherits it instead of
re-deriving it (addendum §10). Ran nothing? `None.`

- `[command]` → [result]

---

## 3. Maintainer decisions (your attention)

Numbered items that need a **human call** only. Each must be decidable without reading
the briefing, handoff, or diff. If none: `None.`

1. **[Decision]** — [yes/no or A vs B]
   - **Why you:** [spec/VISION conflict, product trade-off, pause-and-ask, …]
   - **Options:** A — [consequence]; B — [consequence]
   - **Recommendation (optional):** […]

---

## Quick Copy Templates

### Blocking Issue
```
🔴 **[blocking]** `CONFIRMED` — [Title]

**Location:** `path/to/file.py:123`

[Description of the issue]

**Suggested fix:**
\`\`\`python
# suggested code
\`\`\`

**Trigger:** [input/state → failure]
```

### Important Suggestion
```
🟡 **[important]** `PLAUSIBLE` — [Title]

**Location:** `path/to/file.py:123`

[Why this is important]

**Consider:**
- Option A: [description]
- Option B: [description]
```

### Minor Suggestion
```
🟢 **[nit]** [Suggestion]

Small, but please fix on this PR: [improvement].
```

### Praise
```
🎉 **[praise]** Great work on [specific thing]!

[Why this is good]
```

### Learning
```
📚 **[learning]** [Educational note]

For context, [X] works this way because [Y]. No action needed — just sharing.
```

### Maintainer decision
```
1. **[Decision title]** — choose A or B
   - **Why you:** [conflict / trade-off]
   - **Options:** A — [consequence]; B — [consequence]
   - **Recommendation (optional):** [A because …]
```
