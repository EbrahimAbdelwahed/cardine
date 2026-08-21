"""Bounded reads over canonical learner-visible conversation history."""

from __future__ import annotations

import re
from dataclasses import dataclass

from study_agent.domain import (
    CourseId,
    SessionId,
    TutorPresentationKind,
    TutorSnapshotV1,
    TutorTimelineKind,
)
from study_agent.ports.session import TutorPresentationViewPort
from study_agent.ports.tutor_snapshot import TutorSnapshotPort

MAX_HISTORY_SEARCH_RESULTS = 8
MAX_HISTORY_READ_RESULTS = 12
MAX_HISTORY_EXCERPT_CHARS = 1_000
MAX_HISTORY_QUERY_CHARS = 240
_TOKEN = re.compile(r"[\wÀ-ÿ]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class ConversationHistoryEntry:
    role: str
    course_sequence: int
    excerpt: str

    def __post_init__(self) -> None:
        if self.role not in {"learner", "assistant"}:
            raise ValueError("conversation history role is invalid")
        if type(self.course_sequence) is not int or self.course_sequence < 1:
            raise ValueError("conversation history sequence must be positive")
        if not isinstance(self.excerpt, str) or not self.excerpt.strip():
            raise ValueError("conversation history excerpt is required")
        if len(self.excerpt) > MAX_HISTORY_EXCERPT_CHARS:
            raise ValueError("conversation history excerpt exceeds its bound")


@dataclass(frozen=True, slots=True)
class ConversationHistorySearchResult:
    entries: tuple[ConversationHistoryEntry, ...]
    total_entries: int
    match_count: int
    through_sequence: int


@dataclass(frozen=True, slots=True)
class ConversationHistoryReadResult:
    entries: tuple[ConversationHistoryEntry, ...]
    total_entries: int
    through_sequence: int
    next_cursor: int | None
    has_more: bool


class ConversationHistoryReader:
    """Expose canonical conversation through one small, bounded interface."""

    def __init__(
        self,
        snapshots: TutorSnapshotPort,
        presentations: TutorPresentationViewPort,
    ) -> None:
        if not hasattr(snapshots, "get") or not hasattr(presentations, "presentations"):
            raise TypeError("conversation history requires canonical session views")
        self._snapshots = snapshots
        self._presentations = presentations

    def search(
        self,
        course_id: CourseId,
        session_id: SessionId,
        query: str,
        *,
        limit: int = MAX_HISTORY_SEARCH_RESULTS,
        through_sequence: int | None = None,
    ) -> ConversationHistorySearchResult:
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > MAX_HISTORY_QUERY_CHARS
        ):
            raise ValueError("conversation search query is required")
        _bounded_limit(limit, MAX_HISTORY_SEARCH_RESULTS, "conversation search")
        entries, high_water = self._entries(course_id, session_id, through_sequence)
        terms = tuple(dict.fromkeys(token.casefold() for token in _TOKEN.findall(query)))
        if not terms:
            raise ValueError("conversation search query has no lexical terms")
        matches = tuple(
            entry
            for entry in entries
            if all(term in entry.excerpt.casefold() for term in terms)
        )
        return ConversationHistorySearchResult(
            matches[-limit:], len(entries), len(matches), high_water
        )

    def read(
        self,
        course_id: CourseId,
        session_id: SessionId,
        *,
        cursor: int | None = None,
        direction: str = "backward",
        limit: int = MAX_HISTORY_READ_RESULTS,
        through_sequence: int | None = None,
    ) -> ConversationHistoryReadResult:
        _bounded_limit(limit, MAX_HISTORY_READ_RESULTS, "conversation read")
        if direction not in {"backward", "forward"}:
            raise ValueError("conversation read direction is invalid")
        if cursor is not None and (type(cursor) is not int or cursor < 1):
            raise ValueError("conversation read cursor is invalid")
        entries, high_water = self._entries(course_id, session_id, through_sequence)
        if direction == "backward":
            eligible = tuple(
                item for item in entries if cursor is None or item.course_sequence < cursor
            )
            page = eligible[-limit:]
            has_more = len(eligible) > len(page)
            next_cursor = page[0].course_sequence if page and has_more else None
        else:
            eligible = tuple(
                item for item in entries if cursor is None or item.course_sequence > cursor
            )
            page = eligible[:limit]
            has_more = len(eligible) > len(page)
            next_cursor = page[-1].course_sequence if page and has_more else None
        return ConversationHistoryReadResult(
            page, len(entries), high_water, next_cursor, has_more
        )

    def _entries(
        self,
        course_id: CourseId,
        session_id: SessionId,
        through_sequence: int | None,
    ) -> tuple[tuple[ConversationHistoryEntry, ...], int]:
        if not isinstance(course_id, CourseId) or not isinstance(session_id, SessionId):
            raise TypeError("conversation history requires typed scope")
        snapshot = self._snapshots.get(course_id, session_id)
        if not isinstance(snapshot, TutorSnapshotV1):
            raise TypeError("conversation history snapshot is invalid")
        if snapshot.course_id != course_id or snapshot.session_id != session_id:
            raise ValueError("conversation history snapshot belongs to another session")
        high_water = snapshot.high_water_sequence
        if through_sequence is not None:
            if type(through_sequence) is not int or not 0 <= through_sequence <= high_water:
                raise ValueError("conversation history high-water bound is invalid")
            high_water = through_sequence

        by_sequence: dict[int, ConversationHistoryEntry] = {}
        for item in snapshot.timeline:
            role = {
                TutorTimelineKind.LEARNER: "learner",
                TutorTimelineKind.ASSISTANT: "assistant",
            }.get(item.kind)
            if role is None or item.course_sequence > high_water:
                continue
            _insert(
                by_sequence,
                ConversationHistoryEntry(role, item.course_sequence, _excerpt(item.content)),
            )
        for presentation in self._presentations.presentations(course_id, session_id):
            if (
                presentation.course_sequence > high_water
                or presentation.kind
                not in {
                    TutorPresentationKind.ASSISTANT_MESSAGE,
                    TutorPresentationKind.LEARNER_QUESTION,
                }
            ):
                continue
            _insert(
                by_sequence,
                ConversationHistoryEntry(
                    "assistant",
                    presentation.course_sequence,
                    _excerpt(presentation.content),
                ),
            )
        return tuple(by_sequence[key] for key in sorted(by_sequence)), high_water


def _insert(
    entries: dict[int, ConversationHistoryEntry], entry: ConversationHistoryEntry
) -> None:
    previous = entries.get(entry.course_sequence)
    if previous is not None and previous != entry:
        raise ValueError("conversation history has conflicting canonical entries")
    entries[entry.course_sequence] = entry


def _excerpt(content: str) -> str:
    normalized = " ".join(content.split())
    if not normalized:
        raise ValueError("canonical conversation entry has no visible content")
    return normalized[:MAX_HISTORY_EXCERPT_CHARS]


def _bounded_limit(value: int, maximum: int, name: str) -> None:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} limit is invalid")


__all__ = [
    "ConversationHistoryEntry",
    "ConversationHistoryReadResult",
    "ConversationHistoryReader",
    "ConversationHistorySearchResult",
]
