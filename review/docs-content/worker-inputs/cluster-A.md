# A. Opening, detection, sources

Specs: `format-detection`, `archive-reading`, `compressed-streams`.
Pages: `opening-and-listing`, `install`, `formats`, `index`, `migrating`, `philosophy`.

| # | Claim | Stated at | Settles it | Ruling | V |
|---|---|---|---|---|---|
| A-1 | `open_archive(path)` detects the format and returns a reader; iterating it yields members in **archive order** | `opening-and-listing.md:11-13`, `index.md:9-11`, `migrating.md:14-15` | `archive-reading:20`, `archive-reading:259` | Keep | |
| A-2 | `reader.members()` returns the **full** list or raises — never a quietly shortened one | `opening-and-listing.md:15`, `opening-and-listing.md:155-156`, `errors-and-diagnostics.md:116-117` | `archive-reading:199`, `documentation:103` | Keep | |
| A-3 | `reader.get(name)` returns the member with that name | `opening-and-listing.md:16` | `archive-reading:406` | Keep | |
| A-4 | An open reader exposes `.format` and `.cost` | `opening-and-listing.md:17` | `access-mode-and-cost:151` | Keep | |
| A-5 | **By default any member may be opened in any order** — random access is the default access mode | `opening-and-listing.md:20-23` | `access-mode-and-cost:19` | Keep | |
| A-6 | Without `streaming=True` a non-seekable source **fails at open**, not halfway through, and archivey never silently buffers it to memory or a temp file. **Reword:** the no-buffering half is true of the *pipe* case it sits in (ADR 0010's scope) but reads as absolute, and RAR-from-a-seekable-stream contradicts the absolute reading — see **E-71** | `opening-and-listing.md:25-28`, `access-and-cost.md:141-143`, `philosophy.md:42`, `migrating.md:170-172` | `access-mode-and-cost:50`, `archive-reading:701`, ADR 0010 (scoped to non-seekable) | Keep, **reword** | **`wrong` as written** (absolute reading). Repro §Coordinator-verified |
| A-7 | `open_archive` on a plain `.gz` yields an archive with **exactly one** member, named after the file | `opening-and-listing.md:41-43`, `formats.md:143` | `format-single-file-compressors:27` | Keep | |
| A-8 | `open_stream(path)` returns the decompressed bytes rather than an archive | `opening-and-listing.md:36-39`, `migrating.md:20` | `compressed-streams:36` | Keep | |
| A-9 | `[code]` the `open_archive` / `open_stream` two-liner runs | `opening-and-listing.md:36-39` | — (executable) | Keep | |
| A-10 | A path to a **directory** opens as a pseudo-archive, one member per file | `opening-and-listing.md:50`, `formats.md:136-137` | `format-directory:20` | Keep | |
| A-11 | Passing a `format=` that is anything but `DIRECTORY`, for a path that is a directory, raises `ArchiveyUsageError` rather than reading the tree | `opening-and-listing.md:54-55` | `src/archivey/core.py:240-247` | Keep | |
| A-12 | A **seekable stream is read from its current position**: that position is treated as byte 0, so an archive at a known offset opens without copying it out | `opening-and-listing.md:57-61` | `archive-reading:20` | Keep | |
| A-13 | There is **no matching end bound** — the archive must run to the end of the stream, else the caller wraps it in a bounded view | `opening-and-listing.md:61-62` | `archive-reading:20` | Keep | |
| A-14 | A non-seekable stream with `streaming=True` works for **TAR (including compressed tar) and the single-file compressors** | `opening-and-listing.md:64-66`, `formats.md:11-12` | `access-mode-and-cost:50`, `format-tar:20` | Keep | |
| A-15 | **ZIP, 7z, RAR and ISO must seek**; opening one from a pipe raises `StreamNotSeekableError`, fix is to buffer to a file or `BytesIO` | `opening-and-listing.md:66-68` | `format-zip:118`, `format-iso:22`, `format-7z:25`, `format-rar:25` | Keep | |
| A-16 | **`access-and-cost.md` is the wrong side.** It names only **ZIP (stdlib) and ISO** as always needing seek, which implies 7z and RAR can be opened from a pipe under `streaming=True`. They cannot. `opening-and-listing.md:66-68` is correct | `access-and-cost.md:145-146` vs `opening-and-listing.md:66-68` | `access-mode-and-cost:233`; `required_source` is `SEEKABLE` for ZIP / 7z / RAR / ISO alike (Part 1 sweep) | Keep (`opening-and-listing`) · **fix** `access-and-cost.md:145-146` | **`wrong` — `access-and-cost.md`.** Repro §Coordinator-verified |
| A-17 | `format_availability(fmt).required_source` is **the weakest source shape the format can be read from**, so a comparison replaces a `try`/`except` | `opening-and-listing.md:70-73` | `src/archivey/internal/registry.py:69`, `:314` | Keep — canonical home | |
| A-18 | `[code]` the `required_source` / `StreamCapability` comparison block runs as written (imports `StreamCapability, detect_format, format_availability` from `archivey`) | `opening-and-listing.md:74-81` | — (executable) | Keep | |
| A-19 | `StreamCapability` is **ordered**, `FORWARD_ONLY < SEEKABLE`, and the same comparison works against `reader.cost.stream_capability` | `opening-and-listing.md:83-85`, `access-and-cost.md:48-52` | `src/archivey/cost.py:48-84` | Keep — canonical home | |
| A-20 | **Only 7z and RAR** split across volumes | `opening-and-listing.md:89` | `format-7z:180`, `format-rar:345`, `format-zip:169` | Keep | |
| A-21 | Passing **any one volume** finds the rest, in all three listed naming schemes (`.7z.001…`, `.partN.rar`, `.rar`+`.rNN`) | `opening-and-listing.md:89-96` | `format-rar:345`, `format-7z:180`, `src/archivey/internal/volumes.py` | Keep | |
| A-22 | A **7z** volume set is checked for completeness — a missing middle part errors rather than short-reading | `opening-and-listing.md:98-99` | `format-7z:180` | Keep | |
| A-23 | The **old RAR scheme needs its `.rar`**; a `.rNN` alone is read as a lone file, not as part of a set | `opening-and-listing.md:99-101` | `format-rar:345` | Keep | |
| A-24 | An explicit ordered **sequence** of paths or streams is used in the order given, with no discovery | `opening-and-listing.md:103-105` | `archive-reading:152` | Keep | |
| A-25 | A **one-item** sequence is treated as a single source | `opening-and-listing.md:105-106` | `archive-reading:152` | Keep | |
| A-26 | A multi-volume sequence for any format **other than 7z or RAR raises** | `opening-and-listing.md:106-107` | `src/archivey/core.py:115-124` | Keep | |
| A-27 | `detect_format(p)` returns a `FormatInfo` carrying `.format` and `.confidence` | `opening-and-listing.md:114-117`, `formats.md:227-228`, `migrating.md:21` | `format-detection:19` | Keep — canonical home | |
| A-28 | **Content wins over filename**: bytes first, extension only when the bytes are inconclusive | `opening-and-listing.md:119-120`, `formats.md:224`, `migrating.md:109-110`, `philosophy.md:66` | `format-detection:68` | Keep — canonical home | |
| A-29 | When bytes and extension disagree, the bytes are used **and** a `FORMAT_EXTENSION_CONFLICT` diagnostic names both candidates | `opening-and-listing.md:120-123` | `format-detection:84`, `src/archivey/diagnostics.py:64` | Keep | |
| A-30 | `detect_format` reports **the same format `open_archive` would use** | `opening-and-listing.md:125` | `format-detection:19` | Keep | |
| A-31 | Telling `.tar.zst` from plain `.zst` needs decompressing a little to look for a tar header; **when the compressor's package is absent the check cannot run** and the bare compressor is reported | `opening-and-listing.md:126-128` | `format-detection:174` | Keep · `cfg` | |
| A-32 | Opening such a file then raises **`UnsupportedFormatError`, naming the package to install** | `opening-and-listing.md:128-130` | `compressed-streams:124` | Keep | |
| A-33 | **Conflicts with A-32:** `formats.md` says a missing backend raises **`PackageNotInstalledError`**. Two pages name two exception types for one situation | `formats.md:36-37`, `formats.md:58-59` vs `opening-and-listing.md:128-130` | `compressed-streams:124`, `src/archivey/exceptions.py:113`, `:224` | Keep (both) | |
| A-34 | **SFX stubs** are detected when the payload sits behind an executable header | `formats.md:225-226` | `format-detection:231` | `→ page, 2 lines` — the SFX line is the unique claim and stays on `formats.md` | |
| A-35 | Detection lives on `opening-and-listing.md`; `formats.md` §Detection is a second copy of magic-first + confidence | `formats.md:222-228` (dup of `opening-and-listing.md:109-131`) | `format-detection:68` | `→ page, 2 lines` | |
| A-36 | `password=` accepts **three forms** — a string, a list, a `PasswordProvider` callable — and all three behave alike on the unused-password rule | `opening-and-listing.md:136-137`, `:148` | `archive-reading:632` | Trim | |
| A-37 | **Put the most likely password first**: every wrong candidate costs work before rejection, especially on 7z | `opening-and-listing.md:140-141`, `access-and-cost.md:154-158` | `archive-reading:668`, `format-7z:197` | Trim | |
| A-38 | A password passed to a format with **no encryption at all** is accepted, never consulted, and records `PASSWORD_ARGUMENT_UNUSED` | `opening-and-listing.md:143-147`, `errors-and-diagnostics.md:59` | `diagnostics:253` | Trim | |
| A-39 | A **wrong** password on a genuinely encrypted archive raises `EncryptionError` | `opening-and-listing.md:150-151`, `migrating.md:135` | `src/archivey/exceptions.py:133`, `archive-reading:632` | Trim | |
| A-40 | `[code]` the `is_current` filter block runs | `opening-and-listing.md:191-194` | — (executable) | Keep | |
| A-41 | `[code]` the history-view loop runs | `opening-and-listing.md:198-203` | — (executable) | `Trim to one` — this is the block slated for removal; the claim is still recorded so the removal is a decision, not a loss | |
| A-42 | `[code]` the Home open+list snippet runs | `index.md:6-12` | — (executable) | Keep | |
| A-43 | `[code]` Home recipe 4 — reading from a pipe with `streaming=True` and `stream_members()` | `index.md:37-40` | — (executable) | Keep, frozen | |
| A-44 | `[code]` the `zipfile` before/after pair runs, and the "after" half is the idiomatic equivalent of the "before" half | `migrating.md:25-41` | — (executable) | Keep | |

## A — problems and gaps met while extracting

- **A-16 and A-33 are the two live cross-page contradictions in this cluster**, and both
  are the O-2 shape: a fact stated on two pages, checked against each other rather than
  against the spec. Neither was in `scope.md` §Findings.
- `opening-and-listing.md:126-131`'s wrinkle is the only place the guide describes a
  *detection* outcome that changes with the installed extras. It is the row most likely
  to read differently on `[core-only]`.

---

