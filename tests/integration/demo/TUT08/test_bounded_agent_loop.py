from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from cardine.demo.ui_application import RepositoryUiApplication
from study_agent.domain._validation import JsonObject
from tests.integration.demo.TUT08.test_repository_backed_chat import (
    COURSE,
    SESSION,
    _command,
    _repository,
)


def test_tool_result_is_observed_before_luna_answers_the_same_turn(tmp_path: Path) -> None:
    root, adapters, model = _repository(
        tmp_path,
        decisions=(
            {"kind": "invoke_tool", "tool_name": "context.get", "arguments": {}},
            {
                "kind": "assistant_message",
                "message": "Il contesto è vuoto: possiamo iniziare dalla prima fonte.",
            },
        ),
    )
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    before = app.get("/api/v1/session")

    receipt = app.post(
        "/api/v1/session/turns",
        _command(
            "agent-observes-tool",
            cast(int, before["high_water_sequence"]),
            "Controlla il contesto e dimmi come procedere",
        ),
    )

    assert receipt["status"] == "assistant_message"
    session = app.get("/api/v1/session")
    timeline = cast(tuple[JsonObject, ...], session["timeline"])
    assert timeline[-1]["content"] == (
        "Il contesto è vuoto: possiamo iniziare dalla prima fonte."
    )
    decision_requests = tuple(
        request
        for request in model.requests
        if request.metadata.get("prompt_id") == "tutor_decision.v1"
    )
    assert len(decision_requests) == 2
    observed_context = json.loads(decision_requests[-1].messages[-1].content)
    observations = observed_context["tutor_snapshot"]["agent_observations"]
    assert len(observations) == 1
    assert len(observations[0]["action_fingerprint"]) == 64
    assert observations[0] | {"action_fingerprint": "<opaque>"} == {
        "action_fingerprint": "<opaque>",
        "result": {
            "conflict_count": 0,
            "sequence": observed_context["tutor_snapshot_sequence"],
            "statement_count": 0,
        },
        "status": "succeeded",
        "tool_name": "context.get",
    }


def test_exact_duplicate_tool_call_is_skipped_before_luna_recovers(tmp_path: Path) -> None:
    root, adapters, model = _repository(
        tmp_path,
        decisions=(
            {"kind": "invoke_tool", "tool_name": "context.get", "arguments": {}},
            {"kind": "invoke_tool", "tool_name": "context.get", "arguments": {}},
            {
                "kind": "assistant_message",
                "message": "Ho già controllato il contesto una volta: non ripeto la lettura.",
            },
        ),
    )
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    before = app.get("/api/v1/session")

    receipt = app.post(
        "/api/v1/session/turns",
        _command(
            "agent-skips-duplicate",
            cast(int, before["high_water_sequence"]),
            "Controlla il contesto senza ripeterti",
        ),
    )

    assert receipt["status"] == "assistant_message"
    activities = cast(tuple[JsonObject, ...], receipt["activity_records"])
    assert sum(item.get("ref") == "context.get" for item in activities) == 1
    decision_requests = tuple(
        request
        for request in model.requests
        if request.metadata.get("prompt_id") == "tutor_decision.v1"
    )
    assert len(decision_requests) == 3
    final_context = json.loads(decision_requests[-1].messages[-1].content)
    observations = final_context["tutor_snapshot"]["agent_observations"]
    assert [item["status"] for item in observations] == [
        "succeeded",
        "duplicate_skipped",
    ]
    assert observations[-1]["error_code"] == "duplicate_action"
