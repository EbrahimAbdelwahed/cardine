"""Product-level acceptance journeys over Cardine's canonical repository surface.

These tests intentionally sit above unit/integration contracts. They model what
an actual learner does and assert durable product outcomes rather than internal
implementation details. No network credential is required.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from cardine.demo.ui_application import RepositoryUiApplication
from study_agent.domain import CourseId, SessionId
from tests.integration.demo.TUT08.test_repository_backed_chat import _command, _repository

COURSE = CourseId("cardine-course")
SESSION = SessionId("cardine-session")


def _timeline(app: RepositoryUiApplication) -> tuple[dict[str, object], ...]:
    return cast(tuple[dict[str, object], ...], app.get("/api/v1/session")["timeline"])


def test_learner_can_open_course_see_sources_chat_and_resume_after_restart(tmp_path: Path) -> None:
    root, adapters, model = _repository(tmp_path)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)

    bootstrap = app.get("/api/v1/bootstrap")
    assert bootstrap["shell_status"] == "ready"
    materials = cast(tuple[dict[str, object], ...], app.get("/api/v1/materials")["items"])
    assert materials and materials[0]["title"] == "Valve notes"

    sequence = cast(int, bootstrap["high_water_sequence"])
    receipt = app.post(
        "/api/v1/session/turns",
        _command("product-chat", sequence, "aortic"),
    )
    assert receipt["status"] == "assistant_message"
    assert len(model.requests) == 1

    before_restart = _timeline(app)
    restarted = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    after_restart = _timeline(restarted)
    assert tuple((row["role"], row["content"]) for row in after_restart) == tuple(
        (row["role"], row["content"]) for row in before_restart
    )

    retry = restarted.post(
        "/api/v1/session/turns",
        _command("product-chat", sequence, "aortic"),
    )
    assert retry["presentation_id"] == receipt["presentation_id"]
    assert len(model.requests) == 1


def test_explicit_source_question_is_grounded_without_routing_model_guesswork(
    tmp_path: Path,
) -> None:
    root, adapters, model = _repository(tmp_path)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])

    receipt = app.post(
        "/api/v1/session/turns",
        _command("product-grounded", sequence, "Leggi Valve notes"),
    )

    assert receipt["status"] == "completed"
    answer = str(_timeline(app)[-1]["content"])
    assert "three cusps" in answer
    assert [request.metadata.get("prompt_id") for request in model.requests] == [
        "explain_concept.v1"
    ]
    citations = cast(tuple[dict[str, object], ...], _timeline(app)[-1]["citations"])
    assert citations and citations[0]["source_id"] == "valves"


def test_navigation_surfaces_remain_consistent_after_learning_activity(tmp_path: Path) -> None:
    root, adapters, _model = _repository(tmp_path)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    app.post(
        "/api/v1/session/turns",
        _command("product-learning-turn", sequence, "aortic"),
    )

    routes = (
        "/api/v1/bootstrap",
        "/api/v1/session",
        "/api/v1/materials",
        "/api/v1/artifacts",
        "/api/v1/assessments",
        "/api/v1/evidence",
        "/api/v1/recall/due",
        "/api/v1/plan",
        "/api/v1/context/conflicts",
    )
    payloads = tuple(app.get(route) for route in routes)
    assert all(payload["schema_version"] == 1 for payload in payloads)
    assert app.get("/api/v1/materials")["status"] == "ready"
    assert app.get("/api/v1/session")["status"] == "active"


def test_product_retry_does_not_duplicate_canonical_learner_progress(tmp_path: Path) -> None:
    root, adapters, model = _repository(tmp_path)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    command = _command("product-idempotent", sequence, "aortic")

    first = app.post("/api/v1/session/turns", command)
    first_timeline = _timeline(app)
    second = app.post("/api/v1/session/turns", command)
    second_timeline = _timeline(app)

    assert second["presentation_id"] == first["presentation_id"]
    assert len(second_timeline) == len(first_timeline) == 2
    assert len(model.requests) == 1
