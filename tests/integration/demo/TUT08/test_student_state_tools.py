from __future__ import annotations

from pathlib import Path
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
    tmp_path: Path,
) -> None:
    root, adapters, model = _repository(
        tmp_path,
        (
            {
                "kind": "invoke_tool",
                "tool_name": "student_state.record",
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
        entries = repository.student_state.search(COURSE, query="aortic")
        all_entries = repository.student_state.search(COURSE)

    assert tuple(entry.kind for entry in entries) == (
        "topic_covered",
        "learner_signal",
    )
    assert {entry.kind for entry in all_entries} == {
        "learner_signal",
        "topic_covered",
    }
    assert all(entry.origin_sequence > 0 for entry in all_entries)


def test_failed_capability_does_not_record_a_covered_topic(tmp_path: Path) -> None:
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

    # Keep this on the model-routed path so the test exercises a failed
    # capability, rather than the deterministic explicit-explanation fast path.
    receipt = app.post(
        "/api/v1/session/turns",
        _command("study-memory-failed-turn", sequence, "aortic valve"),
    )

    assert receipt["status"] == "failed"
    with LocalRepository.open(root, model_adapters=adapters) as repository:
        assert repository.student_state.search(COURSE) == ()


def test_ui_and_tutor_read_the_same_file_without_mutating_course_events(tmp_path: Path) -> None:
    root, adapters, _ = _repository(tmp_path, ())
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    assert app.get("/api/v1/student-state")["entries"] == ()
    command = {
        "schema_version": 1,
        "request_id": "ui-difficulty",
        "expected_sequence": sequence,
        "payload": {
            "kind": "learner_signal",
            "topic": "valvola aortica",
            "summary": "Non distinguo i lembi.",
        },
    }
    receipt = app.post("/api/v1/student-state", command)
    before = (root / "state" / "student-state.json").read_bytes()
    assert app.post("/api/v1/student-state", command) == receipt
    assert (root / "state" / "student-state.json").read_bytes() == before
    assert app.get("/api/v1/bootstrap")["high_water_sequence"] == sequence
    reloaded = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    assert reloaded.get("/api/v1/student-state") == receipt["result"]
    with LocalRepository.open(root, model_adapters=adapters) as repository:
        entries = repository.student_state.search(COURSE, query="valvola")
        assert entries[0].recorded_by == "student"
        assert entries[0].origin_sequence == 0
        assert "evidence.get" not in {m.name for m in repository.harness_tools().manifests}
        assert "context.get" not in {m.name for m in repository.harness_tools().manifests}
