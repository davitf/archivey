"""Every ZIP the tests have, read from a pipe and from a file: the same members after
the pass, the same data or failure for each, and the same files on disk.

The fixtures are the committed ``.zip`` files, the adversarial set
(``tests/create_adversarial.py``), and the corpus entries with encrypted members,
under the right password, no password, a wrong one, and a list. The corpus entries
without passwords are compared in ``test_zip_pipe.py`` and
``test_zip_pipe_extract.py``. Where the two reads may differ is in the native ZIP
reader's ``design.md`` (§"Failures found at the end of the pass"); ``_DIFFERS`` names
the fixtures that reach it.
"""

from __future__ import annotations

import contextlib
import io
import struct
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

import archivey
from archivey.exceptions import ArchiveyError, UnsupportedFeatureError
from archivey.types import OnError
from tests.create_adversarial import adversarial_archives
from tests.sample_archives import CORPUS, build_archive, skip_unless_runnable
from tests.streams_util import NonSeekableBytesIO
from tests.test_zip_pipe import _visible
from tests.test_zip_pipe_extract import _tree

_FIXTURES = Path(__file__).parent / "fixtures"

# Committed fixtures with encrypted members: the right password, and wrong ones that
# pass a member's check byte (tests/fixtures/zipcrypto/README.md).
_FIXTURE_PASSWORDS: dict[str, tuple[str, ...]] = {
    "zipcrypto/check_byte_collision.zip": ("secret", "wrong896", "wrong173"),
    "external/aes_ae1_pyzipper036.zip": ("secret",),
    "sevenzip/encrypted_link.zip": ("SECRET",),
}

_PasswordArg = str | list[str] | None

# Where the two reads may differ, by test id. ``"error"``: the same results but for
# the error type, the files on disk the same. A link the pass wrote as a file keeps
# the error its data read gave (no password, ``EncryptionError``); from a file the
# link is refused for its missing target (``LinkTargetNotFoundError``).
# ``"disk"``: the directory refuses the archive, which a file read finds before
# writing anything and the pass only after writing every member.
# The file read is to stop refusing these (design.md; tasks 3.0c and 4.1), which
# removes the rows.
_DIFFERS: dict[str, str] = {
    "sevenzip/encrypted_link.zip-no-password": "error",
    "sevenzip/encrypted_link.zip-wrong": "error",
    "adversarial-zip-name-wtf8-flagged": "disk",
    "adversarial-zip-name-invalid-utf8-flagged": "disk",
    "adversarial-zip-name-overlong-utf8-flagged": "disk",
}


def _password_cases(passwords: tuple[str, ...]) -> Iterator[tuple[str, _PasswordArg]]:
    """No password, each one alone, a wrong one, and all of them after a wrong one."""
    if not passwords:
        yield "", None
        return
    yield "no-password", None
    for password in passwords:
        yield f"password={password}", password
    yield "wrong", "not-the-password"
    yield "list", ["not-the-password", *passwords]


def _fixture_params() -> Iterator[pytest.ParameterSet]:
    for path in sorted(_FIXTURES.rglob("*.zip")):
        rel = path.relative_to(_FIXTURES).as_posix()
        for label, password in _password_cases(_FIXTURE_PASSWORDS.get(rel, ())):
            ident = f"{rel}-{label}" if label else rel
            yield pytest.param(
                path.read_bytes(), password, _DIFFERS.get(ident), id=ident
            )
    for entry, data in adversarial_archives():
        if entry.fmt == "zip":
            ident = f"adversarial-{entry.id}"
            yield pytest.param(data, None, _DIFFERS.get(ident), id=ident)


def _corpus_params() -> Iterator[pytest.ParameterSet]:
    for entry in CORPUS:
        if not entry.passwords:
            continue
        for key in ("zip", "zip-aes"):
            if key in entry.formats:
                for label, password in _password_cases(entry.passwords):
                    yield pytest.param(
                        entry, key, password, id=f"{entry.id}-{key}-{label}"
                    )


def _outcome(read: object) -> object:
    try:
        return read()  # type: ignore[operator]
    except ArchiveyError as exc:
        return type(exc)


def _from_file(data: bytes, password: _PasswordArg) -> object:
    try:
        with archivey.open_archive(io.BytesIO(data), password=password) as reader:
            members = reader.members()
            contents = {
                m._member_id: _outcome(lambda m=m: reader.read(m))  # type: ignore[misc]
                for m in members
                if m.is_file
            }
            return [_visible(m) for m in members], contents
    except ArchiveyError as exc:
        return type(exc)


def _from_pipe(data: bytes, password: _PasswordArg) -> object:
    try:
        with archivey.open_archive(
            NonSeekableBytesIO(data), streaming=True, password=password
        ) as reader:
            contents = {}
            for member, stream in reader.stream_members():
                if stream is not None:
                    contents[member._member_id] = _outcome(stream.read)  # type: ignore[misc]
            members = list(reader.members_report().members)
            # The pass reads every member as its local header says; one the
            # directory makes no file is not compared, as the file read reads none.
            contents = {
                m._member_id: contents.get(m._member_id)  # type: ignore[misc]
                for m in members
                if m.is_file
            }
            return [_visible(m) for m in members], contents
    except ArchiveyError as exc:
        return type(exc)


def _extracted(
    data: bytes, dest: Path, *, piped: bool, password: _PasswordArg
) -> object:
    source = NonSeekableBytesIO(data) if piped else io.BytesIO(data)
    try:
        with archivey.open_archive(
            source, streaming=piped, password=password
        ) as reader:
            report = reader.extract_all(dest, on_error=OnError.CONTINUE)
    except ArchiveyError as exc:
        return type(exc)
    return [
        (
            r.member.name,
            r.status,
            None if r.path is None else r.path.relative_to(dest).as_posix(),
            None if r.error is None else type(r.error),
        )
        for r in report.results
    ]


def _assert_same(
    data: bytes, password: _PasswordArg, tmp_path: Path, differs: str | None = None
) -> None:
    assert _from_pipe(data, password) == _from_file(data, password)
    started = time.time() - 1
    from_file = _extracted(data, tmp_path / "file", piped=False, password=password)
    from_pipe = _extracted(data, tmp_path / "pipe", piped=True, password=password)
    if differs == "error":
        assert isinstance(from_file, list) and isinstance(from_pipe, list)
        from_file = [result[:3] for result in from_file]
        from_pipe = [result[:3] for result in from_pipe]
    assert from_pipe == from_file
    for side in ("file", "pipe"):
        (tmp_path / side).mkdir(exist_ok=True)
    piped_tree = _tree(tmp_path / "pipe", since=started)
    tree = _tree(tmp_path / "file", since=started)
    if differs == "disk":
        assert tree == {} != piped_tree
    else:
        assert piped_tree == tree


@pytest.mark.filterwarnings("ignore:Duplicate name")
@pytest.mark.parametrize(("data", "password", "differs"), list(_fixture_params()))
def test_a_fixture_reads_from_a_pipe_as_from_a_file(
    data: bytes, password: _PasswordArg, differs: str | None, tmp_path: Path
) -> None:
    _assert_same(data, password, tmp_path, differs)


@pytest.mark.parametrize(("entry", "key", "password"), list(_corpus_params()))
def test_an_encrypted_corpus_entry_reads_from_a_pipe_as_from_a_file(
    entry: object, key: str, password: _PasswordArg, tmp_path: Path
) -> None:
    skip_unless_runnable(entry, key)  # type: ignore[arg-type]
    path = tmp_path / "corpus.zip"
    build_archive(entry, key, path)  # type: ignore[arg-type]
    _assert_same(path.read_bytes(), password, tmp_path / "out")


def test_an_encrypted_directory_writes_nothing_either_way(tmp_path: Path) -> None:
    """PKWARE Strong Encryption over the directory masks the local headers too and
    encrypts every member's data with it: neither read gets a member out."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.txt", b"data")
    raw = bytearray(buf.getvalue())
    flags = struct.unpack_from("<H", raw, 6)[0]
    struct.pack_into("<H", raw, 6, flags | 0x41)  # encrypted, Strong Encryption
    cd = raw.index(b"PK\x01\x02")
    body = raw.rindex(b"PK\x05\x06") - cd - 8
    raw[cd : cd + 8 + body] = b"PK\x06\x08" + struct.pack("<I", body) + b"\xa5" * body
    assert _from_file(bytes(raw), None) is UnsupportedFeatureError
    _assert_same(bytes(raw), None, tmp_path)
    assert not any((tmp_path / "pipe").iterdir())


# --- A ZIP with no end record ------------------------------------------------------


def _members_zip(*, pipe: bool) -> bytes:
    """Three deflated members, sized when written to a file, bit 3 when written to a
    sink that cannot seek."""
    out: io.BytesIO | NonSeekableSink = NonSeekableSink() if pipe else io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for n in range(3):
            zf.writestr(f"m{n}.txt", bytes(range(256)) * (n + 2))
    return bytes(out.getvalue())


class NonSeekableSink(io.RawIOBase):
    """Where stdlib ``zipfile`` writes bit-3 members: it cannot seek back."""

    def __init__(self) -> None:
        super().__init__()
        self._data = bytearray()

    def writable(self) -> bool:
        return True

    def write(self, b: bytes | bytearray | memoryview) -> int:  # type: ignore[override]
        self._data += b
        return len(b)

    def getvalue(self) -> bytes:
        return bytes(self._data)


def _no_end_record_params() -> Iterator[pytest.ParameterSet]:
    """A ZIP cut at each kind of place before its end record, or with bytes that are
    no ZIP record where the directory or the end record should be.

    The second value says whether the two reads raise in the same words. A bit-3
    member followed by junk is the exception: the pass decodes it and looks for a
    descriptor with its CRC, while the file read, which decodes nothing, looks for
    one followed by a ZIP signature and reaches the end of the file; both fail it."""
    for kind, data in (
        ("sized", _members_zip(pipe=False)),
        ("bit3", _members_zip(pipe=True)),
    ):
        directory = data.index(b"PK\x01\x02")
        second = data.index(b"PK\x03\x04", 4)
        end = data.rindex(b"PK\x05\x06")
        shapes = {
            "before-the-directory": data[:directory],
            "inside-the-directory": data[: directory + 30],
            "before-the-end-record": data[:end],
            "inside-a-local-header": data[: second + 10],
            "inside-a-member": data[: second + 60],
            "junk-after-the-members": data[:directory] + b"junk" * 16,
            "junk-after-the-directory": data[:end] + b"junk" * 16,
            "junk-past-the-search-window": data[:directory] + b"\x01" * (1 << 20),
        }
        for shape, cut in shapes.items():
            alike = not (kind == "bit3" and shape.startswith("junk-after-the-members"))
            alike = alike and (kind, shape) != ("bit3", "junk-past-the-search-window")
            yield pytest.param(cut, alike, id=f"{kind}-{shape}")


def _report(data: bytes, *, piped: bool) -> object:
    source = NonSeekableBytesIO(data) if piped else io.BytesIO(data)
    try:
        with archivey.open_archive(source, streaming=piped) as reader:
            if piped:
                # The pass raises the walk's error; the report then holds it.
                with contextlib.suppress(ArchiveyError):
                    for _member, _stream in reader.stream_members():
                        pass
            report = reader.members_report()
    except ArchiveyError as exc:
        return type(exc), str(exc)
    error = report.error
    return (
        [_visible(m) for m in report.members],
        None if error is None else (type(error), str(error)),
    )


@pytest.mark.parametrize(("data", "alike"), list(_no_end_record_params()))
def test_a_zip_with_no_end_record_lists_from_a_file_as_from_a_pipe(
    data: bytes, alike: bool, tmp_path: Path
) -> None:
    """The members its local headers hold are listed, then the error the walk met is
    raised, in the same words; each member reads, and extracts, alike."""
    from_file = _report(data, piped=False)
    from_pipe = _report(data, piped=True)
    assert isinstance(from_file, tuple) and isinstance(from_file[0], list)
    assert from_file[0], "the members before the cut are listed"
    assert from_file[1] is not None
    if alike:
        assert from_file == from_pipe
    else:
        assert isinstance(from_pipe, tuple) and from_pipe[1] is not None
        assert from_file[0] == from_pipe[0]
        assert "member #2" in from_file[1][1] and "member #2" in from_pipe[1][1]
    if alike:
        _assert_same(data, None, tmp_path)
        return
    started = time.time() - 1
    for side in ("file", "pipe"):
        _extracted(data, tmp_path / side, piped=side == "pipe", password=None)
        (tmp_path / side).mkdir(exist_ok=True)
    tree = _tree(tmp_path / "file", since=started)
    assert tree and _tree(tmp_path / "pipe", since=started) == tree


def _descriptor_stating_size(size: int) -> bytes:
    """One bit-3 deflated member with 8-byte descriptor sizes, its descriptor stating
    an uncompressed size of ``size``, cut inside the directory after it."""
    out = NonSeekableSink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        with zf.open("a.txt", "w", force_zip64=True) as member:
            member.write(b"hello world")
    data = bytearray(out.getvalue())
    at = data.index(b"PK\x07\x08")
    struct.pack_into("<Q", data, at + 16, size)
    return bytes(data[: data.index(b"PK\x01\x02") + 30])


@pytest.mark.parametrize("size", [2**62, 2**64 - 1])
def test_a_descriptor_a_file_read_cannot_check_is_listed_as_it_states(
    size: int,
) -> None:
    """The file read decodes nothing at open, so it lists a deflated bit-3 member's
    size as its descriptor states it, as it lists a directory's; the pipe read
    measures it while decoding. Both refuse the data, and both raise the cut."""
    data = _descriptor_stating_size(size)
    with archivey.open_archive(io.BytesIO(data)) as reader:
        report = reader.members_report()
        (member,) = report.members
        assert member.size == size
        with pytest.raises(ArchiveyError):
            reader.read(member)
    with archivey.open_archive(NonSeekableBytesIO(data), streaming=True) as reader:
        with contextlib.suppress(ArchiveyError):
            for _member, stream in reader.stream_members():
                assert stream is not None
                with pytest.raises(ArchiveyError):
                    stream.read()
        piped = reader.members_report()
    assert [m.size for m in piped.members] == [11]
    assert type(piped.error) is type(report.error)
    assert str(piped.error) == str(report.error)
