## Why

7-Zip picks BCJ2 for x86 executables at its top level, `-mx9`, and only there
(`dev-docs/formats/7z.md` §3). So the 7z archive archivey is most likely to refuse is
one the reference tool wrote at its headline setting. Today such a folder raises
`UnsupportedFeatureError`, and the message says "multiple packed streams" rather than
BCJ2, because the reader refuses the folder's four pack streams before it looks at the
coder.

Among the pure-Python readers, none decodes BCJ2. Measured 2026-09-23 on a 7-Zip
23.01 `-mx9` archive of `/usr/bin/git`: `py7zr` 1.1.3 raises
`UnsupportedCompressionMethodError` ("BCJ2 filter is not supported by py7zr"), and
`pybcj` exports decoders for the six simple branch filters and nothing else. Two
C-backed packages do decode it, and both produced correct bytes: `pylzma` 0.6.1
(`bcj2_decode`) and `libarchive-c` 5.3 over the system libarchive 3.7.2. Design D8
covers why neither is the route: pylzma allocates the whole output from the declared
size, and libarchive would be a second 7z reader. So this is not a regression to fix
before 0.2.0, and the way to close the gap is archivey's own decoder.

BCJ2 is small. The decoder copies one stream up to each x86 branch opcode, decodes one
range-coded bit, and on a 1 takes a 4-byte target from one of two other streams. The
four inputs are separate pack streams, so each needs its own read position on the
archive, which is what `SharedSource` views already give. A prototype written for
this proposal (`prototype/bcj2.py`) decodes 7-Zip's own output
byte-for-byte at about 14 MB/s. The numbers are in design.md.

## What Changes

- The 7z pipeline accepts a folder whose coder graph is a **tree**: each input of a
  multi-input coder is decoded as its own branch, and each branch is a linear chain
  planned by today's rules and ending at one packed stream. BCJ2 is the only
  multi-input coder accepted. Anything that is not a tree still raises
  `UnsupportedFeatureError`.
- Each packed stream in a folder gets its own `SharedSource` view. `_folder_pack_view`
  becomes a per-pack-stream lookup, and `open_folder_pipeline` takes the list of views
  instead of one.
- A new internal stream, `Bcj2DecoderStream` (`internal/streams/bcj2.py`), decodes BCJ2
  in pure Python on a core install. It is forward-only. The prototype in this change's
  `prototype/` directory is the starting point.
- The password check, `member.compression`, solid-folder streaming and the member CRC
  work for BCJ2 folders without changes of their own. An encrypted BCJ2 folder is four
  branches, each with its own AES coder.
- The encoded header stays linear-only. No writer puts BCJ2 on a header.
- The refusal goes away from the specs, docs and handbook that promise it. These are
  `format-7z`, `packaging-and-extras`, `error-handling`, `backend-registry`,
  `testing-contract`, `docs/formats.md`, `7z.md`, `AGENTS.md` and `openspec/project.md`.
- No public API change. No new extra, no new dependency.

## Capabilities

### New Capabilities

### Modified Capabilities
- `format-7z`: folders with a tree-shaped coder graph decode, and BCJ2 moves from
  "unsupported" to a core codec. A new requirement states how BCJ2 decodes and what it
  refuses.
- `packaging-and-extras`: BCJ2 is read on a core install. The rule that it stays
  unsupported by every extra is removed.
- `error-handling`: 7z BCJ2 leaves the list of example `UnsupportedFeatureError` causes.
- `backend-registry`: BCJ2 leaves the list of by-design unsupported features.
- `testing-contract`: BCJ2 joins the 7z oracle corpus, with the `7z` CLI as its only
  oracle, because `py7zr` cannot read it.

## Impact

- `src/archivey/internal/backends/sevenzip_pipeline.py`: the tree planner and a BCJ2
  stage. `plan_folder` stays pure.
- `src/archivey/internal/backends/sevenzip_reader.py`: one view per pack stream, in
  `_open_folder_stream` and in the password check.
- `src/archivey/internal/streams/bcj2.py`: new.
- Tests: the two tests that pin the refusal become decode tests. New fixtures are
  written with the `7z` CLI, and they skip when the CLI is absent, as the other CLI
  fixtures do.
- Performance: BCJ2 folders read at roughly a third of the speed of the same folder
  with BCJ, because the Python stage runs after liblzma. A hostile folder can push the
  stage down to about 1.5 MB/s. The design has the measurements and why the existing
  extraction limits bound it.
- Not needed for 0.2.0.
