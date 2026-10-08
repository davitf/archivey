"""Importing archivey does not import the optional packages that re-enable the GIL.

On a free-threaded CPython, importing an extension module that has not declared
free-thread support re-enables the GIL for the whole process. pyppmd, inflate64, brotli
and rapidgzip are such modules, so ``codecs.py`` finds them without importing them and
imports each one only when a stream needs it (``_LazyOptional``). These tests run a
fresh interpreter, because this pytest process has imported them already.
"""

from __future__ import annotations

import subprocess
import sys
import sysconfig
import textwrap

from archivey.internal.streams import codecs

_GIL_REENABLING = ("pyppmd", "inflate64", "brotli", "rapidgzip")

_PROBE = textwrap.dedent(
    """
    import sys
    import archivey
    from archivey import list_supported_formats

    list_supported_formats()
    print("imported=" + ",".join(m for m in {names!r} if m in sys.modules))
    print(sys._is_gil_enabled() if hasattr(sys, "_is_gil_enabled") else "n/a")
    """
)


def test_import_and_format_listing_do_not_import_gil_reenabling_packages() -> None:
    proc = subprocess.run(
        [sys.executable, "-W", "ignore", "-c", _PROBE.format(names=_GIL_REENABLING)],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    imported, gil = proc.stdout.strip().splitlines()
    assert imported == "imported=", f"importing archivey {imported}"
    if sysconfig.get_config_var("Py_GIL_DISABLED"):
        assert gil == "False", "importing archivey re-enabled the GIL"


def test_lazy_optional_looks_up_without_importing() -> None:
    lazy = codecs._LazyOptional("archivey_no_such_package")
    assert not lazy.available()
    assert lazy.load() is None
    assert lazy.loaded() is None

    present = codecs._LazyOptional("json")
    assert present.available()
    assert present.loaded() is None
    assert present.load() is sys.modules["json"]
    assert present.loaded() is sys.modules["json"]
