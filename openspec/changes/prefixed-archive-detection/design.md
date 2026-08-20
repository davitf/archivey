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
widening it would be dangerous. Because it is a cost gate, widening it is free wherever the
cost does not change — and irrelevant wherever the cost was never there to begin with.

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

This is the same shape as the sibling `brotli-probe-framing-gate`: *the thing declares
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

`sfx-format-detection` (#254) should land first. This change rewrites the requirement that
one modifies, and depends on its `payload_offset` plumbing through `open_archive` and the
backends. Landing this first would mean writing the same requirement twice.
