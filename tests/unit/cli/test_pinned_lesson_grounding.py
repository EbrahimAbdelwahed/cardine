from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cardine.cli import (
    LocalRepository,
    LocalRepositoryConfig,
    ModelAdapterConfig,
    ModelAdapterRegistry,
    initialize_local_repository,
)
from cardine.cli.repository import _PinnedRetrieval
from cardine.knowledge import SourcePin
from study_agent.domain import CorrelationId, CourseId, ExecutionContext, PrincipalKind, SourceId
from study_agent.ports import RetrievalQuery
from tests.course_fixtures import create_canonical_course


def _repository(tmp_path: Path, builds: list[int]) -> tuple[Path, CourseId]:
    root = tmp_path / "repository"

    def build(_config: ModelAdapterConfig, _credential: str | None) -> object:
        builds.append(1)
        return object()

    initialize_local_repository(
        root,
        LocalRepositoryConfig(ModelAdapterConfig("fixture-adapter")),
    )
    course_id = CourseId("course-pinned-lesson")
    with LocalRepository.open(
        root,
        model_adapters=ModelAdapterRegistry({"fixture-adapter": build}),
        environment={},
    ) as repository:
        create_canonical_course(repository.events, course_id)
        repository.for_course(course_id).ingestion.ingest(
            filename="lessons.md",
            content=b"# Lezione 1\nVago nervo.\n# Lezione 2\nAltro.",
            source_id=SourceId("source-pinned-lesson"),
            title="Lezioni",
            trust_level=100,
            source_role="reference",
            context=ExecutionContext(
                PrincipalKind.SERVICE,
                "pinned-lesson-test",
                course_id,
                CorrelationId("pinned-lesson-ingest"),
            ),
        )
        repository.rebuild_retrieval()
    return root, course_id


def _pin(root: Path, course_id: CourseId) -> SourcePin:
    with LocalRepository.open(root, environment={}) as repository:
        result = repository.search_lessons(course_id, "Lezione 1")
        assert len(result.candidates) == 1
        return repository.select_lesson(course_id, "Lezione 1", result.candidates[0].candidate_id)


def test_invalid_foreign_and_partial_pins_fail_before_provider_construction(
    tmp_path: Path,
) -> None:
    builds: list[int] = []
    root, course_id = _repository(tmp_path, builds)
    pin = _pin(root, course_id)

    with LocalRepository.open(
        root,
        model_adapters=ModelAdapterRegistry(
            {"fixture-adapter": lambda _config, _credential: builds.append(1) or object()}
        ),
        environment={},
    ) as repository:
        receipt = repository.course_index_receipt(course_id, repository.rebuild_retrieval())
        with pytest.raises(ValueError, match="another course"):
            repository.grounding_service(
                course_id, receipt, lesson_pin=replace(pin, course_id="foreign-course")
            )
        with pytest.raises(ValueError, match="current canonical candidate"):
            repository.grounding_service(
                course_id,
                receipt,
                lesson_pin=replace(pin, start_offset=1, end_offset=2),
            )
    assert builds == []


def test_whole_source_pin_is_not_a_current_lesson_candidate(tmp_path: Path) -> None:
    builds: list[int] = []
    root, course_id = _repository(tmp_path, builds)
    pin = _pin(root, course_id)
    with LocalRepository.open(
        root,
        model_adapters=ModelAdapterRegistry(
            {"fixture-adapter": lambda _config, _credential: builds.append(1) or object()}
        ),
        environment={},
    ) as repository:
        receipt = repository.course_index_receipt(course_id, repository.rebuild_retrieval())
        source = repository.validate_lesson_pin(pin)
        forged = replace(pin, start_offset=0, end_offset=len(source.text))
        with pytest.raises(ValueError, match="current canonical candidate"):
            repository.grounding_service(course_id, receipt, lesson_pin=forged)
    assert builds == []


def test_pinned_retrieval_discards_cross_section_and_partial_chunks(tmp_path: Path) -> None:
    root, course_id = _repository(tmp_path, [])
    pin = _pin(root, course_id)
    with LocalRepository.open(root, environment={}) as repository:
        scoped = _PinnedRetrieval(repository.for_course(course_id).retrieval, pin)
        inside = scoped.search(RetrievalQuery(course_id, "vago"))
        assert inside.evidence
        assert all(
            item.citation.source_id == SourceId(pin.source_id)
            and item.citation.revision_id.value == pin.revision_id
            and item.citation.start_offset >= pin.start_offset
            and item.citation.end_offset <= pin.end_offset
            for item in inside.evidence
        )

        outside = scoped.search(RetrievalQuery(course_id, "altro"))
        assert outside.evidence == ()
        assert outside.status.value == "insufficient"
