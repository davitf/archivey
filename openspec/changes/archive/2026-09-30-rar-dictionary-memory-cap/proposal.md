# Count the RAR dictionary against the decoder memory cap

## Why

A RAR file header declares the dictionary its decompressor uses, up to 4 GiB on RAR5,
and archivey did not check it: `unrar` or `unar` decodes in another process, so
`DecoderLimits.max_decoder_memory` never applied. Every in-process codec checks the
dictionary or window its header declares; RAR was the one format outside the cap, and
`format-rar` said nothing about it.

## What changes

The reader checks the dictionary before it starts `unrar` or `unar`, and raises
`ResourceLimitError` when the count is over the cap. The count is what the program that
will run allocates, measured per program (`dev-docs/formats/rar.md` §7): `unar` touches
the whole declared dictionary; `unrar` touches at most the unpacked bytes the read
decodes, which includes the earlier members a shared name mask selects.

## Impact

- `format-rar`: one added requirement with its matrix.
- Code: `rar_parser.py` (`RarMemberInfo.dictionary_size`), `rar_reader.py`, `rar_unar.py`,
  `internal/config.py` (`check_decoder_memory` reports the declared value beside the
  count).
