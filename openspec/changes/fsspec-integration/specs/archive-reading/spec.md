# archive-reading — fsspec `OpenFile` sources

## ADDED Requirements

### Requirement: An fsspec OpenFile is an archive source

`open_archive()`'s `source` SHALL accept an `fsspec.core.OpenFile` (the unentered object
`fsspec.open(url, **options)` returns), alone or as an item of a volume sequence. The
reader SHALL open it, read it through the remote fetch planner (`fsspec-integration`),
and close what it opened, as it does for a path's handle.

A `str` or `PathLike` source SHALL stay a local path whatever it contains: archivey MUST
NOT open a network connection because of a string. The `OpenFile` check SHALL run only
when the `fsspec` module is already imported, so `open_archive()` never imports fsspec.

An already-open fsspec file object (the object `fs.open(path)` returns) SHALL keep the
contract of any seekable `BinaryIO`: archivey reads it through the caller's object, with
the caller's cache, and does not close it.

#### Scenario: OpenFile source matrix

| Case | Expected |
| --- | --- |
| `open_archive(fsspec.open("memory://a.zip"))` | Opens; the reader opens the file and closes it on `reader.close()` |
| `open_archive(["s3://…/a.7z.001", …])` as strings | Treated as local paths; `FileNotFoundError`, no request |
| `open_archive([fsspec.open(u1), fsspec.open(u2)])` | Opens as an explicit volume list |
| `open_archive(fs.open("a.zip"))`, an open fsspec file | Read through the caller's object; not closed by the reader |
| `import archivey; archivey.open_archive(path)` without fsspec imported | `fsspec` is not in `sys.modules` afterwards |
