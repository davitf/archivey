## 0. Relationship to the archived framing gate

- [x] 0.0 **Implement this change first, ahead of `probe-provenance-unconfirmed` and `prefixed-archive-detection`.** It has the smallest blast radius of the three — the completeness half is probe-internal, needs no extra I/O and no interface change — and the largest single effect: it rejects **91 of 128** measured fabrications (71%) at provably zero cost to genuine streams, since "a complete valid stream that fails to decode completely" is an empty set. Both siblings quote census numbers that this change invalidates, so landing it first means everything downstream measures against a stable base rather than re-deriving a moving one.

  **Split it if the chain walk stalls.** The completeness rule needs no design decision; the chain walk needs the bounded-read question in task 2.1 settled. If that question is still open when the completeness work is ready, ship completeness alone and let the chain walk follow — the proposal is written to allow exactly that split, and the two halves address disjoint populations (below and above the peeked prefix).

- [x] 0.1 ~~`brotli-probe-framing-gate` is implemented but deliberately not archived~~ — **archived** as `openspec/changes/archive/2026-08-23-brotli-probe-framing-gate`, its deltas synced into `openspec/specs/`. Its requirements are therefore **live**, and this change's deltas are written against shipped text rather than against another change's pending block
- [x] 0.2 ~~On archiving whichever goes second, reconcile the "MAY follow the chain" clause~~ — **done up front instead.** Now that the framing requirement is live, this change MODIFIES it directly: the paragraph that deferred the chain walk to "`tasks.md` 5.7" pointed at a task list that is now inside an archive directory, so it is replaced by a pointer to this change's own chain-walk requirement. No dangling reference, and no two requirements claiming the same rule
- [x] 0.3 ~~This change closes `brotli-probe-framing-gate` task 5.7~~ — **done**: that task is checked and marked as relocated here in the archived list
- [x] 0.4 The completeness rule is **new** — it was not one of that change's follow-ups. It came out of `scripts/exploration/probe_residual_census.py` while sizing 5.8, and it is 71% of the remaining residual against the chain walk's much smaller share
- [x] 0.5 **Archive order against the sibling.** `probe-provenance-unconfirmed` MODIFIES `error-handling` and ADDs to `format-detection`; this change MODIFIES `format-detection`'s framing requirement and `compressed-streams`. **No requirement is touched by both**, so the two archive in either order. The *sequencing* recommendation (this one first) is about measurement, not deltas — see 4.1

## 1. Completeness when the source is fully visible

- [x] 1.1 Add the rule where the probe's decode outcome is interpreted, not inside any one codec: when `source_length is not None and source_length <= len(prefix)`, a decode ending in "needs more input" is a **rejection**
- [x] 1.2 Apply it to **every** decoding probe — Brotli, zlib, LZMA Alone. Two of the four measured Alone fabrications are under the prefix; scoping it to Brotli would leave a general truth living in one codec
- [x] 1.3 Do **not** express it as a size threshold. `brotli.compress(b"hello")` is 9 bytes, decodes to completion, and must be accepted. The test is how the decode *ended*
- [x] 1.4 Skip the rule when `source_byte_size` returned `None`, exactly as the framing gate does
- [x] 1.5 Confirm no interface change is needed: the probe already receives both the prefix and `source_length` after #261

## 2. The chain walk

- [x] 2.1 **Settle who owns the reads first** (design.md open question). **Settled: B** — optional `read_at(offset, length) -> bytes | None`, absent by default; `None` = declined (cannot disprove). Non-seekable max offset **1 MiB**. Link cap **8** (revisit with hard data). See design.md.
- [x] 2.2 Follow the chain of byte-aligned self-describing meta-blocks, stopping at the first compressed block; reject a link that overruns the source or a declared end with trailing bytes. Reference implementation: `chain_walk` in `scripts/exploration/brotli_probe_field_survey.py`
- [x] 2.3 Bound the walk in link count. **Hitting the bound means "cannot disprove"** — keep the earlier verdict, do not reject. Cap is **8** (real-tree census never needed more than 2; revisit with hard data). Survey's 64 was a resource-guard default only.
- [x] 2.4 Keep the walk free of decompression, and bounded in bytes read as well as in links
- [x] 2.5 Confirm the walk terminates immediately on a compressed first meta-block (79 of 150 corpus streams), having read four bytes

## 3. Verify

- [x] 3.1 **Zero false negatives is the binding constraint**, as it was for the framing gate. Reuse the real-stream corpus (qualities 0/1/5/9/11 × `lgwin` 10/22/24 × payloads from empty to 1 MiB) and **extend it downward**: `brotli.compress(b"")`, `b"hello"`, and a handful of payloads under 100 bytes, all of which fit inside the prefix and must survive completeness
- [x] 3.2 Red–green for completeness: a 5-byte text file whose first meta-block parses as compressed is accepted today and must be rejected after. The census found 67 such files under 16 bytes on one tree — take fixtures from real families, not only synthetic bytes
- [x] 3.3 Red–green for the chain walk: a source whose first block fits but whose second link overruns; and one that reaches a declared end with trailing bytes
- [x] 3.4 Regression: the 16 MiB case where the first-block check is vacuous (MLEN ceiling 2²⁴) is caught by the walk and not by the gate — this is the walk's whole justification, so pin it
- [x] 3.5 Regression: link-cap exhaustion keeps the earlier verdict and does **not** reject
- [x] 3.6 Regression: the OLE/CFB and COFF fixtures from `brotli-probe-framing-gate` task 4.3 **still** pass both rules. This change does not close that family, and a test asserting otherwise would be wrong
- [x] 3.7 Non-seekable source of unknown length keeps today's behaviour for both rules
- [x] 3.8 Cost regression: completeness adds no I/O at all; the walk adds a bounded number of small reads and no decompression
- [x] 3.9 Re-run `scripts/exploration/probe_residual_census.py` and record the new residual in the investigation. Re-measured after the 64 KiB completeness drain: **29 fabricated / 150 623 (0.019%)**, down from 128 (0.193%) after the first-block gate alone (earlier post-gate census at the 256-byte drain was 30 / 147 601)
- [x] 3.10 `./scripts/test.sh --all-configs` (the Brotli extra is optional — the probe must stay skipped, not crash, when it is absent)
- [x] 3.11 `openspec validate --strict probe-completeness-gate`
- [x] 3.12 Update `dev-docs/open-issues.md` P12 and `dev-docs/threat-model.md` O10 with the new residual figure. The three-clause wording from #261 stands: the listing is wrong, a full read raises, and a prefix of fabricated bytes may already have been produced
- [x] 3.13 Archive this change (`openspec archive probe-completeness-gate --yes`) and commit the synced `openspec/specs/` delta

## 4. Follow-ups (explicitly not in this change)

- [ ] 4.1 Confidence and error provenance are `probe-provenance-unconfirmed`'s subject. **Land this change first and re-run the census before sizing it** — completeness removes 91 of the 128 fabrications, including 57 of the 64 that today carry no unconfirmed signal, which materially changes that change's argument
- [ ] 4.2 The OLE/CFB and COFF residual above the prefix survives both rules by construction. Still declined as a magic denylist (`brotli-probe-framing-gate` design.md); revisit only with extension-first ordering
