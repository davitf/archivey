## 1. Specify the guarantee

- [x] 1.1 Add the escaping requirement to `openspec/specs/cli/spec.md` (via the delta),
      covering the print path and the log path, so the behavior has a written test
- [x] 1.2 Sync the delta into the main `cli` spec

## 2. Escape the log path

- [x] 2.1 Add the escaping `logging.Formatter` to `cli/logging_config.py`
- [x] 2.2 Escape the record's *message* only, leaving an `exc_info` traceback alone —
      escaping a traceback's newlines would collapse it into one unreadable line
- [x] 2.3 Do not mutate the shared `LogRecord`: other handlers (pytest's `caplog`, an
      embedding app's) format the same record, and `cli_logging`'s docstring is explicit
      that library records must still reach them unchanged

## 3. Tests

- [x] 3.1 Red-green: a member name carrying `\x1b[2K` + `\r` reaches stderr escaped
      through the log path, not only the print path
- [x] 3.2 A record with `exc_info` keeps its traceback readable across lines
- [x] 3.3 The formatter does not alter the record other handlers see
- [x] 3.4 Ordinary messages are unchanged (no separator doubling, no cosmetic churn)

## 4. Review follow-ups (PR #236)

- [x] 4.1 The new SHALL overclaimed: `archivey test`'s `FAIL` detail, the extract abort
      notice, and `main()`'s three top-level handlers printed the exception unescaped.
      Reproduced a live spoof via `x --abort-on name-collision`. Maintainer decision:
      finish the sites rather than narrow the requirement
- [x] 4.2 Drop the false "a traceback is archivey's own text" rationale from the formatter
      docstring and the spec — the final line is the exception's message, which may be the
      archive's. Maintainer decision: record as an accepted residual (no call site passes
      `exc_info`), not escape it
- [x] 4.3 Record the two residuals O9 omitted: `exc_info` exception lines, and native
      Windows paths doubling their separators in log messages
- [x] 4.4 Disambiguate the PR #235 cross-reference rather than removing it

## 5. Close the gap register

- [x] 5.1 Flip threat-model O9 to implemented, naming the formatter and the tests

## 6. Verify

- [x] 6.1 Full suite in all three dependency configurations
- [x] 6.2 `openspec validate --all --strict`

## 7. Rework: escape at the message source, not the display site (PR #236)

Sections 1–6 landed a handler-side formatter. Review established that this guards the
weaker of the two paths — a formatter never sees an uncaught exception's traceback,
which is the likeliest way an archivey message reaches a terminal. Reworked in place
rather than landed and reverted, so the spec history never claims a guarantee we no
longer hold. The unchecked boxes below supersede the checked ones above.

- [x] 7.1 Move the escaping primitive to `archivey/escaping.py`, below both
      `exceptions` and `cli`; `escape_member_name` becomes a thin CLI-facing alias
- [x] 7.2 Escape `message` at construction in `ArchiveyError` and `ArchiveyUsageError`;
      keep `archive_name` / `member_name` / `source_format` raw
- [x] 7.3 Escape `Diagnostic.message` at construction; keep `context` raw as the
      structured channel. Closes the one library log site (`log.warning("%s", message)`)
      that interpolates neither an exception nor `%r`
- [x] 7.4 Delete `_EscapingFormatter` — it would double every backslash an exception
      already wrote — and document in `cli_logging` why the handler does not escape
- [x] 7.5 Add `format_error_detail`, deciding by type in one place, so call sites
      holding an `ArchiveyError | OSError` union do not each have to reason about it
- [x] 7.6 Convert the CLI's exception print sites: `main()`'s handlers, `archivey test`'s
      FAIL lines, the extract abort notice, the `failed:` / `blocked:` detail, the hoist.
      `CliError` is outside the archivey hierarchy and keeps its print-site escape
- [x] 7.7 Rewrite the spec: `error-handling` and `diagnostics` gain the
      escape-at-construction requirements, `cli` is rewritten to escape at the source
- [x] 7.8 Rewrite the tests: the log-path tests now assert the exception and the
      diagnostic escape themselves, and that the CLI handler leaves records alone
- [x] 7.9 Cover the route that motivated the rework — an uncaught `ArchiveyError`'s
      traceback reaching stderr inert, with no display site involved
- [x] 7.10 Record the two residuals this design has: double-escaping when a message
      interpolates `{name!r}` or `{exc}`, and Windows separators doubling in messages
- [x] 7.11 Re-verify: full suite in all three dependency configurations,
      `openspec validate --all --strict`, and the live spoof reproduction

## 8. Review follow-ups, round 2 (PR #236)

- [x] 8.1 `escape_control_chars`: astral code points emitted a 5-hex-digit `\uXXXXX`
      that reads back as a different character (955,086 code points affected). Rendering
      now delegates to `repr`, whose escape set is exactly `not str.isprintable()`
- [x] 8.2 Narrow the surrogateescape range to `U+DC80`–`U+DCFF`; `U+DC00`–`U+DFFF`
      reversed 768 code points into bytes they never came from
- [x] 8.3 The docstring's losslessness claim was false (`U+009B` vs surrogateescaped
      byte `0x9B` collide). Narrowed to inertness, which is what is actually guaranteed
- [x] 8.4 Audit the doubling residual rather than accepting it: 52 message sites
      interpolated an archive-derived name with `!r`. Added `quoted()` and converted
      them all
- [x] 8.5 Audit whether a message ever wraps an archivey exception: two broad
      `except Exception` sites in `rar_parser.py`. Added `raw_message` /
      `raw_message_of()` and converted them
- [x] 8.6 Confirm the inverse rule for `logger.*`: the 9 `%r` log sites are now
      load-bearing, since the CLI handler no longer escapes. Kept, and guarded by a test
- [x] 8.7 Static tests for both rules, so a future call site cannot reintroduce either
      failure silently
- [x] 8.8 Rewrite the `Diagnostic` docstring: lead with the field contract rather than
      the CLI, name what "every call site" means, and say what a future `{name}` would
      undo. Record why `__post_init__` rather than a hand-written `__init__`
- [x] 8.9 Spec the escape-exactly-once rule in `error-handling`, referenced from
      `diagnostics`
- [x] 8.10 Re-verify: full suite in all three dependency configurations,
      `openspec validate --all --strict`, and the live spoof reproduction

## 9. Message shape: names live in structured fields (PR #236)

Review asked whether the constructor could append the member name so messages never
carry it. An inventory of all 418 exception sites said: not as a general mechanism —
`__str__` already renders `member=`, and 9 two-name messages cannot use it. But 31 of
the 36 name-bearing messages were reducible, most of them printing the name twice.

- [x] 9.1 Add `link_target` to `ArchiveyError` (+ `DiagnosticRaisedError` passthrough),
      rendered by `__str__` as `target=`. Six two-name messages become prose
- [x] 9.2 Convert the single-name messages that already set `member_name=` — the name
      was being printed twice, once in the text and once by `__str__`
- [x] 9.3 Fix `base_reader.py`'s mislabeled link-target message: it named the target in
      the text while passing the *member* name as `member_name=`
- [x] 9.4 Convert the four `zip_reader` `EncryptionError` sites — `_stamp_error_context`
      sets `member_name` after construction, which a constructor-kwarg audit cannot see
- [x] 9.5 Revert `quoted()` on the ~8 values that are not archive-derived (operation
      names in `reader_state`, an enum value in `base_reader`) back to `!r`
- [x] 9.6 `quoted()` chooses its delimiter (`"` when the text holds `'` and no `"`)
      rather than escaping it, which would reintroduce the doubling
- [x] 9.7 Fix the guard's own blind spots found on the way: `\b` never matched inside
      `member_name` (underscore is a word character), and non-f-string re-wraps
      (`SomeError(exc.message)`) were invisible to it. Third static test added
- [x] 9.8 Render member-derived paths `/`-separated in messages (`display_path`), closing
      the Windows separator-doubling residual at its 4 sites
- [x] 9.9 Re-verify: full suite in all three dependency configurations,
      `openspec validate --all --strict`, live spoof reproduction

**Irreducible (4 sites), left interpolating by hand:**

- `rar_parser.py:606` — two RAR *volume* filenames (`{new} does not continue {old}`);
  neither is a member, and `archive_name` holds only one
- `extraction.py:652` `NameRewrittenError` — `{transformed} -> {portable}`; the rewritten
  spelling has no structured field (`ExtractionResult.presented_name` is the result-side
  equivalent)
- `base_reader.py:1625`, `base_reader.py:1679` — `ArchiveyUsageError`, whose root has no
  structured attributes at all, unlike `ArchiveyError`

## 10. CI failures (PR #236)

- [x] 10.1 `display_path(path: object)` was too loose for `os.fspath`; typed as
      `str | os.PathLike[str]`. Caught by `pyrefly`, which I had not run locally —
      `ruff` alone does not cover it
- [x] 10.2 The three static tests read source with `Path.read_text()`, which uses the
      locale encoding: `UnicodeDecodeError` under cp1252 on Windows. Pinned to UTF-8
- [x] 10.3 `test_abort_notice_escapes_the_error_message` used the ANSI spoof without the
      repo's `_ANSI_ONLY` guard. Control bytes are illegal in NTFS names, so the member
      is never written and no collision occurs. Split: the portable U+2028 spoof covers
      the abort print site everywhere, and the ANSI variant is Unix-only, matching the
      existing pairing for the overwrite reports
- [x] 10.4 The `_NON_ARCHIVE_MODULES` exemption was keyed on a POSIX-spelled path but
      compared against `str(path)`, which uses backslashes on Windows — so the exemption
      matched nothing there and the check fired on `reader_state`. Compare via
      `as_posix()`, and assert each exemption names a real file, since one that matches
      nothing looks like coverage while providing none
