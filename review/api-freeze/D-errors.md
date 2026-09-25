# D. The error tree at the boundary

26 classes in `archivey.exceptions`, all exported, all now on `docs/api.md` in tree
order (`#465`). The contract (`CONTRIBUTING.md`, ADR 0012, and since `#465` the guide's
"What is translated, and what passes through") is: recognised library and codec
exceptions become `ArchiveyError` subclasses with the original as `__cause__`;
`OSError`, `KeyboardInterrupt` and `MemoryError` pass through; anything else from inside
archivey is a bug and propagates raw; `ArchiveyUsageError` is outside the tree on
purpose. Argument validation was swept in `#380`/`#382` and is not re-tested here.

## The tree

```
ArchiveyError
├── OpenError
│   ├── FormatDetectionError
│   ├── UnsupportedFormatError        format known, no backend (missing package)
│   └── StreamNotSeekableError
├── ReadError
│   ├── CorruptionError
│   ├── TruncatedError
│   ├── EncryptionError
│   └── LinkTargetNotFoundError
├── ExtractionError
│   ├── FilterRejectionError
│   │   ├── PathTraversalError
│   │   ├── SymlinkEscapeError
│   │   ├── SpecialFileError
│   │   ├── UnportableNameError
│   │   └── DeceptiveNameError
│   ├── NameCollisionError            raised only under abort_on=
│   └── NameRewrittenError            raised only under abort_on=
├── ResourceLimitError
├── UnsupportedFeatureError           recognised but unhandled variant/codec
├── UnsupportedOperationError         not valid for this archive/mode
├── PackageNotInstalledError
├── DiagnosticRaisedError             a policy escalation; carries the Diagnostic
└── WriteError                        no write API exists (demoted from __all__)

ArchiveyUsageError                    outside the tree, on purpose
└── ConcurrentAccessError
```

## Is the shape right to freeze?

**Yes.** The granularity is the one a caller `except`s on: "could not open" versus
"could not read" versus "could not write to disk" versus "you hit a limit", and one
level below each. The July review said the same and nothing has moved a class between
branches since. Three points are worth a decision or a sentence before the tag.

### The three name errors

`DeceptiveNameError` is a **filter rejection**: under `STRICT` and `STANDARD` the
member is blocked because its name displays as something it is not (a bidi override).
`NameCollisionError` and `NameRewrittenError` are **run outcomes** that a caller
opted into raising with `abort_on=`; without the opt-in they are rows on the
extraction report, never raised. So the taxonomy is precise, not arbitrary: one is a
verdict about the name, two are the caller's chosen tripwires. What makes it read as
arbitrary is only that all three end in `NameError`. The guide's exception table already
says "raised only when you opted in with `abort_on`" for the two, and `DeceptiveNameError`
sits under `FilterRejectionError` where the other four rejections are. Verdict: keep all
three, no rename.

### D-1 · Medium · The guide's exception table is missing eight classes

`docs/errors-and-diagnostics.md` §The exception tree names 15 of the 26. Absent:
`ReadError` (the parent a caller is told to catch at line 214, "`except
archivey.ReadError` catches both", so it is load-bearing and not in the table),
`ExtractionError`, `LinkTargetNotFoundError`, `UnportableNameError`,
`DeceptiveNameError`, `UnsupportedFeatureError`, `DiagnosticRaisedError` and
`WriteError`. `#465` put all of them on `api.md`, so the reference is complete; the
guide, which is where a user decides what to catch, is not.

**Recommendation.** Add the eight rows. `ReadError` first, since the page already relies
on it.

### D-2 · Low · Three `Unsupported*` names whose difference is not in the name

`UnsupportedFormatError` (an `OpenError`: the format is known and no backend can open
it, usually a missing package), `UnsupportedFeatureError` (a variant or codec this
backend does not handle: BCJ2, PPMd without the package), and
`UnsupportedOperationError` (the call is not valid here: `members()` on a streaming
reader, `seek()` where the format cannot). All three are correct and distinct. A user
meeting one in a traceback cannot tell from the word which of the three it is, and
`PackageNotInstalledError` overlaps the first two in practice (7z PPMd without the
package raises `PackageNotInstalledError` on read, per `how-it-works.md`; ISO without
`pycdlib` raises `UnsupportedFormatError` at open).

**Recommendation.** No rename: each name is right in isolation and a rename at a
freeze buys nothing. Two sentences in the guide table distinguishing them, which D-1
supplies. Recorded so the next reviewer does not propose merging them.

### D-3 · Low · No public entry point has a "Raises" section

`open_archive`, `open_stream`, `extract` and `detect_format` describe what they raise
in prose (the `open_archive` docstring names `ArchiveyUsageError` and `EncryptionError`
in context; `detect_format` names `FormatDetectionError`). None has a `Raises:` block the
API page would render as a list. The guide's table is the contract, and after D-1 it is
complete. A per-function list would duplicate it.

**Recommendation.** Nothing, unless the maintainer wants the API page self-contained.
If so, a `Raises:` block on the three entry points listing the branch (`OpenError`,
`ReadError`, `ExtractionError`, `ResourceLimitError`, `ArchiveyUsageError`) rather than
every leaf.

## What is actually fine

- **`ArchiveyUsageError` outside the tree** is documented on three pages and in ADR
  0012, pickles, and is what `open()` on a directory member raises (a caller mistake,
  correctly classified).
- **`DiagnosticRaisedError` inside the tree**, so `except ArchiveyError` catches a
  policy escalation; and `escalate_as` keeps the typed error (`CorruptionError` with
  `format_unconfirmed=True`) where one already exists, so a `RAISE` policy never hides
  a more specific type behind the generic one.
- **Messages are escaped at construction; structured fields stay raw.** The same rule
  on `ArchiveyError` and `Diagnostic`, stated in both docstrings and in the guide.
- **Every exception pickles and copies** with its attributes, so a `ProcessPoolExecutor`
  worker's error arrives as the same type. Tested.
- **`CorruptionError` vs `TruncatedError` is documented as a best-effort guess**, with
  `ReadError` as the thing to catch. Right, and honest.
