"""The shared read-only stream bases (`ReadOnlyIOStream`, `DelegatingStream`)."""

from __future__ import annotations

import io
from typing import Never, get_type_hints

import pytest

from archivey.internal.streams.streamtools import DelegatingStream, ReadOnlyIOStream
from tests.streams_util import NonSeekableBytesIO


class _FixedReader(ReadOnlyIOStream):
    """Minimal subclass: only implements read(), to exercise the base's derived methods."""

    def __init__(self, data: bytes) -> None:
        super().__init__()
        self._buf = io.BytesIO(data)

    def read(self, n: int = -1, /) -> bytes:
        return self._buf.read(n)


def test_readonly_base_derives_readinto_readall_and_flags() -> None:
    s = _FixedReader(b"hello world")
    # readinto is derived from read()
    buf = bytearray(5)
    assert s.readinto(buf) == 5
    assert bytes(buf) == b"hello"
    # readall reads the rest via the read-loop
    assert s.readall() == b" world"
    assert s.readable() is True
    assert s.writable() is False
    with pytest.raises(io.UnsupportedOperation):
        s.write(b"x")


def test_readonly_readinto_raises_on_overlong_read() -> None:
    """A misbehaving read() that ignores n is a contract violation, not silent truncation."""

    class _OverRead(ReadOnlyIOStream):
        def read(self, n: int = -1, /) -> bytes:
            return b"abcdef"

    buf = bytearray(4)
    with pytest.raises(ValueError, match=r"read\(4\) returned 6 bytes"):
        _OverRead().readinto(buf)


def test_readonly_base_read_is_the_runtime_guard() -> None:
    # read() is @abstractmethod. On Python 3.12+ ABCMeta rejects construction of a
    # subclass that omits it (TypeError). On 3.11, io.RawIOBase's C __new__ still lets
    # the instance form, so the NotImplementedError body in read() is the runtime guard.
    class _ForgotRead(ReadOnlyIOStream):
        pass

    with pytest.raises((TypeError, NotImplementedError)):
        _ForgotRead().read()


def test_readonly_base_name_is_absent() -> None:
    """Nameless streams must not expose ``name`` (pycdlib Windows + reopen-by-name)."""
    s = _FixedReader(b"x")
    assert not hasattr(s, "name")


def test_readonly_base_name_annotation_does_not_claim_str() -> None:
    """The getter always raises; annotating ``str`` lets a checker accept ``.name.upper()``."""
    getter = ReadOnlyIOStream.name.fget
    assert getter is not None
    assert get_type_hints(getter).get("return") is Never


def test_delegating_base_name_absent_without_inner_path() -> None:
    s = DelegatingStream(io.BytesIO(b"x"))
    assert not hasattr(s, "name")


def test_delegating_base_forwards_name_from_inner_file(tmp_path) -> None:
    path = tmp_path / "x.bin"
    path.write_bytes(b"x")
    with open(path, "rb") as inner:
        s = DelegatingStream(inner)
        assert s.name == str(path)


def test_delegating_base_forwards_to_inner() -> None:
    inner = io.BytesIO(b"abcdefgh")
    s = DelegatingStream(inner)
    assert s.read(3) == b"abc"
    assert s.tell() == 3
    assert s.seekable() is True
    assert s.seek(0) == 0
    assert s.read(2) == b"ab"
    # zero-copy readinto passthrough to the inner
    buf = bytearray(4)
    assert s.readinto(buf) == 4
    assert bytes(buf) == b"cdef"
    assert s.readable() is True and s.writable() is False


def test_delegating_seekable_is_fixed_at_construction() -> None:
    class _Flip(io.BytesIO):
        def __init__(self) -> None:
            super().__init__(b"x")
            self.flag = True

        def seekable(self) -> bool:
            return self.flag

    inner = _Flip()
    s = DelegatingStream(inner)
    assert s.seekable() is True
    inner.flag = False
    assert s.seekable() is True


def test_replace_inner_recaches_seekable() -> None:
    s = DelegatingStream(io.BytesIO(b"x"))
    assert s.seekable() is True
    s._replace_inner(NonSeekableBytesIO(b"y"))
    assert s.seekable() is False


def test_delegating_base_close_closes_inner() -> None:
    inner = io.BytesIO(b"data")
    s = DelegatingStream(inner)
    s.close()
    assert inner.closed
    assert s.closed
    s.close()  # idempotent


def test_delegating_close_marks_closed_when_inner_close_fails() -> None:
    class _FailingClose(io.BytesIO):
        def close(self) -> None:
            raise OSError("boom")

    s = DelegatingStream(_FailingClose(b"x"))
    with pytest.raises(OSError, match="boom"):
        s.close()
    assert s.closed
    s.close()  # idempotent after the failed inner close


def test_delegating_manual_inner_close_skips_inner() -> None:
    inner = io.BytesIO(b"data")
    s = DelegatingStream(inner, manual_inner_close=True)
    s.close()
    assert s.closed
    assert not inner.closed
    inner.close()


def test_delegating_base_readinto_falls_back_without_inner_readinto() -> None:
    class _NoReadinto:
        def __init__(self, data: bytes) -> None:
            self._b = io.BytesIO(data)

        def read(self, n: int = -1, /) -> bytes:
            return self._b.read(n)

    s = DelegatingStream(_NoReadinto(b"xyz"))  # type: ignore[arg-type]
    buf = bytearray(2)
    assert s.readinto(buf) == 2
    assert bytes(buf) == b"xy"


class _RawIOWithoutReadinto(io.RawIOBase):
    """``io.RawIOBase`` advertises ``readinto`` but the default raises ``NotImplementedError``."""

    def __init__(self, data: bytes) -> None:
        super().__init__()
        self._b = io.BytesIO(data)

    def readable(self) -> bool:
        return True

    def read(self, n: int = -1, /) -> bytes:
        return self._b.read(n)


def test_delegating_readinto_falls_back_when_inner_readinto_unimplemented() -> None:
    s = DelegatingStream(_RawIOWithoutReadinto(b"xyz"))
    buf = bytearray(2)
    assert s.readinto(buf) == 2
    assert bytes(buf) == b"xy"


def test_delegating_readinto_none_raises_blocking() -> None:
    class _NonBlockingReadinto(io.BytesIO):
        def readinto(self, b):  # type: ignore[no-untyped-def]
            return None

    with pytest.raises(BlockingIOError):
        DelegatingStream(_NonBlockingReadinto(b"x")).readinto(bytearray(4))


def test_delegating_readinto_passthrough_false_routes_through_read() -> None:
    """With readinto_passthrough=False, readinto goes through the subclass's read() (so a
    side-effecting read override is not bypassed) — even when the inner has its own readinto."""
    reads: list[int] = []

    class _Tracking(DelegatingStream):
        def __init__(self, inner: io.BytesIO) -> None:
            super().__init__(inner, readinto_passthrough=False)

        def read(self, n: int = -1, /) -> bytes:
            data = self._inner.read(n)
            reads.append(len(data))  # side effect that must run on readinto too
            return data

    s = _Tracking(io.BytesIO(b"abcdef"))
    buf = bytearray(4)
    assert s.readinto(buf) == 4
    assert bytes(buf) == b"abcd"
    assert reads == [4]  # read() ran (passthrough would have left this empty)


def test_delegating_stream_does_not_forward_resume_offset() -> None:
    class _Inner(io.BytesIO):
        def nearest_resume_offset(self, target: int) -> int:
            return 0

    s = DelegatingStream(_Inner(b"x"))
    assert not hasattr(s, "nearest_resume_offset")


def test_ask_resume_offset_helper() -> None:
    from archivey.internal.streams.resume import ask_resume_offset

    class _Inner:
        def nearest_resume_offset(self, target: int) -> int:
            return target // 2

    assert ask_resume_offset(_Inner(), 10) == 5
    assert ask_resume_offset(io.BytesIO(b"x"), 10) is None
    assert ask_resume_offset(None, 10) is None


def test_verifying_stream_forwards_resume_offset() -> None:
    from archivey.internal.streams.verify import VerifyingStream

    class _Inner(io.BytesIO):
        def nearest_resume_offset(self, target: int) -> int:
            return 7

    s = VerifyingStream(_Inner(b"x"), {})
    assert s.nearest_resume_offset(1) == 7


def _import_all_archivey_modules() -> None:
    """Import every archivey module so ``__subclasses__()`` is not collection-order-blind.

    ``archivey.__main__`` calls ``main()`` at import, so it is skipped.

    This walk also proves every ``archivey.*`` module imports with no extras
    (the ``[core-only]`` lazy-optional-import boundary). A top-level extra
    import fails here, not as an unrelated ``ImportError`` later in the suite.
    """
    import importlib
    import pkgutil

    import archivey

    for module in pkgutil.walk_packages(archivey.__path__, prefix="archivey."):
        if module.name.endswith("__main__"):
            continue
        try:
            importlib.import_module(module.name)
        except Exception as exc:  # noqa: BLE001 - any import-time failure is this check
            raise ImportError(
                f"{module.name} failed to import while walking archivey modules "
                "for the ReadOnlyIOStream resume-offset inventory. This walk also "
                "proves every archivey module imports with no extras ([core-only]); "
                "a top-level extra import fails here."
            ) from exc


def _readonly_stream_subclasses() -> set[type]:
    found: set[type] = set()
    stack = [ReadOnlyIOStream]
    while stack:
        cls = stack.pop()
        for sub in cls.__subclasses__():
            if sub not in found:
                found.add(sub)
                stack.append(sub)
    found = {
        cls for cls in found if getattr(cls, "__module__", "").startswith("archivey.")
    }
    # Seed is ReadOnlyIOStream; subclasses include DelegatingStream. Discard both
    # bases so the inventory is the wrappers that need a resume-offset decision.
    found.discard(ReadOnlyIOStream)
    found.discard(DelegatingStream)
    return found


def test_readonly_stream_resume_offset_inventory() -> None:
    """Every ReadOnlyIOStream subclass is classified: forwards/owns, or not on the chain.

    Forwarding is opt-in. A new wrapper that sits between ArchiveStream and a
    seek-point table and forgets nearest_resume_offset becomes a silent
    diagnostic hole (None → resume 0). Walking only ``DelegatingStream`` misses
    ``VerifyingStream``-shaped holes; importing five modules by hand misses
    subclasses in modules this test never imported.

    Importing the package first also proves every ``archivey.*`` module imports
    with no extras (see ``_import_all_archivey_modules``).
    """
    _import_all_archivey_modules()

    import archivey.internal.backends.iso_reader as iso_reader
    import archivey.internal.backends.rar_reader as rar_reader
    import archivey.internal.detection as detection
    import archivey.internal.streams.archive_stream as archive_stream
    import archivey.internal.streams.codecs as codecs
    import archivey.internal.streams.counting as counting
    import archivey.internal.streams.crypto as crypto
    import archivey.internal.streams.decompressor_stream as decompressor_stream
    import archivey.internal.streams.peekable as peekable
    import archivey.internal.streams.streamtools.base as streamtools_base
    import archivey.internal.streams.streamtools.locked as locked
    import archivey.internal.streams.streamtools.slice as slice_mod
    import archivey.internal.streams.streamtools.solid as solid
    import archivey.internal.streams.verify as verify
    import archivey.internal.zip_aes as zip_aes

    forwards_or_owns = {
        archive_stream.ArchiveStream,
        codecs._AcceleratorStream,  # owns rapidgzip available_block_offsets
        codecs._GzipTruncationCheckStream,
        counting.OutputCountingStream,
        decompressor_stream.DecompressorStream,
        verify.VerifyingStream,
    }
    remaps_or_not_on_chain = {
        locked.LockedStream,
        locked.CloseLockedStream,
        counting.CountingReader,
        counting.SeekCountingStream,
        rar_reader._UnrarOwnedStream,
        rar_reader._BoundedMemberPipe,
        rar_reader._UnrarRespawnStream,
        iso_reader._PyCdlibStream,
        solid._MemberSlice,
        slice_mod.SlicingStream,  # explicit decline of a remapped offset space
        slice_mod.SharedView,  # same remapped space; locked subclass of SlicingStream
        peekable.PeekableStream,
        streamtools_base.FullCountStream,  # source boundary; not on the decompressed chain
        crypto.AesDecryptStream,
        zip_aes.WinZipAesDecryptStream,
        detection._BoundedPeekReader,
    }

    found = _readonly_stream_subclasses()
    leftover = found - forwards_or_owns - remaps_or_not_on_chain
    assert leftover == set(), (
        "new ReadOnlyIOStream subclass needs a nearest_resume_offset decision "
        f"(forwards/owns a table, or remaps / not on the decompressed chain): {leftover}"
    )
    extra_classified = (forwards_or_owns | remaps_or_not_on_chain) - found
    assert extra_classified == set(), (
        "classified a class the walk did not find (typo or it is no longer "
        f"a ReadOnlyIOStream): {extra_classified}"
    )
    missing_method = [
        cls.__name__
        for cls in forwards_or_owns
        if "nearest_resume_offset" not in cls.__dict__
    ]
    assert missing_method == [], (
        "classified as forwards/owns but does not define nearest_resume_offset: "
        f"{missing_method}"
    )
