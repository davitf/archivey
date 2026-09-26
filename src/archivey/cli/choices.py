"""The command-line spellings of the enum-valued options, and the way back to the enum.

``--policy``, ``--overwrite`` and ``--abort-on`` offer each enum member under one
spelling: its value with ``_`` written as ``-``. Both directions live here so they
cannot drift apart. The way back looks the spelling up among the members instead of
undoing the substitution, so a member whose value already holds a ``-`` still maps
back to itself.
"""

from __future__ import annotations

from enum import Enum
from typing import TypeVar

from archivey.cli.errors import CliError
from archivey.cli.exit_codes import EXIT_USAGE

E = TypeVar("E", bound=Enum)


def cli_choice(member: Enum) -> str:
    """The one spelling this CLI advertises for ``member``."""
    return str(member.value).replace("_", "-")


def cli_choices(enum_cls: type[Enum]) -> list[str]:
    """The spellings this CLI advertises for an enum, derived from the enum itself.

    Written down in one place so a member added to ``AbortOn`` or ``OverwritePolicy``
    reaches the command line the day it is declared. A literal list here would silently
    make the CLI accept less than the library does, which is what it used to do.
    """
    return [cli_choice(member) for member in enum_cls]


def from_cli_choice(enum_cls: type[E], choice: str) -> E:
    """The member ``choice`` names, where ``choice`` is one of ``cli_choices(enum_cls)``.

    argparse has already checked the spelling against ``choices=``, so a miss here means
    a caller reached the command code without the parser. It is refused as a usage
    error that names the accepted spellings, not as a bare ``ValueError``.
    """
    for member in enum_cls:
        if cli_choice(member) == choice:
            return member
    accepted = ", ".join(cli_choices(enum_cls))
    raise CliError(
        f"invalid choice {choice!r} (choose from {accepted})", code=EXIT_USAGE
    )
