## 1. Reader

- [x] 1.1 `GzipDecoder` records member starts with output as seek points on declared seek demand (`index=config.seekable`)
- [x] 1.2 `codecs/bgzf_index.py` walks BGZF members by their `BC` subfield and ISIZE, one read per member, thinned over the cap

## 2. Tests and docs

- [x] 2.1 `tests/test_bgzf_seek.py`: size and points from the walk, block resume, other subfields, plain and multi-member gzip, a plain member after BGZF, trailing zeros, undeclared streams, the O17 pin, thinning, a real `bgzip` file when the tool is installed
- [x] 2.2 gzip handbook page, single-file page and threat-model O17 name BGZF; the blocked-gzip proposal keeps only mgzip
- [x] 2.3 `openspec validate --strict gzip-member-seek-points`, then archive
