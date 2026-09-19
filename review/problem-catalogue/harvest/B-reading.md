# Harvest B — Reading, member lifetime, concurrency

Bounded drop from Worker B verification (Topic 8 pass 1). Neutral phrasing; no
investigation beyond what the claim rows already touched.

---

### 1. Foreign-member exception type disagrees across specs

- **Problem:** `archive-reading` still names `ValueError` for `open`/`read` of an
  `ArchiveMember` from another reader; `error-handling` lists the same case under
  `ArchiveyUsageError`, and the library raises `ArchiveyUsageError`.
- **Symptom:** A reader who trusts only `archive-reading` expects a bare
  `ValueError`; published guide + runtime raise `ArchiveyUsageError` (outside
  `ArchiveyError`).
- **Evidence:** `openspec/specs/archive-reading/spec.md` Reading member data
  (“foreign `ArchiveMember` → `ValueError`” / matrix row); `openspec/specs/error-handling/spec.md`
  Usage errors bullet “using an `ArchiveMember` from another reader”; B-26
  spot-check message “does not belong to this reader”.
- **Today:** Runtime and guide match `error-handling`.

### 2. One-live-stream / ConcurrentAccessError restated on five pages

- **Problem:** The default single live member stream and the
  `ConcurrentAccessError` opt-in are explained repeatedly.
- **Symptom:** Trim risk — five homes including a migration “things that will bite
  you” bullet that `scope.md` did not count.
- **Evidence:** `docs/reading-members.md:26-28`, `docs/access-and-cost.md:127-128`,
  `docs/support-matrix.md:112-115`, `docs/philosophy.md:39`,
  `docs/migrating.md:164-166`; B-6.

### 3. `stream_members()` link asymmetry absent from Gotchas

- **Problem:** `open()`/`read()` follow links; `stream_members()` yields
  `(link, None)`. That asymmetry is load-bearing and only spelled on Reading
  members (twice).
- **Symptom:** A caller who only reads Gotchas can write a `stream is None`
  skip loop and silently drop link targets they expected to follow.
- **Evidence:** `docs/reading-members.md:86-93`, `:130-133`; B-19/B-20; Gotchas
  has no corresponding trap line.

### 4. Integrity “never short” vs sized-`read(n)` short-on-truncation

- **Problem:** Reading members (and index) say a full read raises rather than
  handing over short/wrong data; the same page then documents that
  `read(member.size)` on truncation returns a short buffer with no exception.
- **Symptom:** Two adjacent contracts look like one absolute rule.
- **Evidence:** `docs/reading-members.md:101-110`, `docs/index.md:25-26`;
  `compressed-streams` size-declared truncation matrix; B-23.

### 5. Migrating “non-regular → typed error” is broader than directories

- **Problem:** Migrating contrasts `extractfile` → `None` for non-regular members
  with archivey raising a typed error.
- **Symptom:** Symlinks/hardlinks are non-regular in tar terms but archivey
  *follows* them on `read()`/`open()`; only directories/other non-payload types raise.
- **Evidence:** `docs/migrating.md:84-85`; B-45 spot-check (`etc/` →
  `ArchiveyUsageError`; `link-to-readme` → target bytes); Transparent link
  following in `archive-reading`.

### 6. Header-encrypt cite on Formats points at BLAKE2sp

- **Problem:** Cluster Stated-at for B-18 includes `formats.md:117`, which is the
  BLAKE2sp / HASHMAC paragraph, not the header-password-at-open rule.
- **Symptom:** Weak cross-link if someone navigates by that line alone; the real
  statement lives on Reading members (+ 7z “zero file records” / RAR
  `[recommended]` header-encrypt notes).
- **Evidence:** `docs/formats.md:105-106`, `:116-117`; `docs/reading-members.md:79-84`;
  B-18.

### 7. Support-matrix concurrency block duplicates Access costs

- **Problem:** Fail-fast one-stream demo, “cheap path” rationale, and concurrent
  fan-out setup are also on Access costs / Reading members.
- **Symptom:** Trim target already flagged (`Trim to ~4 + links`); unique residue
  is free-threading extras + close-blocks caveat.
- **Evidence:** `docs/support-matrix.md:110-139` vs `docs/access-and-cost.md:125-137`;
  B-42/B-43 rulings.

### 8. Streaming drain helper only unique on Access costs

- **Problem:** `scan_members()` after partial streaming pass is the one unique
  claim in a block otherwise repeated with Reading members / Gotchas.
- **Symptom:** Easy to trim the wrong sentence if the block is shortened without
  keeping the drain tip.
- **Evidence:** `docs/access-and-cost.md:148-152`; B-31/B-32; verified drain
  after early `break`.

### 9. Tar before/after listing shapes differ for directories

- **Problem:** Migrating before/after prints `name, size` for every member; stdlib
  and archivey disagree on directory name slash and size (`0` vs `None`).
- **Symptom:** Copy-paste comparison of printed lines looks like a behaviour
  change beyond the intended `read` / `extract_all` equivalence.
- **Evidence:** B-48 run: tarfile `('etc', 0)` vs archivey `('etc/', None)`;
  file member `etc/config` bytes match.

---
