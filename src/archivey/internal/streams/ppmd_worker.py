"""Child-process side of :class:`~archivey.internal.streams.ppmd_child.PpmdChildDecoder`.

pyppmd can take the whole process down on corrupt input (it decodes past the end of
a stream that has already ended; see ``dev-docs/known-issues.md``). Large PPMd members
are therefore decoded in a child process that runs this file, so a crash costs the
member and not the caller.

This file is run as a script (``python -P ppmd_worker.py``), never imported by the
worker: importing any ``archivey`` module imports the whole package, which costs more
start-up time than the decode of a small member. It depends on the standard library
and ``pyppmd`` only. The parent keeps all the decoding logic; the child owns one native
decoder and answers one request at a time.

Protocol, all integers little-endian, over the child's stdin and stdout:

- Parent sends ``<BBIB`` (variant, order, mem_size, restore_method) once.
- Then, per request: ``<iI`` (length, data size) and the data bytes. The child calls
  ``decode(data, length)``.
- Every reply, including the one to the opening message: ``<BBBI`` (status, eof,
  needs_input, payload size) and the payload. Status 0 carries the decoded bytes;
  status 1 carries ``"<exception type name>\\n<message>"`` in UTF-8.
- The parent closes stdin to end the child.
"""

from __future__ import annotations

import struct
import sys
from typing import IO

_OPEN = struct.Struct("<BBIB")
_REQUEST = struct.Struct("<iI")
_REPLY = struct.Struct("<BBBI")


def _read_exact(stream: IO[bytes], size: int) -> bytes | None:
    parts: list[bytes] = []
    while size:
        chunk = stream.read(size)
        if not chunk:
            return None
        parts.append(chunk)
        size -= len(chunk)
    return b"".join(parts)


def _reply(out: IO[bytes], status: int, decoder: object, payload: bytes) -> None:
    eof = bool(getattr(decoder, "eof", False))
    needs_input = bool(getattr(decoder, "needs_input", True))
    out.write(_REPLY.pack(status, eof, needs_input, len(payload)))
    out.write(payload)
    out.flush()


def _error_payload(exc: BaseException) -> bytes:
    return f"{type(exc).__name__}\n{exc}".encode("utf-8", "replace")


def main() -> None:
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    header = _read_exact(stdin, _OPEN.size)
    if header is None:
        return
    variant, order, mem_size, restore_method = _OPEN.unpack(header)
    decoder = None
    try:
        import pyppmd

        if variant == 8:
            decoder = pyppmd.Ppmd8Decoder(order, mem_size, restore_method)
        else:
            decoder = pyppmd.Ppmd7Decoder(order, mem_size)
    except Exception as exc:  # noqa: BLE001 - reported to the parent, which raises
        _reply(stdout, 1, decoder, _error_payload(exc))
        return
    _reply(stdout, 0, decoder, b"")
    while True:
        request = _read_exact(stdin, _REQUEST.size)
        if request is None:
            return
        length, size = _REQUEST.unpack(request)
        data = _read_exact(stdin, size) if size else b""
        if data is None:
            return
        try:
            result = decoder.decode(data, length)
        except Exception as exc:  # noqa: BLE001 - reported to the parent, which raises
            _reply(stdout, 1, decoder, _error_payload(exc))
            continue
        _reply(stdout, 0, decoder, result)


if __name__ == "__main__":
    main()
