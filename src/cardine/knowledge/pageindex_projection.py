"""Provider-neutral PageIndex navigation projection over canonical text."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import cast


class PageIndexStatus(StrEnum):
    QUEUED = "queued"
    INDEXING = "indexing"
    READY = "ready"
    DEGRADED = "degraded"
    FAILED = "failed"
    DISABLED = "disabled"


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{name} must be non-empty trimmed text")
    return value


def _digest(value: object, name: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _error_code(value: str) -> str:
    if re.fullmatch(r"[a-z][a-z0-9_]{2,63}", value) is None:
        raise ValueError("error_code must be a portable machine code")
    return value


@dataclass(frozen=True, slots=True)
class CanonicalSpanCandidate:
    course_id: str
    source_id: str
    revision_id: str
    node_id: str
    title: str
    start_offset: int
    end_offset: int
    content_sha256: str

    def __post_init__(self) -> None:
        for name in (
            "course_id",
            "source_id",
            "revision_id",
            "node_id",
            "title",
        ):
            _text(getattr(self, name), name)
        if type(self.start_offset) is not int or type(self.end_offset) is not int:
            raise ValueError("candidate offsets must be integers")
        if self.start_offset < 0 or self.end_offset <= self.start_offset:
            raise ValueError("candidate offsets are invalid")
        _digest(self.content_sha256, "content_sha256")


@dataclass(frozen=True, slots=True)
class PageIndexProjection:
    course_id: str
    source_id: str
    revision_id: str
    content_sha256: str
    status: PageIndexStatus
    attempt: int
    candidates: tuple[CanonicalSpanCandidate, ...] = ()
    error_code: str | None = None
    lease_until: float | None = None

    def __post_init__(self) -> None:
        for name in ("course_id", "source_id", "revision_id"):
            _text(getattr(self, name), name)
        _digest(self.content_sha256, "content_sha256")
        if not isinstance(self.status, PageIndexStatus):
            raise ValueError("status is not a PageIndexStatus")
        if type(self.attempt) is not int or self.attempt < 0:
            raise ValueError("attempt must be a non-negative integer")
        if self.error_code is not None:
            _error_code(_text(self.error_code, "error_code"))
        if self.lease_until is not None and (
            type(self.lease_until) not in {int, float}
            or not math.isfinite(self.lease_until)
            or self.lease_until < 0
        ):
            raise ValueError("lease_until must be a finite non-negative number")
        object.__setattr__(self, "candidates", tuple(self.candidates))
        for candidate in self.candidates:
            if candidate.course_id != self.course_id:
                raise ValueError("candidate course does not match projection")
            if candidate.source_id != self.source_id:
                raise ValueError("candidate source does not match projection")
            if candidate.revision_id != self.revision_id:
                raise ValueError("candidate revision does not match projection")
            if candidate.content_sha256 != self.content_sha256:
                raise ValueError("candidate digest does not match projection")
        if self.status is not PageIndexStatus.INDEXING and self.lease_until is not None:
            raise ValueError("only indexing projections may carry a lease")


def _walk_tree(tree: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(tree, list):
        return ()
    flattened: list[Mapping[str, object]] = []
    stack: list[tuple[object, int]] = [(node, 1) for node in reversed(tree)]
    seen_node_ids: set[str] = set()
    while stack:
        if len(flattened) >= 256:
            return ()
        raw, depth = stack.pop()
        if not isinstance(raw, Mapping) or depth > 32:
            continue
        node_id = raw.get("node_id")
        if type(node_id) is not str or not node_id or node_id in seen_node_ids:
            continue
        line_num = raw.get("line_num")
        if type(line_num) is not int or line_num < 1:
            continue
        seen_node_ids.add(node_id)
        flattened.append(raw)
        children = raw.get("nodes", ())
        if isinstance(children, list):
            stack.extend((child, depth + 1) for child in reversed(children))
    return tuple(flattened)


def map_structural_tree(
    *,
    course_id: str,
    source_id: str,
    revision_id: str,
    content: str,
    tree: object,
) -> tuple[CanonicalSpanCandidate, ...]:
    """Map exact structural node text to unique canonical character offsets.

    PageIndex output is navigation metadata only. A node is retained only when
    its complete text occurs exactly once in the canonical revision. Ambiguous,
    malformed, duplicate, or out-of-bound nodes are discarded.
    """

    course_id, source_id, revision_id = (
        _text(course_id, "course_id"),
        _text(source_id, "source_id"),
        _text(revision_id, "revision_id"),
    )
    if type(content) is not str or not content:
        raise ValueError("content must be non-empty text")
    digest = sha256(content.encode("utf-8")).hexdigest()
    candidates: list[CanonicalSpanCandidate] = []
    for raw in _walk_tree(tree):
        node_id, title, text = raw.get("node_id"), raw.get("title"), raw.get("text")
        if not all(type(value) is str and value for value in (node_id, title, text)):
            continue
        node_id = cast(str, node_id)
        title = cast(str, title)
        text = cast(str, text)
        start = content.find(text)
        if start < 0 or content.find(text, start + 1) >= 0:
            continue
        end = start + len(text)
        candidates.append(
            CanonicalSpanCandidate(
                course_id,
                source_id,
                revision_id,
                node_id,
                title,
                start,
                end,
                digest,
            )
        )
    return tuple(candidates)


__all__ = [
    "CanonicalSpanCandidate",
    "PageIndexProjection",
    "PageIndexStatus",
    "map_structural_tree",
]
