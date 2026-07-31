## Context

Pure rename across ten specs. The interesting part is not *what* to change but *how* to
change 57 references spread over 21 requirements without corrupting one of them, since a
`MODIFIED` delta must reproduce the entire requirement body verbatim and some of these
bodies are over a hundred lines.

## Decision: generate the deltas, do not retype them

Every delta body in this change was produced by a script that reads the live spec, splits
it on `### Requirement:` headers, applies an explicit list of `(spec, old, new)`
substitutions, and emits the resulting block. Each substitution asserts it matches
**exactly once** before applying.

The alternative — copying each requirement into a delta file by hand — puts a
transcription risk on text that becomes normative on `openspec archive`. A dropped line
in a 127-line requirement would silently delete a `SHALL` from the authoritative spec.
Generating the body means the delta is *verbatim plus the intended diff*, by construction.

The same approach was used for the sibling-spec deltas in
`member-stream-capability-booleans`, for the same reason.

## Decision: substitutions are per-line, not a regex

`s/\[7z\]/[recommended]/g` would be wrong in at least three ways:

| Site | Why a blind swap fails |
| --- | --- |
| `7z availability without [7z] or [crypto]` | Both map to `[recommended]`, so the sentence would read "without `[recommended]` or `[recommended]`" |
| `[lz4]` (also pulled by `[7z]`) | The parenthetical only existed because the extras overlapped; it is now noise |
| `AES stream without [crypto]` | The scenario is about the **package** being absent, not the extra — `cryptography` is the honest word |
| `pip install archivey[7z]` → *Fails: the extra no longer exists* | Deliberately names the dead extra; must **not** be renamed |

So the substitution table is explicit, one entry per site, with a match-count assertion.
Where the subject is really a package rather than an extra, the package name wins —
`[recommended]` is a broad bundle, and "install `[recommended]`" says less than
"`cryptography` is missing".

## Decision: prose edits go directly into the main specs

Deltas carry `### Requirement:` blocks only, so `openspec sync` would silently drop edits
to a Purpose paragraph or a Related-specs table row. Six such rows (in `archive-writing`,
`cli`, `format-7z`, `format-iso`, `format-rar`, `packaging-and-extras`) are therefore
edited in `openspec/specs/` directly, in the same commit.

`format-iso` has **no** requirement-level change at all — both of its references are in the
Purpose line and the Related-specs table — so it has no delta file. An empty delta would
fail validation, and inventing a requirement change to carry a prose fix would be worse.

## Decision: rename the one requirement whose title names a dead extra

`format-7z` has `### Requirement: Stage LZMA1+BCJ through pybcj under [7z]`. Its body
changes too, so the delta carries both a `## RENAMED Requirements` FROM/TO pair and a
`## MODIFIED Requirements` block under the new name. Verified by dry-run archive that the
two apply together and produce a single correctly-named requirement — the spec still has
exactly 12 requirements afterwards, with the body intact.

**Known side effect:** `openspec archive` applies a rename as remove-then-append, so on
archive this requirement moves from its position among the other 7z codec requirements to
the **end** of the spec. Content is unaffected and the dry run confirmed the body is
byte-identical; only the reading order changes. Accepted rather than worked around: the
alternative is leaving a deleted extra in a requirement title, and re-ordering it back by
hand after archive would be an untracked manual step that the next archive could undo.
Worth knowing before the archive, so it does not look like an accidental reshuffle in the
diff.

## Verification

`openspec validate --strict` passes, but it does not check that a `MODIFIED` header names
a requirement that actually exists in the parent spec — a mis-targeted delta validated
green in `member-stream-capability-booleans` and had to be restructured after review. So
this change is verified by **dry-run archive** instead: apply it to a scratch tree, diff
the resulting `openspec/specs/` against the intended text, confirm zero stale references
outside the two deliberate historical mentions, then reset. The check is recorded in
`tasks.md`.
