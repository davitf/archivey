## ADDED Requirements

### Requirement: Decode TAR member names as UTF-8 by default

When the caller does not pass `encoding=`, the TAR backend SHALL decode ustar and GNU
long-name fields, and the other header strings `tarfile` decodes with the archive codec
(`uname`, `gname`, `linkname`), as UTF-8 with `errors="surrogateescape"`. The result MUST
NOT depend on the process locale or `sys.getfilesystemencoding()`. A caller-passed
`encoding=` SHALL replace UTF-8 for those fields, with the same error handler. A PAX
record is decoded as UTF-8 either way (the archive codec only for `hdrcharset=BINARY` or
when the UTF-8 decode fails).

#### Scenario: TAR default name decoding

| Case | Expected |
| --- | --- |
| ustar or GNU long name stored as UTF-8 `café.txt`, no `encoding=`, filesystem encoding Latin-1 or ASCII | `name == "café.txt"`; `raw_name` is the UTF-8 bytes |
| ustar name stored as Latin-1 `caf\xe9.txt`, no `encoding=`, any locale | `name == "caf\udce9.txt"`; `raw_name == b"caf\xe9.txt"` |
| ustar name stored as UTF-8 `café.txt`, `encoding="latin-1"` | `name == "cafÃ©.txt"`; `raw_name` is the UTF-8 bytes |
