# Harvest H — Command line

Bounded drop from Worker H verification (Topic 8 pass 1). Neutral phrasing; no
investigation beyond what the claim rows already touched.

---

### 1. CLI↔library extract defaults diverge, but only inside a bash comment

- **Problem:** The three CLI extract defaults are stated as a `#` comment in the
  Safer extract demo; the fact that two of them diverge from the library is never
  written as guide prose.
- **Symptom:** A script ported from `archivey.extract(...)` to the CLI (or the
  reverse) silently changes overwrite and on-error behaviour.
- **Evidence:** `docs/cli.md:18-22`; library `extract()` → `OverwritePolicy.ERROR` /
  `OnError.STOP` (`src/archivey/core.py`); CLI → `rename` / continue
  (`src/archivey/cli/main.py`); H-5/H-6.
- **Today:** `policy=strict` matches both sides; `overwrite` and `on_error` do not.

### 2. Passwords on argv / `ps` still absent from guide pages

- **Problem:** Scope §10 / outline item — passwords passed as `--password` are
  visible in process lists — is unwritten on every `docs/` page.
- **Symptom:** Operators who never read `--help` have no guide warning.
- **Evidence:** H-16; no `docs/` hit for argv/`ps` visibility; CLI `--help` already
  says “prefer a TTY prompt; visible in process lists”; `cli/password.py` returns
  the argv string when supplied.
- **Today:** Help text covers it; published guide does not.

### 3. CLI page never mentions terminal escaping (`#236`)

- **Problem:** After escaping moved to message construction / print sites, the
  48-line CLI page still says nothing about archive-derived names being
  terminal-inert.
- **Symptom:** Thinnest page against the largest recent CLI-output change; readers
  do not learn why hostile names print as `\x1b` forms.
- **Evidence:** H-17; `cli` “Archive-derived text is escaped…”; `error-handling`
  construction escape; repro listing of `ev\x1b[2Kil\rSUCCESS.txt` with no raw
  ESC/CR; D-52 is the errors-page twin.

### 4. Demo claim count vs actual invocations

- **Problem:** Claim H-12 says “six bash invocations” in `cli.md:17-37`; the block
  has five runnable lines.
- **Symptom:** Pure count slip in the claim table (behaviour of the five lines is
  correct).
- **Evidence:** H-12 `[code]` run; `docs/cli.md:17-37`.

### 5. Spec scenario `./x` vs known-verb-wins implementation

- **Problem:** CLI behaviour matrix lists `archivey ./x` as dispatching `extract`
  (known-verb-wins); the injector only treats bare verb tokens as verbs, so
  `./x` is a path and defaults to `list`.
- **Symptom:** Spec scenario and code disagree; guide H-13 (“use `archivey list
  ./x`”) matches code.
- **Evidence:** `openspec/specs/cli/spec.md` scenario row for `./x`;
  `_inject_default_list` in `src/archivey/cli/main.py`; H-13 spot-check.

### 6. Settles-it cite for argv passwords points at unrar, not the CLI flag

- **Problem:** Cluster H-16 Settles-it names `format-rar` “Constrain unrar argv…”,
  which is member-path argv hygiene for the decompressor (E-38 territory), not
  `--password` visibility on the `archivey` process.
- **Symptom:** Easy to conflate two different argv surfaces when writing the
  ~2-line guide note.
- **Evidence:** H-16; E-38; `format-rar` unrar argv matrix vs `cli` `--password`
  help / `password.py`.
