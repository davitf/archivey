# Harvest D — Errors, diagnostics, translation

Bounded drop from Worker D verification (Topic 8 pass 1). Neutral phrasing; no
investigation beyond what the claim rows already touched.

---

### 1. FilterRejectionError subtypes missing from the guide table

- **Problem:** The errors-page subtype table lists three `FilterRejectionError`
  children and omits `UnportableNameError` and `DeceptiveNameError`, which the
  hierarchy requires.
- **Symptom:** Callers matching only the published names miss portable-name and
  bidi-deceptive blocks; the table is the only published tree reference for most
  types.
- **Evidence:** `error-handling` Single rooted hierarchy; `docs/errors-and-diagnostics.md:29`;
  D-8.
- **Today:** Runtime and spec include all five subtypes.

### 2. Exception inventory vs published surface (scope Q3 evidence)

- **Problem:** `exceptions.py` defines 27 exception classes; the guide table names
  a minority; `api.md` renders five (`ArchiveyError`, `ArchiveyUsageError`,
  `ConcurrentAccessError`, `DiagnosticRaisedError`, `ResourceLimitError`).
- **Symptom:** Completeness of the published tree is invisible without reading
  the hierarchy requirement — exactly the Q3 inventory question.
- **Evidence:** D-8 cluster note; `docs/api.md` names; hierarchy in
  `openspec/specs/error-handling/spec.md`.
- **Today:** No decision recorded here.

### 3. `VerificationMode.STRICT` documented but not shipped

- **Problem:** Errors page presents `VerificationMode.STRICT` as the way to verify
  a whole member before any bytes are returned.
- **Symptom:** Import fails; no symbol in `src/` or current capability specs. ADR
  0014 points at an open `verification-integrity-mode` proposal.
- **Evidence:** `docs/errors-and-diagnostics.md:177-179`; D-48 wrong; ADR 0014.
- **Today:** Streaming verify-at-EOF contract is real; STRICT mode is not.

### 4. `detect_format()` “refuses zeros” advice is path-sensitive

- **Problem:** Gotchas says use `detect_format()`, which refuses zero-filled
  bytes, as a check when “0 members” would be wrong.
- **Symptom:** `detect_format("z.tar")` returns TAR via extension (`GUESS`);
  refusal requires nameless bytes or a non-archive extension. The classic empty
  `z.tar` case is not refused by path-based detect.
- **Evidence:** format-detection unconfirmed-format matrix (“same bytes with no
  name”); D-19 spot-check; `docs/gotchas.md:100-103`.
- **Today:** Content detection refuses zeros; extension fallback still guesses TAR.

### 5. Silence: exception translation and passthrough rules

- **Problem:** Specs + CONTRIBUTING define known third-party → `ArchiveyError`,
  unrecognized propagate, `OSError` / `KeyboardInterrupt` / `MemoryError`
  passthrough, `ArchiveyUsageError` outside the tree — but no errors-page
  section states the full rule.
- **Symptom:** `extracting.md` hardening bullet is a one-liner; callers lack a
  canonical home.
- **Evidence:** `error-handling` Genuine runtime and I/O errors; Original cause
  preserved; CONTRIBUTING exception translation; D-51.
- **Today:** Unwritten inbound to `errors-and-diagnostics.md`.

### 6. Silence: inert messages / escape-at-construction

- **Problem:** `ArchiveyError` / `ArchiveyUsageError` / `Diagnostic` escape at
  construction so printing cannot move the cursor or forge terminal output.
- **Symptom:** No guide page states the terminal-safety contract (`#236`
  survivor).
- **Evidence:** `error-handling` Exception messages inert; `diagnostics`
  Diagnostic messages inert; D-52.
- **Today:** Unwritten inbound (~3 lines).

### 7. Silence: escape exactly once (library ↔ CLI)

- **Problem:** Escaping must happen once; CLI must not re-escape library
  messages (double backslashes).
- **Symptom:** Part of D-52 inbound; easy to violate in new print/log sites.
- **Evidence:** `error-handling` Archive-derived text escaped exactly once;
  `cli` Archive-derived text escaped before terminal display; D-53.
- **Today:** Unwritten in the guide.

### 8. Chunked-loop recipe duplicated on Reading members

- **Problem:** Byte-identical chunked `ReadError` loop appears on Errors
  (canonical) and Reading members.
- **Symptom:** Trim already ruled `→ page` for the reading-members copy; drift
  risk if one side is edited alone.
- **Evidence:** `docs/errors-and-diagnostics.md:167-175`;
  `docs/reading-members.md:114-122`; D-46.
- **Today:** Both present and executable.

### 9. Spec drift: closed-reader error type

- **Problem:** `UnsupportedOperationError` meaning table lists “operation on
  closed reader”; the Caller-misuse requirement lists post-`close()` operations
  under `ArchiveyUsageError`.
- **Symptom:** Two authoritative rows disagree on the same case.
- **Evidence:** `error-handling` hierarchy meaning table vs Caller misuse
  remains outside ArchiveyError; guide follows the misuse requirement (D-11).
- **Today:** Guide + misuse requirement align; meaning-table row is the outlier.

### 10. Integrity matrix is dense for the guide page

- **Problem:** The 14-cell call × failure matrix is correct per
  `compressed-streams` but is the densest block on the errors page.
- **Symptom:** Trim pressure already noted (`Keep`); survivors are
  `read(member.size)` asymmetry and “short ≠ complete.”
- **Evidence:** D-44/D-45/D-49; `docs/errors-and-diagnostics.md:183-198`.
- **Today:** Spec and codec tests agree with the matrix.
