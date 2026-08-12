from __future__ import annotations

from types import SimpleNamespace

from cardine.application.conversation_turn import (
    ConversationTurnError,
    ConversationTurnErrorCode,
)
from cardine.application.flashcard_proposals import _lesson_plan
from cardine.demo.ui_application import _conversation_ui_error, _source_grounding_status
from cardine.hosts import TutorHostRunResult, TutorHostRunStatus
from study_agent.domain import CourseId, RevisionId, SourceId

COURSE = CourseId("a2-regression-course")
SOURCE = SourceId("a2-regression-source")


def test_retired_sources_are_excluded_from_flashcard_lesson_plan() -> None:
    source = SimpleNamespace(source_id=SOURCE, title="Retired lesson")
    chunk = SimpleNamespace(
        source_id=SOURCE,
        revision_id=RevisionId("revision-retired"),
        start_offset=0,
        end_offset=28,
        section_path=("Lesson",),
        ordinal=0,
    )
    record = SimpleNamespace(source=source, chunks=(chunk,), is_current_revision=True)
    content = SimpleNamespace(catalog=lambda: (record,))

    plan = _lesson_plan(content, frozenset({SOURCE}))

    assert plan.index == ()
    assert plan.bundles == ()


def test_all_retired_sources_report_empty_grounding_status() -> None:
    snapshot = SimpleNamespace(
        materials=(SimpleNamespace(source_id=SOURCE, chunk_count=2),)
    )
    source_lifetime = SimpleNamespace(retired_source_ids=lambda course_id: frozenset({SOURCE}))
    repository = SimpleNamespace(source_lifetime=source_lifetime)

    status = _source_grounding_status(repository, COURSE, snapshot)

    assert status == {"status": "empty", "indexed_chunks": 0}


def test_consent_required_host_result_is_not_runtime_failure() -> None:
    result = TutorHostRunResult(
        TutorHostRunStatus.FAILED,
        failure_reason="consent_required",
    )
    assert result.failure_reason == "consent_required"

    error = ConversationTurnError(
        ConversationTurnErrorCode.CONSENT_REQUIRED,
        "provider consent is required before tutor execution",
    )
    ui_error = _conversation_ui_error(error, request_id="request-1", trace_id="trace-1")
    assert ui_error.status_code == 428
    assert ui_error.diagnostic_code == "provider_consent_required"
    assert str(ui_error) == "provider consent is required before tutor execution"
