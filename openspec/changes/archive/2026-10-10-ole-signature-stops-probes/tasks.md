## 1. OLE signature

- [x] 1.1 `_PROBE_STOPPING_SIGNATURES` in `detection.py` stops the content probes and the
      SFX scan; the `FormatDetectionError` names the signature, or the strong executable
      cue
- [x] 1.2 Tests in `tests/test_audit_backup_scan.py` (xfail markers removed from the two
      OLE reproducers) and `tests/test_sfx.py`; handbook pages updated
- [x] 1.3 `openspec validate --strict ole-signature-stops-probes`, then archive
