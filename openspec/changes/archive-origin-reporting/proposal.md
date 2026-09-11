## Why

An archive that starts after byte zero is opened correctly today, and then archivey
forgets where it started. Two things follow, both measured on `main` (`056c429`) with a
4 224-byte `MZ` stub in front of a real 7z / RAR / ZIP payload:

**1. The caller cannot ask.** `ArchiveInfo` carries format, version, solidity, member
count, comment, encryption, multivolume, cost and `extra` — nothing about the payload
origin. The reader knows it (`SevenZipReader._origin`, `RarReader._origin`,
`RarArchive.sfx_offset`) and drops it on the floor. Archivey's own CLI works around this by
calling `detect_format()` itself (`cli/info_cmd.py:52`) and printing `detected.payload_offset`
— so the file is detected twice, once by the CLI and once inside `open_archive`, because the
reader will not answer.

**2. The two open paths know different amounts, and only one can be asked.** The same
SFX file opened two ways:

| | `payload_offset` reachable by caller | parser scan runs |
| --- | --- | --- |
| auto-detect | only by detecting the file a second time yourself | **no** (magic already at the offset) |
| `format=SEVEN_Z` / `format=RAR` | **no** | yes — the parser finds the origin, then discards it |
| `format=ZIP` | **no** | none — stdlib `zipfile` self-adjusts; archivey never learns the offset |

The forced path is the sharper gap: the parser *does the work of finding the origin* and
throws the answer away. A caller who passes `format=` — the path that skips detection
precisely because they already know the format — ends up with strictly less information
than one who did not.

`archive-reading` states the divergence as a rule rather than a defect: *"An explicit
`format=` that bypasses detection retains each backend's own start-offset / SFX rules."*
That was the right call when the offset was only an internal open-time input. Once it is
reportable metadata, "each backend's own rules" is a parity hole (§2 *no surprises*): the
same archive gives a different answer depending on which door the caller used.

**Why the resolution logic is also worth touching.** Origin resolution is done three
different ways for the three formats that can carry a prefix:

| | mechanism | forced-path fallback |
| --- | --- | --- |
| ZIP | `SlicingStream(handle, start=start_offset)` — a real view | none; relies on stdlib `zipfile` self-adjusting past the stub |
| 7z | `_shared.view(start_offset)`, then `start_offset + find_signature_offset(probe)`, rebased through `_view` | bounded forward scan |
| RAR | `self._origin = start_offset` plain, no view (`unrar` needs the original path) | bounded forward scan **plus** RAR4/RAR5 version resolution |

The *scanning* is already shared (`internal/sfx.scan_for_magic`, one `SFX_MAX`). What is
triplicated is the wrapper around it — "fast-path read, else scan, else raise" — in two
shapes with different return types (`int` vs `tuple[version, offset]`) and different error
messages, plus a third format that does not participate at all. That is also why the
answer has nowhere to go: there is no one place that produces it.

`format-7z` specifies its SFX/start-offset behaviour; `format-rar` specifies none, despite
the RAR parser having carried the same scan longer.

## What Changes

- **`ArchiveInfo` gains `prefix_kind` and `payload_offset`**, so the origin is
  archive-level metadata a caller can read from the reader, on **either** open path. The
  CLI reads the origin from `reader.info` instead of from its own detection result.
- **`payload_offset` is `int | None`**, with `None` meaning *not established* — the honest
  answer for an empty ZIP behind a prefix, which has no local file header to measure from.
  Absence is data, not a zero that would falsely claim "starts at byte 0".
- **`prefix_kind` reuses `PrefixKind`** from `prefixed-archive-detection` (`NONE` /
  `EXECUTABLE` / `SCRIPT` / `UNKNOWN`) rather than a new `is_sfx: bool`. A bool cannot express
  *not established*, and — more importantly — cannot separate a self-extracting archive from
  one merely embedded in something else, which is the distinction that change added the enum
  for. See Decisions.
- **One origin resolver.** A shared `resolve_payload_origin()` in `internal/sfx.py`
  replaces `sevenzip_parser.find_signature_offset` and `rar_parser._find_sfx_header`:
  fast-path read at the open position, bounded scan on a miss, `CorruptionError` on a miss
  past the bound. It returns the `MagicHit` that `detection-prefix-workspace` introduced,
  so RAR's version resolution falls out of `hit.needle` instead of a second code path.
- **Backends report the origin they resolved** back to the reader, which is what lets the
  forced path report the same `payload_offset` and `prefix_kind` as the detected one. A
  forced open classifies the prefix from one short read of the source's leading bytes — the
  same pure function detection applies — so the two doors agree on both fields.
- **`format-rar` gets the SFX/start-offset requirement it never had**, stated as the same
  contract `format-7z` already carries, so the two are specified as one behaviour rather
  than two coincidences.
- **Every format that can receive a `start_offset` reports one**, not just ZIP / 7z / RAR.
  Sequencing this after `prefixed-archive-detection` means TAR and the single-file codecs
  become prefix-capable (a makeself `.run` detects as `TAR_GZ` at the gzip offset), so a
  census frozen at today's three would recreate the same hole for them.

Not in scope: changing *when* a scan runs, widening the cue set, or the ZIP tail probe —
those are `prefixed-archive-detection`. This change moves no detection tier and changes no
detection answer.

## Capabilities

### New Capabilities

### Modified Capabilities

- `archive-data-model` — `ArchiveInfo` gains `prefix_kind` and `payload_offset`, with the
  `NONE` ↔ `0` and `None` ↔ `None` pairings stated as an invariant, and `payload_offset`
  defined as an offset from the start of `source`.
- `archive-reading` — the payload-offset hand-off requirement gains its return half:
  backends report the origin they used, and `format=` no longer means "less information".
- `format-7z` — the SFX requirement is restated against the shared resolver and gains the
  reporting obligation.
- `format-zip` — a prefixed ZIP's origin is reported on both open paths, derived from the
  central directory the reader already parsed; not-established only for an empty archive.
- `format-rar` — gains an explicit start-offset / SFX requirement, matching `format-7z`.
  The capability already exists; only the requirement is new.

## Decisions

- **Enum, not `is_sfx: bool`.** Three states exist, not two: the archive starts at byte 0;
  it starts later; or we opened it correctly without establishing where. A bool forces the
  third into a lie. And `prefixed-archive-detection` already needs the finer split for
  `FormatInfo` — a `.pyz`, a Spring Boot JAR and a JPEG with an appended ZIP are all
  "offset > 0" and none of them is self-extracting. Two enums describing the same property
  on two objects would be the parity smell this repo names in §2, so `ArchiveInfo` reuses
  `PrefixKind`.
- **"Not established" is spelled as absence.** `prefix_kind is NONE` ⟺ `payload_offset ==
  0`; `prefix_kind is None` ⟺ `payload_offset is None`. The extra state an opened archive can
  be in that a detection result cannot is *not established*, and it gets its own spelling
  rather than borrowing `UNKNOWN`. `prefixed-archive-detection` defines `UNKNOWN` as a prefix
  that matched no cue — which always has a positive offset — so overloading it would make the
  same member mean different things on `FormatInfo` and `ArchiveInfo`. That is the §2
  inconsistency this change is otherwise arguing against.
- **Forced `format=ZIP` reports the real origin, from data it already has.** No tail probe
  and no extra read: `zipfile` adjusts every entry's `header_offset` while parsing the
  central directory, so the smallest one is the payload start in source coordinates.
  Measured correct for `zipapp`, shebang, `MZ` and JPEG prefixes (`design.md`). Recorded
  because the obvious wrong answer is `concat`, `zipfile`'s adjustment value, which is `0`
  for a `zipapp` and would report the headline prefixed case as unprefixed. `None` is left
  for the one archive that truly cannot answer: an empty ZIP has no local file header.
- **`prefix_kind` is classified on both paths, and never inferred from the offset.** With
  `OTHER_FORMAT` merged into `UNKNOWN` (`prefixed-archive-detection`), classification is a
  pure function of the leading bytes — `MZ`/ELF/Mach-O → `EXECUTABLE`, `#!` → `SCRIPT`, else
  `UNKNOWN` — so a forced-format open reaches the same answer detection does from one short
  read, polyglots included. What stays forbidden is deriving the kind from `payload_offset >
  0` without that read: a `zipapp`, an executable JAR and a JPEG-with-appended-ZIP all have
  a non-zero offset and only the first two carry a cue.
- **The resolver returns `MagicHit`, not `int`.** RAR needs the *version* as well as the
  offset, and the fast-path read already has the bytes that answer both. Returning the hit
  keeps the two formats on one signature instead of forcing RAR to keep a wider one.
- **Sequenced after `prefixed-archive-detection`.** `PrefixKind` is defined there, and this
  change assumes the member set that change is settling on — `NONE` / `EXECUTABLE` / `SCRIPT`
  / `UNKNOWN`, with `OTHER_FORMAT` merged away. That merge is what makes classification cheap
  enough to run on the forced path (`design.md`), so the two changes must agree on it; if
  `OTHER_FORMAT` survives there, the forced-path classification here has to be revisited.
  Landing this first would mean defining the enum in one change and its semantics in another.
  See `design.md` §Sequencing for the fallback if the order is inverted.

## Impact

- Modules: `src/archivey/internal/sfx.py` (the shared resolver),
  `src/archivey/internal/backends/sevenzip_parser.py` (`find_signature_offset` becomes a
  thin wrapper or goes), `sevenzip_reader.py`, `rar_parser.py` (`_find_sfx_header` goes),
  `rar_reader.py`, `zip_reader.py` (report the slice origin),
  `src/archivey/internal/base_reader.py` (how a backend reports its origin back),
  `src/archivey/types.py` (`ArchiveInfo`), `src/archivey/cli/info_cmd.py` (drop the second
  `detect_format`).
- Public API: `ArchiveInfo` gains two fields. Additive — `ArchiveInfo` is constructed by
  backends, not by callers, so no positional-argument break for library users; the
  dataclass gains defaults so backends that do not set them stay valid.
- Tests: SFX 7z / RAR / ZIP each opened both auto-detected and with `format=`, asserting
  the *same* `prefix_kind` and `payload_offset`; forced ZIP asserting `UNKNOWN` / `None`
  rather than a wrong `0`; plain archives asserting `NONE` / `0`; a stub carrying a decoy
  magic asserting the reported origin is the real payload; the RAR4/RAR5 version still
  resolved through the shared resolver; `reject_start_offset` backends unchanged.
- Docs: `docs/formats.md` self-extracting prose, and the CLI `info` output gaining the
  field from the reader instead of a second detection.
- Depends on `prefixed-archive-detection` (`PrefixKind`) and on
  `detection-prefix-workspace` (`MagicHit`, already landed in #273).
