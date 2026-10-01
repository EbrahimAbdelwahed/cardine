from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

from cardine.demo.ui_application import RepositoryUiApplication
from study_agent.domain import CourseId, SessionId

if TYPE_CHECKING:
    from tests.integration.demo.TUT08.test_repository_backed_chat import _command, _repository
else:
    from test_repository_backed_chat import _command, _repository

COURSE = CourseId("cardine-course")
SESSION = SessionId("cardine-session")


def test_natural_italian_lesson_request_recovers_from_promise_and_answers(
    tmp_path: Path,
) -> None:
    """A study request must not surface Luna's promise as the final answer."""

    root, adapters, model = _repository(
        tmp_path,
        (
            {
                "kind": "assistant_message",
                "message": "Certo, avvio una spiegazione basata sulle fonti.",
            },
        ),
        source_content=b"# Lezione 1\nThe aortic valve has three cusps.",
    )
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])

    receipt = app.post(
        "/api/v1/session/turns",
        _command(
            "natural-lesson-request",
            sequence,
            "certo, studiamo la lezione 1. Di cosa parla?",
        ),
    )

    assert receipt["status"] == "completed"
    timeline = cast(tuple[dict[str, object], ...], app.get("/api/v1/session")["timeline"])
    answer = str(timeline[-1]["content"])
    assert "three cusps" in answer
    assert "avvio una spiegazione" not in answer
    assert [request.metadata.get("prompt_id") for request in model.requests] == [
        "tutor_decision.v1",
        "explain_concept.v1",
    ]


def test_social_lesson_mention_remains_conversational(tmp_path: Path) -> None:
    """Mentioning a lesson socially is not itself a request to explain it."""

    root, adapters, model = _repository(
        tmp_path,
        (
            {
                "kind": "assistant_message",
                "message": "In bocca al lupo per la lezione di oggi!",
            },
        ),
        source_content=b"# Lezione 1\nThe aortic valve has three cusps.",
    )
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])

    receipt = app.post(
        "/api/v1/session/turns",
        _command("social-lesson-mention", sequence, "Ciao, oggi ho la lezione 1"),
    )

    assert receipt["status"] == "assistant_message"
    timeline = cast(tuple[dict[str, object], ...], app.get("/api/v1/session")["timeline"])
    assert timeline[-1]["content"] == "In bocca al lupo per la lezione di oggi!"
    assert [request.metadata.get("prompt_id") for request in model.requests] == [
        "tutor_decision.v1",
    ]


def test_answered_clarification_gets_one_semantic_retry_then_explains(
    tmp_path: Path,
) -> None:
    """A direct answer to the tutor's choice cannot restart the same clarification."""

    root, adapters, model = _repository(
        tmp_path,
        (
            {
                "kind": "ask_learner",
                "question": (
                    "Vuoi approfondire gli effetti sulla proteina in generale "
                    "oppure l'acetilazione degli istoni?"
                ),
            },
            {
                "kind": "ask_learner",
                "question": (
                    "Vuoi approfondire gli effetti sulla proteina in generale "
                    "oppure l'acetilazione degli istoni?"
                ),
            },
            {
                "kind": "start_capability",
                "capability_id": "explain_concept",
                "inputs": {
                    "query": "acetilazione istoni",
                    "target": "acetilazione degli istoni",
                    "language": "it",
                    "learner_goal": None,
                    "continuation_summary_json": None,
                },
            },
        ),
        source_content=(
            b"# Acetilazione degli istoni\n"
            b"L'acetilazione degli istoni regola l'accessibilita della cromatina."
        ),
    )
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    first = app.post(
        "/api/v1/session/turns",
        _command(
            "clarification-question",
            sequence,
            "voglio capire gli effetti sulla proteina",
        ),
    )

    receipt = app.post(
        "/api/v1/session/turns",
        _command(
            "clarification-answer",
            cast(int, first["high_water_sequence"]),
            "negli istoni",
        ),
    )

    assert receipt["status"] == "completed"
    timeline = cast(tuple[dict[str, object], ...], app.get("/api/v1/session")["timeline"])
    assert "three cusps" in str(timeline[-1]["content"])
    assert "Vuoi approfondire" not in str(timeline[-1]["content"])
    assert [request.metadata.get("prompt_id") for request in model.requests] == [
        "tutor_decision.v1",
        "tutor_decision.v1",
        "tutor_decision.v1",
        "explain_concept.v1",
    ]
    recovery_context = json.loads(model.requests[2].messages[-1].content)
    assert recovery_context["tutor_snapshot"]["clarification_resolution"] == {
        "current_answer": "negli istoni",
        "previous_question": (
            "Vuoi approfondire gli effetti sulla proteina in generale "
            "oppure l'acetilazione degli istoni?"
        ),
        "instruction": (
            "Treat the current answer as resolving the previous question. "
            "Choose the study action now; ask again only if the answer is genuinely unusable."
        ),
    }
