"""Guard: only ``internal/streams/crypto.py`` imports ``cryptography``.

Format code reaches AES through :class:`~archivey.internal.streams.crypto.CryptoBackend`
so the backend stays swappable (``packaging-and-extras``: one crypto backend, kept behind
an abstraction). ``zip_aes.py`` once imported ``cryptography`` directly, under a comment
stating this very rule, and nothing noticed until a sweep review did.

The walk reads import statements and literal ``importlib.import_module`` /
``__import__`` calls, not text, so prose that names the package does not count.
Mutation it was checked against: restoring ``from cryptography.hazmat.primitives.ciphers
import Cipher, algorithms, modes`` inside a function in ``backends/zip_aes.py`` fails it.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src" / "archivey"
_WRAPPER = _SRC / "internal" / "streams" / "crypto.py"
_PACKAGE = "cryptography"


def _names_package(module: str | None) -> bool:
    return module is not None and (
        module == _PACKAGE or module.startswith(_PACKAGE + ".")
    )


def _crypto_imports(path: Path) -> list[int]:
    """Line numbers in ``path`` that import ``cryptography``, at any depth."""
    lines = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), str(path))):
        if isinstance(node, ast.Import):
            if any(_names_package(alias.name) for alias in node.names):
                lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and _names_package(node.module):
                lines.append(node.lineno)
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", None)
            )
            first = node.args[0]
            if (
                name in ("import_module", "__import__")
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and _names_package(first.value)
            ):
                lines.append(node.lineno)
    return lines


def test_only_the_crypto_wrapper_imports_cryptography() -> None:
    offenders = {
        str(path.relative_to(_SRC)): lines
        for path in sorted(_SRC.rglob("*.py"))
        if path != _WRAPPER and (lines := _crypto_imports(path))
    }
    assert not offenders, (
        "import cryptography only in internal/streams/crypto.py and reach it through "
        f"CryptoBackend; found: {offenders}"
    )


def test_the_walk_sees_the_wrappers_own_imports() -> None:
    """Keeps the guard above from passing vacuously: the wrapper's imports are found."""
    assert _crypto_imports(_WRAPPER)
