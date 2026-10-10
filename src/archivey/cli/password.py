"""Password helpers for the CLI."""

from __future__ import annotations

import getpass
import sys

from archivey.config import PasswordInput, PasswordRequest


def resolve_password(cli_password: str | None) -> PasswordInput:
    """Return a password value or a TTY ``getpass`` provider when none was given."""
    if cli_password is not None:
        return cli_password

    def provider(request: PasswordRequest) -> str | None:
        if not sys.stdin.isatty():
            return None
        # A later ask always follows a failed answer, so say so: otherwise a person who
        # typed the wrong password sees the same prompt again with no reason why.
        prompt = "Password: " if request.attempt == 1 else "Wrong password, try again: "
        try:
            return getpass.getpass(prompt)
        except EOFError:
            # Ctrl-D / end-of-input at the prompt → treat as "no password given".
            return None

    return provider
