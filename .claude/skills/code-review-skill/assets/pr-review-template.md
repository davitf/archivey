# PR Review Template

The fill-in form for a review in this repo. **The rules are not here** — they are in
[addendum §0](../reference/archivey-review-addendum.md) (output shape, verdicts, round
budget, severity × confidence) and §10 (posting, IDs, re-reviews). This file is a shape to
copy, and is deliberately not a second copy of §0.

---

## 1. Maintainer briefing (read this first)

**What this change is** — *once per PR, not once per round and not once per reviewer (§0)*

[2–4 sentences: intent, areas touched, behaviour delta — readable without the diff]

**Snapshot**

- **PR size:** [Small/Medium/Large] (~X lines)
- **Review scope:** [`main...HEAD`, or `<last-reviewed-sha>..HEAD` from round 2 on]
- **Gates (CI status, not re-run):** [ruff / pyrefly / ty / pytest — or `CI pending`]
- **Verdict:** [✅ Approve / ✅ Approve conditional on <your-prefix><n>, … / 💬 Comment / 🔄 Request Changes]

**Main points**

- 🔴/🟡 [one-sentence gist]
- …

**What’s fine** (optional)

- [Load-bearing thing that looked correct]

---

## 2. Implementor handoff (goes on the PR)

**Context:** [PR # / branch / scope — so this block stands alone once §10 splits it]

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

🟢 **[nit]** [Suggestion — small, and fixed on this PR]

💡 **[suggestion]** [Alternative approach]

📚 **[learning]** [Educational note — no action needed]

🎉 **[praise]** [Specific strength worth keeping]

**Verdict:** [as above]

### Measured this round

One line per command you ran, with its result, so the next round inherits it instead of
re-deriving it (§10). Ran nothing? `None.`

- `[command]` → [result]

---

## 3. Maintainer decisions (your attention)

Numbered items that need a **human call** only. Each must be decidable without reading the
briefing, handoff, or diff. If none: `None.`

1. **[Decision]** — [yes/no or A vs B]
   - **Why you:** [spec/VISION conflict, product trade-off, pause-and-ask, …]
   - **Options:** A — [consequence]; B — [consequence]
   - **Recommendation (optional):** […]

---

## Quick copy blocks

### Blocking issue
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

### Important suggestion
```
🟡 **[important]** `PLAUSIBLE` — [Title]

**Location:** `path/to/file.py:123`

[Why this is important]

**Consider:**
- Option A: [description]
- Option B: [description]
```

### Minor suggestion
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
