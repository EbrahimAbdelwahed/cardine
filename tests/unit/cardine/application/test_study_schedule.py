from __future__ import annotations

from datetime import date
from typing import Any, cast

from cardine.application.study_schedule import (
    OutlineSource,
    OutlineSpan,
    build_schedule,
    lesson_topic,
    outline_json,
    outline_source,
    studied_keys,
)

TODAY = date(2026, 10, 5)
DIGEST = "a" * 64


def _structured(count: int, source_id: str = "s1") -> OutlineSource:
    spans = [OutlineSpan(f"Lezione {n}", n * 100, n * 100 + 100) for n in range(count)]
    return outline_source(
        source_id=source_id,
        revision_id="r1",
        title=f"Fonte {source_id}",
        text_length=count * 100,
        content_sha256=DIGEST,
        spans=spans,
    )


def test_top_level_spans_are_the_lessons_and_nested_sections_are_not() -> None:
    spans = [
        OutlineSpan("Lezione 1", 0, 100),
        OutlineSpan("1.1 Dettaglio", 10, 50),
        OutlineSpan("Lezione 2", 100, 200),
    ]
    source = outline_source(
        source_id="s", revision_id="r", title="T", text_length=200,
        content_sha256=DIGEST, spans=spans,
    )
    assert [lesson.title for lesson in source.lessons] == ["Lezione 1", "Lezione 2"]
    assert source.structured is True
    lesson = cast(Any, outline_json([source], frozenset()))[0]["lessons"][0]
    assert lesson["structure_lesson"] == {
        "title": "Lezione 1", "start_offset": 0, "end_offset": 100, "content_sha256": DIGEST,
    }


def test_a_lone_document_title_is_unwrapped_to_its_chapters() -> None:
    spans = [
        OutlineSpan("Biochimica", 0, 300),
        OutlineSpan("Capitolo 1", 0, 100),
        OutlineSpan("Capitolo 2", 100, 200),
        OutlineSpan("Capitolo 3", 200, 300),
    ]
    source = outline_source(
        source_id="s", revision_id="r", title="T", text_length=300,
        content_sha256=DIGEST, spans=spans,
    )
    assert [lesson.title for lesson in source.lessons] == [
        "Capitolo 1", "Capitolo 2", "Capitolo 3",
    ]


def test_a_lesson_with_one_subsection_stays_one_lesson() -> None:
    spans = [OutlineSpan("Regolazione renale", 0, 300), OutlineSpan("Sistema RAA", 120, 300)]
    source = outline_source(
        source_id="s", revision_id="r", title="T", text_length=300,
        content_sha256=DIGEST, spans=spans,
    )
    assert [lesson.title for lesson in source.lessons] == ["Regolazione renale"]


def test_an_unstructured_source_is_one_lesson_without_a_note_span() -> None:
    source = outline_source(
        source_id="s", revision_id="r", title="Appunti", text_length=40,
        content_sha256=None, spans=None,
    )
    assert source.structured is False
    assert [lesson.title for lesson in source.lessons] == ["Appunti"]
    lesson = cast(Any, outline_json([source], frozenset()))[0]["lessons"][0]
    assert lesson["structure_lesson"] is None


def test_journal_topics_mark_lessons_studied_case_and_space_insensitively() -> None:
    source = _structured(3)
    studied = studied_keys([source], ["  lezione   1", "Altro"])
    assert studied == frozenset({source.lessons[1].key})


def test_long_titles_become_bounded_topics() -> None:
    topic = lesson_topic("x" * 300)
    assert len(topic) == 120
    assert topic.endswith("…")


def test_without_an_exam_date_there_is_no_schedule() -> None:
    schedule = build_schedule(today=TODAY, exam_date=None, sources=[_structured(4)],
                              studied=frozenset())
    assert schedule.status == "unset"
    assert schedule.days == ()


def test_few_lessons_are_spaced_out_with_practice_days_then_review() -> None:
    source = _structured(10)
    schedule = build_schedule(today=TODAY, exam_date=date(2026, 10, 25), sources=[source],
                              studied=frozenset())
    kinds = [day.kind for day in schedule.days]
    assert schedule.days_remaining == 20
    assert schedule.review_days == 3  # round(20 * .15)
    assert kinds.count("study") == 10
    assert set(kinds[:17]) == {"study", "practice"}
    assert kinds[0] == "study"
    assert kinds[17:20] == ["review"] * 3
    assert kinds[-1] == "exam"
    assert schedule.days[0].day == TODAY
    assert [key for day in schedule.days for key in day.lesson_keys] == [
        lesson.key for lesson in source.lessons
    ]
    assert schedule.to_json()["lessons_per_study_day"] == 1.0


def test_more_lessons_than_days_are_grouped_evenly_and_keep_order() -> None:
    source = _structured(30)
    schedule = build_schedule(today=TODAY, exam_date=date(2026, 10, 15), sources=[source],
                              studied=frozenset({source.lessons[0].key}))
    study = [day for day in schedule.days if day.kind == "study"]
    assert schedule.review_days == 2  # round(10 * .15)
    assert len(study) == 8
    flat = [key for day in study for key in day.lesson_keys]
    assert flat == [lesson.key for lesson in source.lessons[1:]]
    sizes = {len(day.lesson_keys) for day in study}
    assert sizes <= {3, 4}
    assert schedule.to_json()["lessons_pending"] == 29


def test_everything_studied_leaves_only_review() -> None:
    source = _structured(2)
    done = frozenset(lesson.key for lesson in source.lessons)
    schedule = build_schedule(today=TODAY, exam_date=date(2026, 10, 8), sources=[source],
                              studied=done)
    assert [day.kind for day in schedule.days] == ["review", "review", "review", "exam"]
    assert schedule.study_days == 0


def test_exam_today_and_past_exams() -> None:
    source = _structured(2)
    today = build_schedule(today=TODAY, exam_date=TODAY, sources=[source], studied=frozenset())
    assert [day.kind for day in today.days] == ["exam"]
    past = build_schedule(today=TODAY, exam_date=date(2026, 10, 1), sources=[source],
                          studied=frozenset())
    assert past.status == "past"
    assert past.days == ()
