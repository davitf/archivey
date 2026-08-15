# 0015 — A zero-filled file is a valid empty TAR; report it, never refuse it

- **Status:** accepted
- **Date:** 2026-08-08 (`strict-archive-eof-trailing-bytes`, PR #232; review finding F20 / O8)
- **Provenance:** `review/archive/2026-08-15-simplicity-consistency/` (F20, O8a/O8b); OpenSpec `format-tar`,
  `format-detection`, `diagnostics`; `VISION.md` (damaged input is first-class; no
  silent success; no quirk-driven architecture)

## Context

A file containing nothing but zero bytes, named `z.tar`, opens as an empty TAR archive
and lists zero members. The obvious reading is that this is a hole: any junk file
becomes a "valid" archive. Three fixes were proposed over two review rounds, and each
looked correct until it was measured:

1. **Raise when a TAR yields zero members.** Closes the hole in one line.
2. **Raise when there are zero members *and* the file continues past the trailer.**
   Proposed as a middle option that would spare a genuinely empty archive.
3. **Require an exact block count** — accept an empty TAR only at the canonical
   sizes, so an odd zero-filled length can be rejected on length alone.

The load-bearing question underneath all three is whether a zero-filled blob can be
*distinguished* from a legitimately empty archive.

### What the measurements found

**An empty TAR is all zeros.** The end-of-archive marker is two 512-byte zero blocks
and writers pad beyond it, so an empty archive has no non-zero byte anywhere. Python's
`tarfile` emits 10240 bytes; Go's `archive/tar` emits 1024.

That kills options 1 and 2 immediately. Option 1 would reject what `tar(1)` itself
produces. Option 2 fails on the same input for a subtler reason: the trailer ends at
1024 bytes and a `tarfile` empty archive continues to 10240, so "continues past the
trailer" is *true for the valid file* — the middle option would have rejected every
empty tar in existence.

**Option 3 survives longer, and fails on `tar -b`.** The blocking factor is an ordinary
documented GNU tar option, and it makes every block-aligned zero length a legitimate
empty archive:

| Command | Size | Blocks | All zero | archivey |
| --- | --- | --- | --- | --- |
| `tar -b 1` | 1024 | 2 | yes | 0 members |
| `tar -b 5` | 2560 | 5 | yes | 0 members |
| `tar -b 10` | 5120 | 10 | yes | 0 members |
| `tar -b 20` (default) | 10240 | 20 | yes | 0 members |
| `tar -b 64` | 32768 | 64 | yes | 0 members |
| `tar -b 128` | 65536 | 128 | yes | 0 members |

The decisive case, because 32 KiB of zeros is the exact file that prompted the finding:

```
$ tar -b 64 -cf e64.tar --files-from /dev/null
sha256(e64.tar)        = c35020473aed1b4642cd726cad727b63...
sha256(b"\x00" * 32768) = c35020473aed1b4642cd726cad727b63...
BYTE-IDENTICAL: True          # and `tar -tvf e64.tar` lists it, exit 0
```

**The "junk" file that motivated the finding is a valid empty archive.** There is no
predicate over the bytes that separates them, because there is no difference between
them.

**What is actually in the wild.** Two sizes dominate, and both are canonical:

- **1024 bytes** — Go's `archive/tar`, verified directly. Its empty archive has
  sha256 `5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef`, which is
  the Docker/OCI *empty layer* blob. Every image with metadata-only layers (`ENV`,
  `CMD`, `LABEL`) carries one. Go also pads member data to 512 rather than to a
  20-block record — a one-file Go tar is 2048 bytes — so the entire cloud-native
  ecosystem writes tars that are not 10240-aligned.
- **10240 bytes** — GNU tar's default blocking factor and Python `tarfile`.

Surveying the tarballs present on a development machine: 87% of real-world archives are
10240-aligned, against 5% of Go's `archive/tar` test corpus. Non-default blocking
factors, by contrast, left almost no trace — the non-aligned real files are explained by
writers that pad to 512, not by anyone passing `-b N`.

**Detection never accepts these on content.** TAR's magic is `ustar` at offset 257,
*inside a member header*; an empty archive has no header to carry it. So a legitimately
empty tar and a zero blob are equally unconfirmable, and both reach the TAR reader only
by file extension or an explicit `format=`:

| Input | `detect_format()` | `open_archive()` without `format=` |
| --- | --- | --- |
| real empty tar, `.tar` | TAR, `detected_by=extension` | 0 members + diagnostics |
| 32 KiB zeros, `.tar` | TAR, `detected_by=extension` | 0 members + diagnostics |
| either, renamed `.bin` | `FormatDetectionError` | `FormatDetectionError` |

## Decision

**A zero-filled, block-aligned file *is* a valid empty TAR. archivey reads it as one,
reports what it observed, and never refuses it on those grounds.**

1. **Zero members is not an error.** No raise, under any configuration. An empty
   archive is legal, common, and produced by both dominant writers.
2. **Report it instead.** `EMPTY_ARCHIVE` states the observation ("this archive is
   empty") rather than a guess ("this file is probably garbage"). Where the format came
   from the *extension* and content detection would have refused the bytes,
   `EXTENSION_FORMAT_UNCONFIRMED` says so; an explicit `format=` gets
   `EXPLICIT_FORMAT_LISTED_EMPTY`.
3. **No canonical-size heuristic.** Rejected — see below.
4. **`strict_archive_eof` remains the caller's opt-in for the adjacent question.** It
   asserts that every byte from the trailer to EOF is zero, which catches appended junk
   and concatenated archives. It deliberately does **not** fire on an empty archive,
   because zeros to EOF is exactly what one is.

### Why not the canonical-size heuristic

Restricting empty archives to 1024 or 10240 bytes would cover both dominant writers, and
it *would* have flagged the 32 KiB case. It was still rejected:

- **It is unsound, and the failure lands on a real user.** `tar -b 64` output is a valid
  archive that the rule calls suspect. The rule cannot refuse to open — decision 1
  forbids that — so the only thing it can do is emit a *wrong* advisory to someone whose
  archive is fine.
- **It buys cosmetics.** The most it achieves is suppressing
  `EXTENSION_FORMAT_UNCONFIRMED` on canonical empty archives. But that diagnostic is
  *true* for them: the bytes genuinely did not confirm the format. Suppressing a true
  advisory to reduce noise trades honesty for tidiness, against `VISION.md`.
- **It is quirk-driven architecture** (an explicit non-goal): two magic constants in the
  detection path, encoding "the writers we happened to survey", that a future writer or
  a non-default flag silently invalidates.

A caller who wants the distinction has the material to make it — `len(reader) == 0`, the
diagnostics, and the file size — and can apply their own policy. The library does not
guess on their behalf.

### What this does not cover

Two shapes near this decision are already handled and are not affected:

- **Short zero files** (under two blocks) get `ARCHIVE_EOF_MARKER_MISSING`, escalating
  to `TruncatedError` under `strict_archive_eof`.
- **Non-block-aligned zero files** (e.g. 32775 bytes) are not valid under any blocking
  factor. They open with `EMPTY_ARCHIVE` + `EXTENSION_FORMAT_UNCONFIRMED`, so the caller
  is told. A dedicated alignment rule was considered and judged not to earn its keep:
  the shape is rare, and the caller already has a report.

## Consequences

- **A Docker/OCI empty layer reads correctly.** Under any of the three rejected options
  it would have raised — the single most widely distributed empty tar in existence.
- **Callers who care must read diagnostics.** A one-off user with no diagnostic handling
  sees a zero-member archive and nothing else. Accepted knowingly: the alternative is
  refusing valid archives, and no exception exists that is correct.
- **`EXTENSION_FORMAT_UNCONFIRMED` fires on legitimately empty archives**, including
  every Docker layer. This is correct — the format really was unconfirmed — but it means
  a pipeline over container images will see it constantly and should set that code to
  `IGNORE` via `DiagnosticPolicy`.
- **The question will be re-asked.** It has been proposed three times across two review
  rounds. The `tar -b 64` byte-identity above is the short answer: the file we wanted to
  reject is a file `tar` produces.
