"""Red coverage for automatic structural lesson grounding in repository chat."""

from __future__ import annotations

from pathlib import Path

import pytest

from cardine.cli import EMPTY_CONFIG, LocalRepository, initialize_local_repository
from cardine.knowledge import LessonSelectionError, SourcePin
from study_agent.domain import CorrelationId, CourseId, ExecutionContext, PrincipalKind, SourceId
from tests.course_fixtures import create_canonical_course

COURSE = CourseId("course-automatic-lesson-grounding")


def _ingest_markdown(
    repository: LocalRepository,
    *,
    source_id: str,
    content: bytes,
) -> None:
    create_canonical_course(repository.events, COURSE)
    repository.for_course(COURSE).ingestion.ingest(
        filename=f"{source_id}.md",
        content=content,
        source_id=SourceId(source_id),
        title="Lezioni",
        trust_level=100,
        source_role="reference",
        context=ExecutionContext(
            PrincipalKind.SERVICE,
            "automatic-lesson-grounding-test",
            COURSE,
            CorrelationId(f"ingest-{source_id}"),
        ),
    )
    repository.rebuild_retrieval()
    repository.reconcile_pageindex(COURSE, budget=4)


def _repository_with_one_structured_source(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    initialize_local_repository(root, EMPTY_CONFIG)
    with LocalRepository.open(root) as repository:
        _ingest_markdown(
            repository,
            source_id="source-lesson-scope",
            content=(
                b"# Lezione 1\n\n"
                b"Prima parte della lezione uno.\n\n"
                b"Seconda parte della lezione uno.\n\n"
                b"# Lezione 2\n\n"
                b"Contenuto che non appartiene alla lezione uno.\n"
            ),
        )
    return root


def test_repository_chat_automatically_scopes_explain_query_to_all_lesson_chunks(
    tmp_path: Path,
) -> None:
    root = _repository_with_one_structured_source(tmp_path)

    with LocalRepository.open(root) as repository:
        pin = repository.resolve_lesson_scope(COURSE, "Spiegami la lezione 1")

        assert isinstance(pin, SourcePin)
        source = repository.validate_lesson_pin(pin)
        scoped_chunks = tuple(
            chunk
            for chunk in source.chunks
            if chunk.start_offset >= pin.start_offset and chunk.end_offset <= pin.end_offset
        )

        assert len(scoped_chunks) >= 3
        assert {chunk.section_path for chunk in scoped_chunks} == {("Lezione 1",)}
        assert "Prima parte della lezione uno." in {
            source.text[chunk.start_offset : chunk.end_offset] for chunk in scoped_chunks
        }
        assert "Seconda parte della lezione uno." in {
            source.text[chunk.start_offset : chunk.end_offset] for chunk in scoped_chunks
        }
        assert all(
            "Contenuto che non appartiene alla lezione uno." not in source.text[
                chunk.start_offset : chunk.end_offset
            ]
            for chunk in scoped_chunks
        )
        assert "# Lezione 2" not in source.text[pin.start_offset : pin.end_offset]


def test_repository_chat_does_not_silently_choose_ambiguous_same_title_lessons(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    initialize_local_repository(root, EMPTY_CONFIG)
    with LocalRepository.open(root) as repository:
        _ingest_markdown(
            repository,
            source_id="source-lesson-one-a",
            content=b"# Lezione 1\n\nPrima versione della lezione.\n",
        )
        repository.for_course(COURSE).ingestion.ingest(
            filename="source-lesson-one-b.md",
            content=b"# Lezione 1\n\nSeconda versione della lezione.\n",
            source_id=SourceId("source-lesson-one-b"),
            title="Lezioni duplicate",
            trust_level=100,
            source_role="reference",
            context=ExecutionContext(
                PrincipalKind.SERVICE,
                "automatic-lesson-grounding-test",
                COURSE,
                CorrelationId("ingest-source-lesson-one-b"),
            ),
        )
        repository.rebuild_retrieval()
        repository.reconcile_pageindex(COURSE, budget=4)

        with pytest.raises(LessonSelectionError, match="ambiguous"):
            repository.resolve_lesson_scope(COURSE, "Spiegami la lezione 1")


def test_repository_chat_uses_lexical_fallback_when_no_structural_lesson_matches(
    tmp_path: Path,
) -> None:
    root = _repository_with_one_structured_source(tmp_path)

    with LocalRepository.open(root) as repository:
        assert repository.resolve_lesson_scope(COURSE, "Spiegami il nervo vago") is None
