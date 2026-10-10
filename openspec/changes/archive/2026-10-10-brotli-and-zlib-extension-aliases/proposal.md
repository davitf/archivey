# Register `.brotli` and `.zlib` as extensions

## Why

Brotli and zlib have no magic, and their content probes now run only for a source whose
name claims the format (`content-probes-need-extension`). Files of both formats are
often named with the long form: `.brotli` by tools that spell out the codec, `.zlib` for
raw zlib dumps. Without the alias such a file is refused unless the caller turns every
probe on, and before that change a genuine `.brotli` file was uncorroborated, so a failed
read was stamped `format_unconfirmed`.

## What changes

- `StreamCodec.extension_aliases`: extensions besides the canonical one. Zlib gets
  `.zlib`, Brotli gets `.brotli`.
- The TAR backend registers the `.tar.` form of each alias.
- Single-file member names strip the alias as they strip the canonical extension.

LZMA Alone already has `.lzma` and, for its probe, `.tlz`. Other aliases (`.zstd`) are for
formats with magic and are out of scope here.

## Impact

- `format-detection`: the table-aggregation requirement.
- `dev-docs/formats/brotli.md`.
