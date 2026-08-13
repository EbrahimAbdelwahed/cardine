"""Repository/UI seam regression for sanitized pinned retrieval activity."""

from __future__ import annotations

from typing import cast

import pytest

from cardine.cli.repository import LocalRepository
from cardine.demo.ui_application import RepositoryUiApplication, UiRequestError
from study_agent.ports import ModelError, ModelErrorCode
from tests.integration.demo.TUT08.test_repository_backed_chat import (
    _command,
    _repository,
    _workspace_command,
)


def test_pinned_turn_receipt_exposes_only_sanitized_retrieval_activity(tmp_path) -> None:
    root, adapters, _model = _repository(
        tmp_path,
        (
            {
                "kind": "start_capability",
                "capability_id": "explain_concept",
                "inputs": {
                    "query": "avvia la spiegazione",
                    "target": "avvia la spiegazione",
                    "language": "it",
                    "learner_goal": None,
                    "continuation_summary_json": None,
                },
            },
        ),
        source_content=(
            b"# Lezione 4\n\n" + (b"Emostasi e coagulazione con evidenza canonica. " * 80) + b"\n\n"
            b"# Lezione 5\n\nContenuto estraneo."
        ),
    )
    with LocalRepository.open(root, model_adapters=adapters) as repository:
        repository.queue_indexing()
        repository.reconcile_indexing()
    app = RepositoryUiApplication(
        root, "cardine-course", "cardine-session", model_adapters=adapters
    )
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    search = app.post(
        "/api/v1/lessons/search",
        _workspace_command("activity-lesson-search", sequence, {"query": "Lezione 4"}),
    )
    candidate = cast(tuple[dict[str, object], ...], search["candidates"])[0]
    selected = app.post(
        "/api/v1/lessons/select",
        _workspace_command(
            "activity-lesson-select",
            sequence,
            {"query": "Lezione 4", "candidate_id": candidate["candidate_id"]},
        ),
    )
    command = _command("activity-pinned-turn", sequence, "Avvia la spiegazione")
    cast(dict[str, object], command["payload"])["lesson_pin"] = selected["pin"]

    receipt = app.post("/api/v1/session/turns", command)
    assert receipt["status"] == "completed", (receipt, len(_model.requests))
    records = cast(tuple[dict[str, object], ...], receipt["activity_records"])

    retrieval = [record for record in records if record["kind"] == "retrieval"]
    assert len(retrieval) == 1
    assert retrieval[0]["target"] == "Lezione 4"
    assert retrieval[0]["status"] in {"done", "failed"}
    serialized = repr(records)
    for forbidden in ("avvia la spiegazione", "query", "source_id", "revision_id", "chunk_id"):
        assert forbidden not in serialized


def test_dialogue_turn_receipt_exposes_truthful_model_activity(tmp_path) -> None:
    """Ordinary tutor dialogue must not leave the activity area permanently empty."""

    root, adapters, _model = _repository(
        tmp_path,
        (
            {
                "kind": "assistant_message",
                "message": "Continuiamo dalla tua risposta.",
            },
        ),
    )
    app = RepositoryUiApplication(
        root, "cardine-course", "cardine-session", model_adapters=adapters
    )
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])

    receipt = app.post(
        "/api/v1/session/turns",
        _command("activity-dialogue-turn", sequence, "Abbiamo terminato il nucleo della lezione?"),
    )

    records = cast(tuple[dict[str, object], ...], receipt["activity_records"])
    assert len(records) == 1
    assert records[0]["kind"] == "model"
    assert records[0]["ref"] == "model.assistant_message"
    assert records[0]["label"] == "Elaboro la risposta"
    assert records[0]["target"] == ""
    assert records[0]["status"] == "done"


def test_learner_question_receipt_exposes_truthful_model_activity(tmp_path) -> None:
    root, adapters, _model = _repository(
        tmp_path,
        (
            {
                "kind": "ask_learner",
                "question": "Quale modificazione post-traduzionale ricordi?",
            },
        ),
    )
    app = RepositoryUiApplication(
        root, "cardine-course", "cardine-session", model_adapters=adapters
    )
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])

    receipt = app.post(
        "/api/v1/session/turns",
        _command("activity-question-turn", sequence, "Continua con una domanda."),
    )

    records = cast(tuple[dict[str, object], ...], receipt["activity_records"])
    assert len(records) == 1
    assert records[0]["kind"] == "model"
    assert records[0]["ref"] == "model.ask_learner"
    assert records[0]["label"] == "Preparo una domanda"
    assert records[0]["target"] == ""
    assert records[0]["status"] == "done"


def test_failed_capability_settles_a_verification_activity(tmp_path) -> None:
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
        explain_error=ModelError(ModelErrorCode.TIMEOUT, "fixture timeout"),
    )
    with LocalRepository.open(root, model_adapters=adapters) as repository:
        repository.queue_indexing()
        repository.reconcile_indexing()
    app = RepositoryUiApplication(
        root, "cardine-course", "cardine-session", model_adapters=adapters
    )
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])

    with pytest.raises(UiRequestError):
        app.post(
            "/api/v1/session/turns",
            _command("activity-failed-turn", sequence, "Explain the aortic valve"),
        )

    records = cast(
        tuple[dict[str, object], ...],
        app.get("/api/v1/turns/activity-failed-turn/activity")["records"],
    )
    verification = [item for item in records if item["kind"] == "verification"]
    assert len(verification) == 1
    assert verification[0]["status"] == "failed"
    assert verification[0]["error_code"] == "capability_failed"
