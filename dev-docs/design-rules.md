# Design rules

These are the rules the maintainer has applied, again and again, when settling a design
question. They exist so that most questions get settled by a rule instead of by a new
question to the maintainer. Each rule says what it is and the rulings it was derived
from. Most also say why it holds and what would reopen it; a rule without a "Reopen if"
has no known reason to reopen yet, so bring new arguments if you want to.

Use this page before you ask. If a rule here settles the question, do what it says and
name the rule in the pull request ("settled by DR-7"), so the maintainer can still object.
If the question is a
[clash between consistency and the official tool](#when-consistency-and-the-official-tool-disagree)
that the factors do not settle, or is on the
[escalation list](#what-still-goes-to-the-maintainer), ask.

Where these rules come from: maintainer rulings in pull requests, ADRs, handbook pages
and the project's decision threads, collected in October 2026. VISION.md stays the
product tie-breaker; these rules are how its priorities have been applied in practice.
Format-specific rulings stay on their handbook pages (`dev-docs/formats/`); this page
holds only what has generalised across formats.

To test a question against concrete callers, use [`scenarios.md`](scenarios.md): who we
imagine using archivey and what would hurt each of them.

## Index

One line per rule, to find the one that applies. The rule text below is what
binds.

- [DR-0](#dr-0-fix-the-class-not-the-instance). **Fix the class, not the instance.** A bug
  in one format is a question about every format and shared path.
- [DR-1](#dr-1-a-wrong-answer-is-the-worst-failure). **A wrong answer is the worst
  failure.** Reported absence beats a refusal, a refusal beats silent loss, and a wrong
  value is worst.
- [DR-2](#dr-2-deliver-everything-recoverable-then-raise). **Deliver everything
  recoverable, then raise.** On damage, deliver every intact member, then raise at the
  point of damage.
- [DR-3](#dr-3-hidden-bytes-are-errors-inside-a-member-warnings-outside-it). **Hidden
  bytes are errors inside a member, warnings outside it.** Leftover bytes inside a
  member's data are corruption; outside it, a warning.
- [DR-4](#dr-4-unsupported-is-not-corrupt). **Unsupported is not corrupt.** A valid
  feature archivey cannot decode is `UnsupportedFeatureError`, not corruption.
- [DR-4a](#dr-4a-the-integrity-guarantee-is-for-a-start-to-end-read). **The integrity
  guarantee is for a start-to-end read.** Integrity checks are promised for a start-to-end
  read without seeks.
- [DR-5](#dr-5-same-input-same-outcome). **Same input, same outcome.** One archive gives
  one result across formats, access modes, sources and OSes.
- [DR-5a](#dr-5a-malformed-corner-cases-may-differ-a-little). **Malformed corner cases may
  differ a little.** A malformed, crafted-only shape may keep per-format behaviour when
  matching costs code.
- [DR-6](#dr-6-when-the-semantics-are-open-match-the-formats-official-tool). **When the
  semantics are open, match the format's official tool.** For an open format-specific
  question, test the reference tool and match it.
- [DR-7](#dr-7-trust-self-validating-data-over-caller-hints). **Trust self-validating data
  over caller hints.** Use what the archive can verify; a caller hint fills only the gap.
- [DR-8](#dr-8-defaults-are-complete-and-consistent-laziness-is-an-explicit-opt-in).
  **Defaults are complete and consistent; laziness is an explicit opt-in.** Defaults give
  full, consistent information; a cheaper partial behaviour is an opt-in.
- [DR-9](#dr-9-every-cost-is-bounded-by-a-public-raisable-limit). **Every cost is bounded
  by a public, raisable limit.** Every resource bound is a public, raisable limit, checked
  before the cost is paid.
- [DR-9a](#dr-9a-the-upload-server-test). **The upload-server test.** No archive may cause
  unbounded memory or disk, an unbounded hang, or a crash.
- [DR-10](#dr-10-costly-or-risky-capability-is-declared-and-a-declaration-is-a-guarantee).
  **Costly or risky capability is declared, and a declaration is a guarantee.** A costly
  or risky capability is off until asked for, and then guaranteed.
- [DR-10a](#dr-10a-read-sequentially-seek-only-when-the-format-needs-it). **Read
  sequentially; seek only when the format needs it.** Read forward in few large reads;
  seek only where the format needs it.
- [DR-11](#dr-11-environment-trouble-falls-back-explicit-requests-fail-loudly).
  **Environment trouble falls back; explicit requests fail loudly.** An `AUTO` choice
  falls back on environment trouble; an explicit request fails loudly.
- [DR-12](#dr-12-before-the-first-release-remove-rather-than-keep). **Before the first
  release, remove rather than keep.** Until 0.2.0 ships, a breaking cleanup goes in now.
- [DR-13](#dr-13-a-public-name-earns-its-place). **A public name earns its place.** A
  public name or exception type exists only if a caller would use it differently.
- [DR-14](#dr-14-one-obvious-way-and-one-mechanism-per-job). **One obvious way, and one
  mechanism per job.** No convenience wrapper for a short chain of public calls; one
  mechanism per job.
- [DR-14a](#dr-14a-easy-to-explain-easy-to-use-right-the-first-time). **Easy to explain,
  easy to use right the first time.** Between designs that meet principles 1 and 2, pick
  the easier to explain and use.
- [DR-15](#dr-15-usage-errors-are-for-what-the-types-cannot-rule-out). **Usage errors are
  for what the types cannot rule out.** A wrong argument raises `TypeError` or
  `ValueError`; `ArchiveyUsageError` is for misuse the signature cannot express.
- [DR-15a](#dr-15a-translate-archive-problems-let-io-problems-through). **Translate
  archive problems; let I/O problems through.** Archive-content errors become archivey
  errors; I/O failures pass through.
- [DR-15b](#dr-15b-fail-at-open-but-do-no-expensive-work-there). **Fail at open, but do no
  expensive work there.** Raise a cheap-to-detect problem at open, but do no work
  proportional to the archive there.
- [DR-16](#dr-16-a-feature-must-deliver-its-promise). **A feature must deliver its
  promise.** An advertised capability works in every format that claims it, or the format
  says it does not.
- [DR-17](#dr-17-extracting-beats-refusing-refuse-only-what-is-unsafe). **Extracting beats
  refusing; refuse only what is unsafe.** Refuse a member only when writing it is unsafe
  or the outcome differs by OS.
- [DR-18](#dr-18-extraction-never-damages-what-it-did-not-create). **Extraction never
  damages what it did not create.** Extraction never changes or removes what it did not
  create.
- [DR-19](#dr-19-prefer-one-structural-invariant-over-many-string-checks). **Prefer one
  structural invariant over many string checks.** Replace several ad-hoc checks for one
  hazard with one structural invariant.
- [DR-20](#dr-20-parse-in-python-isolate-native-code-that-can-crash). **Parse in Python;
  isolate native code that can crash.** Parsers are pure Python; a native codec that can
  crash runs in a child process.
- [DR-21](#dr-21-defer-a-fix-that-a-planned-rewrite-absorbs-and-document-it). **Defer a
  fix that a planned rewrite absorbs, and document it.** A fix that a planned rewrite
  absorbs waits for it, documented meanwhile.
- [DR-21a](#dr-21a-dont-ship-what-cant-be-tested). **Don't ship what can't be tested.**
  Refuse and document a case no tool can produce and archivey cannot test.
- [DR-22](#dr-22-docs-say-what-is-true-now-with-reasons). **Docs say what is true now,
  with reasons.** Docs say what is true now, with reasons.
- [DR-22a](#dr-22a-a-caveat-in-the-docs-is-a-question-for-the-code). **A caveat in the
  docs is a question for the code.** Before writing a caveat, ask whether the code can
  change instead.
- [DR-23](#dr-23-zero-debt-in-the-code-you-touch). **Zero debt in the code you touch.**
  Leave no debt in the code you touch; existing code is no justification.
- [DR-23a](#dr-23a-share-what-must-agree-copy-what-agrees-by-coincidence). **Share what
  must agree; copy what agrees by coincidence.** If one copy changes, must the other? If
  yes, share it.
- [DR-24](#dr-24-tests-pin-behaviour-against-real-producers). **Tests pin behaviour
  against real producers.** A bug fix starts red; tests check behaviour against fixtures
  from real tools.

## The principles

The maintainer's own summary of what he focuses on consistently (2026-10-09, in his
words). He then asked for the two he weighs most to go first, and proposed principle 4 as
a further test ("useful or redundant?"), which this page adopts. The split into "firm"
and "strong defaults", and the order within the strong defaults, are this page's reading,
not his ranking. Every numbered rule below is one of these applied to a kind of
question.

**Firm.** No other principle outranks these, and "only crafted archives hit it" is no
excuse.

1. **Never output incorrect or incomplete data without raising on a straightforward full
   read** (DR-1, DR-2, DR-4a). The one exception is data that is unknowable: the format
   carries no checksum or check value and the stream does not validate itself, so
   nothing can tell good bytes from bad. Then archivey says so with a diagnostic
   (`DIGEST_UNVERIFIABLE`) instead of implying the bytes were checked (maintainer,
   2026-10-09).
2. **Unbounded memory or crashes are never acceptable.** An attacker crafts archives at
   will and will choose the loopholes. The test: if a server lets anyone upload an
   archive to be tested, scanned or have its members hashed, could an upload bring the
   server down? (DR-9a).

**Strong defaults.** Whether these win depends on the case.

3. **No surprises or gotchas for users** (DR-8, DR-10).
4. **Easy to use and to explain, for people and for agents.** Archivey aims to be the
   default library for archives in human- and agent-written code. Using it should take
   little work, few tokens and no detours during development. The test is "don't make me
   think": which option is easiest to explain and to use correctly the first time?
   (DR-14a).
5. **Consistent behaviour between formats and libraries** (DR-5). This can be relaxed a
   little for malformed data, particularly for a corner case so specific that only
   crafted archives hit it (DR-5a).
6. **Follow the official tool's behaviour** (DR-6). When this and principle 5 conflict,
   that needs a real decision. The factors below settle the clear cases; the rest go to
   the maintainer.
7. **Generalise fixes and approaches across all formats** (DR-0).

The numbered rules below work the same way as the strong defaults: a rule settles a
question when the case looks like the rulings it came from, and a case that differs in
an important way goes back to the maintainer.

## Generalise

### DR-0. Fix the class, not the instance

**Rule.** A bug found in one format is a question about every format and every shared
path. Before fixing it, check whether the other formats have the same problem, and put
the fix in the shared path when there is one. A ruling on one case is a prompt to sweep
for the rest of its class.

**Why.** Every time the maintainer asked "does this affect other formats?", the answer
was yes. A cross-format check of recent format-specific bugs found 20 more
(2026-10-06), and a cross-OS check found 13 gaps.

**Rulings.** RAR password lists moved into the shared `password_confirm` logic so every
format benefits (2026-10-06, PR 627). The Windows drive-letter link refusal covers ZIP,
7z and RAR alike (PR 620). Junk after the archive end is reported in every container,
not only TAR (2026-10-07).

---

## When consistency and the official tool disagree

Neither wins by default; it depends on the case. Weigh these factors. When they all
point the same way, follow them and say so in the pull request. When they split, ask the
maintainer, with what each format's tool does, what the other formats do, and how each
factor came out.

- **Is the tool's result useful?** If it is useless to a caller, consistency wins. Device,
  FIFO and socket entries are `OTHER` in every format, although unzip and 7-Zip write
  them as empty files: "they're useless as files" (2026-10-06, PR 610).
- **Would the tool's result break a promise archivey makes?** Principles 1 and 2, and the
  policy levels' promises, come first. A 7z name holding a lone UTF-16 surrogate lists
  the way 7-Zip does, but is percent-escaped under STRICT and STANDARD, which promise the
  same result on every OS. TRUSTED, the faithful level, writes 7-Zip's bytes (PRs 564 and
  577).
- **Could the lenient behaviour hide bytes or weaken a check?** Then the stricter side
  wins. Leftover compressed input inside a ZIP or 7z member raises, as 7-Zip does, though
  unzip accepts some of it (2026-10-07).
- **Does only a crafted archive reach the case?** Then prefer the answer that needs the
  least code (DR-5a), within principles 1 and 2.
- **Would matching need plumbing across layers?** For a rare case, that weighs against
  matching. U+DC80 to U+DCFF surrogates keep archivey's one-byte meaning instead of
  7-Zip's bytes (2026-10-03, PR 564).
- **Would users compare the two results directly?** Extraction output on disk is compared
  with the tool's far more often than a diagnostic's wording. Where the comparison is
  likely, the tool's behaviour weighs more.

---

## Correctness and damaged input

### DR-1. A wrong answer is the worst failure

**Rule.** Rank outcomes: reported absence (a field is `None`, a diagnostic says why) is
better than a refusal, a refusal is better than silent loss, and silent loss is better
than wrong data. Never truncate, clamp or guess to produce a value. Verify before
serving bytes whenever a stored digest makes that possible.

**Why.** A caller can act on an error or a `None`. A caller cannot detect a wrong value.

**Unknowable data.** When the format gives nothing to check against (no stored digest,
no check value, a codec that does not validate its own stream), a wrong value cannot be
detected by anyone, so it is not a breach of this rule. What would be a breach is
staying silent where a check normally exists and was skipped. Emit
`DIGEST_UNVERIFIABLE`, with a `reason`, whenever a member's bytes reach the caller
unchecked although the caller would expect them checked. Example: 7z AES with stored
data and no CRC, where a wrong password yields garbage
(`reason="no_integrity_anchor"`). A format or codec that never carries a checksum
(plain TAR, legacy LZ4) says so once in the user docs (`docs/opening-and-listing.md`,
`docs/formats.md`) rather than with a diagnostic on every member.

**Rulings.**
- An over-long symlink target is left unset with `SYMLINK_TARGET_UNAVAILABLE`, never
  truncated (2026-09-23, PR 412).
- A cut-short RAR5 extra area: the member still lists, but its stored bytes are checked
  against a surviving digest before any are returned (2026-09-22, PR 401).
- The RAR5 encryption record stays fatal: a member shown as plaintext would be a wrong
  answer (PR 371, `formats/rar.md` §6).
- `created` never holds a change time in any format (DR-5).
- Streams raise rather than clamp an over-long read (CONTRIBUTING §Coding standards).

**Reopen if** a case appears where the only alternative to a guess is refusing data that
real tools read correctly; then the guess must be reported as a diagnostic, not silent.

### DR-2. Deliver everything recoverable, then raise

**Rule.** On damage, list and read every member that is intact, then raise a typed error
at the point of damage. Never fail the whole open for a defect that only affects later
members. `extract_all` writes the members before the damage, then raises.

**Why.** The founding use case is decades of messy backups (VISION). Every official tool
recovers what it can and reports the rest.

**Rulings.**
- A RAR set whose last volume is missing lists every member in the present parts; a
  member that runs into the gap raises `TruncatedError` (2026-10-06, PR 611). A missing
  middle volume: "read past the gap. list everything in all available parts, then raise
  at the end". A set opens from any present part, including when part 1 is missing
  (2026-10-06).
- A listing that ends in damage: every format writes the prefix, then raises
  (2026-10-03, PR 562).
- A cut plain RAR header lists the members before the cut, then raises (PR 541).

**Reopen if** recovering past a defect would require trusting data that cannot be
verified (DR-1 wins).

### DR-3. Hidden bytes are errors inside a member, warnings outside it

**Rule.**
- Leftover compressed input inside a member's declared data is a `CorruptionError` by
  default.
- Bytes after the end of an archive or stream are reported as `ARCHIVE_TRAILING_DATA`: a
  warning by default, refused under strict.
- Zero padding is silent.
- Damage to trailing structure that does not affect member data (an end block, an
  optional header record) becomes a diagnostic in `ARCHIVE_INTEGRITY_CODES`, so the
  default lists everything and strict refuses.

**Why.** Extra bytes are where a polyglot hides a second file, and inside a member they
could act as a side channel ("raising by default and refusing for strict seems
correct", 2026-10-07). Outside the member data nothing is misread, so 7-Zip's warning is
the right weight.

**Rulings.**
- ZIP and 7z members whose decoder finishes before the declared compressed size raise,
  matching 7-Zip, fixed before 0.2.0 (2026-10-07).
- Junk after ZIP, 7z, RAR and ISO archives is reported like TAR's (2026-10-07).
- A RAR end block with a bad CRC keeps the listing (2026-10-03, PR 561); a damaged second
  TAR end block keeps it too, with `ARCHIVE_EOF_MARKER_MISSING` (2026-10-06, PR 614).
- A malformed optional RAR header record is dropped with `MEMBER_HEADER_RECORD_SKIPPED`
  (PR 371).
- A zero-filled file is a valid empty TAR (ADR 0015).

**Reopen if** a real producer writes the extra bytes routinely (then check DR-6).

### DR-4. Unsupported is not corrupt

**Rule.** A valid file using a feature archivey cannot decode raises
`UnsupportedFeatureError`. `CorruptionError` is only for data that is actually damaged.
The truncated/corrupt split is best-effort: `TruncatedError` is a `CorruptionError`
subclass, and callers should not branch on it.

**Why.** Exception types are control flow. A caller sorting a backup corpus treats
"corrupt" files differently from "needs a newer tool".

**Rulings.** lzip version 0 is unsupported, not corrupt (2026-10-03, PR 567). LZMA with
`lc + lp > 4` stays unsupported because liblzma cannot decode it, and the handbook names
the one library that could (2026-10-08, PR 621). `TruncatedError` became a
`CorruptionError` subclass before 0.2.0 (2026-09-27, PR 508).

### DR-4a. The integrity guarantee is for a start-to-end read

**Rule.** Archivey promises integrity checks only for a member read from start to end
without seeking. Seeks trust the format's own index and are best-effort. A seek back to
0 re-arms the check.

**Why.** A seek skips the bytes that a check would cover. The format's own tools trust
their indexes the same way.

**Rulings.** 2026-09-23 (index trust, PR 407); 2026-09-28 (PR 509); seek to 0,
2026-10-01.

---

## Consistency

### DR-5. Same input, same outcome

**Rule.** One archive gives one result, whichever of these changes:
- **Format.** A field means the same thing in every format. A value that does not fit
  goes to a format-prefixed `extra` key, or to a dedicated cross-format field.
- **Access mode.** Streaming and random access end up the same on disk "unless there's a
  strong reason that makes it impossible" (2026-09-25).
- **Operating system.** The same archive extracts the same way on Linux, macOS and
  Windows, or is refused everywhere.
- **Accelerators and extras.** An accelerator changes speed, never the result or the
  error type.
- **Entry point.** Public functions agree with each other (`detect_format` recognises
  what `open_archive` opens).

**Why.** "Consistency between formats is a core promise of the library" (PR 290).
"Consistent behavior unless we have a good reason not to" (2026-10-07). A field
that is right on one format and wrong on another breaks callers silently.

**Rulings.**
- `created` is birth time only, never `st_ctime`, in any format; the change time goes to
  `ArchiveMember.ctime` (2026-09-25, PR 470).
- Device, FIFO and socket entries are `OTHER` everywhere (2026-10-06, PR 610).
- Timestamp diagnostics name the member attribute (`modified`, not `mtime` or
  `date_time`) in every format (2026-10-07, PR 608).
- A valid UTF-8 name wins over `encoding=` in every format; `encoding=` only replaces the
  fallback guess (2026-10-07).
- Plain TAR names decode as UTF-8 everywhere, not by the process locale (2026-09-26).
- `stream_members()` never seeks, on any format (ADR 0003 amendment, 2026-09-26).
- Hard links resolve backward-only in both access modes (2026-10-02, PR 532).
- A link target with a drive letter or UNC root is refused on every OS
  (2026-10-06, PR 620, ADR 0017).
- A truncated `.bz2` raises the same error with the accelerator on as off (2026-10-03).
  zlib under the accelerator checks Adler-32 like the standard library (2026-09-27).

**Reopen if** matching would require an OS feature that one platform lacks; then refuse
on every platform rather than diverge.

### DR-5a. Malformed corner cases may differ a little

**Rule.** Consistency can be relaxed for malformed input, particularly a shape so specific
that only a crafted archive produces it. There, a format may keep its own behaviour if
making it match would add real code or plumbing. This never relaxes DR-1 or DR-9a: no
wrong or incomplete data on a full read without raising, no unbounded resources, no
crash.

**Why.** Cross-format plumbing for a shape nobody writes costs more than the difference
it removes.

**Rulings.** Lone surrogates in U+DC80 to U+DCFF in 7z names keep archivey's one-byte
meaning instead of 7-Zip's three bytes, because matching would mean carrying each name's
origin through the filters "for a name nobody writes on purpose" (2026-10-03, PR 564).
The quadratic crafted hard-link shape was settled by the simpler backward-only rule
rather than more memo state (2026-10-02, PR 532).

**Reopen if** a real producer writes the shape.

### DR-6. When the semantics are open, match the format's official tool

**Rule.** For a format-specific behaviour question, test what the format's reference tool
does and match it: `unrar` for RAR (over `unar`), GNU `tar` for TAR, 7-Zip for 7z,
`bzip2`, `xz`, `lzip` and `zstd` for their streams. Before refusing an input as junk,
check whether a real tool produces it.

**Why.** It is a tie-breaker that avoids relitigating each case, and it is what users
already expect from their tools (2026-10-02, extraction re-audit).

**Rulings.** Hard links resolve to earlier members only, like unrar and tar
(2026-10-02, PR 532). The RAR end-block CRC keeps listing like `unrar t` (PR 561).
Absolute member names re-root inside the destination like every mainstream extractor
(2026-09-30, PR 524). A zero-filled TAR is valid because `tar -b 64` writes one
(ADR 0015). A bare `.bz2` or `.gz` stops at zero bytes between streams or members, as
`bzip2` 1.0.8 and GNU `gzip` do: what follows them is trailing data, and only zeros that
end the file are silent padding. Every reader measured but Python's `gzip` stops there,
and no writer produces the shape (2026-10-10; `formats/bzip2.md` and `formats/gzip.md`
§6). The same day the rule was extended to zstd, LZ4 and LZMA Alone, whose tools stop
there too; xz keeps reading past the Stream Padding its format defines. Zeros at the end
stay silent for every codec, although `zstd`, `lz4` and `xz --format=lzma` refuse them:
one cross-codec rule, after the tape and block padding GNU `gzip` and `bzip2` accept
(`formats/single-file.md` §6).

**Limits.** When this rule and DR-5 disagree, that is a
[real decision](#when-consistency-and-the-official-tool-disagree): weigh the factors,
and ask when they split.
ZIP and ISO have no single official tool; see [Open gaps](#open-gaps).

### DR-7. Trust self-validating data over caller hints

**Rule.** When the archive says something verifiable (a UTF-8 flag, a valid UTF-8
sequence, a stored digest), use it. A caller hint such as `encoding=` fills only the gap
the archive leaves. An explicit caller assertion that conflicts with the source, such as
`format=` on a directory, is refused loudly, never silently overruled.

**Rulings.** Valid UTF-8 names win over `encoding=` (2026-10-07). `format=` on a
directory path raises `ArchiveyUsageError` (#225). An empty listing under an explicit
`format=` is reported, not refused, because an override is obeyed (diagnostics design,
2026-08).

---

## Defaults, limits and opt-ins

### DR-8. Defaults are complete and consistent; laziness is an explicit opt-in

**Rule.** A default should be "as lazy as possible while providing full info and
consistency" (2026-09-23). If a cheaper behaviour fills a field only sometimes, it is an
opt-in config field, and its users accept the gap. When the opt-in removes information,
give the caller a way to see the gap before any cost is paid.

**Why.** "Having link targets filled sometimes but not always is a recipe for bugs."

**Rulings.** `read_link_targets` defaults to `True`; with `False`, `extract_all`'s
filter runs before a link target is read (PR 404). The same field decides whether solid
7z link targets are read at listing (2026-10-06). Checksum checks run on possibly
encrypted stored members before any byte is served.

**Do not** recommend "lazy by default, complete on request".

### DR-9. Every cost is bounded by a public, raisable limit

**Rule.**
- Every resource bound is a field on a public limits object (`ListingLimits`,
  `ExtractionLimits`, `DecoderLimits`, `SpoolLimits`), with `None`/`UNLIMITED` as the
  escape hatch. Not a module constant, unless the bound is structural rather than
  policy; then the reason goes in a comment at the constant and the bound gets a spec
  row (CONTRIBUTING §Coding standards).
- Defaults admit the real archives real tools write. Tighter numbers belong in a preset,
  not the default.
- Work the archive chooses counts against the limits. Work the destination causes counts
  only against absolute caps.
- Reuse an existing limit before inventing a new one.
- Implementation constants (a cache size, a candidate cap) are the implementer's to pick
  and move on evidence. Default *limit* values are the maintainer's.

**Why.** A user with a legitimately huge archive must be able to raise any cap
(CONTRIBUTING). The caller's own configuration is trusted (threat model §1).

**Rulings.**
- Decoder memory: a new public `DecoderLimits`, not a field on `ExtractionLimits`,
  "with 1GB so that real files always decode fine", later 2 GiB (2026-09-19 and
  2026-09-23, PR 398).
- RAR stream spool: "let's add a cap, it would be a config field" (2026-09-26, PR 485).
- PPMd in-process threshold is a config field, not a constant (2026-09-26, PR 481).
- ISO path-table entries count against `max_members` (2026-10-06, PR 612).
- Hard-link copies at the link-count limit count toward `max_ratio`; cross-device copies
  count only toward bytes (2026-10-07, PR 623).
- Default numbers (decided 2026-10-07, **not yet applied**): `max_members` and
  `max_entries` 262,144; `max_key_derivation_rounds` 2**25. These replace the 2026-10-01
  decision to keep 1,048,576 and 2**27, after measuring per-member listing cost. Until
  the change lands, the shipped defaults are still 1,048,576 and 2**27. It has to land
  in `config.py`, the archive-reading and safe-extraction specs, `docs/extracting.md`
  and the threat model together.

**Reopen if** a measured real archive class hits a default.

### DR-9a. The upload-server test

**Rule.** No archive may cause unbounded memory, unbounded disk, a hang without a
bound, or a crash of the process. Picture a server that lets anyone upload an archive to
be tested, scanned or have its members hashed, with default limits. If some upload could
bring it down, that is a bug, however unlikely the archive is to occur naturally.
Native code that can crash runs in a child process (DR-20). Every allocation an archive
can steer is checked against a limit before it is made (DR-9).

**Why.** An attacker crafts archives at will and picks whichever loophole is left
(2026-10-09). "Only crafted archives hit it" can relax consistency (DR-5a), never this
rule.

**Rulings.**
- PPMd decoding past the end could corrupt memory, so it runs in a child process
  (2026-09-26, PR 481).
- rapidgzip killed the process on a truncated gzip, so it moved into a child process
  (2026-09-26, PR 493).
- `detect_format` must not allocate an LZMA dictionary at whatever size a crafted header
  declares (2026-10-07).
- ISO path-table entries count against `max_members` (2026-10-06, PR 612).
- Decoder memory is capped before allocation (PR 398).

**Known limits.** Limits cap bytes, entries and key-derivation rounds, not time; the
accepted non-guarantees are in `threat-model.md` §4. Each of them must still pass this
test at default limits, or be listed there with its reasons.

### DR-10. Costly or risky capability is declared, and a declaration is a guarantee

**Rule.** Seekable streams, concurrent streams, accelerators, external programs and
anything else that costs resources or changes guarantees is off until the caller asks
for it by name. Once asked, it is a guarantee, not "where the backend can". A
non-seekable source in random-access mode fails at open instead of being buffered.

**Why.** "An unconditional 'always seekable' API lets developers test on ZIP and ship a
footgun on TAR/7z" (ADR 0003). Blocking a cheap read too catches the mistake during
development, not in production on a different archive (2026-09-29).

**Rulings.** ADR 0003, 0004, 0010. `stream_members()` is never seekable (2026-09-26). The
gzip AUTO accelerator stays seek-only for 0.2.0 (2026-09-27).

### DR-10a. Read sequentially; seek only when the format needs it

**Rule.** Prefer forward reads over seeks, and few large reads over many small ones.
Read only the parts the operation needs: the index to list, the member's bytes to read
it. Count seeks and bytes read as costs, the same as bytes decompressed.

**Why.** Sources are often remote file objects, such as an HTTP range reader or a
storage-bucket client, where each seek can be a network request (2026-10-09,
[scenario 9](scenarios.md#9-loading-from-a-remote-file-object)). Pipes cannot seek at all.

**Rulings.** Performance claims cite bytes decompressed and seeks, not wall time
(CONTRIBUTING). Solid blocks are decoded once per pass (VISION).

### DR-11. Environment trouble falls back; explicit requests fail loudly

**Rule.** When an automatic choice (an `AUTO` setting) cannot run because of the
environment (a package not installed, a child process that cannot start), fall back to
the slower correct path, with a log warning when the cause is the host rather than the
archive. When the caller asked for that path explicitly (`ON`), raise.

**Rulings.** rapidgzip under AUTO falls back to the standard library when its child
process cannot start: "a log warning may be warranted. it's not an archive related
issue, but an environment issue" (2026-09-26, PR 493). A refused `unar` is never used,
whatever the setting (ADR 0002 amendment, 2026-10-01).

---

## Public API

### DR-12. Before the first release, remove rather than keep

**Rule.** Until 0.2.0 ships, any breaking cleanup goes in now: deleting a name, renaming
a field, changing an exception's base class. If a public name has a single value, no
caller, or an obvious two-line alternative, delete it. Offer "remove it" as an option on
every API question. The maintainer still reviews every removal or rename of a public
name, before 0.2.0 too: he is for the cleanup and wants to check it and know it happened
(2026-10-09). Propose it, don't hold it back.

**Why.** Removing a public name after the release breaks callers. Adding it back later
breaks no one ("no need to delay until after release", 2026-09-27).

**Rulings.**
- `DiagnosticSeverity` removed: "let's simplify the API" (2026-09-26, PR 479).
- `WriteError` deleted outright (PR 479).
- Exception tree pruned from 27 to 18 classes (2026-09-27, PR 506).
- Top-level `archivey.extract()` removed (ADR 0019).
- `extract_all(config=)` dropped because the reader already has a config.
- The seven string enums became `StrEnum` (PR 639).
- Leftover compressed input raises before 0.2.0 rather than after (DR-3).

In several of these the maintainer went further than the recommendation, toward
removal.

**Reopen if** a removed name is needed by a real caller; add it back then.

### DR-13. A public name earns its place

**Rule.**
- A public exception type exists only if a caller would handle it differently from its
  parent (ADR 0012).
- Niche names live in a public submodule, not at the package root (`archivey.terminal`,
  `archivey.detection_cost`).
- `__all__` grows one export at a time, with the reason in the PR.
- The CLI uses only public API, apart from what `tests/test_cli_uses_public_api.py`
  allowlists with a stated reason. What the CLI needs becomes public, which is how `dry_run=`
  and `archivey.terminal` were made public.

**Rulings.** `detection_cost` stays in its module: "minimizes root exports, these are
too niche for root" (2026-09-25). The CLI escaping helpers became a public submodule not
re-exported at the root (2026-09-24). `dry_run=` is public (2026-10-01, PR 531).
`MemberStreams` was demoted (PR 479).

### DR-14. One obvious way, and one mechanism per job

**Rule.**
- Do not add a convenience wrapper that duplicates a short chain of public calls.
- When two mechanisms disagree, delete one rather than choose between them.
- When a new need appears, look for an existing option or class first. Extend it and
  document it before adding a new one.
- Collapse layered wrappers into one owned abstraction at the boundary.

**Why.** The two rulings in the September sweep both removed surface instead of choosing
between two behaviours. Later questions were often answered with "don't we already have
an option for this?".

**Rulings.**
- `strict_archive_eof` was removed rather than reconciled with `IGNORE`.
- The solid 7z link-target cost: "don't we already have an option…", so the card closed
  as "Leave it", with a doc fix (2026-10-06).
- Password lists for RAR were folded into the shared `password_confirm` logic, which
  helps every format (2026-10-06, PR 627).
- The bzip2 resume reused the gzip bit-shift approach (2026-10-03).
- One `ArchiveSource` wraps every caller stream or path (2026-09-22, PR 419).
- Exception refusal reasons: "I was only considering reusing if something already
  existed" (2026-09-27).

### DR-14a. Easy to explain, easy to use right the first time

**Rule.** Between two designs that both meet principles 1 and 2, pick the one that is
easier to explain in a sentence and easier to use correctly on the first try, by a person
reading the docs or an agent reading a docstring. Concretely:
- The common path needs no options. `open_archive(path)` then iterate, read or
  `extract_all`.
- When a declared opt-in is required (DR-10), the error that asks for it names the
  option to set, so the fix costs one step during development rather than a search.
- Names say what they govern (`detection_budget`, `DecoderLimits`), so a reader does not
  need the docs to guess.
- Docs lead with what a caller chooses, then the consequences, and frame options by use
  case.
- A mechanism its own author cannot explain in one page gets simplified before it is
  frozen.

**Why.** Archivey aims to be the default library for archives, in code written by
people and by agents. Every extra concept costs each caller time and tokens, and a
library that makes callers stop and think becomes a bottleneck (2026-10-09).

**Rulings.**
- Top-level `extract()` was removed because a near-identical second way "causes more
  confusion than it saves" (ADR 0019).
- The `open_archive` options got a use-case table after the maintainer asked whether
  the docs made them look "more annoying than they actually are" (PR 513).
- Diagnostics were re-explained after "I still don't fully understand how it works and
  how it should be used" (2026-09-25).
- `budget=` became `detection_budget` on the config: "it's unclear otherwise … most
  users won't care … let's think about what makes more sense from the user's POV"
  (PR 275).

**Tension.** Declared opt-ins (DR-10) add a thing to know. They stay, because they turn a
production surprise on another format into an error during development. This rule asks
that the error make the fix obvious.

### DR-15. Usage errors are for what the types cannot rule out

**Rule.** A wrong argument type or value raises `TypeError` or `ValueError`.
`ArchiveyUsageError` is for misuse the signature cannot express, such as calling a
method in the wrong mode. Translate only known third-party exceptions; never add a
catch-all.

**Rulings.** 2026-09-25, recorded in the ADR 0012 amendment.

### DR-15a. Translate archive problems; let I/O problems through

**Rule.** Errors about the archive's content become archivey errors. Runtime failures not
about decoding, such as a disk read error or a dropped network connection, raise the
original exception. A known library exception is mapped; an unknown one propagates. An
error message states the fact, with no advice the reader cannot act on.

**Rulings.** "Actual runtime unpredictable filesystem-level errors … should still raise
the original exception, as they're not directly related to archive decoding" (PR 3,
reaffirmed in PR 18). `MemoryError` is not translated, so running out of memory is never
mistaken for damage (threat model §4).

### DR-15b. Fail at open, but do no expensive work there

**Rule.** A problem that open can detect cheaply (contradictory options, a missing
package, an unreadable header) raises at open, not at the first read. Work proportional
to the archive's data (a full CRC scan, decoding members) waits until a read asks for it.

**Rulings.** Single-file readers open their stream eagerly so init errors surface at open
(PR 13). The gzip trailer CRC is decided at read and never scanned at open (2026-09-24).

### DR-16. A feature must deliver its promise

**Rule.** If archivey advertises a capability, it works in every format that claims it,
or the format says it does not. A heuristic that silently degrades the capability is a
bug.

**Rulings.** RAR3/4 password lists tried only the first candidate. Taking the first
candidate "would defeat the whole purpose of supporting multiple passwords", so every
candidate is probed (2026-10-06; shipped in PR 627). Anything on by default is fuzzed:
"if they're the default, we should fuzz them", so the accelerator fuzzing gap was closed
instead of keeping the "accelerators are not fuzzed" caveat in the docs (2026-10-06, PR
638). Closing a gap rather than documenting it is DR-22a.

---

## Extraction

### DR-17. Extracting beats refusing; refuse only what is unsafe

**Rule.**
- Refuse a member only when writing it is unsafe, or when the outcome would differ by OS.
- Rewrite what is merely non-portable (percent-escape, re-root).
- Policy decides everything else, with TRUSTED as the faithful escape hatch.
- Prefer a composable hook to a new policy value.

**Why.** Refusing an odd but harmless name forces the user into TRUSTED, which couples a
naming question to a permissions decision (ADR 0013).

**Rulings.** Absolute names re-root under STANDARD and TRUSTED, and STRICT refuses. The
caller filter runs first, so `sanitize_names` can fix them; it was picked over a fourth
policy (2026-09-30, PR 524). Bidi overrides are policy-keyed (ADR 0017).

### DR-18. Extraction never damages what it did not create

**Rule.** Do not change the mode of a pre-existing directory, the destination root
included. `REPLACE` never removes a non-empty directory. Directory modes and times are
applied in a final pass, like GNU tar.

**Rulings.** 2026-09-30 extraction re-audit (PR 532). Directory modes in a final pass
(2026-10-08).

### DR-19. Prefer one structural invariant over many string checks

**Rule.** When several ad-hoc checks guard one hazard, look for the invariant that makes
them unnecessary. For hard links, the invariant is that a target must name an earlier,
non-refused member.

**Rulings.**
- Hard-link targets: one rule replaced the drive, backslash and re-root path checks
  (2026-10-07, PR 632).
- The CLI hoist check walks each link's real path, replacing the earlier
  "any `..` blocks" rule, which over-blocked (2026-09-30).

---

## Dependencies and formats

### DR-20. Parse in Python; isolate native code that can crash

**Rule.** Container and header parsers are pure Python. Native code is for codecs only.
A codec with known crashes runs in a child process or is designed around. Choose a
library by how it behaves on damaged input, measured. A tool that is known to decode
wrongly is refused, not used.

**Rulings.** ADR 0001, 0002, 0009. PPMd (PR 481) and rapidgzip (PR 493) run in child
processes rather than reading the whole file first, which "defeats much of the
accelerator usefulness". Only a `unar` that passes the RAR5 probe is used
(2026-10-01). Zero-dependency core (ADR 0011).

### DR-21. Defer a fix that a planned rewrite absorbs, and document it

**Rule.** If a fix would mean replacing a component that is already planned for
replacement, record the limitation where users will find it and fix it in the rewrite.

**Rulings.** A ZIP name with the UTF-8 flag but invalid UTF-8 keeps refusing the archive
until the post-0.2.0 zipfile replacement (2026-10-06).

### DR-21a. Don't ship what can't be tested

**Rule.** If no available tool can produce a case and archivey cannot test it, refuse it
up front with a clear error and document it as a known limitation, rather than ship an
untested path.

**Rulings.** Encrypted comments in old RAR archives: "skip up front. note in docs as a
known limitation due to being untestable" (2026-09-24).

---

## Documentation

### DR-22. Docs say what is true now, with reasons

**Rule.**
- VISION describes what the project wants to provide, not a claim that it is done.
- `dev-docs/` is a clean set of developer docs, not task tracking.
- The threat model is a model, not a list of known security issues.
- Handbook and topic pages explain how things work today and why ("we do Y because X
  would cause…"), not a history.
- A recorded decision keeps its decider and date, and adds its reasons and what would
  reopen it: "just citing doesn't help us reevaluate or accept later" (2026-10-02).
- User-facing numbers are ballparks: higher precision gives a false sense of exactness.

**Rulings.** Top-level docs review (2026-09-28). Detection topic page (2026-09-26).
Ballpark numbers (2026-10-07). User-doc voice: see the `write-user-docs` skill.

### DR-22a. A caveat in the docs is a question for the code

**Rule.** When a behaviour needs a caveat, a gotcha or a "be aware that" in the docs,
first ask whether the code can change so the caveat is no longer needed. If it can, fix
the code and drop the caveat. Keep the caveat only when the fix is impossible (the
format gives nothing to work with, as in DR-21a, or the data is unknowable, principle 1)
or costs more than it buys; then the caveat says why. When the fix changes public
behaviour, put it to the maintainer as a choice between "fix it" and "document it",
with the fix recommended unless something argues against it.

**Why.** A caveat is work for every reader, and most readers never see it (principles
3 and 4). Writing the docs keeps turning up inconsistencies: questions the maintainer
raised while rewriting the user docs repeatedly turned out to be behaviour to fix, not
to explain (2026-10-09).

**Rulings.**
- Accelerators were not fuzzed, and the docs said so. The gap was closed instead of
  keeping the caveat (DR-16, 2026-10-06).
- `MEMBER_TIMESTAMP_INVALID` named its field differently in ZIP and TAR. "Keep the split
  and list both vocabularies in the docs" was offered and turned down in favour of one
  vocabulary (2026-10-07, PR 608).
- The `encoding=` rule came out of writing the opening page: valid UTF-8 names are to
  win over `encoding=` in every format, rather than the page explaining a per-format
  difference (ruled 2026-10-07).

**Reopen if** a fix would break the upload-server test or hide data (principles 1 and
2); then the caveat stays.

---

## Code and tests

### DR-23. Zero debt in the code you touch

**Rule.**
- "Previously existing code is not a good justification, we're striving for zero debt."
  Remove dead code and unused fields; prefer the cleaner structure over the smallest
  diff.
- A simple bug in code the PR already touches is fixed in that PR. A sweep across files
  the PR does not touch is its own PR.
- Review nits are fixed, not deferred. Something too big to fix in place is split into
  its own PR, not parked.
- Before adding a class or mechanism, check whether the standard library or an existing
  helper does it. "Do we really need a custom class for this cache?" (PR 418).
- Comments and maintainer docs describe the code as it is and why: "Don't talk about the
  past. Code maintainers need to understand the current state and the reasons behind
  it" (PR 400). History belongs in PRs and ADRs.
- Names are spelled out and carry their context (`detection_budget`, not `budget`).
  No logic in `__init__.py` beyond the export surface's own machinery (registration,
  module pinning, `__getattr__`). Code is grouped by format, not by pipeline phase (PR 443).
- Raise naming questions at proposal review. A rename after implementation needs a
  reason beyond taste.

**Reopen if** a cleanup would change public behaviour; then it is its own PR with its own
review, not part of the PR that found it.

### DR-23a. Share what must agree; copy what agrees by coincidence

**Rule.** Before sharing or copying code in `src/`, `tests/` or `scripts/`, ask: if one
copy changes, must the other change too?
- If yes, share it. Copies that must agree for correctness drift apart, and the drift is
  a bug: limits and resource checks, error translation, integrity checks, diagnostics
  wiring, and the workarounds for one library that several codecs use.
- If no, keep the copies. Code that happens to look alike today (two formats' header
  parsers, two tests' setup) may diverge for good reasons. Keeping copies does not
  narrow DR-0: a bug found in one copy is still a question for the others.
- A shared helper that needs a flag, mode or callback per caller to cover their
  differences is worse than plain copies. Share the part that must agree and leave the
  rest in each caller.
- If you can't tell, keep the copies and add a comment at each one naming the other, so
  a fix to one reaches the other.

**Why.** The maintainer, 2026-10-09: some duplication makes sense; "nothing is black and
white". The same day's duplication check of the codec modules (on the layout PRs 647 and
649 introduce) found copies that had drifted into different behaviour, and one bug where
a copy missed the diagnostics collector the others pass (tracked internally). DR-14
covers duplicated public mechanisms; this rule covers internal code.

### DR-24. Tests pin behaviour against real producers

**Rule.** A bug fix starts with a failing test (red, then green). Tests check behaviour,
not internals. Fixtures come from the real tools users run (the gzip command line,
RARLAB `rar`, 7-Zip), pinned by hash where they are committed, and a matrix row says
when no available tool writes that case. A refactor that breaks a test means the fix or
the test is wrong: find out which and report it, never quietly edit the test.

**Reopen if** a real producer cannot make a case that users do hit; then a crafted
fixture is acceptable, with a comment saying why.

**Rulings.** gzip `FNAME` is Latin-1 per RFC 1952, fixed red/green with a
command-line fixture (PR 13). Committed RAR fixtures (ADR 0016). The corner-case
cleanup's rule for refactors (2026-10-02).

---

## Questions to ask yourself first

The maintainer's questions have repeatedly turned up the real answer, often a better one
than any option offered. Ask them yourself, and put the answers in the question before it
reaches him.

- **Does this affect other formats? Is there a shared path?** A cross-format check of
  recent format-specific bugs found 20 more (2026-10-06).
- **What does the official tool do?** Run it (DR-6).
- **Do streaming and random access agree? Does each OS agree?** (DR-5)
- **Is this a limit of the library we use, or are we refusing by spec?** (the lc=8
  question, 2026-10-08)
- **Don't we already have an option or class for this?** (DR-14)
- **How hard is it to do it properly instead of exempting it?** The exemption on hard-link
  copies did not survive "how hard is it to count?" (2026-10-07).
- **Did a later change make this code redundant?** The gzip size-check fallback "was
  useful at first but became redundant" (2026-10-06).
- **Is it a breaking change after release?** If yes, do it now (DR-12).
- **Could these bytes hide something?** (DR-3)
- **Can the user still get what they need through a filter or an opt-in?**
- **Is it actually simpler?** For a large design, ask for a fresh review before building
  it. The detection evidence ledger was dropped after one said the small fixes were
  enough (2026-09-25).
- **Does this need a caveat in the docs?** If so, can the code change so it doesn't?
  (DR-22a)
- **Is the premise right?** Check the finding against current `main` and measure before
  asking. Several questions were withdrawn because the finding was stale.

## What still goes to the maintainer

- A **default limit value** or any number users will see as a default.
- A **new public name**, or **removing or renaming** one, before 0.2.0 as well as after
  (DR-12). Expect a yes; the point is that he checks and knows.
- Accepting a **residual risk** in the threat model.
- **Reversing** an earlier ruling. This needs a new argument, not a restatement (ADR 0019).
- A **clash between consistency and the official tool** where the factors split.
- Anything **outside the repo**: upstream bug reports (agents draft them, the maintainer
  files them), repository settings, publishing.
- Merging a **tricky** pull request: a behaviour trade-off, a design reversal, removing
  or renaming a public name (before 0.2.0 too), a very large diff, or anything an open
  decision touches. A straightforward one an agent may merge once its review approves and
  CI is green.

How to ask is in `AGENTS.md` §Working with the maintainer.

## Open gaps

Questions no rule here settles yet.

- **The official tool for ZIP and ISO.** DR-6 names none. Asked on 2026-10-02 and not
  answered.
- **The stricter `DecoderLimits` preset.** The numbers are chosen (256 MiB, 2**24); the
  name, and whether it should be a mode rather than numbers, are open.

## Recording a new ruling

When the maintainer rules on something:
- If it is format-specific, add a bullet to that format's handbook page.
- If it generalises, add it here as an example under the rule it applies, or as a new
  rule.
- If it contradicts a rule here, update the rule and keep the old ruling as history in
  the example list.
- Either way, write the date, the reasons and what would reopen it.
