"""Release-closure journeys for the repository-backed Cardine surface.

These checks exercise ``RepositoryUiApplication`` directly for precise route,
retry, stale, and restart assertions.  The companion
``tests/e2e/test_cardine_repository_browser_journey.py`` runs the same product
boundary in a real browser; the static assertions here are supplemental.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest

from cardine.cli.repository import LocalRepository, ModelAdapterRegistry
from cardine.demo.ui_application import RepositoryUiApplication, UiRequestError
from study_agent.adapters.filesystem import initialize_local_repository
from study_agent.domain import (
    CorrelationId,
    CourseId,
    CourseProfile,
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
from study_agent.repository_config import LocalRepositoryConfig, ModelAdapterConfig
from tests.receipt_assertions import without_transient_activity

COURSE = CourseId("closure-course")
SESSION = SessionId("closure-session")
DEMO_DIR = Path(__file__).parents[4] / "src" / "cardine" / "demo"
ROUTES = {
    "oggi": "/api/v1/bootstrap",
    "sessione": "/api/v1/session",
    "fonti": "/api/v1/materials",
    "proposte": "/api/v1/artifacts",
    "verifiche": "/api/v1/assessments",
    "evidenze": "/api/v1/evidence",
    "ripasso": "/api/v1/recall/due",
    "piano": "/api/v1/plan",
    "conflitti": "/api/v1/context/conflicts",
}


class _ClosureModel:
    capabilities = ModelCapabilities(structured_output=True)

    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            "",
            None,
            ModelFinishReason.STOP,
            ModelInvocation("closure-fixture", "1.0.0", "fixture", "closure"),
            structured_output={
                "decision": {
                    "kind": "assistant_message",
                    "message": "Which valve should we focus on?",
                }
            },
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        del request
        if False:  # pragma: no cover
            yield ModelStreamEvent(None)
        raise AssertionError("closure fixture does not stream")

    async def cancel(self, token: CancellationToken) -> None:
        del token
        raise AssertionError("closure fixture cancellation is not supported")


def _repository(tmp_path: Path) -> tuple[Path, ModelAdapterRegistry, _ClosureModel]:
    root = tmp_path / "repository"
    initialize_local_repository(
        root,
        LocalRepositoryConfig(ModelAdapterConfig("closure-fixture", {}, None)),
    )
    model = _ClosureModel()
    adapters = ModelAdapterRegistry(
        {"closure-fixture": lambda _config, _credential: model},
        versions={"closure-fixture": "1.0.0"},
    )
    with LocalRepository.open(root, model_adapters=adapters) as repository:
        repository.course_service.create(
            CourseProfile(COURSE, "Closure course", "en", learning_goals=("Study",)),
            ExecutionContext(
                PrincipalKind.SERVICE,
                "closure-course",
                COURSE,
                CorrelationId("closure-course-create"),
            ),
        )
        repository.for_course(COURSE).ingestion.ingest(
            filename="valves.md",
            content=b"The aortic valve has three cusps.",
            source_id=SourceId("closure-source"),
            title="Valve notes",
            trust_level=90,
            source_role="primary",
            context=ExecutionContext(
                PrincipalKind.SERVICE,
                "closure-ingest",
                COURSE,
                CorrelationId("closure-source-ingest"),
            ),
        )
        repository.session_service.start(
            ExecutionContext(
                PrincipalKind.HUMAN,
                "closure-session",
                COURSE,
                CorrelationId("closure-session-start"),
                session_id=SESSION,
            )
        )
        repository.provider_consent_service.grant(
            ExecutionContext(
                PrincipalKind.HUMAN,
                "closure-session",
                COURSE,
                CorrelationId("closure-provider-consent"),
            ),
            "closure-provider-consent",
        )
    return root, adapters, model


def _command(request_id: str, expected_sequence: int, payload: JsonObject) -> JsonObject:
    return {
        "schema_version": 1,
        "request_id": request_id,
        "expected_sequence": expected_sequence,
        "payload": payload,
    }


def test_repository_route_control_matrix_and_restart_safe_chat(tmp_path: Path) -> None:
    """All navigation reads are truthful, and a chat turn survives restart/retry."""

    root, adapters, model = _repository(tmp_path)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    initial = app.get(ROUTES["oggi"])
    assert initial["mode"] == "local_repository"
    sequence = cast(int, initial["high_water_sequence"])

    responses = {route: app.get(path) for route, path in ROUTES.items()}
    assert set(responses) == set(ROUTES)
    for route, payload in responses.items():
        assert payload["schema_version"] == 1, route
        if "high_water_sequence" in payload:
            assert isinstance(payload["high_water_sequence"], int), route
            assert payload["high_water_sequence"] <= sequence, route
    assert responses["fonti"]["status"] == "ready"
    assert responses["proposte"]["status"] == "empty"
    assert responses["verifiche"]["status"] == "empty"
    assert responses["evidenze"]["status"] == "empty"
    assert responses["ripasso"]["status"] == "not_configured"
    assert responses["conflitti"]["status"] == "empty"

    command = _command("closure-chat", sequence, {"content": "aortic valve"})
    receipt = app.post("/api/v1/session/turns", command)
    assert receipt["status"] == "assistant_message"
    assert len(model.requests) == 1

    restarted = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    session = restarted.get(ROUTES["sessione"])
    timeline = cast(tuple[dict[str, object], ...], session["timeline"])
    assert [item["role"] for item in timeline] == ["learner", "assistant"]
    assert timeline[-1]["content"] == "Which valve should we focus on?"

