# DEFLATE and zlib accelerator: the verdicts of the standard library

## Why

The accelerator fuzz targets found three ways rapidgzip changed a verdict for raw DEFLATE
and zlib. rapidgzip is told no format, so a `deflate` source that starts like gzip or zlib,
and a `zlib` source that starts like gzip or raw DEFLATE, decoded as that other format where
the standard library raises at the header. rapidgzip ends a raw DEFLATE stream cut before
any output softly, as an empty one. And once the standard library took over from rapidgzip,
it raised a raw `zlib.error`, which the over-run probe of a declared size reads as the end of
the data: a stream declared empty that does not decode read as empty. An accelerator changes
speed, not behaviour.

## What changes

The codec checks the first two bytes before it starts rapidgzip, and leaves a source
rapidgzip could take for another format to the standard library. A raw DEFLATE stream that
rapidgzip ends before any output is decoded again by the standard library. After the
takeover, the standard library's errors are translated to the codec's typed errors where
they are raised.

## Impact

- `seekable-decompressor-streams`: the DEFLATE-family requirement and three matrix rows.
- `dev-docs/formats/gzip.md`: a paragraph in §2.3, a sharp-edge row, verify rows.
