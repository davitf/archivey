# Architecture Review Guide

Optional. Open it when a PR moves code between layers, adds a format or codec, or
changes what the public package imports. The everyday layering checks are in
[`code-pr.md`](code-pr.md) §API & layering; this page is the longer form.

## Layer model

```
Public API        archivey/__init__, core.py, reader.py, types.py, exceptions.py, config.py
Orchestration     internal/: registry.py, detection.py, extraction.py, base_reader.py
Format backends   internal/backends/: *_reader.py, *_parser.py, *_detect.py
Codecs & streams  internal/streams/: codecs/, decompressor_stream.py, child_process.py, …
Outer             stdlib I/O, optional extras (lazy), external programs (unrar, unar)
```

The public modules import from `internal/`; users and the CLI do not. The CLI goes
through the public API, with one allowlisted exception
(`tests/test_cli_uses_public_api.py`). Backends implement `ReadBackend`
(`internal/base_reader.py`) and register with `BackendRegistry`
(`internal/registry.py`); orchestration picks a backend through the registry, never by a
suffix or a backend class name.

## Checklist

**Layer boundaries:**
- [ ] Public modules do not expose parser types — a backend's structs stay out of
  `types.py` and the exception messages' contract
- [ ] Optional extras are imported at the boundary, lazily, never at `import archivey`
- [ ] Orchestration (`core`, `reader`, `internal/extraction.py`) carries no
  format-specific parse logic
- [ ] The CLI does not reach into `internal/`; if it needs to, that is an API gap

**Format and codec:**
- [ ] A new format registers a backend plus detection, with minimal edits outside its
  own modules
- [ ] A new codec goes behind `internal/streams/codecs/` (its own `<name>_codec.py`),
  not inline in a backend
- [ ] A missing optional dependency is reported through `FormatSupport` /
  `MissingComponent`, not a bare `ImportError`
- [ ] Limits come from `ArchiveyConfig`, not module constants in a parser
- [ ] Parser changes stay in one backend module; shared header or codec logic used by
  two backends is a shared helper, not a copy

**Smells:**
- [ ] No circular import between the registry and backends
- [ ] No abstraction "for later" with no second implementation
- [ ] No `isinstance` checks on a concrete reader type in callers — a caller that
  needs one is a hole in the `ReadBackend` contract

## Example findings

```markdown
🔴 [blocking] "ArchiveMember now carries a sevenzip_parser struct — map it to public fields"
🟡 [important] "Extraction path contains ZIP-specific logic — move it to zip_reader"
🟡 [important] "Codec hard-coded in the backend — register it in streams/codecs/"
💡 [suggestion] "Gate the optional import behind the backend's availability check"
```
