"""CLI exit codes (argparse-aligned)."""

from __future__ import annotations

EXIT_OK = 0
EXIT_FAIL = 1  # operational failure / aborted extract (STOP-path failure / always-stop)
EXIT_USAGE = 2  # CLI usage error (argparse default)
# Q8 Option A: completed extract with ≥1 policy BLOCKED and no FAILED (safe members on disk).
# Applies under CONTINUE or STOP — STOP never aborts on a policy block.
EXIT_POLICY = 3
# 4 to 127 reserved
# Interrupted by Ctrl-C: 128 + SIGINT (2), the shell's convention for a signal.
EXIT_INTERRUPTED = 130
# Output pipe closed by its reader (``archivey list x | head``): 128 + SIGPIPE (13).
# Used on Windows too, which has no SIGPIPE, so one code means the same everywhere.
EXIT_BROKEN_PIPE = 141
