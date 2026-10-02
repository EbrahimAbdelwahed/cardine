from __future__ import annotations

import cProfile
from pathlib import Path
from typing import cast

from cardine.demo.ui_application import RepositoryUiApplication
from tests.integration.demo.TUT08.test_repository_backed_chat import (
    COURSE,
    SESSION,
    _command,
    _repository,
)


def test_chat_large_source_has_bounded_local_work(tmp_path: Path) -> None:
    """A read must not repeatedly deserialize and validate the entire textbook.

    A deterministic Python-call budget catches the observed CPU amplification
    without relying on CI machine speed or paying for a model response.
    """
    source = "\n\n".join(
        f"# Lesson {index}\n\nThe aortic valve has three cusps. " + "Anatomy. " * 30
        for index in range(400)
    ).encode()
    root, adapters, model = _repository(tmp_path, source_content=source)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    profile = cProfile.Profile()
    with profile:
        response = app.get("/api/v1/session")
    assert response["session_id"] == str(SESSION)
    assert model.requests == ()
    calls = sum(entry.callcount for entry in profile.getstats())
    assert calls < 2_000_000, f"chat read exceeded local work budget: {calls:,} Python calls"


def test_explanation_large_source_has_bounded_local_work(tmp_path: Path) -> None:
    source = "\n\n".join(
        f"# Lesson {index}\n\nThe aortic valve has three cusps. " + "Anatomy. " * 30
        for index in range(400)
    ).encode()
    root, adapters, model = _repository(
        tmp_path,
        source_content=source,
        decisions=(
            {
                "kind": "start_capability",
                "capability_id": "explain_concept",
                "inputs": {
                    "query": "Lesson 2",
                    "target": "Lesson 2",
                    "language": "en",
                    "learner_goal": None,
                    "continuation_summary_json": None,
                },
            },
        ),
    )
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    snapshot = app.get("/api/v1/session")
    profile = cProfile.Profile()
    with profile:
        response = app.post(
            "/api/v1/session/turns",
            _command(
                "latency-explanation",
                cast(int, snapshot["high_water_sequence"]),
                "Explain Lesson 2",
            ),
        )
    assert response["status"] == "completed"
    assert len(model.requests) == 1
    assert "The aortic valve has three cusps." in str(response["result"])
    calls = sum(entry.callcount for entry in profile.getstats())
    assert calls < 4_000_000, f"explanation exceeded local work budget: {calls:,} Python calls"
