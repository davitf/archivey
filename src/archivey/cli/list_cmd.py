"""``list`` / ``l`` verb."""

from __future__ import annotations

import sys
from typing import TextIO

from archivey.cli.common import open_for_cli, reject_salvage
from archivey.cli.exit_codes import EXIT_FAIL, EXIT_OK
from archivey.cli.filters import MemberSelection
from archivey.cli.format import format_member_line
from archivey.cli.password import resolve_password
from archivey.config import PasswordInput


def run_list(
    *,
    archive: str,
    patterns: list[str],
    exclude: list[str],
    digests: bool,
    verbose: bool,
    salvage: bool,
    password: str | None,
    track_io: bool,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    reject_salvage(salvage)
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    pwd: PasswordInput = resolve_password(password)
    selection = MemberSelection(patterns, exclude)

    with open_for_cli(archive, password=pwd, track_io=track_io, err=err) as reader:
        report = reader.members_report()
        # Select before printing, so the pattern warnings come first. ``list`` exits 0
        # when the patterns select nothing: the listing itself succeeded.
        selected = [member for member in report if selection(member)]
        selection.report(err=err)
        for member in selected:
            print(
                format_member_line(member, digests=digests, verbose=verbose),
                file=out,
            )
        if report.error is not None:
            print(f"archivey: {report.error}", file=err)
            return EXIT_FAIL
    return EXIT_OK
