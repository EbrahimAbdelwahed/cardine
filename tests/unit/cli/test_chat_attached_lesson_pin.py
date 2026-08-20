"""Coverage for the lesson a learner attaches to the chat composer.

The browser sends the attached pin with the turn.  The attachment must be
validated before any provider work, and it must outrank a lesson reference
inferred from the wording of the turn.
"""

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
from cardine.cli.repository import _RepositoryTutorGateway
from cardine.demo.ui_application import UiRequestError, _command
from cardine.knowledge import SourcePin
from study_agent.domain import (
    CorrelationId,
    CourseId,
    ExecutionContext,
    PrincipalKind,
    SessionId,
    SourceId,
)
from study_agent.skills import ArtifactReference, SemanticVersion
from tests.course_fixtures import create_canonical_course

COURSE = CourseId("course-chat-attached-lesson")


def _repository(tmp_path: Path, builds: list[int]) -> Path:
    root = tmp_path / "repository"

    def build(_config: ModelAdapterConfig, _credential: str | None) -> object:
        builds.append(1)
        return object()

    initialize_local_repository(root, LocalRepositoryConfig(ModelAdapterConfig("fixture-adapter")))
    with LocalRepository.open(
        root,
        model_adapters=ModelAdapterRegistry({"fixture-adapter": build}),
        environment={},
    ) as repository:
        create_canonical_course(repository.events, COURSE)
        repository.for_course(COURSE).ingestion.ingest(
            filename="lessons.md",
            content=(
                b"# Lezione 1\n\nIl nervo vago innerva il cuore.\n\n"
                b"# Lezione 2\n\nContenuto che non appartiene alla lezione uno.\n"
            ),
            source_id=SourceId("source-chat-attached-lesson"),
            title="Lezioni",
            trust_level=100,
            source_role="reference",
            context=ExecutionContext(
                PrincipalKind.SERVICE,
                "chat-attached-lesson-test",
                COURSE,
                CorrelationId("chat-attached-lesson-ingest"),
            ),
        )
        repository.rebuild_retrieval()
        repository.reconcile_pageindex(COURSE, budget=4)
    return root


def _pin(root: Path) -> SourcePin:
    with LocalRepository.open(root, environment={}) as repository:
        result = repository.search_lessons(COURSE, "Lezione 1")
        assert len(result.candidates) == 1
        return repository.select_lesson(COURSE, "Lezione 1", result.candidates[0].candidate_id)


def test_foreign_attachment_fails_before_provider_construction(tmp_path: Path) -> None:
    builds: list[int] = []
    root = _repository(tmp_path, builds)
    pin = _pin(root)
    builds.clear()

    with LocalRepository.open(
        root,
        model_adapters=ModelAdapterRegistry(
            {"fixture-adapter": lambda _config, _credential: builds.append(1) or object()}
        ),
        environment={},
    ) as repository, pytest.raises(ValueError, match="another course"):
        repository.tutor_conversation(
            COURSE, lesson_pin=replace(pin, course_id="foreign-course")
        )
    assert builds == []


def test_stale_attachment_fails_before_provider_construction(tmp_path: Path) -> None:
    builds: list[int] = []
    root = _repository(tmp_path, builds)
    pin = _pin(root)
    builds.clear()

    with LocalRepository.open(
        root,
        model_adapters=ModelAdapterRegistry(
            {"fixture-adapter": lambda _config, _credential: builds.append(1) or object()}
        ),
        environment={},
    ) as repository, pytest.raises(ValueError):
        repository.tutor_conversation(
            COURSE, lesson_pin=replace(pin, start_offset=1, end_offset=2)
        )
    assert builds == []


def test_attached_lesson_outranks_a_lesson_named_in_the_turn(tmp_path: Path) -> None:
    root = _repository(tmp_path, [])
    pin = _pin(root)

    with LocalRepository.open(root, environment={}) as repository:
        session_id = repository.session_service.start(
            ExecutionContext(
                PrincipalKind.HUMAN,
                "chat-attached-lesson-test",
                COURSE,
                CorrelationId("chat-attached-lesson-session"),
                session_id=SessionId("session-chat-attached-lesson"),
            )
        ).id

        def refuse(*_args: object, **_kwargs: object) -> SourcePin | None:
            raise AssertionError("an attached lesson must not be re-resolved from the wording")

        repository.resolve_lesson_scope = refuse  # type: ignore[method-assign]
        gateway = _RepositoryTutorGateway(
            repository,
            COURSE,
            session_id,
            object(),  # type: ignore[arg-type]
            ArtifactReference("fixture-adapter", SemanticVersion.parse("1.0.0")),
            None,
            pin,
        )

        # The turn names lesson 2 while lesson 1 is attached; building the
        # capability gateway must not consult the wording at all.
        assert gateway._gateway(
            {"query": "spiegami la lezione 2"},
            ExecutionContext(
                PrincipalKind.HUMAN,
                "chat-attached-lesson-test",
                COURSE,
                CorrelationId("chat-attached-lesson-turn"),
                session_id=session_id,
            ),
        )


def test_wording_still_resolves_a_lesson_when_nothing_is_attached(tmp_path: Path) -> None:
    root = _repository(tmp_path, [])

    with LocalRepository.open(root, environment={}) as repository:
        resolved = repository.resolve_lesson_scope(COURSE, "spiegami la lezione 1")
        assert isinstance(resolved, SourcePin)


def _turn_command(payload: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "request_id": "request-1",
        "expected_sequence": 0,
        "payload": payload,
    }


def test_turn_command_admits_only_the_declared_optional_attachment() -> None:
    request_id, expected, payload = _command(
        _turn_command({"content": "spiegami", "lesson_pin": {"course_id": str(COURSE)}}),
        payload_key="content",
        optional_payload_keys={"lesson_pin"},
    )

    assert request_id == "request-1"
    assert expected == 0
    assert set(payload) == {"content", "lesson_pin"}

    # Without the declaration the payload stays strict, and an undeclared key
    # is never admitted.
    with pytest.raises(UiRequestError):
        _command(
            _turn_command({"content": "spiegami", "lesson_pin": {}}),
            payload_key="content",
        )
    with pytest.raises(UiRequestError):
        _command(
            _turn_command({"content": "spiegami", "unexpected": 1}),
            payload_key="content",
            optional_payload_keys={"lesson_pin"},
        )
    with pytest.raises(UiRequestError):
        _command(
            _turn_command({"lesson_pin": {}}),
            payload_key="content",
            optional_payload_keys={"lesson_pin"},
        )
