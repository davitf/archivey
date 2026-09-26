## ADDED Requirements

### Requirement: Decode Rock Ridge and plain names as UTF-8 first

The ISO backend SHALL decode Rock Ridge `NM` names, plain ISO 9660 identifiers and Rock
Ridge `SL` link targets strictly as UTF-8 first. Bytes that are not valid UTF-8 SHALL
decode with the caller's `encoding=` and `errors="surrogateescape"`, and without
`encoding=`, or when that codec raises on the bytes, a Rock Ridge `NM` name SHALL take
the name of the same file or directory in the Joliet tree, and the member SHALL carry
`MEMBER_NAME_ENCODING_INFERRED`. The counterpart is a Joliet file at the same extent,
the one whose name fits when several share it, or for a directory the Joliet parent of a
file found under it; its name is used only when its ASCII runs equal those of the
stored bytes. Failing that, the bytes SHALL decode as UTF-8 with
`errors="surrogateescape"`. Decoding MUST NOT raise. Joliet names SHALL decode as
UTF-16BE whatever `encoding=` says. `raw_name` SHALL be the stored bytes in the Rock
Ridge and plain namespaces. `ReadBackend.USES_ENCODING` SHALL be `True` for ISO.

#### Scenario: ISO name decoding

| Case | Expected |
| --- | --- |
| Rock Ridge name stored as Latin-1 `caf\xe9\xe9.txt`, no Joliet tree, no `encoding=` | `name == "caf\udce9\udce9.txt"`; `raw_name == b"caf\xe9\xe9.txt"` |
| Latin-1 Rock Ridge names beside a Joliet tree, no `encoding=` (`genisoimage -R -J -input-charset iso8859-1`) | Files and directories take their Joliet names, one `MEMBER_NAME_ENCODING_INFERRED` each; `raw_name` is the stored bytes |
| The same, where the Joliet name was cut at 64 characters | Escaped, no diagnostic |
| The same image, `encoding="cp1252"` | Names decode with cp1252; no diagnostic |
| The same image, `encoding="latin-1"` | `name == "caféé.txt"`; a symlink to it has `link_target == "caféé.txt"`; no `ENCODING_ARGUMENT_UNUSED` |
| Rock Ridge name stored as UTF-8 `café.txt`, `encoding="latin-1"` | `name == "café.txt"` |
| The Latin-1 image, `encoding="utf-32"` or `"idna"` | Lists; `name == "caf\udce9\udce9.txt"` |

### Requirement: Contain a System Use entry pycdlib cannot parse to its own record

While the ISO backend opens an image, the System Use bytes of each record SHALL be
filtered before `pycdlib` parses them, and only then: other code using `pycdlib` in the
same process MUST see `pycdlib`'s own behaviour. An entry of a type `pycdlib` does not
parse SHALL be skipped, as SUSP specifies. An entry whose header is malformed (a length
under 4 or past the area, or a version other than 1 on a type `pycdlib` parses) SHALL end
that record's area: the member SHALL list from the entries before it and carry
`MEMBER_HEADER_RECORD_SKIPPED` with `list_truncated=True` and an empty `record`. A
symlink cut this way SHALL list with `link_target=None` and `SYMLINK_TARGET_UNAVAILABLE`.
The other members of the image MUST NOT be affected.

#### Scenario: malformed Rock Ridge entries

| Case | Expected |
| --- | --- |
| A file whose `TF` entry has version 99 | Lists with its `PX` mode, reads, one `MEMBER_HEADER_RECORD_SKIPPED`; the other members carry nothing |
| A symlink cut the same way (genisoimage writes this for a target over 250 bytes) | `link_target is None`; `MEMBER_HEADER_RECORD_SKIPPED` then `SYMLINK_TARGET_UNAVAILABLE` |
| The same image under `DiagnosticPolicy.strict()` | Refused |
| `pycdlib.PyCdlib().open_fp` on a zisofs image, outside archivey | `pycdlib`'s own `Unknown SUSP record` |

### Requirement: Read zisofs members

A file whose Rock Ridge area carries a `ZF` entry SHALL list `size` from that entry,
`compressed_size` as its stored length, and one `DEFLATE` entry in `compression`. Reading
it SHALL return the decoded bytes, seekable by block, inflating one block at a time and
never past the block size. A block that inflates past its size, a damaged block, or a
header that disagrees with the `ZF` entry SHALL raise `CorruptionError`; data cut by the
end of the image SHALL raise `TruncatedError`. A `ZF` entry of version 2 (zisofs2), an
algorithm other than `pz`, a header size other than 16 bytes, or a block size outside 32
to 128 KiB SHALL list with `CompressionAlgorithm.UNKNOWN` and raise
`UnsupportedFeatureError` when read, without affecting other members.

#### Scenario: zisofs

| Case | Expected |
| --- | --- |
| `mkzftree` + `genisoimage -R -z`, or `xorriso -zisofs default` | Every member reads byte-for-byte as the source |
| Seek to several offsets across blocks | Each read matches the source |
| A block compressed from one byte more than the block size | `CorruptionError` naming the block |
| Image cut inside the header, the pointer table, or a block | `TruncatedError`; the member before it reads |
| zisofs2 member | Lists with `UNKNOWN`; read raises `UnsupportedFeatureError`; the member beside it reads |
