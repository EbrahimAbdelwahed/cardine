from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from cardine.application.conversation_turn import (
    ConversationTurnError,
    ConversationTurnErrorCode,
)
from cardine.application.flashcard_proposals import _lesson_plan, _LessonEvidenceResolver
from cardine.cli import main as cli_main
from cardine.cli.repository import LocalRepository
from cardine.demo.ui_application import _conversation_ui_error, _source_grounding_status
from cardine.hosts import TutorHostRunResult, TutorHostRunStatus
from cardine.integrations.study_agent.course_policy import ProviderConsentRequiredError
from study_agent.domain import (
    ChunkId,
    Citation,
    CourseId,
    ExecutionContext,
    ResolvedCitation,
    RevisionId,
    SourceChunk,
    SourceId,
    TutorSnapshotV1,
)

from study_agent.flashcards import FlashcardLessonPlan, PlannedFlashcardBundle
from study_agent.retrieval import CourseSourceContent

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

    plan = _lesson_plan(cast(CourseSourceContent, content), frozenset({SOURCE}))

    assert plan.index == ()
    assert plan.bundles == ()


def test_retired_source_keeps_raw_historical_flashcard_evidence_resolvable() -> None:
    text = "historical source text"
    chunk = SourceChunk(
        ChunkId("chunk-historical"),
        SOURCE,
        RevisionId("revision-historical"),
        0,
        len(text),
        ("Lesson",),
        0,
        "a" * 64,
        "chunker@1",
    )
    document = SimpleNamespace(
        source_id=SOURCE,
        revision_id=chunk.revision_id,
        chunk=chunk,
    )

    class HistoricalContent:
        def documents(self, *, include_superseded: bool = False) -> tuple[SimpleNamespace, ...]:
            return (document,) if include_superseded else ()

        def get_text(self, revision_id: RevisionId) -> str:
            assert revision_id == chunk.revision_id
            return text

        def resolve(self, citation: Citation) -> ResolvedCitation:
            return ResolvedCitation(citation, text)

    span = SimpleNamespace(
        source_id=SOURCE,
        revision_id=chunk.revision_id,
        start_offset=chunk.start_offset,
        end_offset=chunk.end_offset,
        locator="historical locator",
    )
    slot = SimpleNamespace(span=span)
    evidence = _LessonEvidenceResolver(
        cast(CourseSourceContent, HistoricalContent()), lambda: frozenset({SOURCE})
    ).resolve(
        cast(FlashcardLessonPlan, SimpleNamespace(plan_fingerprint="a" * 64)),
        cast(PlannedFlashcardBundle, SimpleNamespace(slots=(slot,), bundle_id="bundle-historical")),
        (),
        cast(ExecutionContext, SimpleNamespace()),
    )

    assert evidence.envelope.items[0].evidence.text == text
    assert evidence.bundle_id == "bundle-historical"


def test_all_retired_sources_report_empty_grounding_status() -> None:
    snapshot = SimpleNamespace(
        materials=(SimpleNamespace(source_id=SOURCE, chunk_count=2),)
    )
    source_lifetime = SimpleNamespace(retired_source_ids=lambda course_id: frozenset({SOURCE}))
    repository = SimpleNamespace(source_lifetime=source_lifetime)

    status = _source_grounding_status(
        cast(LocalRepository, repository), COURSE, cast(TutorSnapshotV1, snapshot)
    )

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


def test_cli_maps_provider_consent_to_truthful_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    async def fail(*args: object, **kwargs: object) -> None:
        raise ProviderConsentRequiredError("provider consent is required")

    monkeypatch.setattr(cli_main, "execute", fail)
    status = cli_main.main(
        (
            "--json",
            "--repository",
            str(tmp_path),
            "ask",
            "course-a2",
            "question",
        )
    )

    assert status == 4
    assert capsys.readouterr().out == (
        '{"error":{"code":"consent_required",'
        '"message":"provider consent is required before tutor execution"},"ok":false}\n'
    )
