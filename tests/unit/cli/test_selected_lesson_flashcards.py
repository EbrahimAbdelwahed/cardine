from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from cardine.application.flashcard_proposals import (
    _ScopedCourseSourceContent,
)
from cardine.cli import (
    LocalRepository,
    ModelAdapterConfig,
    ModelAdapterRegistry,
    initialize_local_repository,
)
from cardine.cli.repository import ModelAdapterBuilder
from cardine.demo.ui_application import _flashcard_review_content
from cardine.integrations.study_agent.course_policy import ProviderConsentRequiredError
from cardine.knowledge import SourcePin
from study_agent.artifacts import (
    AnswerBlock,
    ArtifactRevisionRecord,
    HybridFlashcardContent,
    StudyArtifactEnvelope,
)
from study_agent.domain import (
    CorrelationId,
    CourseId,
    ExecutionContext,
    HybridFlashcardRole,
    PrincipalKind,
    RetrievalForm,
    RevisionId,
    SourceId,
    StudyArtifactKind,
)
from study_agent.ports import ModelPort
from study_agent.repository_config import LocalRepositoryConfig
from tests.course_fixtures import create_canonical_course


def _repository(tmp_path: Path, builds: list[int]) -> tuple[Path, CourseId, SourcePin]:
    root = tmp_path / "repository"

    def build(_config: ModelAdapterConfig, _credential: str | None) -> ModelPort:
        builds.append(1)
        return cast(ModelPort, object())

    initialize_local_repository(root, LocalRepositoryConfig(ModelAdapterConfig("fixture-adapter")))
    course_id = CourseId("course-selected-flashcards")
    with LocalRepository.open(
        root,
        model_adapters=ModelAdapterRegistry(
            {"fixture-adapter": cast(ModelAdapterBuilder, build)}
        ),
        environment={},
    ) as repository:
        create_canonical_course(repository.events, course_id)
        repository.for_course(course_id).ingestion.ingest(
            filename="lessons.md",
            content=b"# Lezione 1\nVago nervo.\n# Lezione 2\nAltro.",
            source_id=SourceId("source-selected-flashcards"),
            title="Lezioni",
            trust_level=100,
            source_role="reference",
            context=ExecutionContext(
                PrincipalKind.SERVICE,
                "selected-flashcards-test",
                course_id,
                CorrelationId("selected-flashcards-ingest"),
            ),
        )
        result = repository.search_lessons(course_id, "Lezione 1")
        pin = repository.select_lesson(course_id, "Lezione 1", result.candidates[0].candidate_id)
    return root, course_id, pin


def _record_build(
    builds: list[int], _config: ModelAdapterConfig, _credential: str | None
) -> ModelPort:
    builds.append(1)
    return cast(ModelPort, object())


def test_scoped_lesson_content_contains_only_complete_selected_chunks(tmp_path: Path) -> None:
    root, course_id, pin = _repository(tmp_path, [])
    with LocalRepository.open(root, environment={}) as repository:
        scoped = _ScopedCourseSourceContent(repository.for_course(course_id).content, pin)
        records = scoped.catalog()
        assert len(records) == 1
        assert records[0].chunks
        assert all(chunk.section_path == ("Lezione 1",) for chunk in records[0].chunks)
        assert all(
            chunk.start_offset >= pin.start_offset and chunk.end_offset <= pin.end_offset
            for chunk in records[0].chunks
        )
        assert "Altro" not in records[0].text
        assert "Altro" not in scoped.get_text(RevisionId(pin.revision_id))
        assert all("Altro" not in document.text for document in scoped.documents())
        assert all(
            "Altro" not in scoped.canonical_document(chunk.chunk_id).text
            for chunk in records[0].chunks
        )
        assert "Altro" not in records[0].text[pin.start_offset : pin.end_offset]


def test_selected_lesson_pin_rejects_foreign_and_partial_before_model_build(tmp_path: Path) -> None:
    builds: list[int] = []
    root, course_id, pin = _repository(tmp_path, builds)
    with LocalRepository.open(
        root,
        model_adapters=ModelAdapterRegistry(
            {
                "fixture-adapter": lambda config, credential: _record_build(
                    builds, config, credential
                )
            }
        ),
        environment={},
    ) as repository:
        from study_agent.domain import SessionId

        selected_session = SessionId("selected-flashcards-session")
        session_id = repository.session_service.start(
            ExecutionContext(
                PrincipalKind.HUMAN,
                "selected-flashcards-session",
                course_id,
                CorrelationId("selected-flashcards-session"),
                    session_id=selected_session,
            )
        ).id
        context = ExecutionContext(
            PrincipalKind.HUMAN,
            "selected-flashcards-request",
            course_id,
            CorrelationId("selected-flashcards-request"),
            session_id=session_id,
            idempotency_key="selected-flashcards-request",
        )
        with pytest.raises(ValueError, match="another course"):
            asyncio.run(
                repository.propose_flashcards_for_pin(
                    course_id,
                    session_id,
                    replace(pin, course_id="foreign-course"),
                    "Crea flashcard",
                    context,
                )
            )
        with pytest.raises(ValueError, match="current canonical candidate"):
            asyncio.run(
                repository.propose_flashcards_for_pin(
                    course_id,
                    session_id,
                    replace(pin, start_offset=pin.start_offset + 1),
                    "Crea flashcard",
                    context,
                )
            )
    assert builds == []


def test_complete_selected_pin_requires_consent_before_model_build(tmp_path: Path) -> None:
    builds: list[int] = []
    root, course_id, pin = _repository(tmp_path, builds)
    with LocalRepository.open(
        root,
        model_adapters=ModelAdapterRegistry(
            {
                "fixture-adapter": lambda config, credential: _record_build(
                    builds, config, credential
                )
            }
        ),
        environment={},
    ) as repository:
        from study_agent.domain import SessionId

        session_id = repository.session_service.start(
            ExecutionContext(
                PrincipalKind.HUMAN,
                "selected-flashcards-consent-session",
                course_id,
                CorrelationId("selected-flashcards-consent-session"),
                session_id=SessionId("selected-flashcards-consent-session"),
            )
        ).id
        context = ExecutionContext(
            PrincipalKind.HUMAN,
            "selected-flashcards-consent-request",
            course_id,
            CorrelationId("selected-flashcards-consent-request"),
            session_id=session_id,
            idempotency_key="selected-flashcards-consent-request",
        )
        with pytest.raises(ProviderConsentRequiredError):
            asyncio.run(
                repository.propose_flashcards_for_pin(
                    course_id, session_id, pin, "Crea flashcard", context
                )
            )
    assert builds == []


def test_flashcard_review_dto_is_bounded_and_excludes_private_fields() -> None:
    content = StudyArtifactEnvelope(
        StudyArtifactKind.FLASHCARD,
        HybridFlashcardContent(
            RetrievalForm.DIRECT_RECALL,
            "Qual è il nervo?",
            (AnswerBlock("Risposta", "Il vago.", ("parasimpatico",)),),
            HybridFlashcardRole.DETAIL,
            "private rationale must not be exposed",
            (0,),
        ),
    )
    ready = _flashcard_review_content(
        cast(ArtifactRevisionRecord, SimpleNamespace(content=content))
    )
    assert ready["status"] == "ready"
    assert ready["prompt"] == "Qual è il nervo?"
    assert "rationale" not in ready
    assert "source_commitment_indices" not in ready

    malformed = StudyArtifactEnvelope(
        StudyArtifactKind.FLASHCARD,
        HybridFlashcardContent(
            RetrievalForm.DIRECT_RECALL,
            "x" * 1_201,
            (AnswerBlock("Risposta", "Il vago."),),
            HybridFlashcardRole.DETAIL,
            "rationale",
            (0,),
        ),
    )
    unavailable = _flashcard_review_content(
        cast(ArtifactRevisionRecord, SimpleNamespace(content=malformed))
    )
    assert unavailable == {"status": "unavailable", "reason": "content_oversized"}
