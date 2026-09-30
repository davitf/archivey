## ADDED Requirements

### Requirement: Dry-run extraction

`extract_all()` and `extract()` SHALL accept `dry_run: bool = False`, read for its
truthiness like the other boolean flags.

With `dry_run=True`, the extraction SHALL run the same pass as a real extraction into
a private scratch directory that starts empty, and SHALL NOT create or change anything
under `dest`. Directories, symlinks and hardlinks SHALL be created in the scratch
directory, so every check that consults the filesystem behaves as in a real extraction.
Every FILE body SHALL be read, decompressed, verified and counted against the
extraction limits; its bytes SHALL be discarded and the file created empty.

The returned report SHALL equal the report of a real extraction with the same
arguments into an empty `dest`, with every path in it (`path`, `requested_path`,
`collided_with`) under `dest`. An absolute link target that names a path under `dest`
SHALL be checked as naming the same path under the scratch directory. Two cases are
exempt, because the scratch directory cannot reproduce them: a link target that leaves
`dest` and comes back into it through a symlink outside `dest`, or by climbing above
the directory that holds `dest`, MAY be refused where a real extraction accepts it; and
bytes a real extraction counts for copying a hardlink across a filesystem boundary
inside `dest` are not counted. A `dest` that exists and is not a directory, or that
cannot be created because part of its path is not a directory, SHALL be refused as a
real extraction refuses it. Errors raised, recorded or logged SHALL name paths under
`dest`, not the scratch directory. The scratch directory SHALL be removed before
the call returns or raises, including when the archive stored modes that make its
directories unwritable or its files read-only.

#### Scenario: dry-run matrix

| Case | Expected |
| --- | --- |
| Any archive, any policy, overwrite and access mode | Report equals that of a real extraction into an empty `dest` |
| A member whose data fails its digest | `FAILED`, as in a real extraction |
| Output over `max_extracted_bytes` | `ResourceLimitError`, as in a real extraction |
| `dest` holds a file of the same name as a member | Member `EXTRACTED` at `dest/<name>`; the existing file is unchanged |
| `dest` is a regular file | `ExtractionError`; nothing created |
| `dest`'s parent is a regular file | `OSError`, as in a real extraction; nothing created |
| A symlink whose absolute target names a file under `dest` | `EXTRACTED`, as in a real extraction |
| A symlink at the top of `dest` whose target is `../<dest name>/<file>` | `EXTRACTED`, as in a real extraction |
| `dest` does not exist | Not created |
| Archive stores a directory with mode `0o555` or `0o000` under `TRUSTED` | Scratch directory still removed |
| `abort_on` fires | The error names paths under `dest`; scratch directory removed |
