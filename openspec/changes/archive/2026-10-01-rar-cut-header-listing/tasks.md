## 1. RAR header cut

- [x] 1.1 The RAR3 and RAR5 header readers signal a file that ends before a header's
      declared bytes; both plain walks list the members before it and record the cut
- [x] 1.2 Tests in `tests/test_audit2_cross_format.py` and `tests/test_rar_parser.py`;
      docs and handbook updated
- [x] 1.3 `openspec validate --strict rar-cut-header-listing`, then archive
