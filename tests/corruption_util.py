"""Assert corruption that is not truncation.

:class:`~archivey.exceptions.TruncatedError` is a :class:`~archivey.exceptions.CorruptionError`
subclass, so ``pytest.raises(CorruptionError)`` also passes when the data merely ends early.
These helpers keep the strength the existing asserts had while the two types were
siblings; they do not record that each test meant "damaged, not short". A test whose
error could reasonably be either uses ``pytest.raises(CorruptionError)``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from archivey.exceptions import CorruptionError, TruncatedError


@contextmanager
def raises_corruption_not_truncation(
    **kwargs: Any,
) -> Iterator[pytest.ExceptionInfo[CorruptionError]]:
    """``pytest.raises(CorruptionError, **kwargs)`` that also fails on a ``TruncatedError``."""
    with pytest.raises(CorruptionError, **kwargs) as info:
        yield info
    assert not isinstance(info.value, TruncatedError), (
        f"expected damaged data, got truncation: {info.value!r}"
    )


def is_corruption_not_truncation(error: BaseException | None) -> bool:
    """Whether ``error`` is a ``CorruptionError`` and not a ``TruncatedError``."""
    return isinstance(error, CorruptionError) and not isinstance(error, TruncatedError)
