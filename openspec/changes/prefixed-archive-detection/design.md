# Design — prefixed archive detection

## The idea, in one line

**What it costs to find an archive that starts late depends on the format, so tier the
search by cost instead of applying one rule to every format.**

## Why the old rule was shaped the way it was

The shipped requirement says: *if leading bytes look like executable (`MZ` / ELF), scan for
RAR or 7z magic within a bounded forward window.* The cue is easy to misread as a
false-positive defence — a reviewer of the sibling Brotli change read it that way — but it
is not. It exists so that opening an ordinary file does not read up to `SFX_MAX` (2 MiB)
looking for a stub that is not there. `sfx.py` makes that explicit: the window is stepped in
geometric peeks of 64 KiB → 256 KiB → 1 MiB → 2 MiB, and **each peek asks for the first
N bytes again** rather than continuing. A full miss therefore hands the scanner
64 + 256 + 1024 + 2048 KiB = 3392 KiB to cover a 2048 KiB window — **1.66×**, counted by
instrumenting the loop. (`sfx.py`'s comment says "a little over 2×"; that overstates it.)

Whether that 1.66× is real I/O depends on the source, and it is worth being precise
because the tiering argument leans on it:

| source | what a repeat peek costs |
| --- | --- |
| `Path` | `_peek_prefix` reopens the file and re-reads from byte 0 — **1.66× real reads**, mostly absorbed by the page cache |
| seekable stream | `tell` / `read_exact` / `seek` back — **1.66× reads** on the file object |
| `PeekableStream` | `_fill_to` reads only the delta, so **1× I/O**; the 1.66× is the repeated `bytes(buffer[:n])` copy, and the buffer grows to the full window |

The reason it re-requests rather than continuing is the **peek contract**: detection must
leave the source positioned where the caller left it, because the backend opens it next.
`peek_more(n)` means "the first n bytes, without consuming", so there is no cursor to
continue from — not having one is the point.

That is fixable for the seekable kinds (seek to `searched - overlap`, read only the
delta), and for `PeekableStream` the I/O is already optimal — handing the scanner a
`memoryview` over the buffer instead of a fresh copy would remove the rest. It is written
the current way because one uniform `peek_more` callable serves all four source kinds,
which is a genuine simplification, and the cost was believed to be bounded and small.

Worth noting for honesty: fixing it narrows the tail-probe-versus-scan gap from about 53×
to about 32×. The tiering argument survives that comfortably — it does not depend on the
inefficiency.

Getting the rationale right is what unlocks the design. If the cue were a correctness gate,
widening it would be dangerous — it would be trading away a defence. Because it is a cost
gate, widening it is a **cost** decision, and the honest way to state that cost is as a
*population*: the cue does not change what a matching file pays, it changes **which files
match**.

Today only `MZ` and ELF enter the `SFX_MAX` path. Adding Mach-O and `#!` enrolls every
shell and Python script a caller points at — including the ordinary non-archive scripts in
a backup corpus, which is VISION's founding use case. Counted on this container's `/usr`
tree, 72 100 files:

| prefix | files | share | status |
| --- | --- | --- | --- |
| `#!` shebang | 734 | 1.0% | **newly enrolled** |
| Mach-O | 8 | 0.01% | **newly enrolled** |
| ELF | 2 837 | 3.9% | already paying |
| `MZ` | 31 | 0.04% | already paying |

So the scanned population grows by 742 files against 2 868 — about **26% more files**
paying the scan. That is the real cost move, and it is not free.

What keeps it small is that the scan is bounded by the source as well as by `SFX_MAX`: a
2 KB shell script costs 2 KB, not 2 MiB. Across those 734 shebang files the median is
2 959 B, the mean 14 781 B, and exactly **one** file reaches `SFX_MAX` at all — a total of
**10.3 MiB** of additional reads to sweep the entire tree. The population grows by a
quarter; the bytes do not, because the newly enrolled population is made of small files.

Both halves belong in the argument. "Widening is free" is wrong, and "every `#!` file now
reads 2 MiB" is wrong by two orders of magnitude in the other direction. The claim that
survives is: *widening enrolls more files, each bounded by `min(size, SFX_MAX)`, and for
the shebang population that bound is small in practice.*

Two consequences for the implementation. A tier-2 hit SHOULD short-circuit tier 3 — a
`zipapp` or Spring Boot JAR is a seekable ZIP, so the tail probe answers it and it should
never reach the scan at all, which removes the most common shebang case from the new
population. And the cost regression (task 4.8) needs a shebang row: a `#!` non-archive
source must read no more than `min(size, SFX_MAX)`, so the bound is pinned by a test rather
than by this paragraph.

## The cost asymmetry

| tier | bound | who bounds it |
| --- | --- | --- |
| tail probe (ZIP) | 65535 + 22 bytes, one seek | **the format** — the EOCD comment length is a `uint16` |
| forward scan (7z/RAR/TAR) | `SFX_MAX` = 2 MiB window; 3392 KiB scanned on a miss (1.66×, fixable to ~1×) | a constant we chose |
| exhaustive scan | the whole source | nothing |

Tier 1 is not a tuning parameter. No valid EOCD can sit further back, so searching further
cannot find anything and searching less would reject legal archives. A bound the format
hands you can run unconditionally; a bound you invented needs a reason to run.

## What this fixes, measured on `main`

| file | `detect_format` today | `open_archive(format=ZIP)` today |
| --- | --- | --- |
| `zipapp` `.pyz` | `FormatDetectionError` | opens, both members, contents intact |
| Spring Boot executable JAR | `FormatDetectionError` | opens |
| JPEG + appended ZIP | `FormatDetectionError` | opens |
| makeself `.run` (script + tar.gz) | `FormatDetectionError` | fails — genuinely needs an offset |

The first three are pure detection bugs: the reader already works, and stdlib `zipfile`
opens all three, so archivey is currently worse than `zipfile` on a file the standard
library itself produces. The cue never fires because the prefix is `#!`, not `MZ`.

## Ordering: strength of evidence first, cost second

> Since this section was written, an independent design analysis
> (`dev-docs/investigations/archive-format-detection-algorithm.md`, added by PR #263) reviewed it and was
> accepted as redesign input. It **agrees** that far magic must precede the content probes
> and calls the bootable-ISO reproduction decisive. It **disagrees** with two things this
> change kept: that all near magic deserves one `CERTAIN`, and that first-match-wins is a
> sound selection rule. Both are now marked provisional in the spec rather than quietly
> retained. It also found an implementability gap nobody here had: needles carry an anchor
> offset inside their own format, so a TAR `ustar` hit is 257 bytes past its candidate
> origin, and the current peek primitive cannot express a candidate-relative view at all.

Writing the tiers down forced the question of where they sit relative to everything else,
and answering it surfaced a defect that has nothing to do with prefixes.

The rule has to be **evidence strength first, cost only as a tie-break between comparable
signals.** A weak signal placed early answers first, and the strong one is never asked — so
ordering by cost alone silently trades correctness for latency. Ranked by what each actually
establishes:

| evidence | proves | measured |
| --- | --- | --- |
| exact magic at a fixed offset, near **or far** | specific bytes in a specific place | ISO's 5-byte `CD001` collides at ~2⁻⁴⁰ |
| validated structural hit (tail probe, 7z/RAR scan) | magic **and** self-consistency | near-certain by construction |
| content probe | a bounded decode did not fail | **8.2%** of arbitrary binary data, **3.5%** of a real `/usr` tree, ~0.15% after the framing gate |
| extension | what someone named the file | nothing about the content |

The content probe is the weakest signal archivey has, by a wide margin and by measurement.
Shipped, it runs **fourth of five — ahead of far magic.** That inverts the rule, and ISO is
where it bites: ISO 9660 reserves its first 32 KiB as a system area for a bootloader, so
every bootable or hybrid image has real executable code sitting exactly where detection
peeks, and executable code is the data class the Brotli probe accepts.

Reproduced on a genuine `pycdlib`-built ISO, changing **only** the reserved area — the
filesystem stays byte-identical, and other tools keep reading it:

| system area | `detect_format` | `open_archive` |
| --- | --- | --- |
| zeroed | `ISO` / `CERTAIN` / `magic` | lists `README.`, reads correctly |
| boot-code-shaped | **`BROTLI` / `GUESS` / `content_probe`** | one fabricated `*.uncompressed`; read raises `CorruptionError` |

Exact magic was available at a known offset the entire time and was never consulted. This is
the same silent-wrong-answer shape as the Mach-O defect, reached from the other end — there
a missing cue let a probe claim a stub, here a late tier let a probe claim a whole
filesystem.

So far magic moves to second, right behind near magic. The cost is one bounded peek on
sources that nothing cheaper identified, and it is gated on the source being at least as
large as the window — `source_byte_size()` is already computed at the probe step for the
framing gate, so hoisting it is free, and no ISO is under 32 KiB. Small files pay nothing.

**A note on the extension, because "extension versus probes" is a false dichotomy.** The
extension is read up front and used as a *corroborator* throughout: it is what
`_brotli_probe_confidence` consults to split `PROBABLE` from `GUESS`, and what a format
conflict is raised against. It is last to *answer* and available *throughout*, and those two
facts are not in tension — it never outvotes evidence drawn from the bytes, but it does
sharpen what that evidence is worth. The live requirement's "magic → extension → probes"
reads as though the extension competes with the probes, which is what made it easy to
restate wrongly.

## Why the scan can trust itself

Not the reason to scan — cost decides that — but the reason a hit can be reported at
`CERTAIN` rather than hedged. Both scanned formats carry their own proof:

**7z.** The 32-byte signature header contains `StartHeaderCRC`, a CRC32 over the 20-byte
StartHeader that follows it, and that StartHeader gives `NextHeaderOffset` /
`NextHeaderSize`. Verified against a real archive behind stubs of 23 bytes, 4 KiB and
100 KB: CRC valid in every case, and `offset + 32 + NextHeaderOffset + NextHeaderSize`
landed exactly on EOF in every case. Combined, a false hit needs a 48-bit magic *and* a
32-bit CRC *and* a size agreement.

Use `<=` for the gate and `==` as the tiebreak. Appending 16 bytes to a 7z leaves it
perfectly readable while breaking the exact-EOF equality, and some SFX tools append
configuration after the payload — measured, not assumed.

**RAR 5.** The 8-byte marker is followed by a main archive header with its own CRC32,
which validated at every stub offset tried.

This is the same shape as `brotli-probe-framing-gate` (proposed in PR #255): *the thing declares
where it ends, so check that against the source.* Worth noting the recurrence — it is
becoming this codebase's standard way to make a cheap signal trustworthy.

## Why the tail probe does not generalise

It was tempting to make tier 2 "probe the last 64 KiB for anything". It only works for ZIP:

- **7z** puts its metadata at the end, but the *pointer* to it lives at offset 12, inside
  the signature header at the start. From the tail alone there is nothing to find — no
  magic, and no way to know where the header begins.
- **RAR** has no tail magic either. Its last records are service blocks — which is where
  quick-open caching lives — but RAR 5 encodes header sizes as variable-length integers, so
  the chain cannot be walked backwards. Quick-open exists to avoid re-reading *file*
  headers scattered through the archive, not to locate the archive.
- **tar and raw compressed streams** have neither a tail structure nor self-validation.
  These stay the genuinely hard case and are the reason the shebang cue matters.

Checked against archives produced here by `7z a`, `rar a`, and `rar a -qo+`; the plain and
quick-open RARs had byte-identical tails.

## Open question this change does not settle

**Are there prefixed 7z/RAR files in the wild that are not self-extracting executables?**
Everything found so far says no — `7z.sfx` / `7zCon.sfx` are PE, `rar -sfx` produces PE on
Windows and ELF on Linux, and every non-executable prefix encountered was ZIP or tar. If
that holds, tier 3's cue is sufficient in practice and the exhaustive scan stays a rarity.

The known exception is script-wrapped payloads, which is why the cue gains `#!`. The
unknown is DOS/Windows-era installers and media images, which the maintainer plans to
survey. If that corpus turns up a shape the cue misses, the answer is to widen the cue
again — it is a cost gate, so widening is cheap — not to abandon the tiering.

**This is why there is no ADR yet.** Per `CONTRIBUTING.md`, a decision that still needs an
open-questions section is not an ADR. The load-bearing "why" here — *tier detection cost by
what the format guarantees* — is stable and should become one once this change is applied;
this file is written so that write-up is a summary rather than a re-derivation.

## Sequencing

`sfx-format-detection` (#254) had to land first — this change rewrites the requirement that
one modifies, and depends on its `payload_offset` plumbing through `open_archive` and the
backends. Both are done: #254 merged as `6e71eba` and #258 archived the change into the live
specs at `da427a0`, so the deltas here are written against shipped text.

The archive also promoted a second requirement, *Executable-looking prefixes must not
silently become a wrong stream format*, which this change now modifies too: it enumerates
the cue as `MZ` / `\x7fELF`, and widening that set is the fix for the macOS defect below.
`brotli-probe-framing-gate` (PR #255) also modifies that same executable-prefix
requirement — for its confidence rows and its probe-tightening paragraph, disjoint from the
cue enumeration this change rewrites. Since OpenSpec replaces a MODIFIED requirement whole,
the two are **not** archive-order independent: whichever lands second rebuilds on the
other's text.

**That order is now settled: the framing gate archived first, in #262 (`49d8b4a`), so this
change is the one that rebuilt.** The MODIFIED block below has been rebased onto the
resulting live text, and task 0.3 lists the five things it inherited — the narrowed
threshold prohibition, the three-clause residual paragraph with its figures, three
confidence rows in place of one, the changed attribution line, and the normalised quotes.

The rebuild was done at rebase time rather than at archive time on purpose. Doing it later
would land a wholesale rewrite of a requirement in the same PR as the implementation, where
a reviewer would have to separate "text inherited from a sibling change" from "behaviour
this PR is proposing" — and those read identically in a diff. Doing it now keeps this PR
proposal-only and leaves the implementation PR to contain only code.

Both changes edited **disjoint parts** of that requirement — the framing gate the
probe-tightening paragraph and the confidence rows, this change the cue enumeration and the
Mach-O defect — so the merge lost nothing. That was verified rather than assumed: the
rebuilt block was diffed against live, and every remaining difference is an edit this change
intends to make.

**The far-magic hoist has been taken out of this change and shipped by
`detection-format-gaps`.** That change removes the LZMA Alone zero-dictionary guard, which
is unsafe until far magic precedes the content probes, and it could not wait for this one
now that this is sequenced behind `detection-evidence-ledger`. Two in-flight changes
MODIFYing the same requirement is the archive-order conflict the investigation §14 warns
about, so on revision this change **drops** its far-magic Impact bullet and the far-magic
step from its `Magic-first detection…` delta, and tasks 3.4c–3.4e go with them. The
bootable-ISO reproduction above stays useful: it is the justification recorded in
`detection-format-gaps`'s design for making the move.
