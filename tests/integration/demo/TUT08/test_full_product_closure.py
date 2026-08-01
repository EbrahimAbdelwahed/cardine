"""Release-closure journeys for the repository-backed Cardine surface.

These checks exercise ``RepositoryUiApplication`` directly for precise route,
retry, stale, restart, and public-demo assertions.  The companion
``tests/e2e/test_cardine_repository_browser_journey.py`` runs the same product
boundary in a real browser; the static assertions here are supplemental.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest

from study_agent.adapters.filesystem import initialize_local_repository
from study_agent.cli.repository import LocalRepository, ModelAdapterRegistry
from study_agent.demo.ui_application import (
    DemoUiApplication,
    RepositoryUiApplication,
    UiRequestError,
)
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

COURSE = CourseId("closure-course")
SESSION = SessionId("closure-session")
DEMO_DIR = Path(__file__).parents[4] / "src" / "study_agent" / "demo"
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
        if False:  # pragma: no cover - keeps this method an async generator
            yield ModelStreamEvent(None)  # type: ignore[arg-type]
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
            assert cast(int, payload["high_water_sequence"]) <= sequence, route
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

    # Exact retry is a no-op after restart; a different request at the old
    # sequence is rejected before model invocation or canonical writes.
    assert restarted.post("/api/v1/session/turns", command) == receipt
    with pytest.raises(UiRequestError) as stale:
        restarted.post(
            "/api/v1/session/turns",
            _command("closure-stale", sequence, {"content": "new question"}),
        )
    assert stale.value.status_code == 409
    assert len(model.requests) == 1


def test_public_demo_is_stateless_sanitized_and_mutations_are_blocked() -> None:
    demo = DemoUiApplication()
    before = demo.get("/api/v1/bootstrap")
    before_sequence = before["high_water_sequence"]

    for path in ROUTES.values():
        payload = demo.get(path)
        encoded = json.dumps(payload, sort_keys=True)
        assert payload["schema_version"] == 1
        assert "/private/" not in encoded
        assert "api_key" not in encoded.lower()
        assert "authorization" not in encoded.lower()
        assert "raw_output" not in encoded

    with pytest.raises(UiRequestError) as blocked:
        demo.post(
            "/api/v1/artifacts/revision/decisions",
            _command("public-artifact", int(before_sequence), {"decision": "accepted"}),
        )
    assert blocked.value.status_code == 405

    # A demo submission returns a new presentation value only; the cached GET
    # view and its sequence remain unchanged across the equivalent reload.
    result = demo.post(
        "/api/v1/session/turns",
        _command(
            "public-chat",
            int(before_sequence),
            {"content": "private learner question that must not persist"},
        ),
    )
    assert result["status"] == "demo_completed"
    assert result["result"]["persistence"] == "stateless_public_demo"
    after = demo.get("/api/v1/session")
    assert after["learner_entry"] != "private learner question that must not persist"
    assert demo.get("/api/v1/bootstrap")["high_water_sequence"] == before_sequence


def test_browser_control_matrix_keyboard_and_responsive_contracts() -> None:
    page = (DEMO_DIR / "browser.html").read_text(encoding="utf-8")
    javascript = (DEMO_DIR / "browser.js").read_text(encoding="utf-8")
    css = (DEMO_DIR / "browser.css").read_text(encoding="utf-8")

    routes = set(re.findall(r'data-route="([^"]+)"', page))
    assert set(ROUTES).issubset(routes)
    assert 'id="entry-form"' in page
    assert 'id="global-status" role="status" aria-live="polite"' in page
    assert '<div id="view-root" class="view-root">' in page
    assert 'id="view-root" class="view-root" aria-live="polite"' not in page
    assert 'id="rail-toggle" aria-expanded="false" aria-controls="rail"' in page

    # Every mutating UI family has one delegated, keyboard-focusable button
    # path; no raw provider/repository controls are rendered in the page.
    for command in (
        "artifact",
        "enroll",
        "assessment-present",
        "assessment-attempt",
        "assessment-grade",
        "review",
        "context",
    ):
        assert f'data-command="{command}"' in javascript
    assert "$$('[data-command]')" in javascript
    assert "submitComposerFromKeyboard" in javascript
    assert "navigationVersion: 0" in javascript
    assert "navigationVersion !== state.navigationVersion" in javascript
    assert "commandNavigationVersion === state.navigationVersion" in javascript
    assert "bootstrapNavigationVersion === state.navigationVersion" in javascript
    assert "disabledBeforeBusy" in javascript
    assert 'data-command="assessment-attempt"' in javascript
    assert '${hasResponse ? "" : " disabled"}' in javascript
    assert "submit.disabled = control.type === \"checkbox\"" in javascript
    assert "if (submit) submit.disabled = !text(control.value).trim();" in javascript
    for token in ("--ink-subtle:", "--ink-muted:", "--surface-hover:"):
        assert token in css
        assert token in css.split("@media (prefers-color-scheme: dark)", 1)[1]
    assert 'event.key === "Escape"' in javascript
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "overflow-wrap: anywhere" in css
    assert "@media (max-width: 900px)" in css
