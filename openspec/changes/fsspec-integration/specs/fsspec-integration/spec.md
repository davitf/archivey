# fsspec integration

## ADDED Requirements

### Requirement: Remote sources are read through a bounded fetch planner

For an `fsspec.core.OpenFile` source (`archive-reading`), the `ArchiveSource` SHALL read
every byte through the file system's range reads (`cat_file`, or `cat_ranges` for
batched fetches), with fsspec's own file cache unused, and SHALL decide each fetch's
start and length itself:

| Rule | Behaviour |
| --- | --- |
| Sequential run | A read that continues the last fetch grows the next fetch, up to a fixed maximum fetch size |
| Short forward gap | A forward seek no longer than a fixed read-through distance continues the run instead of starting a new request |
| Jump | A backward seek, or a longer forward one, starts a new run at the minimum fetch size |
| Backend range hint | A span the backend declares it will read next is fetched in one request, up to the maximum fetch size, then continued as a run |

The buffered bytes per source SHALL be bounded by a fixed constant that does not depend on
the archive. Hints SHALL change only how bytes are fetched, never which bytes a read
returns, and SHALL do nothing on a local source. The size SHALL be learned once, at open.
`ArchiveSource.seek_is_expensive` SHALL be `True` for such a source.

The ZIP, 7z and ISO backends SHALL hint the index they read at open and each member's
stored span before reading it. The RAR backend SHALL hint each header position during
its listing walk.

#### Scenario: request ceilings on the remote benchmark corpus

Range requests, excluding the one size request. The remote benchmark axis
(`testing-contract`) checks these.

| Archive | List | List and read one member |
| --- | --- | --- |
| ZIP, central directory within the last 64 KiB | ≤ 2 | ≤ 3 |
| 7z, non-solid | ≤ 2 | ≤ 3 |
| RAR5, non-solid or solid | ≤ 3 | not bounded: `unrar` reads a spooled copy |
| TAR, compressed TAR | ≤ ⌈archive size / maximum fetch⌉ + jumps over members larger than the read-through distance | the same |

### Requirement: Multi-volume discovery over fsspec

When the source is one `OpenFile` of a multi-volume set, sibling discovery SHALL list the
parent directory through the source's file system once and SHALL apply the same naming
rules as local discovery (`archive-reading` §Multi-volume), giving the same set for the
same names. Each discovered volume SHALL be opened as its own `OpenFile` source. A file
system that cannot list a directory SHALL be treated as having no siblings.

#### Scenario: fsspec volume discovery

| Case | Expected |
| --- | --- |
| `memory://d/x.7z.001` with `x.7z.002`, `x.7z.003` beside it | One reader over all three; one `ls` of `memory://d` |
| `memory://d/q.part1.rar` with `q.part2.rar` and `q.part02.rar` | The same choice of part 2 as local discovery |
| HTTP first volume, server cannot list | Opens as a lone first volume, as a local lone volume does |

### Requirement: ArchiveyFileSystem exposes an archive as a read-only fsspec file system

`archivey.fsspec.ArchiveyFileSystem` SHALL subclass
`fsspec.archive.AbstractArchiveFileSystem` with protocol `archivey`:

```python
ArchiveyFileSystem(
    fo: str | os.PathLike | fsspec.core.OpenFile | BinaryIO,
    *,
    target_protocol: str | None = None,
    target_options: dict | None = None,
    format: ArchiveFormat | str | None = None,
    password: PasswordInput = None,
    encoding: str | None = None,
    config: ArchiveyConfig | None = None,
    **kwargs,
)
```

A `str` `fo` SHALL be opened with `fsspec.open(fo, protocol=target_protocol,
**(target_options or {}))`, so chained URLs (`archivey://m::s3://b/a.7z`) work. The
archive SHALL be opened once per instance, with `seekable_members=True` and
`concurrent_operations=True`, and SHALL stay open until the instance is closed or
collected. The reader SHALL be available as `fs.reader`.

The tree SHALL be the extraction view: each path SHALL be the relative path
`extract_all()` would write for that member under `config`'s extraction policy, a
duplicate name SHALL show its current entry, and directories implied by member paths
SHALL exist. A member that extraction would refuse SHALL be absent, with the refusal
recorded on `fs.reader.diagnostics`.

| Operation | Behaviour |
| --- | --- |
| `info(path)` | `name`, `size` (declared size, or `None`), `type` (`"file"`, `"directory"` or `"other"`), `mtime` (POSIX seconds) when known, and `member` (the `ArchiveMember`) |
| `ls`, `find`, `glob`, `walk` | From the tree above; no member data read |
| `open(path, "rb")` | A seekable member stream; backward seeks MAY re-decode (loud-slow-rewind) |
| A link that resolves to a member | Listed as the target's type with `islink: True` and `destination`; opens the target |
| A link that resolves to nothing | Listed as `"other"`; `open` raises `FileNotFoundError` |
| Missing path | `FileNotFoundError` |
| `open` on a directory | `IsADirectoryError` |
| Any write method, or a mode other than `"rb"` | `NotImplementedError` |
| A damaged member | archivey's own error from `read()`, unchanged |

An instance SHALL pickle as its constructor arguments and reopen the archive when
unpickled, when `fo` is a `str`, `PathLike` or `OpenFile` and the archivey arguments
pickle. A file-object `fo` SHALL raise `TypeError` on pickling, naming the reason.

#### Scenario: fsspec consumers

| Case | Expected |
| --- | --- |
| `pandas.read_csv("archivey://d/t.csv::memory://a.7z")` | Same frame as from the extracted file |
| `pyarrow.parquet.read_table` on a member through the file system | Same table as from the extracted file |
| A ZIP with members `a/../../x` and `a/b` twice | Tree holds `a/b` (current entry); `x` absent with a diagnostic, under the default policy |
| `pickle.loads(pickle.dumps(fs))` with a URL `fo` | A working file system over the same archive |
| Two threads read two members at once | Both read their members' bytes |
