"""Course outline and a day-by-day study schedule toward the exam.

Pure functions over canonical inputs: the verified lesson structure of each
source, the learner's study plan and the student journal. Nothing here is
stored; the schedule is recomputed from those facts on every read, so it can
never disagree with them. The browser only renders the result.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from hashlib import sha256

from study_agent.domain._validation import JsonObject

TOPIC_LIMIT = 120
MAX_LESSONS_PER_SOURCE = 80
MIN_UNWRAPPED_CHAPTERS = 3
MAX_SCHEDULE_DAYS = 400
MAX_REVIEW_DAYS = 7
REVIEW_SHARE = 0.15


@dataclass(frozen=True, slots=True)
class OutlineSpan:
    """One verified navigation span of a source (title and exact offsets)."""

    title: str
    start_offset: int
    end_offset: int


@dataclass(frozen=True, slots=True)
class OutlineLesson:
    key: str
    title: str
    topic: str
    source_id: str
    revision_id: str
    start_offset: int
    end_offset: int
    content_sha256: str | None

    def to_json(self, *, studied: bool) -> JsonObject:
        return {
            "key": self.key,
            "title": self.title,
            "topic": self.topic,
            "source_id": self.source_id,
            "revision_id": self.revision_id,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "studied": studied,
            # The exact span the notes generator accepts for this lesson.
            "structure_lesson": None
            if self.content_sha256 is None
            else {
                "title": self.title,
                "start_offset": self.start_offset,
                "end_offset": self.end_offset,
                "content_sha256": self.content_sha256,
            },
        }


@dataclass(frozen=True, slots=True)
class OutlineSource:
    source_id: str
    revision_id: str
    title: str
    structured: bool
    lessons: tuple[OutlineLesson, ...]
    structure_status: str = "absent"


def lesson_topic(title: str) -> str:
    """The journal topic that records a lesson as studied."""
    collapsed = " ".join(title.split())
    if len(collapsed) <= TOPIC_LIMIT:
        return collapsed
    return collapsed[: TOPIC_LIMIT - 1].rstrip() + "…"


def _normalized(topic: str) -> str:
    return " ".join(topic.split()).casefold()


def _lesson_key(source_id: str, revision_id: str, start: int, end: int) -> str:
    identity = f"{source_id}\0{revision_id}\0{start}\0{end}".encode()
    return "lesson-" + sha256(identity).hexdigest()[:20]


def _top_level(spans: Sequence[OutlineSpan], text_length: int) -> tuple[OutlineSpan, ...]:
    """The outermost spans; a lone wrapper (a document title) is unwrapped."""
    ordered = sorted(spans, key=lambda item: (item.start_offset, -item.end_offset, item.title))
    level: list[OutlineSpan] = []
    for span in ordered:
        if level and span.start_offset >= level[-1].start_offset and (
            span.end_offset <= level[-1].end_offset
        ):
            continue
        level.append(span)
    while len(level) == 1:
        wrapper = level[0]
        inner = [
            span
            for span in ordered
            if span is not wrapper
            and wrapper.start_offset <= span.start_offset
            and span.end_offset <= wrapper.end_offset
        ]
        chapters = _top_level(inner, text_length) if inner else ()
        # A book title over many chapters is a wrapper; a lesson with one or
        # two subsections is still one lesson.
        if len(chapters) < MIN_UNWRAPPED_CHAPTERS:
            break
        level = list(chapters)
    return tuple(level)


def outline_source(
    *,
    source_id: str,
    revision_id: str,
    title: str,
    text_length: int,
    content_sha256: str | None,
    spans: Sequence[OutlineSpan] | None,
    structure_status: str = "absent",
) -> OutlineSource:
    """Lessons of one source: its verified top-level structure, or the whole source."""
    usable = tuple(
        span
        for span in spans or ()
        if 0 <= span.start_offset < span.end_offset <= text_length
        and span.title.strip()
        and len(span.title) <= 240
    )
    top = _top_level(usable, text_length)[:MAX_LESSONS_PER_SOURCE] if usable else ()
    if top and content_sha256 is not None:
        lessons = tuple(
            OutlineLesson(
                _lesson_key(source_id, revision_id, span.start_offset, span.end_offset),
                span.title.strip(),
                lesson_topic(span.title),
                source_id,
                revision_id,
                span.start_offset,
                span.end_offset,
                content_sha256,
            )
            for span in top
        )
        return OutlineSource(source_id, revision_id, title, True, lessons, structure_status)
    whole = OutlineLesson(
        _lesson_key(source_id, revision_id, 0, max(text_length, 1)),
        title,
        lesson_topic(title),
        source_id,
        revision_id,
        0,
        max(text_length, 1),
        None,
    )
    return OutlineSource(source_id, revision_id, title, False, (whole,), structure_status)


def studied_keys(
    sources: Iterable[OutlineSource], covered_topics: Iterable[str]
) -> frozenset[str]:
    covered = {_normalized(topic) for topic in covered_topics}
    return frozenset(
        lesson.key
        for source in sources
        for lesson in source.lessons
        if _normalized(lesson.topic) in covered
    )


@dataclass(frozen=True, slots=True)
class PlanDay:
    day: date
    kind: str  # "study" | "practice" | "review" | "exam"
    lesson_keys: tuple[str, ...]

    def to_json(self) -> JsonObject:
        return {
            "date": self.day.isoformat(),
            # Monday is 0. The browser names the day; it never does date math.
            "weekday": self.day.weekday(),
            "kind": self.kind,
            "lesson_keys": self.lesson_keys,
        }


@dataclass(frozen=True, slots=True)
class StudySchedule:
    status: str  # "unset" | "past" | "ready"
    today: date
    exam_date: date | None
    days_remaining: int | None
    lessons_total: int
    lessons_studied: int
    study_days: int
    review_days: int
    days: tuple[PlanDay, ...]

    def to_json(self) -> JsonObject:
        pending = self.lessons_total - self.lessons_studied
        return {
            "status": self.status,
            "today": self.today.isoformat(),
            "exam_date": None if self.exam_date is None else self.exam_date.isoformat(),
            "days_remaining": self.days_remaining,
            "lessons_total": self.lessons_total,
            "lessons_studied": self.lessons_studied,
            "lessons_pending": pending,
            "study_days": self.study_days,
            "review_days": self.review_days,
            "lessons_per_study_day": (
                None if not self.study_days else round(pending / self.study_days, 2)
            ),
            "days": tuple(day.to_json() for day in self.days),
        }


def build_schedule(
    *,
    today: date,
    exam_date: date | None,
    sources: Sequence[OutlineSource],
    studied: frozenset[str],
) -> StudySchedule:
    """Spread the lessons not yet studied over the days left, then review.

    Lessons keep source order. The last ~15% of the days (one to seven) are
    the final review; the exam day closes the schedule. When there are more
    lessons than study days they are grouped evenly; when there are fewer,
    they are spaced across the whole period and the days between them are
    practice days (cards and questions on what was already studied).
    """
    lessons = tuple(lesson for source in sources for lesson in source.lessons)
    done = sum(1 for lesson in lessons if lesson.key in studied)
    pending = tuple(lesson.key for lesson in lessons if lesson.key not in studied)
    if exam_date is None:
        return StudySchedule("unset", today, None, None, len(lessons), done, 0, 0, ())
    remaining = (exam_date - today).days
    if remaining < 0:
        return StudySchedule("past", today, exam_date, remaining, len(lessons), done, 0, 0, ())
    if remaining == 0:
        exam = PlanDay(today, "exam", ())
        return StudySchedule("ready", today, exam_date, 0, len(lessons), done, 0, 0, (exam,))
    window = min(remaining, MAX_SCHEDULE_DAYS)
    review = 0 if window <= 2 else max(1, min(MAX_REVIEW_DAYS, round(window * REVIEW_SHARE)))
    study = window - review
    if not pending:
        review, study = window, 0
    assigned: list[tuple[str, ...]] = [() for _ in range(study)]
    if pending and len(pending) >= study:
        for index in range(study):
            first = index * len(pending) // study
            last = (index + 1) * len(pending) // study
            assigned[index] = pending[first:last]
    else:
        for index, key in enumerate(pending):
            assigned[index * study // len(pending)] = (key,)
    days = [
        PlanDay(today + timedelta(days=index), "study" if keys else "practice", keys)
        for index, keys in enumerate(assigned)
    ]
    days.extend(
        PlanDay(today + timedelta(days=index), "review", ())
        for index in range(study, study + review)
    )
    days.append(PlanDay(exam_date, "exam", ()))
    study_days = sum(1 for day in days if day.kind == "study")
    return StudySchedule(
        "ready", today, exam_date, remaining, len(lessons), done, study_days, review, tuple(days)
    )


def outline_json(
    sources: Sequence[OutlineSource], studied: frozenset[str]
) -> tuple[JsonObject, ...]:
    return tuple(
        {
            "source_id": source.source_id,
            "revision_id": source.revision_id,
            "title": source.title,
            "structured": source.structured,
            "structure_status": source.structure_status,
            "lessons": tuple(
                lesson.to_json(studied=lesson.key in studied) for lesson in source.lessons
            ),
        }
        for source in sources
    )
