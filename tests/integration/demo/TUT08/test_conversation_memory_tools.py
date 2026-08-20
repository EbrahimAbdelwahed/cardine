from __future__ import annotations

import json
import sqlite3
from typing import cast

from cardine.demo.ui_application import RepositoryUiApplication
from study_agent.domain._validation import JsonObject
from tests.integration.demo.TUT08.test_flashcard_proposals import (
    _install_hybrid_flashcard_model,
)
from tests.integration.demo.TUT08.test_repository_backed_chat import (
    COURSE,
    SESSION,
    _command,
    _repository,
)


def test_long_chat_can_search_memory_before_source_grounded_flashcards(tmp_path) -> None:
    """A long chat is recoverable without treating its prose as source evidence."""
    decisions: tuple[JsonObject, ...] = (
        *(
            {
                "kind": "assistant_message",
                "message": f"Discussione precedente {index}",
            }
            for index in range(13)
        ),
        {
            "kind": "invoke_tool",
            "tool_name": "conversation.search",
            "arguments": {"query": "pompa sodio potassio", "limit": 8},
        },
        {
            "kind": "start_capability",
            "capability_id": "propose_flashcards",
            "inputs": {
                "query": "MEMORY-SHORT-SENTINEL frase copiata",
                "scope": "MEMORY-SHORT-SENTINEL frase copiata",
                "language": "it",
                "candidate_ceiling": 3,
                "continuation_summary_json": json.dumps(
                    {
                        "conversation_topic": "pompa sodio potassio",
                        "messages_consulted": 8,
                        "verbatim_excerpt": "MEMORY-EXCERPT-ONLY",
                    },
                    sort_keys=True,
                ),
            },
        },
    )
    root, adapters, model = _repository(
        tmp_path,
        decisions=decisions,
        source_content=(
            b"# Pompa sodio-potassio\n\n"
            b"La pompa sodio-potassio trasporta tre ioni sodio fuori e due ioni potassio dentro."
        ),
    )
    flashcard_requests = _install_hybrid_flashcard_model(model)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)

    for index in range(13):
        before = app.get("/api/v1/session")
        app.post(
            "/api/v1/session/turns",
            _command(
                f"history-turn-{index}",
                cast(int, before["high_water_sequence"]),
                (
                    "Abbiamo discusso la pompa sodio-potassio, "
                    f"passaggio {index}. MEMORY-EXCERPT-ONLY"
                    if index == 2
                    else f"Abbiamo discusso la pompa sodio-potassio, passaggio {index}."
                ),
            ),
        )

    before = app.get("/api/v1/session")
    receipt = app.post(
        "/api/v1/session/turns",
        _command(
            "memory-flashcards",
            cast(int, before["high_water_sequence"]),
            "Crea flashcard su quello che abbiamo discusso della pompa sodio-potassio.",
        ),
    )

    assert receipt["status"] == "completed", receipt
    assert cast(dict[str, object], receipt["activity"])["kind"] == "flashcard_generation"
    assert len(flashcard_requests) == 1
    prompt = "\n".join(message.content for message in flashcard_requests[0].messages)
    assert "La pompa sodio-potassio trasporta tre ioni sodio" in prompt

    decision_requests = tuple(
        request
        for request in model.requests
        if request.metadata.get("prompt_id") == "tutor_decision.v1"
    )
    assert len(decision_requests) == 15
    observed = json.loads(decision_requests[-1].messages[-1].content)
    observations = observed["tutor_snapshot"]["agent_observations"]
    memory_search = next(
        item for item in observations if item["tool_name"] == "conversation.search"
    )
    assert memory_search["status"] == "succeeded"
    trajectory = cast(
        tuple[dict[str, object], ...], app.turn_traces.snapshot()["turn_traces"]
    )[-1]
    assert trajectory["steps"] == (
        {"kind": "invoke_tool", "tool_name": "conversation.read"},
        {"kind": "invoke_tool", "tool_name": "conversation.search"},
        {"kind": "start_capability", "capability_id": "propose_flashcards"},
    )

    with sqlite3.connect(root / "state" / "runs.sqlite3") as connection:
        durable_handoffs = tuple(
            bytes(row[0])
            for row in connection.execute(
                "SELECT payload FROM playbook_runs "
                "WHERE run_id LIKE 'tutor-completion-handoff-sha256:%'"
            ).fetchall()
        )
    assert durable_handoffs
    assert all(b"MEMORY-EXCERPT-ONLY" not in payload for payload in durable_handoffs)
    assert all(b"MEMORY-SHORT-SENTINEL" not in payload for payload in durable_handoffs)
