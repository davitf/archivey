# PR Review Template

Copy and use this template for code reviews in this repo.
Matches [addendum §0 output shape](../reference/archivey-review-addendum.md).

---

## 1. Maintainer briefing (read this first)

**What this change is**

[2–4 sentences: intent, areas touched, behaviour delta — readable without the diff]

**Snapshot**

- **PR size:** [Small/Medium/Large] (~X lines)
- **Gates:** [ruff / pyrefly / ty / pytest — if known]
- **Verdict:** [✅ Approve / 💬 Comment / 🔄 Request Changes]

**Main points**

- 🔴/🟡 [one-sentence gist]
- …

**What’s fine** (optional)

- [Load-bearing thing that looked correct]

---

## 2. Implementor handoff (copy-paste ready)

**Context:** [PR # / branch / scope — so this paste stands alone]

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

🟢 **[nit]** [Suggestion — not blocking]

💡 **[suggestion]** [Alternative approach]

📚 **[learning]** [Educational note — no action needed]

🎉 **[praise]** [Specific strength worth keeping]

**Verdict:** [✅ Approve / 💬 Comment / 🔄 Request Changes]

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

Not blocking, but consider [improvement].
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
