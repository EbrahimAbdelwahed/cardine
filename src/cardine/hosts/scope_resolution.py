"""Pure helpers for binding conversational references to canonical lessons."""

from __future__ import annotations

import re
from collections.abc import Sequence

_EXPLICIT_LESSON_REFERENCE = re.compile(
    r"(?:\blezione\s+(?:numero\s+)?\d+\b|"
    r"\bl[\s_-]*0*\d+(?=$|[\s_./-]))",
    re.IGNORECASE,
)


def recent_explicit_lesson_references(texts: Sequence[str], *, limit: int = 12) -> tuple[str, ...]:
    """Return newest-first explicit references, excluding deictic retry text."""

    if type(limit) is not int or limit < 1:
        raise ValueError("lesson reference limit must be positive")
    values = tuple(texts)
    if any(not isinstance(value, str) for value in values):
        raise TypeError("lesson reference history must contain text")
    return tuple(
        value
        for value in reversed(values[-limit:])
        if _EXPLICIT_LESSON_REFERENCE.search(value) is not None
    )


__all__ = ["recent_explicit_lesson_references"]
