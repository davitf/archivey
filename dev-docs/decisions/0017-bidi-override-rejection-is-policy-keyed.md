# 0017 — Bidi-override rejection is policy-keyed, not universal

- **Status:** accepted
- **Date:** 2026-08-08 (PR #232, review finding F10 / O7)
- **Provenance:** `review/archive/2026-08-15-simplicity-consistency/` (O7); OpenSpec `safe-extraction`;
  [ADR 0013](0013-cross-platform-name-safety-policies.md) (the axis this restores);
  `VISION.md` (safe by default); Trojan Source (CVE-2021-42574)

## Context

A member named `evil<U+202E>gnp.exe` displays in every listing a person sees as
`evil<reversed>exe.png` — the U+202E RIGHT-TO-LEFT OVERRIDE reorders the text after it,
so an executable reads as an image. This is the Trojan Source disguise applied to
filenames.

The review decided archivey should refuse such names during safe extraction, and the
first implementation put the check in `check_universal` — the non-bypassable layer that
runs on the original member under **every** `ExtractionPolicy`, including `TRUSTED`.
That made a bidi-override member unextractable by any route: no policy lifted it, and a
caller filter could not rescue it either, because `check_universal` runs on the original
*before* the filter sees it. The only escape was `reader.open(member)` plus a manual
write.

Which set is rejected was settled separately and is not revisited here: the
**overrides and isolates** (U+202A–U+202E, U+2066–U+2069) reorder surrounding text and
are what the disguise needs; the three **directional marks** (U+061C, U+200E, U+200F)
reorder nothing and occur in legitimate Arabic and Hebrew filenames. RTL *script* carries
no control at all. That split stands.

### Why the placement was wrong

**Every other constraint in `check_universal` makes the write itself dangerous or
impossible.** Path traversal and absolute paths escape the destination. A NUL byte is
truncated by the OS into a different path. An unrepresentable name cannot be
materialized. A device node is not a file. In each case there is no correct way to
perform the write.

A bidi override is not like that. The member lands **inside** the destination under
**exactly** its stored bytes. Nothing escapes, nothing is truncated, nothing is
unwritable. What is compromised is the name a person reads back *afterwards* — a
presentation property.

And presentation is already an axis with a home. `ExtractionPolicy` is documented as
governing "the permission/ownership transform … and the cross-platform name safety keyed
off it — collision determinism, reserved/mangled name rejection, portable-name
normalization", with `TRUSTED` meaning **"faithful bytes, no name rejection or rewrite"**.
Name rejection *is* the policy axis, and `TRUSTED` already means off.

**ADR 0013 decided this exact shape, against.** For unrepresentable names (threat-model
O7) it chose sanitize over reject, because reject

> would force a Linux user to drop to `TRUSTED` — a permission/ownership decision — just
> to extract an oddly-named backup member, **coupling two unrelated axes** … the
> tie-breaker is which default is more useful — **extracting beats refusing**.

The universal placement did what 0013 rejected, and went further: dropping to `TRUSTED`
did not help either, so there was no route at all. That is a real cost to real callers —
a mirroring tool, an archive-format converter, a forensic extract, anything that must
round-trip an archive faithfully.

## Decision

**Reject bidi overrides in `apply_name_policy`, not `check_universal`.**

- `STRICT` (the default) and `STANDARD` reject with `DeceptiveNameError`, as before.
- **`TRUSTED` extracts the member unchanged**, under its stored name.
- The check runs on the **final** name, after the caller filter — so a filter that
  renames the member rescues it. Renaming a name that is a lie is the natural remedy, and
  it was unreachable under the old placement.
- The link-target check moves with it, on the same terms.
- `check_universal` keeps only constraints where the write itself is unsafe. Its
  docstring now says so, so the next addition has a test to apply.

Safe-by-default is preserved: the default policy refuses, and a caller reaches the bytes
only by explicitly asking for faithful ones. This matches the ecosystem response to
Trojan Source — rustc's `text_direction_codepoint_in_literal` and GCC's `-Wbidi-chars`
are deny-**by-default** diagnostics over the same codepoint ranges, not unconditional
refusals to process the file.

## Consequences

- **Round-tripping works again.** An archive carrying such a member can be extracted
  faithfully under `TRUSTED` and re-archived.
- **Listing and reading are unaffected** and always were: the name is presented exactly
  as stored, with `MEMBER_NAME_BIDI_CONTROL` reported at listing time for the whole
  advisory set (overrides *and* marks).
- **`TRUSTED` now carries this too.** It already meant "I vouch for this archive"; it now
  additionally means "and I accept a name that may read deceptively". Documented on the
  enum and in `safe-extraction`.
- **The adversarial corpus drives extraction at `TRUSTED`** to prove universal checks
  hold at the most permissive policy. Its two bidi-override cases now assert *both*
  halves — extracted under `TRUSTED`, blocked under `STRICT`/`STANDARD` — so the file's
  one policy-keyed case cannot be mistaken for a universal one.
- **A move back to `check_universal` fails a test**, not just a review:
  `test_apply_name_policy_raises_deceptive_name_error` asserts the check is absent from
  `check_universal` and present in `apply_name_policy`.
