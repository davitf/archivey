"""The RAR owner records (``rar -ow``) fill uid, gid, uname and gname.

RAR5 keeps the owner in a file header extra record; RAR 1.5-4 in a ``UOW`` service
header right after the file. The malformed-record case is in
``test_rar_header_record_leniency.py`` with the other extra records.
"""

from __future__ import annotations

import io
import os
import subprocess
from pathlib import Path

import pytest

from archivey import open_archive
from archivey.config import ArchiveyConfig, ListingLimits
from archivey.exceptions import ResourceLimitError
from archivey.internal.listing_limits import member_metadata_bytes
from tests.conftest import requires_binary

# One member ``a.txt`` ("hi\n"), written with ``-ow`` on Linux. RAR 7.00 stores the
# owner's names when the system resolves them and the numeric ids otherwise. RAR 6.23
# with ``-ma4`` stores names only, and writes no UOW header when there are none.
OWNER_NAMES_RAR5 = bytes.fromhex(
    "526172211a0701003392b5e50a010506000501018080006fdfc2fd300203188300048300a483027a"
    "7a6fed80000105612e7478740a031364d7d06a9e3ac2200c060304726f6f7404726f6f7468690a1d"
    "77565103050400"
)
OWNER_IDS_RAR5 = bytes.fromhex(  # owned by 1234:5678, which has no names
    "526172211a0701003392b5e50a01050600050101808000b6b3f52a2a0203128300048300a483027a"
    "7a6fed80000105612e7478740a031364d7d06a9e3ac22006060cd209ae2c68690a1d775651030504"
    "00"
)
OWNER_NAMES_RAR4 = bytes.fromhex(
    "526172211a0700cf907300000d00000000000000ee457420902a000300000003000000037a7a6fed"
    "d66c4f5d1d300500a4810000612e74787400b0bfdc5368690ad1d17a10802c000000000000000000"
    "0300000000e56e4f5d1d30030000000000554f57726f6f7400726f6f74c43d7b00400700"
)


@pytest.mark.parametrize(
    ("data", "owner"),
    [
        pytest.param(OWNER_NAMES_RAR5, (None, None, "root", "root"), id="rar5-names"),
        pytest.param(OWNER_IDS_RAR5, (1234, 5678, None, None), id="rar5-ids"),
        pytest.param(OWNER_NAMES_RAR4, (None, None, "root", "root"), id="rar4-names"),
    ],
)
def test_owner_record_fills_the_owner_fields(
    data: bytes, owner: tuple[int | None, int | None, str | None, str | None]
) -> None:
    with open_archive(io.BytesIO(data)) as archive:
        (member,) = archive.members()
        assert (member.uid, member.gid, member.uname, member.gname) == owner
        assert not member.diagnostics
        assert archive.read(member) == b"hi\n"


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(OWNER_NAMES_RAR5, id="rar5"),
        pytest.param(OWNER_NAMES_RAR4, id="rar4"),
    ],
)
def test_owner_names_count_at_parse(data: bytes) -> None:
    # The header walk weighs what the member table keeps before any member is
    # registered; the owner names are part of it, so the walk refuses first.
    with open_archive(io.BytesIO(data)) as archive:
        (member,) = archive.members()
    weight = member_metadata_bytes(member)
    assert weight == 2 * len("a.txt") + len("root") + len("root")

    def limited(max_metadata_bytes: int) -> ArchiveyConfig:
        return ArchiveyConfig(
            listing_limits=ListingLimits(max_metadata_bytes=max_metadata_bytes)
        )

    with pytest.raises(ResourceLimitError, match=r"RAR headers of \d+ members hold"):
        open_archive(io.BytesIO(data), config=limited(weight - 1))
    with open_archive(io.BytesIO(data), config=limited(weight)) as archive:
        assert archive.members()[0].uname == "root"


@requires_binary("rar")
@pytest.mark.skipif(not hasattr(os, "getuid"), reason="needs Unix owners")
def test_rar_ow_records_the_current_owner(tmp_path: Path) -> None:
    import grp
    import pwd

    (tmp_path / "a.txt").write_bytes(b"data")
    st = (tmp_path / "a.txt").stat()
    try:
        uname = pwd.getpwuid(st.st_uid).pw_name
        gname = grp.getgrgid(st.st_gid).gr_name
    except KeyError:
        pytest.skip("the file's owner has no name, so rar stores numeric ids")
    subprocess.run(
        ["rar", "a", "-ow", "-idq", "a.rar", "a.txt"], cwd=tmp_path, check=True
    )
    with open_archive(tmp_path / "a.rar") as archive:
        member = archive.get("a.txt")
        assert (member.uname, member.gname) == (uname, gname)
        # With names to store, rar stores no ids.
        assert (member.uid, member.gid) == (None, None)
