## 0. Relationship to the unarchived framing gate

- [ ] 0.1 **`brotli-probe-framing-gate` is implemented but deliberately not archived** (its D7 → A, `bee7735` / #261). Its requirements therefore live in `openspec/changes/brotli-probe-framing-gate/specs/`, not in `openspec/specs/`. This change **ADDs** two requirements rather than MODIFYing that change's *A content probe SHALL NOT accept framing the source cannot hold*, so the deltas do not depend on it being archived and the two may archive in either order
- [ ] 0.2 **On archiving whichever goes second**, reconcile one sentence: the framing requirement says a probe "**MAY** follow the chain of byte-aligned self-describing meta-blocks", which this change's chain-walk requirement supersedes with a stronger obligation. Keep this change's version; delete the permissive clause rather than leaving both
- [ ] 0.3 This change **closes** `brotli-probe-framing-gate` task **5.7** (chain walk, deferred from its 2.3). Check that box when this lands rather than leaving two records of the same work
- [ ] 0.4 The completeness rule is **new** — it is not one of that change's follow-ups. It came out of `scripts/exploration/probe_residual_census.py` while sizing 5.8, and it is 71% of the remaining residual against the chain walk's much smaller share

## 1. Completeness when the source is fully visible

- [ ] 1.1 Add the rule where the probe's decode outcome is interpreted, not inside any one codec: when `source_length is not None and source_length <= len(prefix)`, a decode ending in "needs more input" is a **rejection**
- [ ] 1.2 Apply it to **every** decoding probe — Brotli, zlib, LZMA Alone. Two of the four measured Alone fabrications are under the prefix; scoping it to Brotli would leave a general truth living in one codec
- [ ] 1.3 Do **not** express it as a size threshold. `brotli.compress(b"hello")` is 9 bytes, decodes to completion, and must be accepted. The test is how the decode *ended*
- [ ] 1.4 Skip the rule when `source_byte_size` returned `None`, exactly as the framing gate does
- [ ] 1.5 Confirm no interface change is needed: the probe already receives both the prefix and `source_length` after #261

## 2. The chain walk

- [ ] 2.1 **Settle who owns the reads first** (design.md open question). Recommendation is B — an optional bounded `read_at`-style callback, absent by default — but A (detector-owned) and C (larger peek on request) are written up. Do not start 2.2 before this is decided; it determines where the code lives
- [ ] 2.2 Follow the chain of byte-aligned self-describing meta-blocks, stopping at the first compressed block; reject a link that overruns the source or a declared end with trailing bytes. Reference implementation: `chain_walk` in `scripts/exploration/brotli_probe_field_survey.py`
- [ ] 2.3 Bound the walk in link count. **Hitting the bound means "cannot disprove"** — keep the earlier verdict, do not reject. The survey script used 64; that is a starting point, not a measured optimum
- [ ] 2.4 Keep the walk free of decompression, and bounded in bytes read as well as in links
- [ ] 2.5 Confirm the walk terminates immediately on a compressed first meta-block (79 of 150 corpus streams), having read four bytes

## 3. Verify

- [ ] 3.1 **Zero false negatives is the binding constraint**, as it was for the framing gate. Reuse the real-stream corpus (qualities 0/1/5/9/11 × `lgwin` 10/22/24 × payloads from empty to 1 MiB) and **extend it downward**: `brotli.compress(b"")`, `b"hello"`, and a handful of payloads under 100 bytes, all of which fit inside the prefix and must survive completeness
- [ ] 3.2 Red–green for completeness: a 5-byte text file whose first meta-block parses as compressed is accepted today and must be rejected after. The census found 67 such files under 16 bytes on one tree — take fixtures from real families, not only synthetic bytes
- [ ] 3.3 Red–green for the chain walk: a source whose first block fits but whose second link overruns; and one that reaches a declared end with trailing bytes
- [ ] 3.4 Regression: the 16 MiB case where the first-block check is vacuous (MLEN ceiling 2²⁴) is caught by the walk and not by the gate — this is the walk's whole justification, so pin it
- [ ] 3.5 Regression: link-cap exhaustion keeps the earlier verdict and does **not** reject
- [ ] 3.6 Regression: the OLE/CFB and COFF fixtures from `brotli-probe-framing-gate` task 4.3 **still** pass both rules. This change does not close that family, and a test asserting otherwise would be wrong
- [ ] 3.7 Non-seekable source of unknown length keeps today's behaviour for both rules
- [ ] 3.8 Cost regression: completeness adds no I/O at all; the walk adds a bounded number of small reads and no decompression
- [ ] 3.9 Re-run `scripts/exploration/probe_residual_census.py` and record the new residual in the investigation. Expected from this image: 128 fabrications → ~9 unstamped survivors after completeness alone
- [ ] 3.10 `./scripts/test.sh --all-configs` (the Brotli extra is optional — the probe must stay skipped, not crash, when it is absent)
- [ ] 3.11 `openspec validate --strict probe-completeness-gate`
- [ ] 3.12 Update `dev-docs/open-issues.md` P12 and `dev-docs/threat-model.md` O10 with the new residual figure. The three-clause wording from #261 stands: the listing is wrong, a full read raises, and a prefix of fabricated bytes may already have been produced

## 4. Follow-ups (explicitly not in this change)

- [ ] 4.1 Confidence and error provenance are `probe-provenance-unconfirmed`'s subject. **Land this change first and re-run the census before sizing it** — completeness removes 91 of the 128 fabrications, including 57 of the 64 that today carry no unconfirmed signal, which materially changes that change's argument
- [ ] 4.2 The OLE/CFB and COFF residual above the prefix survives both rules by construction. Still declined as a magic denylist (`brotli-probe-framing-gate` design.md); revisit only with extension-first ordering
