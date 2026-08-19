# Harvest C — Extraction, policies, results

Bounded drop from Worker C verification (Topic 8 pass 1). Neutral phrasing; no
investigation beyond what the claim rows already touched.

---

### 1. Policy table omits `STANDARD` (S-2)

- **Problem:** The extracting.md policy table lists only `STRICT` and `TRUSTED`
  while `ExtractionPolicy` has three members and the same page uses `STANDARD`
  in prose four times.
- **Symptom:** Callers scanning the table never learn the middle trust level
  exists; the missing row is easy to miss because `STANDARD` also has no
  rendered `api.md` docstring (comment-only on the enum member).
- **Evidence:** `docs/extracting.md` policy table (two rows) vs prose at bidi /
  cross-platform / need-to-know rows; `ExtractionPolicy.STRICT|STANDARD|TRUSTED`;
  C-42.
- **Today:** Runtime and `safe-extraction` Policy-Specific Metadata Transforms
  define three policies.

### 2. `STANDARD` sticky bit: spec vs code/guide

- **Problem:** `safe-extraction` metadata matrix says `STANDARD` strips
  setuid/setgid only (sticky preserved). Implementation
  `transform_standard` clears all high bits (`setuid|setgid|sticky`); guide
  says sticky stripped except under `TRUSTED`.
- **Symptom:** A conformance reader of the matrix expects sticky dirs under
  `STANDARD`; disk mode is sticky-free.
- **Evidence:** `openspec/specs/safe-extraction/spec.md` metadata policy table
  row “setuid/setgid/sticky”; `filters.py` `transform_standard` (`& ~_HIGH_BITS`);
  C-25 spot-check `0o1755` dir → `0o755` under `STANDARD`.
- **Today:** Guide claim matches code (B-26: verify guide, harvest drift).

### 3. Silence: `extract_all(config=)` cannot raise the listing ceiling

- **Problem:** No published page states that listing limits are fixed at
  `open_archive` time and a later `extract_all(config=…)` cannot raise them.
- **Symptom:** Callers may pass a higher `ListingLimits` on extract and still
  hit the open-time ceiling during extract-prep materialization.
- **Evidence:** C-66 spot-check (`max_members=5` at open;
  `extract_all(config=…max_members=1000)` still `ResourceLimitError`);
  `archive-reading` Listing resource limits / config lifetime.
- **Today:** Fact true; guide silence (must-explain #8 / §B row-4 survivor).

### 4. Seven extracting.md trust-boundary clauses deferred to TM

- **Problem:** C-5, C-6, C-8, C-9, C-11, C-26, C-27 are marked `[TM]` and were
  not verified in this pass.
- **Symptom:** If the problem catalogue inherits them as “checked”, the
  threat-model edit will skip the only verification gate they have.
- **Evidence:** `cluster-C.md` TM list; Worker C left them `left for TM`.
- **Today:** Caller-visible halves already covered elsewhere (C-10, C-35/37/39,
  etc.).

### 5. NTFS junctions claim is platform-gated

- **Problem:** Guide pairs “special files always rejected” with “NTFS junctions
  detected, flagged, never traversed” in one bullet.
- **Symptom:** Unverifiable on Linux CI/dev sessions; Settles-it pointed at
  universal path-safety, which does not name junctions.
- **Evidence:** C-17; `archive-data-model` `is_junction`; Windows-only
  `tests/test_directory.py` junction test.
- **Today:** Surfacing rule is data-model; traversal rule needs Windows.

### 6. Hardlink identity Settles-it cite is the weaker home

- **Problem:** C-49’s Settles-it pointed at Hardlink Two-Pass; the
  positional-vs-`get` last-wins contrast is stated under `archive-reading`
  Transparent link following.
- **Symptom:** A reader following only the extraction hardlink requirement
  misses “most recent matching target strictly before the link.”
- **Evidence:** C-49 spot-check (hardlink got `FIRST`, `get` returned
  `SECOND`); `archive-reading` hardlink positional rule.

### 7. `zipfile.extractall` mangles traversal into a successful write

- **Problem:** Migrating’s strongest safety contrast is load-bearing: stdlib
  can turn `../evil.txt` into a dest-local write.
- **Symptom:** Migrations that only compare “did a file appear?” miss the
  `BLOCKED` report.
- **Evidence:** C-70: zipfile wrote `evil.txt` inside dest; archivey
  `BLOCKED`/`PathTraversalError`, empty dest.

### 8. Exact same-name duplicates never exercise `RENAME`’s `(N)` suffix

- **Problem:** Need-to-know cites `photo (1).jpg` for intentional duplicates;
  exact same-name pairs mark the earlier row `SUPERSEDED` so only one write
  occurs and `RENAME` does not fire.
- **Symptom:** Copy-paste demo with two `photo.jpg` members will not produce
  `photo (1).jpg`; casefold/NFC pairs (or this-run collisions) will.
- **Evidence:** C-50/`SUPERSEDED`; C-55 casefold → `readme (1)`; exact
  `photo.jpg`×2 → one `EXTRACTED`, one `SUPERSEDED`.

### 9. Always-stop bomb errors are a private `ResourceLimitError` subclass

- **Problem:** Runtime raises `_AlwaysStopResourceLimitError` for global
  guards; docs/specs say `ResourceLimitError`.
- **Symptom:** `except ResourceLimitError` still works (subclass); isinstance
  demos that print the type look “wrong” next to the guide.
- **Evidence:** C-24 / C-63 spot-checks; public catch type remains
  `ResourceLimitError`.

### 10. Abort-on depth lives in `#` comments and the spec table

- **Problem:** Guide `abort_on` table is correct; deeper “why three events /
  why NAME_SANITIZED is a hatch” already exist as enum comments and
  `safe-extraction` Abort-on-event prose.
- **Symptom:** Trim risk if DS work duplicates rather than links the existing
  comment/spec depth (C-34/C-39/C-40 rulings).
- **Evidence:** `AbortOn` enum comments; abort-on requirement; C-32–C-41
  verified.
