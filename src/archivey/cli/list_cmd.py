"""``list`` / ``l`` verb."""

from __future__ import annotations

import sys
from typing import TextIO

from archivey.cli.common import open_for_cli
from archivey.cli.exit_codes import EXIT_FAIL, EXIT_OK
from archivey.cli.filters import MemberSelection
from archivey.cli.format import (
    LONG_HEADER,
    format_member_details,
    format_member_line,
    format_member_long_line,
)
from archivey.cli.password import resolve_password
from archivey.config import ArchiveyConfig, PasswordInput


def run_list(
    *,
    archive: str,
    patterns: list[str],
    exclude: list[str],
    digests: bool,
    long: bool = False,
    details: bool = False,
    verbose: bool,
    password: str | None,
    track_io: bool,
    config: ArchiveyConfig | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    pwd: PasswordInput = resolve_password(password)
    selection = MemberSelection(patterns, exclude)

    with open_for_cli(
        archive, password=pwd, track_io=track_io, config=config, err=err
    ) as reader:
        report = reader.members_report()
        # Select before printing, so the pattern warnings come first. ``list`` exits 0
        # when the patterns select nothing: the listing itself succeeded. A listing
        # that ends in damage gives no pattern warning; its error is printed below.
        selected = selection.settle_from(report, err=err)
        if long and selected:
            print(LONG_HEADER, file=out)
        for index, member in enumerate(selected):
            if details:
                if index:
                    print(file=out)
                for line in format_member_details(member):
                    print(line, file=out)
            elif long:
                print(format_member_long_line(member), file=out)
            else:
                print(
                    format_member_line(member, digests=digests, verbose=verbose),
                    file=out,
                )
        if report.error is not None:
            print(f"archivey: {report.error}", file=err)
            return EXIT_FAIL
    return EXIT_OK
