"""Deterministic lesson navigation without owning canonical evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Protocol

_MAX_SOURCES = 1024
_MAX_CANDIDATES = 256
_MAX_TEXT_BYTES = 16 * 1024 * 1024
_ATX = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$")
_CLOSING_HASHES = re.compile(r"[ \t]+#+[ \t]*$")
_FENCE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$")


class SearchDisposition(StrEnum):
    NOT_FOUND = "not_found"
    UNIQUE = "unique"
    AMBIGUOUS = "ambiguous"


class LessonSelectionError(ValueError):
    """A candidate or pin is absent, stale, foreign, or malformed."""


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be non-empty trimmed text")
    return value


def _digest(value: str, name: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True, slots=True)
class LessonChunk:
    start_offset: int
    end_offset: int
    text: str

    def __post_init__(self) -> None:
        if self.start_offset < 0 or self.end_offset <= self.start_offset:
            raise ValueError("chunk offsets are invalid")
        if not self.text:
            raise ValueError("chunk text must not be empty")


@dataclass(frozen=True, slots=True)
class LessonSource:
    course_id: str
    source_id: str
    revision_id: str
    title: str
    kind: str
    text: str
    content_sha256: str
    catalog_fingerprint: str
    chunks: tuple[LessonChunk, ...] = ()

    def __post_init__(self) -> None:
        for name in ("course_id", "source_id", "revision_id", "title", "kind"):
            _text(getattr(self, name), name)
        if not self.text or len(self.text.encode("utf-8")) > _MAX_TEXT_BYTES:
            raise ValueError("source text is empty or exceeds the navigation bound")
        _digest(self.content_sha256, "content_sha256")
        _digest(self.catalog_fingerprint, "catalog_fingerprint")
        if sha256(self.text.encode()).hexdigest() != self.content_sha256:
            raise ValueError("content_sha256 does not match source text")
        object.__setattr__(self, "chunks", tuple(self.chunks))
        for chunk in self.chunks:
            if chunk.end_offset > len(self.text):
                raise ValueError("chunk lies outside source text")
            if self.text[chunk.start_offset : chunk.end_offset] != chunk.text:
                raise ValueError("chunk text does not match canonical offsets")


@dataclass(frozen=True, slots=True)
class LessonCandidate:
    candidate_id: str
    course_id: str
    source_id: str
    revision_id: str
    section_title: str
    start_offset: int
    end_offset: int
    content_sha256: str
    catalog_fingerprint: str


@dataclass(frozen=True, slots=True)
class SourcePin:
    course_id: str
    source_id: str
    revision_id: str
    section_title: str
    start_offset: int
    end_offset: int
    content_sha256: str
    catalog_fingerprint: str

    def __post_init__(self) -> None:
        for name in ("course_id", "source_id", "revision_id", "section_title"):
            _text(getattr(self, name), name)
        if self.start_offset < 0 or self.end_offset <= self.start_offset:
            raise ValueError("pin offsets are invalid")
        _digest(self.content_sha256, "content_sha256")
        _digest(self.catalog_fingerprint, "catalog_fingerprint")


@dataclass(frozen=True, slots=True)
class LessonSearchResult:
    disposition: SearchDisposition
    candidates: tuple[LessonCandidate, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidates", tuple(self.candidates))
        expected = (
            SearchDisposition.NOT_FOUND
            if not self.candidates
            else SearchDisposition.UNIQUE
            if len(self.candidates) == 1
            else SearchDisposition.AMBIGUOUS
        )
        if self.disposition is not expected:
            raise ValueError("search disposition does not match candidate cardinality")


class LessonEvidencePort(Protocol):
    def search(self, source: LessonSource, query: str) -> tuple[LessonChunk, ...]: ...


def _candidate(source: LessonSource, title: str, start: int, end: int) -> LessonCandidate:
    identity = "\0".join(
        (source.course_id, source.source_id, source.revision_id, str(start), str(end), title)
    ).encode()
    return LessonCandidate(
        f"lesson-sha256:{sha256(identity).hexdigest()}",
        source.course_id,
        source.source_id,
        source.revision_id,
        title,
        start,
        end,
        source.content_sha256,
        source.catalog_fingerprint,
    )


def _markdown_sections(source: LessonSource, query: str) -> tuple[LessonCandidate, ...]:
    headings: list[tuple[int, int, str]] = []
    fence_character: str | None = None
    fence_length = 0
    offset = 0
    for line_with_end in source.text.splitlines(keepends=True):
        line = line_with_end.rstrip("\r\n")
        fence = _FENCE.match(line)
        if fence is not None:
            marker = fence.group(1)
            if fence_character is None:
                fence_character, fence_length = marker[0], len(marker)
            elif marker[0] == fence_character and len(marker) >= fence_length:
                fence_character, fence_length = None, 0
            offset += len(line_with_end)
            continue
        if fence_character is None:
            match = _ATX.match(line)
            if match is not None:
                title = _CLOSING_HASHES.sub("", match.group(2)).strip()
                if title:
                    headings.append((offset, len(match.group(1)), title))
        offset += len(line_with_end)
    matches: list[LessonCandidate] = []
    needle = query.casefold()
    for index, (start, level, title) in enumerate(headings):
        if title.casefold() != needle:
            continue
        end = len(source.text)
        for next_start, next_level, _ in headings[index + 1 :]:
            if next_level <= level:
                end = next_start
                break
        matches.append(_candidate(source, title, start, end))
    return tuple(matches)


class LessonSelectionService:
    def __init__(self, evidence: LessonEvidencePort) -> None:
        self._evidence = evidence

    def search(
        self, course_id: str, query: str, sources: tuple[LessonSource, ...]
    ) -> LessonSearchResult:
        course_id, query = _text(course_id, "course_id"), _text(query, "query")
        sources = tuple(sources)
        if len(sources) > _MAX_SOURCES:
            raise LessonSelectionError("source search exceeds the course bound")
        candidates: list[LessonCandidate] = []
        for source in sources:
            if source.course_id != course_id:
                raise LessonSelectionError("source belongs to another course")
            if source.kind.casefold() == "markdown":
                candidates.extend(_markdown_sections(source, query))
            else:
                for evidence in self._evidence.search(source, query):
                    if (
                        evidence not in source.chunks
                        or source.text[evidence.start_offset : evidence.end_offset] != evidence.text
                    ):
                        raise LessonSelectionError("lexical evidence is not canonical")
                    candidates.append(
                        _candidate(source, source.title, evidence.start_offset, evidence.end_offset)
                    )
            if len(candidates) > _MAX_CANDIDATES:
                raise LessonSelectionError("lesson candidate bound exceeded")
        ordered = tuple(sorted(candidates, key=lambda item: (item.source_id, item.start_offset)))
        disposition = (
            SearchDisposition.NOT_FOUND
            if not ordered
            else SearchDisposition.UNIQUE
            if len(ordered) == 1
            else SearchDisposition.AMBIGUOUS
        )
        return LessonSearchResult(disposition, ordered)

    def select(self, candidate_id: str, result: LessonSearchResult) -> SourcePin:
        candidate_id = _text(candidate_id, "candidate_id")
        matches = tuple(item for item in result.candidates if item.candidate_id == candidate_id)
        if len(matches) != 1:
            raise LessonSelectionError("lesson candidate was not found uniquely")
        item = matches[0]
        return SourcePin(
            item.course_id,
            item.source_id,
            item.revision_id,
            item.section_title,
            item.start_offset,
            item.end_offset,
            item.content_sha256,
            item.catalog_fingerprint,
        )

    def validate_pin(self, pin: SourcePin, sources: tuple[LessonSource, ...]) -> LessonSource:
        matches = tuple(
            source
            for source in sources
            if source.course_id == pin.course_id and source.source_id == pin.source_id
        )
        if len(matches) != 1:
            raise LessonSelectionError("lesson pin source is absent or foreign")
        source = matches[0]
        if (
            source.revision_id != pin.revision_id
            or source.content_sha256 != pin.content_sha256
            or source.catalog_fingerprint != pin.catalog_fingerprint
            or pin.end_offset > len(source.text)
        ):
            raise LessonSelectionError("lesson pin is stale")
        return source
