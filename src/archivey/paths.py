"""Naming rules extraction applies to destination paths, for a front end to apply too.

A front end that moves or renames what extraction wrote should name things the way
extraction does, so the result is the one a direct extraction would have given.
archivey's own CLI moves a single top-level directory up out of its wrapper folder
with this rule.

**Public, not re-exported.** Import from here (``from archivey.paths import
numbered_name``); these names are not in :mod:`archivey`. Everything in ``__all__``
carries the same compatibility promise as ``archivey.__all__``.

- :func:`numbered_name` — the ``name (N)`` spelling of an
  ``OverwritePolicy.RENAME`` rename.
"""

from __future__ import annotations

from pathlib import PurePath

__all__ = ["numbered_name"]


def numbered_name(name: str, n: int, *, is_dir: bool) -> str:
    """Return ``name`` with the counter ``n`` added, as ``OverwritePolicy.RENAME`` spells it.

    When a member's destination is taken, ``OverwritePolicy.RENAME`` writes it under the
    first free name of this form, counting from ``n=1``. A file keeps its extension:
    the counter goes before the final suffix. A directory has no suffix, so the counter
    goes after the whole name.

    ``name`` is a single path component, not a path: only its final suffix moves.

    >>> numbered_name("photo.jpg", 1, is_dir=False)
    'photo (1).jpg'
    >>> numbered_name("notes.tar.gz", 2, is_dir=False)
    'notes.tar (2).gz'
    >>> numbered_name("photos.2024", 1, is_dir=True)
    'photos.2024 (1)'

    Args:
        name: The name that is taken.
        n: The counter, ``1`` or more.
        is_dir: Whether ``name`` is a directory.

    Returns:
        The candidate name. This function does not look at the filesystem: the caller
        checks whether the candidate is free and tries ``n + 1`` when it is not.
    """
    if is_dir:
        return f"{name} ({n})"
    path = PurePath(name)
    return f"{path.stem} ({n}){path.suffix}"
