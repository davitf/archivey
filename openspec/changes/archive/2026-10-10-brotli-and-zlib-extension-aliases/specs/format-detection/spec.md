## MODIFIED Requirements

### Requirement: Magic/extension/probe tables are aggregated from backends and codec descriptors

Detector tables SHALL come from container backends (`ReadBackend.MAGIC` /
`EXTENSIONS` / `CONTENT_PROBES`) and stream-codec descriptors — no per-format
`detect()` logic. Stream-codec rows come from descriptors (not hand-listed on
`SingleFileBackend`). A content probe is the codec's `content_probe` function.
A codec's extensions are its canonical one, derived from its format, then its
`extension_aliases`; today `.zlib` for zlib and `.brotli` for Brotli, each with a `.tar.`
form. Both formats are found only by a probe, which runs only for a name that claims
the format, so a common alias is the difference between a file that opens and one
that is refused.
Detected formats and `detected_by` MUST match prior behavior. Confidence MUST also
match prior behavior **except** for an uncorroborated Brotli content-probe match,
which reports `GUESS` (see the magic-less-formats requirement).

#### Scenario: table sources matrix

| Case | Expected |
| --- | --- |
| `.gz` / `.zst` | Same result as before; magic from codec descriptors |
| zlib / LZMA Alone | `PROBABLE` / `content_probe` from descriptor functions — unchanged |
| Brotli, extension corroborates | `PROBABLE` / `content_probe` |
| Brotli, first meta-block compressed, no corroborating extension | `PROBABLE` / `content_probe` |
| Brotli, first meta-block uncompressed/metadata, no corroborating extension | `GUESS` / `content_probe` |
| ZIP / TAR / ISO | Container backend `MAGIC`, merged into the same table |
| `x.brotli` / `x.tar.brotli` holding Brotli | Brotli probe runs; `BROTLI` / `TAR` × `BROTLI`, corroborated |
| `x.zlib` / `x.tar.zlib` holding zlib | zlib probe runs; `ZLIB` / `TAR` × `ZLIB`, corroborated |
