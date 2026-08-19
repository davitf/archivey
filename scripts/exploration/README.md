# Exploration scripts

## Brotli content probe — field survey

`brotli_probe_field_survey.py` collects the evidence
`dev-docs/investigations/brotli-content-probe-results.md` is thinnest on: PE/DOS header
shapes across real toolchains (especially the `e_lfanew` maximum), the WBITS values real
Brotli streams use, and how often the content probe claims ordinary files.

Stdlib-only and Python 3.8+, so it runs on a bare system interpreter — no venv, no
archivey, on whatever machine you point it at. Read-only; it writes one JSON report and
uploads nothing. Names are basenames by default (`--no-names` / `--full-paths` to change
that).

```bash
python3 scripts/exploration/brotli_probe_field_survey.py --self-test
python3 scripts/exploration/brotli_probe_field_survey.py            # system roots
python3 scripts/exploration/brotli_probe_field_survey.py ~/Downloads --out report.json
```

Windows and macOS runs are the valuable ones — the results doc was measured entirely on
one Linux container.

## ZipCrypto disambiguation — exploration notes

Scripts supporting
`openspec/changes/zip-multipassword-disambiguation` tasks 1.1 and 1.3.

| Script | Purpose |
|--------|---------|
| `zipcrypto_codec_rejection.py` | How quickly stdlib DEFLATE / BZIP2 / LZMA reject random and wrong-key ZipCrypto plaintext |
| `zipcrypto_compressibility_probe.py` | Historical calibration of a STORED compressibility probe (investigated, then **dropped** — see `design.md`) |

```bash
uv run --no-sync python scripts/exploration/zipcrypto_codec_rejection.py
uv run --no-sync python scripts/exploration/zipcrypto_compressibility_probe.py
```

Findings are recorded in
`openspec/changes/zip-multipassword-disambiguation/design.md`
(section **Investigation findings**). Runtime STORED confirmation uses a shared CRC
pass only; the compressibility script is kept as a record, not as a live dependency.
