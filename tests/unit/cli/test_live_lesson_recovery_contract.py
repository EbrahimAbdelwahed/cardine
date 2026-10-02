"""Regression coverage for the converted live lesson and explain seam.

These tests intentionally describe the public repository behavior required by
the live converted Markdown source.  They are expected to remain red until
lesson aliases and structural citation construction are repaired.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from pathlib import Path

from cardine.application.conversation_turn import ConversationTurnCommand
from cardine.cli import (
    EMPTY_CONFIG,
    LocalRepository,
    LocalRepositoryConfig,
    ModelAdapterConfig,
    ModelAdapterRegistry,
    initialize_local_repository,
)
from cardine.hosts import TutorHostRunStatus
from cardine.knowledge import SearchDisposition, SourcePin
from study_agent.domain import (
    CorrelationId,
    CourseId,
    ExecutionContext,
    PrincipalKind,
    SessionId,
    SourceId,
)
from study_agent.domain._validation import JsonObject
from study_agent.ports import (
    CancellationToken,
    ModelCapabilities,
    ModelFinishReason,
    ModelInvocation,
    ModelRequest,
    ModelResponse,
    ModelStreamEvent,
)
from tests.course_fixtures import create_canonical_course

COURSE = CourseId("course-live-lesson-recovery")
SESSION = SessionId("session-live-lesson-recovery")
_EVIDENCE_ID = re.compile(r'"evidence_id"\s*:\s*"([^"]+)"')


class _ExplainFixtureModel:
    capabilities = ModelCapabilities(structured_output=True)

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if request.metadata.get("prompt_id") == "explain_concept.v1":
            rendered = "\n".join(message.content for message in request.messages)
            evidence_ids = tuple(_EVIDENCE_ID.findall(rendered))
            assert len(evidence_ids) >= 3, "explain prompt must carry the lesson evidence"
            output: JsonObject = {
                "status": "answered",
                "segments": (
                    {
                        "kind": "supported_claim",
                        "text": (
                            "Prima sezione canonica della lezione. "
                            "Seconda sezione canonica della lezione."
                        ),
                        "evidence_ids": evidence_ids[1:3],
                    },
                ),
                "unsupported_information_note": None,
            }
        else:
            output = {
                "decision": {
                    "kind": "start_capability",
                    "capability_id": "explain_concept",
                    "inputs": {
                        "query": "lezione 1",
                        "target": "Spiegami la lezione 1",
                        "language": "it",
                        "learner_goal": None,
                        "continuation_summary_json": None,
                    },
                }
            }
        return ModelResponse(
            "",
            None,
            ModelFinishReason.STOP,
            ModelInvocation(
                "fixture-adapter", "1.0.0", "fixture", "live-lesson-recovery"
            ),
            structured_output=output,
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        del request
        if False:  # pragma: no cover - makes this an async generator
            yield
        raise AssertionError("tutor decisions do not stream")

    async def cancel(self, token: CancellationToken) -> None:
        del token
        raise AssertionError("fixture cancellation is not supported")


def _ingest_live_heading(
    repository: LocalRepository,
    *,
    source_id: str = "source-live-converted-lesson",
) -> None:
    create_canonical_course(repository.events, COURSE)
    repository.for_course(COURSE).ingestion.ingest(
        filename="lezioni-convertite.md",
        content=(
            b"# L01_04/03/2025\n\n"
            b"Prima sezione canonica della lezione.\n\n"
            b"Seconda sezione canonica della lezione.\n\n"
            b"# L02_11/03/2025\n\n"
            b"Contenuto di un'altra lezione.\n"
        ),
        source_id=SourceId(source_id),
        title="Lezioni",
        trust_level=100,
        source_role="reference",
        context=ExecutionContext(
            PrincipalKind.SERVICE,
            "live-lesson-recovery-test",
            COURSE,
            CorrelationId(f"ingest-{source_id}"),
        ),
    )
    repository.rebuild_retrieval()
    repository.reconcile_pageindex(COURSE, budget=4)


def test_converted_l01_heading_is_found_and_resolved_as_lezione_1(
    tmp_path: Path,
) -> None:
    """``Lezione 1`` must resolve the live ``L01_<date>`` Markdown heading."""

    root = tmp_path / "repository"
    initialize_local_repository(root, EMPTY_CONFIG)
    with LocalRepository.open(root) as repository:
        _ingest_live_heading(repository)

    with LocalRepository.open(root) as repository:
        found = repository.search_lessons(COURSE, "Lezione 1")

        assert found.disposition is SearchDisposition.UNIQUE
        assert len(found.candidates) == 1
        assert found.candidates[0].section_title == "L01_04/03/2025"

        pin = repository.resolve_lesson_scope(COURSE, "Studiamo la lezione 1")
        assert isinstance(pin, SourcePin)
        assert pin.section_title == "L01_04/03/2025"
        source = repository.validate_lesson_pin(pin)
        selected = source.text[pin.start_offset : pin.end_offset]
        assert "Prima sezione canonica della lezione." in selected
        assert "Seconda sezione canonica della lezione." in selected
        assert "Contenuto di un'altra lezione." not in selected
        assert pin.end_offset < len(source.text)


def test_repository_chat_explain_with_resolved_section_reaches_model_with_canonical_evidence(
    tmp_path: Path,
) -> None:
    """A resolvable structural pin must not fail citation validation before Luna."""

    root = tmp_path / "repository"
    model = _ExplainFixtureModel()
    config = LocalRepositoryConfig(ModelAdapterConfig("fixture-adapter"))
    initialize_local_repository(root, config)
    adapters = ModelAdapterRegistry({"fixture-adapter": lambda _config, _credential: model})

    with LocalRepository.open(root, model_adapters=adapters, environment={}) as repository:
        _ingest_live_heading(repository)
        repository.session_service.start(
            ExecutionContext(
                PrincipalKind.HUMAN,
                "live-lesson-learner",
                COURSE,
                CorrelationId("live-lesson-session"),
                session_id=SESSION,
            )
        )
        repository.provider_consent_service.grant(
            ExecutionContext(
                PrincipalKind.HUMAN,
                "live-lesson-learner",
                COURSE,
                CorrelationId("live-lesson-consent"),
            ),
            "live-lesson-consent",
        )
        conversation = repository.tutor_conversation(COURSE, session_id=SESSION)
        sequence = repository.tutor_snapshots.get(COURSE, SESSION).high_water_sequence
        result = asyncio.run(
            conversation.turn(
                ConversationTurnCommand(
                    # Keep this regression on the model-selected explain path;
                    # explicit "spiegami/leggi" language is intentionally
                    # intercepted by the deterministic source fast path.
                    "Lezione 1",
                    ExecutionContext(
                        PrincipalKind.HUMAN,
                        "live-lesson-learner",
                        COURSE,
                        CorrelationId("live-lesson-turn"),
                        session_id=SESSION,
                        idempotency_key="live-lesson-turn",
                    ),
                    sequence,
                )
            )
        )

        assert result.status is TutorHostRunStatus.COMPLETED
        assert "Prima sezione canonica della lezione." in result.presentation.content
        assert "Seconda sezione canonica della lezione." in result.presentation.content
        assert any(
            request.metadata.get("prompt_id") == "explain_concept.v1"
            for request in model.requests
        )
