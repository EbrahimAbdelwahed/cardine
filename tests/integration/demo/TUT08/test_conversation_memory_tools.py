from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import cast

import pytest

from study_agent.domain._validation import JsonObject
from tests.integration.demo.TUT08.test_flashcard_proposals import (
    RepositoryUiApplication,
    _install_hybrid_flashcard_model,
    _install_semantic_repository_fixture,
)
from tests.integration.demo.TUT08.test_repository_backed_chat import (
    COURSE,
    SESSION,
    _command,
    _repository,
)


def test_long_chat_jev_context_stays_separate_from_flashcard_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A long chat is recoverable without treating its prose as source evidence."""
    decisions: tuple[JsonObject, ...] = (
        *(
            {
                "kind": "assistant_message",
                "message": f"Discussione precedente {index}",
            }
            for index in range(13)
        ),
    )
    root, adapters, model = _repository(
        tmp_path,
        decisions=decisions,
        source_content=(
            b"# Pompa sodio-potassio\n\n"
            b"La pompa sodio-potassio trasporta tre ioni sodio fuori e due ioni potassio dentro."
        ),
    )
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

    _install_semantic_repository_fixture(monkeypatch)
    flashcard_requests = _install_hybrid_flashcard_model(model)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
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

    selected_requests = tuple(request for request in model.requests
                              if request.structured_output is not None
                              and request.structured_output.name == "capability_inputs")
    assert len(selected_requests) == 1
    observed = json.loads(selected_requests[0].messages[-1].content)
    assert observed["recent_conversation"]
    assert "MEMORY-EXCERPT-ONLY" not in prompt

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
