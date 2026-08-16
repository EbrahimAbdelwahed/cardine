from __future__ import annotations

from typing import cast

from cardine.cli.repository import LocalRepository
from cardine.demo.ui_application import RepositoryUiApplication
from study_agent.ports import ModelError, ModelErrorCode
from tests.integration.demo.TUT08.test_repository_backed_chat import (
    COURSE,
    SESSION,
    _command,
    _repository,
)


def test_agent_signal_and_completed_topic_are_canonical_but_prompt_private(
    tmp_path,
) -> None:
    root, adapters, model = _repository(
        tmp_path,
        (
            {
                "kind": "invoke_tool",
                "tool_name": "study_memory.record",
                "arguments": {
                    "topic": "valvola aortica",
                    "summary": "Lo studente chiede una spiegazione di base.",
                    "signal": "self_reported_difficulty",
                    "assistance": "explanation",
                },
            },
            {
                "kind": "start_capability",
                "capability_id": "explain_concept",
                "inputs": {
                    "query": "aortic",
                    "target": "aortic valve",
                    "language": "en",
                    "learner_goal": None,
                    "continuation_summary_json": None,
                },
            },
        ),
    )
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])

    receipt = app.post(
        "/api/v1/session/turns",
        _command(
            "study-memory-agent-turn",
            sequence,
            "Faccio fatica con la valvola aortica: spiegamela.",
        ),
    )

    assert receipt["status"] == "completed", receipt
    decision_requests = tuple(
        request
        for request in model.requests
        if request.metadata.get("prompt_id") == "tutor_decision.v1"
    )
    assert len(decision_requests) == 2
    assert all(
        "study-memory@1:" not in message.content
        for request in model.requests
        for message in request.messages
    )

    with LocalRepository.open(root, model_adapters=adapters) as repository:
        entries = repository.study_memory.search(COURSE, query="aortic")
        all_entries = repository.study_memory.search(COURSE)

    assert tuple(entry.kind for entry in entries) == (
        "topic_covered",
        "learner_signal",
    )
    assert {entry.kind for entry in all_entries} == {
        "learner_signal",
        "topic_covered",
    }
    assert all(entry.origin_sequence > 0 for entry in all_entries)


def test_failed_capability_does_not_record_a_covered_topic(tmp_path) -> None:
    root, adapters, _model = _repository(
        tmp_path,
        (
            {
                "kind": "start_capability",
                "capability_id": "explain_concept",
                "inputs": {
                    "query": "aortic",
                    "target": "aortic valve",
                    "language": "en",
                    "learner_goal": None,
                    "continuation_summary_json": None,
                },
            },
        ),
        explain_error=ModelError(ModelErrorCode.AUTHENTICATION, "fixture-secret"),
    )
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])

    receipt = app.post(
        "/api/v1/session/turns",
        _command("study-memory-failed-turn", sequence, "Spiegami la valvola aortica"),
    )

    assert receipt["status"] == "failed"
    with LocalRepository.open(root, model_adapters=adapters) as repository:
        assert repository.study_memory.search(COURSE) == ()
