# Should the caller care what is inside this archive?

**Written to be read standalone.** No prior knowledge of the codebase is assumed; every
fact you need is inline, with file references if you want to check one. Dated 2026-08-26,
against `main` @ `a3dc408`.

> **Status: open pre-spec brief.** Companion to
> [`investigations/archive-format-detection-algorithm.md`](../../investigations/archive-format-detection-algorithm.md)
> (PR #263), which settles *how detection decides*. This brief is about a different
> question raised in the same review: once archivey has identified an archive, **what
> should it tell the caller about the archive's role** — and what belongs in the public
> API. Design input for a future OpenSpec change, not a description of current behaviour.

## The question

`open_archive()` succeeds on all of these:

- a backup `.zip` a user made of their photos;
- a `.docx`, which *is* a document, whose members are its internal XML parts;
- a `.jar`, whose members are an application's class files;
- LibreOffice's `images_colibre.zip`, 4 522 icon resources belonging to the program;
- a JPEG with a ZIP appended, which the photographer never intended anyone to open;
- a self-extracting installer, which is meant to be extracted.

Archivey reads all six identically, and says nothing to distinguish them. For the founding
use case in `VISION.md` — indexing a backup corpus — only the first is something a caller
wants to recurse into. Being able to open the rest is a feature; being *told* they are
different is the missing part.

## What the tree actually holds

Measured 2026-08-26 on this container, opening every file starting `PK` under
`/usr/share`, `/usr/lib`, `/usr/local`, `/opt` and the repo — **643 readable ZIPs**:

| recognized by | count | extensions seen |
| --- | --- | --- |
| `META-INF/MANIFEST.MF` (JAR) | 363 | `.jar` |
| `mimetype` stored first (ODF / EPUB family) | 176 | `.odt`, `.ods`, `.odb`, `.otg`, `.otp`, `.ots`, `.bau`, `.dat` |
| `*.dist-info/WHEEL` (Python wheel) | 11 | `.whl` |
| **no marker matched** | **91** | **85 × `.zip`**, 2 × `.jar`, `.sym`, `.sop`, `.sob`, `.stw` |

The unmarked bucket is the interesting one. It is **not** user data:

```
images_colibre_dark.zip      4520 members   first: avmedia/res/av02048.png
images_colibre.zip           4522 members   first: avmedia/res/av02048.png
images_colibre_svg.zip       4521 members   first: avmedia/res/av02048.svg
ct.sym                      20621 members   first: 8/java.activation/javax/...
standard.sop                   20 members   first: Pictures/
```

LibreOffice icon themes, a JDK symbol file, StarOffice palettes. **Zero of the 643 ZIPs on
this system are a user-data archive.** Every one is a packaging or resource format.

Three consequences, and they are the argument for the whole brief:

1. **"Should I care what is inside?" is the common case, not an edge case.** 86% are
   marker-recognizable packaging formats, and the remaining 14% are program resources.
2. **The extension is useless for this.** `.zip` is the single most common extension in the
   unmarked bucket — 85 files — and treating any of them as user data would be wrong. Two
   `.jar` files have no manifest, so the extension is not sufficient in the other direction
   either.
3. **Marker recognition cannot partition the world.** A LibreOffice icon bundle and a
   backup of someone's photos are both "a ZIP of files with no marker". Nothing in their
   structure separates them. That is a real limit, and it decides the shape below.

> **Caveat on the sample.** One Linux container, heavy on LibreOffice and the JDK. A user's
> `~/Downloads` or a backup corpus would invert the ratio. The measurement supports "this
> class is large and currently invisible"; it does not support any claim about relative
> frequency in general.

## The proposal: an archive role, recognized rather than inferred

Report what was **recognized**, from marker members at known paths:

| role | recognized by | examples |
| --- | --- | --- |
| `DOCUMENT` | `mimetype` stored first; `[Content_Types].xml` at root | `.docx`, `.odt`, `.epub` |
| `APPLICATION` | `META-INF/MANIFEST.MF`, `AndroidManifest.xml`, `*.dist-info/WHEEL`, `manifest.json`, `__main__.py` | `.jar`, `.apk`, `.whl`, `.crx`, `.pyz` |
| `EMBEDDED` | the archive sits at a nonzero offset behind a prefix that was not recognized as an extractor | ZIP inside an installer |
| `UNKNOWN` | nothing matched | a backup `.zip`; a LibreOffice icon bundle |

Four rules that follow from the measurement:

**`UNKNOWN` is the default and is not a synonym for "data".** The 91 unmarked files above
prove the point: they are all program resources, and a caller who read `UNKNOWN` as "user
data, go ahead and index" would ingest 4 520 icon PNGs per file. Archivey cannot tell a
resource bundle from a backup, so it must say "not recognized", never "this is data".

**This is a recognizer, not a classifier.** It answers "did we identify a packaging role?"
Callers decide what to do with `UNKNOWN`; the library does not decide for them.

**Content decides, extension corroborates.** A `.zip` containing `[Content_Types].xml` is
an OOXML document; a `.docx` full of arbitrary files is not. The same rule the detection
redesign applies everywhere else — and here it is load-bearing rather than theoretical,
since 85 packaging files are named `.zip` and two `.jar` files have no manifest.

**Advisory, never a refusal.** Opening a `.docx` to look inside is legitimate and stays
legitimate. This is metadata that lets a caller decide, in the spirit of `format=` being
an override that gets reported rather than overridden.

### Where it lives, and what it costs

Role recognition needs the **member list**, so it is an `ArchiveInfo` fact, not a
`FormatInfo` one — it cannot be part of `detect_format()`.

Cost is format-dependent and needs stating in the spec rather than assuming:

- **ZIP** — free in practice. The central directory is read at open, and every marker above
  is a name lookup in it. The ODF/EPUB rule additionally needs the *first* entry's name and
  compression method, which the same structure carries.
- **TAR** — not free. There is no index; recognizing a marker means reading forward. For a
  compressed TAR that means decompressing. A role check that silently costs a scan would
  violate the honest-cost contract, so it likely must be opt-in or deferred there.

## The narrower case this started from: prefixes and "SFX"

The question arrived as "not all concatenated archives are meant to be extracted", and it
is the same question in a corner where today's vocabulary is actively wrong.

`src/archivey/internal/detection.py:115`:

```python
payload_offset: int = (
    0  # nonzero only for SFX archives (is-SFX == payload_offset > 0)
)
```

**"Is this a self-extracting archive" is defined as "is the offset nonzero".** The
surrounding names agree — `_scan_for_sfx_payload`, `detected_by="sfx_scan"`,
`ReadBackend.SFX_MAGIC` — and `SFX_MAGIC` does not help: it is a table of *payload*
signatures searched *within* a stub window. Nothing recognizes a stub **as** an extractor.

Cases that currently look alike:

| | example | meant to be extracted? |
| --- | --- | --- |
| a. genuine self-extractor | 7z SFX, WinRAR SFX, makeself, shar | yes — that is the file's purpose |
| b. the archive *is* the program | Python `zipapp` / `pex` / `shiv` (shebang + ZIP), JAR launchers | no — it is meant to be *run* |
| c. polyglot / appended data | JPEG+ZIP, ZIP appended to a signed installer | no, sometimes deliberately concealed |
| d. accidental concatenation | junk prepended to a tar | no — that is damage |

`prefixed-archive-detection`'s `PrefixKind` (`NONE` / `EXECUTABLE` / `SCRIPT` /
`OTHER_FORMAT` / `UNKNOWN`) separates (c) and (d) from (a)/(b) — a real gain. It does not
separate **(a) from (b)**, where the prefix looks identical and the intent is opposite.

**Under the role framing, that separation stops being a prerequisite.** (b) is
`APPLICATION` by its own contents — a `zipapp` has `__main__.py`, a JAR has a manifest — so
it is recognized without ever classifying the wrapper. What is left for the prefix to say
is only "there is a payload here, behind bytes we did not recognize", which is
`PrefixKind.EXECUTABLE` plus role `EMBEDDED`. That is honest and actionable, and it is the
"leave only an executable field" fallback rather than a defeat.

Stub recognition (makeself's `MAKESELF` marker, shar's framing, WinRAR and 7z stubs) stays
available as a later refinement for telling (a) from the rest — but it is now optional, and
it should stay evidence-backed. Where a stub is not recognized, the honest answer is
"unrecognized", never "not an SFX".

### The naming deadline

If these separate, **"SFX" must stop meaning "nonzero offset"** — `detected_by="sfx_scan"`
for a JPEG+ZIP polyglot is simply wrong, and #263 is about to make `detected_by`'s string
values a public commitment. A rename (`prefixed_scan`? `embedded_scan`?) is cheap now and
expensive afterwards.

### Two measurements on the prefix case

Scanned every ELF/PE file under `/usr/bin`, `/usr/lib`, `/usr/local`, `/opt` —
**3 320 executables**:

- **Zero** carry a real appended ZIP. Whatever the priority of prefixed-archive detection
  is, it is not driven by ordinary system binaries.
- **Six** contain the bytes `PK\x05\x06` in their tail, and **all six are false positives**:
  `zip`, `zipnote`, `zipsplit`, `zipcloak`, `libzip.so`, `librevenge-stream.so` — programs
  that *manipulate* ZIPs and carry the signature as a string constant. Every one parses to
  nonsense (entry counts of 19 280–55 381, central-directory offsets past EOF, trailing-byte
  counts that do not reconcile).

The second is a concrete instance of why #263 requires a **validated** structural hit rather
than a located one: a tail probe that locates without validating would claim `/usr/bin/zip`
as a prefixed ZIP, on a directory every developer machine has.

It also means a corpus for stub recognition has to be **deliberately collected** — a normal
system contains none of the samples it would need to be calibrated or tested against.

## `payload_offset` when it is not known

A narrower question on the same field.

`payload_offset` is **already public** — a field on `FormatInfo`, which is in
`archivey.__all__` — and PR #263 §1 actively protects that, requiring it to stay an `int`
and to "never turn unknown into zero". The unresolved part is what happens when an exact
offset cannot be computed within the index budget: for a prefixed ZIP it may require walking
the **entire** central directory, which is not bounded by the 65 557-byte locator window.

#263 offers two options and both have a real cost:

- **pay** — a caller who wanted the *format* is charged a full central-directory walk;
- **raise a budget/incomplete error** — a successful identification becomes a failure
  because one derived field was expensive.

A third is worth considering: keep the field an `int`, and make the *exact* offset a
separately-requested computation, so identification and offset resolution are different asks
with different costs. The evidence ledger already carries the search-completeness record
needed to express "identified; exact offset not computed", which is the honest answer
neither option above can state.

## Not in scope here

- **How detection decides** — PR #263.
- **Which tiers run, in what order** — `openspec/changes/prefixed-archive-detection`.
- **Whether the ZIP tail probe is on by default** — gated on the backup-corpus cost
  measurement, #263 §10/§13.
- **Identifying non-archive file types.** #263 §12 rules that archivey "should not become a
  general file-type detector", and this proposal is deliberately inside that line: it
  classifies archives archivey has already opened, by their own member structure. It never
  identifies a file archivey cannot read. The boundary is worth stating in the spec, because
  it is the first objection anyone will raise.

## Open questions

1. Is `EMBEDDED` a role, or a combination of `PrefixKind` plus role `UNKNOWN`? The latter is
   fewer concepts; the former is easier for a caller to act on.
2. Should extraction from an unrecognized prefix (case c/d) emit an advisory, in the same
   honesty channel as `PROBE_FORMAT_UNCONFIRMED`? It passes the admission rule from
   [`2026-08-diagnostics`](../2026-08-diagnostics/diagnostics-archive-vs-usage.md) — the
   caller cannot see the prefix, and the action (refuse, or extract to a sandbox) is real.
3. What is the marker set, and who owns it? Per-backend declaration like `MAGIC` and
   `EXTENSIONS`, or a central table? Backends already own their detection data.
4. Does the TAR cost make role recognition opt-in, or does it stay ZIP-only until someone
   needs otherwise?
5. Does the `sfx_scan` rename land before or with the detection redesign?
6. Can the public API express "identified; exact offset not computed", given #263's hard
   line against optional offsets?
