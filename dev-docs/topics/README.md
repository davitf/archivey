# Topic handbook pages

Cross-cutting living notes (same genre as [`../formats/`](../formats/README.md)):
short, current, rewritten when claims move. Prefer these over new ADRs for
decisions that span formats.

**Do not restate registers.** [`../threat-model.md`](../threat-model.md) stays the sole
home for the open `O*` gap register — a topic page **links** to `O*` rows (and to
detection specs) rather than copying them. Same rule for other living registers under
`dev-docs/`.

Suggested first pages (create when a change needs them):

| Topic | Suggested file | Seeds (link, don’t duplicate) |
| --- | --- | --- |
| Detection / SFX / prefixes | `detection.md` | `openspec/specs/format-detection`, detection investigations |
| Extraction safety | `extraction-safety.md` | [`threat-model.md`](../threat-model.md) `O*` rows; safe-extraction spec |
| Streaming, seek, cost | `streaming-and-cost.md` | access-mode-and-cost, seekable streams |
| Errors and diagnostics | `errors-diagnostics.md` | error-handling, diagnostics specs/discussions |

Until a page exists, point briefs at the seeds above and write the topic page as part of
the change that needs it.
