# Design rules

These are the rules the maintainer has applied, again and again, when settling a design
question. They exist so that most questions get settled by a rule instead of by a new
question to the maintainer. Each rule says what it is, why it holds, the rulings it was
derived from, and what would reopen it.

Use this page before you ask. If a rule here settles the question, do what it says and
name the rule in the pull request ("settled by DR-7"), so the maintainer can still object.
If the question is a [clash between consistency and the official tool](#when-consistency-and-the-official-tool-disagree) that the factors do not settle,
or is on the [escalation list](#what-still-goes-to-the-maintainer), ask.

Where these rules come from: maintainer rulings in pull requests, ADRs, handbook pages
and the project's decision threads, collected in October 2026. VISION.md stays the
product tie-breaker; these rules are how its priorities have been applied in practice.
Format-specific rulings stay on their handbook pages (`dev-docs/formats/`); this page
holds only what has generalised across formats.

## The principles

The maintainer's own summary (2026-10-09) of what he focuses on consistently. Every
numbered rule below is one of these applied to a kind of question.

1. **Consistent behaviour between formats and libraries** (DR-5).
2. **Follow the official tool's behaviour** (DR-6).
3. **When the previous two conflict, that needs a real decision.** The factors below
   settle the clear cases; the rest go to the maintainer.
4. **No surprises or gotchas for users** (DR-8, DR-10).
5. **Never output incorrect or incomplete data without raising on a straightforward full
   read** (DR-1, DR-2, DR-4a).
6. **Consistency can be relaxed a little for malformed data**, particularly for a corner
   case so specific that only crafted archives hit it (DR-5a).
7. **Unbounded memory or crashes are never acceptable.** An attacker crafts archives at
   will and will choose the loopholes. The test: if a server lets anyone upload an
   archive to be tested, scanned or have its members hashed, could an upload bring the
   server down? (DR-9a).
8. **Generalise fixes and approaches across all formats** (DR-0).

Principles 5 and 7 are firm: no other principle outranks them, and "only crafted archives
hit it" is no excuse there. The others are strong defaults whose answer depends on the
case. The numbered rules below work the same way: a rule settles a question when the case
looks like the rulings it came from, and a case that differs in an important way goes back
to the maintainer.

## When consistency and the official tool disagree

Neither wins by default; it depends on the case. Weigh these factors. When they all
point the same way, follow them and say so in the pull request. When they split, ask the
maintainer, with what each format's tool does, what the other formats do, and how each
factor came out.

- **Is the tool's result useful?** If it is useless to a caller, consistency wins. Device,
  FIFO and socket entries are `OTHER` in every format, although unzip and 7-Zip write
  them as empty files: "they're useless as files" (2026-10-06, PR 610).
- **Would the tool's result break a promise archivey makes?** Principles 5 and 7, and the
  policy levels' promises, come first. A 7z name holding a lone UTF-16 surrogate lists
  the way 7-Zip does, but is percent-escaped under STRICT and STANDARD, which promise the
  same result on every OS. TRUSTED, the faithful level, writes 7-Zip's bytes (PRs 564 and
  577).
- **Could the lenient behaviour hide bytes or weaken a check?** Then the stricter side
  wins. Leftover compressed input inside a ZIP or 7z member raises, as 7-Zip does, though
  unzip accepts some of it (2026-10-07).
- **Does only a crafted archive reach the case?** Then prefer the answer that needs the
  least code (DR-5a), within principles 5 and 7.
- **Would matching need plumbing across layers?** For a rare case, that weighs against
  matching. U+DC80 to U+DCFF surrogates keep archivey's one-byte meaning instead of
  7-Zip's bytes (2026-10-03, PR 564).
- **Would users compare the two results directly?** Extraction output on disk is compared
  with the tool's far more often than a diagnostic's wording. Where the comparison is
  likely, the tool's behaviour weighs more.

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

## Correctness and damaged input

### DR-1. A wrong answer is the worst failure

**Rule.** Rank outcomes: reported absence (a field is `None`, a diagnostic says why) is
better than a refusal, a refusal is better than silent loss, and silent loss is better
than wrong data. Never truncate, clamp or guess to produce a value. Verify before
serving bytes whenever a stored digest makes that possible.

**Why.** A caller can act on an error or a `None`. A caller cannot detect a wrong value.

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

**Why.** "Consistent behavior unless we have a good reason not to" (2026-10-07). A field
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
(ADR 0015).

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
  escape hatch. Never a module constant.
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
- Default numbers (2026-10-07): `max_members` and `max_entries` 262,144;
  `max_key_derivation_rounds` 2**25. These replace the 2026-10-01 decision to keep
  1,048,576, after measuring per-member listing cost.

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
every API question.

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
- The CLI uses only public API. What the CLI needs becomes public, which is how `dry_run=`
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

### DR-15. Usage errors are for what the types cannot rule out

**Rule.** A wrong argument type or value raises `TypeError` or `ValueError`.
`ArchiveyUsageError` is for misuse the signature cannot express, such as calling a
method in the wrong mode. Translate only known third-party exceptions; never add a
catch-all.

**Rulings.** 2026-09-25, recorded in the ADR 0012 amendment.

### DR-16. A feature must deliver its promise

**Rule.** If archivey advertises a capability, it works in every format that claims it,
or the format says it does not. A heuristic that silently degrades the capability is a
bug.

**Rulings.** RAR3/4 password lists tried only the first candidate. "a would defeat the
whole purpose of supporting multiple passwords", so every candidate is probed
(2026-10-06, PR 627). The accelerator fuzzing gap was closed instead of keeping the
"accelerators are not fuzzed" caveat in the docs (2026-10-08, PR 638).

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
- **Is the premise right?** Check the finding against current `main` and measure before
  asking. Several questions were withdrawn because the finding was stale.

## What still goes to the maintainer

- A **default limit value** or any number users will see as a default.
- A **new public name**, or removing or renaming one after 0.2.0.
- Accepting a **residual risk** in the threat model.
- **Reversing** an earlier ruling. This needs a new argument, not a restatement (ADR 0019).
- A **clash between consistency and the official tool** where the factors split.
- Anything **outside the repo**: upstream bug reports (agents draft them, the maintainer
  files them), repository settings, publishing.
- Merging a **tricky** pull request: a behaviour trade-off, a design reversal, a public
  API removal, or a very large diff.

How to ask is in `AGENTS.md` §Working with the maintainer.

## Open gaps

Questions no rule here settles yet.

- **The official tool for ZIP and ISO.** DR-6 names none. Asked on 2026-10-02 and not
  answered.
- **bzip2 after zero padding.** The standard library reads both streams; `bzip2` 1.0.8
  and the accelerator stop after the first. The factors split: nothing hides (the second
  stream is reported as trailing data), the shape is unusual but not crafted, and only
  the standard-library path differs. Raised 2026-10-08.
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
