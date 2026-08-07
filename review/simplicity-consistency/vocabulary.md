# Vocabulary & surface consistency

`brief.md` §C — cheap now, permanent after `0.2.0`. Short file: most of §C turned out
to be already settled, and saying so is the point.

## Settled — do not re-open

### C1 `MemberStreams` vs `open_stream(seekable=…)` — **already decided**

The brief tagged this a `CONFIRMED` split. It is not one any more: `archive-reading`
§"Declared member-stream capabilities" decides it explicitly, and the code matches.

> `open_stream` SHALL keep its `seekable: bool` parameter, and both entry points SHALL
> use the same `seekable` vocabulary for the same concept; concurrency has no meaning
> for a single standalone stream, so `open_stream` MUST NOT gain a concurrency
> parameter.
>
> The `MemberStreams` flag type SHALL remain publicly exported as the internal
> representation the booleans map to at the entry point. It is no longer an input to
> `open_archive`.

Observed: `open_archive(source, *, format, streaming, seekable_members,
concurrent_members, password, encoding, config)` and `open_stream(source, *, format,
seekable, config)`. `MemberStreams` is exported and is not an input
(`core.py:181-185` maps the booleans to it). `open_archive(p, member_streams=…)` →
`TypeError`, as the spec's matrix requires.

**Verdict: fine.** The remaining `seekable_members` / `seekable` difference is the
*specced* one — the flag is named for what it applies to, and both spell the capability
`seekable`.

*(The real problem with `seekable_members` is not its name. It is **P1**: the flag
changes metadata as well as seekability.)*

### C4 exception roots — not reopened

`ArchiveyUsageError` / `ConcurrentAccessError` outside `ArchiveyError` is ADR 0012. The
review checked only the in-scope question — whether call sites put the *wrong* error on
the wrong side of the tree — and found none. Every usage-shaped condition raised
`ArchiveyUsageError` and every archive-shaped one raised an `ArchiveyError` subclass,
across all 24 measured format keys. The one boundary case (`open_stream` on a directory)
is about the *message*, not the root — **P6**.

---

## Live items

### C5 CLI import paths — **P7**, trivial, and isolated

Pre-answered by the brief: import from the public path.

| File | Import |
|---|---|
| `src/archivey/cli/extract_cmd.py` | `from archivey import ExtractionProgress` (public) ✅ |
| `src/archivey/cli/progress.py:10` | `from archivey.internal.extraction_types import ExtractionProgress` |
| `src/archivey/cli/test_cmd.py:22` | `from archivey.internal.extraction_types import ExtractionProgress` |

The brief asked the useful question: **is the pattern isolated?** It is. `ExtractionProgress`
is the only type imported from `internal/` anywhere in `src/archivey/cli/`, and it is
already in `archivey.__all__`. No second pattern emerged, so the negative result the
brief recorded stands and the "CLI reaching into `internal/` is usually an API gap"
heuristic still finds nothing here.

Two-line fix; no API change; no spec change.

### C2 extras naming vs capability — **re-verified, fine**

The `[recommended]` consolidation landed and the install hints are generated from
`MissingComponent.install_hint` per component (`registry.py:233`), not from a
format-keyed table. There is no path that can print "install `[7z]`" for a ZIP
Deflate64 member, because no `[7z]` extra exists to name. **Fine.**

### C3 CLI defaults vs library defaults — **not re-litigated**

`must-explain` #23 records that the interactive CLI's defaults diverge from the
library's. The archived `cli-product` review owns that ground and the brief says not to
re-litigate P4/`--json`. The review confirms only that the divergence is a *product*
choice expressed in the CLI layer rather than a second set of library defaults: the CLI
constructs explicit arguments and passes them down; it does not carry a shadow
`ArchiveyConfig` with different values. **No finding.**

---

## One vocabulary observation that is not on the brief's list

`FormatAvailability` names its axes `format` / `support` / `missing`. A caller asking
"can I read this from a pipe?" finds no vocabulary for the question at all — not a
different spelling, an *absent* one (**P5**). If Q5 is answered by adding a capability
axis, the naming choice lands here and freezes with it; `StreamCapability` already
exists in `__all__` for the cost receipt's source-shape axis, and reusing that
vocabulary rather than inventing a second one is the cheap move.
