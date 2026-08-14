"""Escaping of attacker-controlled text for safe terminal display.

Archive member names are attacker-controlled, and so is anything built from them —
destination paths, link targets, the messages that embed them. A name carrying
``\\x1b[2K\\r`` can erase the line reporting it and author what the operator reads
instead. GNU ``ls`` and ``tar`` quote for the same reason.

This module is deliberately dependency-free: :mod:`archivey.exceptions` escapes its
own messages with it, and ``archivey.cli`` escapes member names for display, so it
has to sit below both without importing either.
"""

from __future__ import annotations

# C0 controls, DEL, and C1 controls — never emit raw into a terminal.
_CONTROL_ESCAPE = {
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\\": "\\\\",
}


def escape_control_chars(text: str) -> str:
    """Backslash-escape control bytes in attacker-controlled text.

    The escaping is **lossless** — every escape names the byte it replaced, so the
    original is recoverable by eye — rather than eliding the offending characters.
    Style follows the cli-product recommendation (Q4 lean): escape everywhere,
    lossless backslash form, no ``--raw`` yet.

    Backslash itself is escaped, without which the form would be ambiguous: a member
    literally named ``a\\nb`` would render identically to one containing a newline.
    The cost is that a native Windows path passed through this doubles its
    separators; callers holding a path should render it ``/``-separated first.
    """
    out: list[str] = []
    for ch in text:
        if ch in _CONTROL_ESCAPE:
            out.append(_CONTROL_ESCAPE[ch])
            continue
        if ch.isprintable():
            out.append(ch)
            continue
        code = ord(ch)
        if 0xDC00 <= code <= 0xDFFF:
            # surrogateescape of a single byte — show the underlying octet.
            out.append(f"\\x{code & 0xFF:02x}")
        elif code <= 0xFF:
            out.append(f"\\x{code:02x}")
        else:
            out.append(f"\\u{code:04x}")
    return "".join(out)
