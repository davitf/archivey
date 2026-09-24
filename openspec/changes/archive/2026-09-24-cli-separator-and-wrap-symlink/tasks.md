# Tasks — the `--` separator and the CLI's own wrap directory

## 1. Code

- [x] 1.1 `_inject_default_list` inserts `list` ahead of a `--` separator.
- [x] 1.2 `_enclosing_dir` probes with `lexists` and steps aside from a symlink at the
      container name under every overwrite policy.

## 2. Proof and documents

- [x] 2.1 Red-green tests in `tests/test_cli.py` for both.
- [x] 2.2 `docs/cli.md` Notes and CHANGELOG.
- [x] 2.3 `openspec validate --strict cli-separator-and-wrap-symlink`, then archive this change.
