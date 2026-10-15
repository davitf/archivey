"""The RAR5 owner record (``rar -ow``) fills uid, gid, uname and gname."""

from __future__ import annotations

import io
import os
import subprocess
from pathlib import Path

import pytest

from archivey import open_archive
from archivey.diagnostics import DiagnosticCode, MemberHeaderRecordContext
from tests.conftest import requires_binary
from tests.test_rar_header_record_leniency import _only_record, _shrink_record

_XTYPE_OWNER = 6

# One member ``a.txt`` ("hi\n"), written by RAR 7.00 with ``-ow`` on Linux. RAR stores
# the owner's names when the system resolves them, and the numeric ids otherwise.
_OWNER_NAMES = bytes.fromhex(
    "526172211a0701003392b5e50a010506000501018080006fdfc2fd300203188300048300a483027a"
    "7a6fed80000105612e7478740a031364d7d06a9e3ac2200c060304726f6f7404726f6f7468690a1d"
    "77565103050400"
)
_OWNER_IDS = bytes.fromhex(  # owned by 1234:5678, which has no names
    "526172211a0701003392b5e50a01050600050101808000b6b3f52a2a0203128300048300a483027a"
    "7a6fed80000105612e7478740a031364d7d06a9e3ac22006060cd209ae2c68690a1d775651030504"
    "00"
)


@pytest.mark.parametrize(
    ("data", "owner"),
    [
        pytest.param(_OWNER_NAMES, (None, None, "root", "root"), id="names"),
        pytest.param(_OWNER_IDS, (1234, 5678, None, None), id="ids"),
    ],
)
def test_owner_record_fills_the_owner_fields(
    data: bytes, owner: tuple[int | None, int | None, str | None, str | None]
) -> None:
    with open_archive(io.BytesIO(data)) as archive:
        (member,) = archive.members()
        assert (member.uid, member.gid, member.uname, member.gname) == owner
        assert not member.diagnostics


def test_a_short_owner_record_is_dropped_with_a_diagnostic() -> None:
    record = _only_record(_OWNER_IDS, _XTYPE_OWNER)
    data = _shrink_record(_OWNER_IDS, record, new_size=3)
    with open_archive(io.BytesIO(data)) as archive:
        (member,) = archive.members()
        assert (member.uid, member.gid) == (None, None)
        (diagnostic,) = member.diagnostics
        assert diagnostic.code is DiagnosticCode.MEMBER_HEADER_RECORD_SKIPPED
        assert isinstance(diagnostic.context, MemberHeaderRecordContext)
        assert diagnostic.context.record == "owner"
        assert archive.read(member) == b"hi\n"


@requires_binary("rar")
@pytest.mark.skipif(not hasattr(os, "getuid"), reason="needs Unix owners")
def test_rar_ow_records_the_current_owner(tmp_path: Path) -> None:
    import grp
    import pwd

    (tmp_path / "a.txt").write_bytes(b"data")
    subprocess.run(
        ["rar", "a", "-ow", "-idq", "a.rar", "a.txt"], cwd=tmp_path, check=True
    )
    st = (tmp_path / "a.txt").stat()
    with open_archive(tmp_path / "a.rar") as archive:
        member = archive.get("a.txt")
        assert member.uname == pwd.getpwuid(st.st_uid).pw_name
        assert member.gname == grp.getgrgid(st.st_gid).gr_name
