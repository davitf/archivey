# A payload at a nonzero offset is not the same as a self-extracting archive

**Written to be read standalone.** No prior knowledge of the codebase is assumed; every
fact you need is inline, with file references if you want to check one. Dated 2026-08-26,
against `main` @ `a3dc408`.

> **Status: open pre-spec brief.** Companion to
> [`investigations/archive-format-detection-algorithm.md`](../../investigations/archive-format-detection-algorithm.md)
> (PR #263), which settles *how detection decides*. This brief is about a different
> question that came out of the same review: **what the library should say about a payload
> it found behind a prefix**, and what belongs in the public API. It is design input for a
> future OpenSpec change, not a description of current behaviour.

## The question

`open_archive("installer.exe")` finds a 7z payload 300 KiB into an executable. So does
`open_archive("holiday.jpg")` on a JPEG with a ZIP appended. Today those two produce the
*same* signal — `payload_offset > 0` — and the codebase calls both "SFX".

They are not the same claim, and the second one is arguably the more interesting: a caller
extracting from it is extracting something the file's author may never have intended to be
extractable.

## Today's vocabulary encodes the conflation

`src/archivey/internal/detection.py:115`:

```python
payload_offset: int = (
    0  # nonzero only for SFX archives (is-SFX == payload_offset > 0)
)
```

That comment is the whole semantics: **is-SFX is defined as "the offset is nonzero"**. The
surrounding names agree — `_scan_for_sfx_payload`, `detected_by="sfx_scan"`,
`ReadBackend.SFX_MAGIC`. But `SFX_MAGIC` is a table of **payload** signatures searched for
*within* a stub window; nothing in the codebase recognizes a stub *as* an extractor.

`prefixed-archive-detection` improves on this with `PrefixKind`
(`NONE` / `EXECUTABLE` / `SCRIPT` / `OTHER_FORMAT` / `UNKNOWN`) and specifies `NONE` to
correspond exactly to `payload_offset == 0`. That is a real gain — it separates *what the
prefix is* from *that there is one*. It still does not answer whether the prefix is an
extractor.

## Four cases that currently look alike

| | example | is it meant to be extracted? | today |
| --- | --- | --- | --- |
| **a. Genuine self-extractor** | 7z SFX, WinRAR SFX, makeself, shar | yes — that is the file's purpose | `payload_offset > 0`, `EXECUTABLE` / `SCRIPT` |
| **b. Executable embedding an archive as a resource** | Go `embed`, Python `zipapp`, PyInstaller onefile, Electron `asar` | no — it is meant to be *run*; the archive is an implementation detail | `payload_offset > 0`, `EXECUTABLE` |
| **c. Polyglot / appended data** | JPEG+ZIP, PNG+ZIP, ZIP appended to a signed installer | no, and sometimes deliberately concealed | `payload_offset > 0`, `OTHER_FORMAT` |
| **d. Accidental concatenation** | junk prepended to a tar; a truncated download | no — it is damage | `payload_offset > 0`, `UNKNOWN` |

`PrefixKind` separates (c) and (d) from (a)/(b). It does **not** separate (a) from (b),
which is the pair where the prefix looks identical and the intent is opposite.

## What is observable, and what is not

Intent is not observable. The library must not claim it.

What *is* observable is whether the prefix was **recognized** as a known self-extractor for
the payload format it precedes. That is evidence-backed for the cases that matter:

- **makeself** writes a literal `MAKESELF` marker plus a `#!/bin/sh` header and a
  well-known variable block;
- **shar** is a shell script with recognizable `# This is a shell archive` framing;
- **WinRAR SFX** carries RAR-specific stub markers;
- **7z SFX** ships a small set of published stub binaries.

And where it is *not* recognized, the honest answer is "unknown", not "yes".

## The proposed inversion

Today `payload_offset > 0` reads as *"this is a self-extracting archive"* — so the **least**
trustworthy case (an unrecognized prefix, categories c/d) produces exactly the same signal
as the most trustworthy one. That is backwards.

The suggestion is to report three separable facts instead of one:

1. **Is there a payload at a nonzero offset?** — `payload_offset`. A measurement.
2. **What precedes it?** — `PrefixKind`. A classification of the bytes.
3. **Was the prefix recognized as an extractor for this payload?** — *new, and absent
   today.* Evidence-backed recognition, never inference.

For (3), the shape that avoids claiming intent is a recognized-stub record rather than a
boolean:

```python
sfx_stub: SfxStub | None    # None = "a payload behind bytes we did not recognize"
```

`None` is then a genuine signal, not a default: **we found an archive behind something we
cannot identify as an extractor.** A boolean `is_sfx` cannot express that, because `False`
and "unrecognized" collapse.

This slots into the evidence-ledger model of PR #263 without a new mechanism: stub
recognition is one more `DetectionEvidence` record, with its own kind and class, and the
`sfx_stub` accessor is derived from it exactly as `confidence` and `detected_by` are.

## The diagnostics angle

Case (c) is the one with a safety edge. `dev-docs/threat-model.md` already tracks silent
wrong answers in detection; "we extracted members from a JPEG" belongs in that family.

Extracting from a source whose prefix was **not** recognized is a candidate advisory in the
same honesty channel as `PROBE_FORMAT_UNCONFIRMED`: the read may well succeed and produce
real members, and the caller should still be told that the container's outer bytes were
not something archivey understands as an extractor. Whether that is worth a code, and
whether it is `strict`-worthy, is exactly the kind of question the diagnostics
admission rule in [`2026-08-diagnostics`](../2026-08-diagnostics/diagnostics-archive-vs-usage.md)
was written to settle — report only what the caller could not determine from the declared
contract and can act on. It passes that test: the caller cannot see the prefix, and the
action (refuse, or extract to a sandbox) is real.

## The naming problem this leaves

If (1)–(3) are separated, "SFX" should stop meaning "nonzero offset". The current spelling
would have `sfx_scan` as the `detected_by` value for a JPEG+ZIP polyglot, which is simply
wrong. A rename (`prefixed_scan`? `embedded_scan`?) is cheap now and expensive after the
detection redesign ships with the term baked into `detected_by`'s public string values.

## `payload_offset` when it is not known

A second, narrower question on the same field.

`payload_offset` is **already public** — a field on `FormatInfo`, which is in
`archivey.__all__` — and PR #263 §1 actively protects that, requiring it to stay an `int`
and to "never turn unknown into zero". The unresolved part is what happens when the offset
genuinely cannot be computed within the index budget: computing an exact offset for a
prefixed ZIP may require walking the **entire** central directory, which is not bounded by
the 65,557-byte locator window.

#263 offers two options and both are unattractive:

- **pay** — a caller who wanted the *format* is charged a full central-directory walk;
- **raise a budget/incomplete error** — a successful identification is turned into a
  failure because one derived field was expensive.

A third option is worth putting on the table: keep the field an `int`, and make the
*exact* offset a separately-requested computation, so identification and offset resolution
are different asks with different costs. The evidence ledger already carries the
search-completeness information needed to say "identified; exact offset not computed",
which is the honest third answer neither option above can express.

## Not in scope here

- **How detection decides** — PR #263.
- **Which tiers run and in what order** — `openspec/changes/prefixed-archive-detection`.
- **Whether the ZIP tail probe is on by default** — gated on the backup-corpus cost
  measurement, per #263 §10/§13.

## Open questions for whoever picks this up

1. Is stub recognition worth the corpus work? It needs real samples of (a) and (b) to
   avoid a rule that fires on Go binaries.
2. Should an unrecognized prefix be an advisory, and if so is it `strict`-worthy?
3. Does the rename land before or with the detection redesign?
4. Is "identified; exact offset not computed" a state the public API should be able to
   express, and if so how — given #263's hard line against optional offsets?
