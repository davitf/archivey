# archive-reading — fsspec `OpenFile` sources

## ADDED Requirements

### Requirement: An fsspec OpenFile is an archive source

`open_archive()`, `open_stream()` and `detect_format()` SHALL accept an
`fsspec.core.OpenFile` (the unentered object `fsspec.open(url, **options)` returns) as
`source`, and `open_archive()` SHALL also accept it as an item of a volume sequence. All
three SHALL read it through the remote fetch planner (`fsspec-integration`), from the
`OpenFile`'s `fs` and `path`, and SHALL NOT enter it. A `str` or `PathLike` source SHALL
stay a local path whatever it contains, and the `OpenFile` check SHALL run only when
`fsspec` is already imported.

#### Scenario: OpenFile source matrix

| Case | Expected |
| --- | --- |
| `open_archive(fsspec.open("memory://a.zip"))` | Opens; `reader.close()` releases the planner's buffers |
| `detect_format(of)` and `open_archive(of).format_info` | Equal, as for any other source |
| `open_stream(fsspec.open("memory://a.gz"))` | Opens the single compressed stream |
| `open_archive(["s3://…/a.7z.001", …])` as strings | Treated as local paths; `FileNotFoundError`, no request |
| `open_archive([fsspec.open(u1), fsspec.open(u2)])` | Opens as an explicit volume list |
| `open_archive(fs.open("a.zip"))`, an already-open fsspec file | Read as any seekable `BinaryIO`, through the caller's object; not closed by the reader |
| `import archivey; archivey.open_archive(path)` without fsspec imported | `fsspec` is not in `sys.modules` afterwards |

### Requirement: OpenFile fields archivey cannot honour are refused

An `OpenFile` whose `mode` is not `"rb"`, or whose `compression` is not `None` after
fsspec resolves it, SHALL be refused with an `ArchiveyUsageError` that is a `ValueError`,
naming the field and its value, before any range is read. Its `encoding`, `errors` and
`newline` SHALL be ignored, as binary mode ignores them.

#### Scenario: refused OpenFile fields

| Case | Expected |
| --- | --- |
| `fsspec.open("memory://x.zip.gz", compression="infer")` | `ArchiveyUsageError` naming `compression='gzip'`; no read |
| `fsspec.open("memory://x.zip.gz")` | Opens the gzip stream (single-file reader with one member) |
| `fsspec.open("memory://x.zip", mode="rt")` | `ArchiveyUsageError` naming the mode |

### Requirement: The source facts of an OpenFile

The `ArchiveSource` built from an `OpenFile` SHALL settle its facts once, at open:

| Fact | Value |
| --- | --- |
| `size` | `fs.size(path)`, as a fact: it clamps reads as a path's `stat` size does |
| `size_hint` | The same value |
| `path`, `volume_paths` | `None` |
| `name` | `of.path` |
| `seek_is_expensive` | `True` |

A backend that needs a local path SHALL treat the source as any non-path stream (RAR's
spool under `SpoolLimits`). Closing the reader SHALL NOT close the `OpenFile`'s file
system.

#### Scenario: OpenFile facts

| Case | Expected |
| --- | --- |
| `detect_format(fsspec.open("memory://d/data.tar.zst"))` | The name `d/data.tar.zst` drives the name-keyed checks as a path's name does |
| Object grows after open | Reads stop at the open-time size; detecting the change is the planner's (`fsspec-integration`) |
| RAR, compressed member, `OpenFile` source | The archive is spooled under `SpoolLimits`, with the existing cost note |
