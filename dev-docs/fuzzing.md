# Coverage-guided fuzz (Atheris)

Atheris lives in the PEP 735 `fuzz` dependency group (`atheris`) and runs via
`.github/workflows/atheris-fuzz.yml` — same shape as the benchmark wall split:

- **Every PR:** short partitioned budgets over all targets (blocks the PR; sharded
  across parallel jobs because each target pays a large Atheris cold-start).
- **Nightly schedule:** full partition, but only if default-branch HEAD moved in the
  last ~3 days (commit-recency guard; dormant stretches skip the expensive run).
- **`workflow_dispatch`:** force the full partition (optional `budget_scale`).

Mutation fuzz (`tests/test_mutation_fuzz.py`) and `ARCHIVEY_FUZZ=1` /
`tests/fuzz_sevenzip_parser.py` / `tests/fuzz_rar_parser.py` stay as they are.

Local smoke (Linux; needs corpus fixture builders). Prefer Python 3.12 for current
Atheris wheels; on 3.11 `uv` resolves `atheris` 3.0.x:

```bash
uv sync --group fuzz --group dev --extra all
uv run --no-sync python -m tests.atheris_fuzz --smoke

# or explicitly:
uv sync --python 3.12 --group fuzz --group dev --extra all
uv run --python 3.12 --no-sync python -m tests.atheris_fuzz --smoke
```

Deepen one target (budget seconds via env, e.g. `ARCHIVEY_FUZZ_BUDGET_SEVENZIP_HEADER=60`,
`ARCHIVEY_FUZZ_BUDGET_ZIP=60`, or `ARCHIVEY_FUZZ_BUDGET_UNIX_COMPRESS=60`):

```bash
uv run --no-sync python -m tests.atheris_fuzz --target sevenzip_header
uv run --no-sync python -m tests.atheris_fuzz --target zip
uv run --no-sync python -m tests.atheris_fuzz --target unix_compress
```

On a crash the harness writes the input under `artifacts/atheris/` and prints a one-line
repro command.
