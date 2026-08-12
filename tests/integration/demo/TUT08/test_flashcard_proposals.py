from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import MethodType
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from tests.integration.demo.TUT08.test_repository_backed_chat import _command, _repository
else:
    try:
        from tests.integration.demo.TUT08.test_repository_backed_chat import _command, _repository
    except ModuleNotFoundError:
        from test_repository_backed_chat import _command, _repository

from study_agent.capabilities import FailedCapabilityOutcome
from cardine.cli.repository import LocalRepository
from cardine.demo.ui_application import RepositoryUiApplication
from study_agent.domain import CorrelationId, CourseId, ExecutionContext, PrincipalKind, SessionId
from study_agent.domain._validation import JsonObject
from study_agent.ports import (
    ModelFinishReason,
    ModelInvocation,
    ModelRequest,
    ModelResponse,
)

COURSE = CourseId("cardine-course")
SESSION = SessionId("cardine-session")


class _FixtureModel(Protocol):
    async def generate(self, request: ModelRequest) -> ModelResponse: ...


def _flashcard_draft(request: ModelRequest) -> JsonObject:
    for message in request.messages:
        match = re.search(r"<layer-data>(.+)</layer-data>", message.content, re.DOTALL)
        if match is None:
            continue
        layer = json.loads(match.group(1))
        if not isinstance(layer, dict) or "prepared_scope" not in layer:
            continue
        prepared = cast(dict[str, object], layer["prepared_scope"])
        active_topics = cast(tuple[str, ...] | list[str], prepared["active_topic_keys"])
        prepared_inner = cast(dict[str, object], prepared["prepared_scope"])
        index = cast(
            tuple[dict[str, object], ...] | list[dict[str, object]],
            prepared_inner["index"],
        )
        evidence_handles = cast(tuple[str, ...] | list[str], index[0]["evidence_handles"])
        topic_key = active_topics[0]
        evidence_id = evidence_handles[0]
        return cast(JsonObject, {
            "topic_plan": (
                {
                    "topic_key": topic_key,
                    "disposition": "generate",
                    "candidate_keys": ("candidate-1",),
                    "omission_reason": None,
                },
            ),
            "candidates": (
                {
                    "candidate_key": "candidate-1",
                    "parent_candidate_key": None,
                    "retrieval_form": "direct_recall",
                    "prompt": "Quante cuspidi ha la valvola aortica?",
                    "answer_blocks": (
                        {
                            "label": "Risposta",
                            "text": "Tre cuspidi.",
                            "key_points": (),
                        },
                    ),
                    "pedagogical_role": "section",
                    "morphology_family": None,
                    "cognitive_function": None,
                    "rationale": "La fonte dichiara il numero di cuspidi.",
                    "evidence_ids": (evidence_id,),
                    "media_evidence_ids": (),
                },
            ),
            "omissions": (),
            "detail_bases": (),
        })
    raise AssertionError("fixture did not receive a prepared flashcard scope")


def _morphology_flashcard_draft(request: ModelRequest) -> JsonObject:
    for message in request.messages:
        match = re.search(r"<layer-data>(.+)</layer-data>", message.content, re.DOTALL)
        if match is None:
            continue
        layer = json.loads(match.group(1))
        if not isinstance(layer, dict) or "prepared_scope" not in layer:
            continue
        prepared = cast(dict[str, object], layer["prepared_scope"])
        active_topics = cast(tuple[str, ...] | list[str], prepared["active_topic_keys"])
        prepared_inner = cast(dict[str, object], prepared["prepared_scope"])
        index = cast(
            tuple[dict[str, object], ...] | list[dict[str, object]],
            prepared_inner["index"],
        )
        evidence_handles = cast(tuple[str, ...] | list[str], index[0]["evidence_handles"])
        topic_key = active_topics[0]
        evidence_id = evidence_handles[0]
        return cast(JsonObject, {
            "object_plans": (
                {
                    "topic_keys": (topic_key,),
                    "macro_candidate_key": "candidate-1",
                    "atomic_candidate_keys": (),
                    "reconstruction_dimensions": ("components", "topology"),
                },
            ),
            "candidates": (
                {
                    "candidate_key": "candidate-1",
                    "parent_candidate_key": None,
                    "retrieval_form": "direct_recall",
                    "prompt": "Ricostruisci le cuspidi della valvola aortica.",
                    "answer_blocks": (
                        {
                            "label": "Ricostruzione",
                            "text": (
                                "La valvola aortica presenta tre cuspidi disposte "
                                "attorno all'orifizio."
                            ),
                            "key_points": (),
                        },
                    ),
                    "pedagogical_role": "macro_reconstruction",
                    "morphology_family": "components",
                    "cognitive_function": "reconstruct",
                    "rationale": "La fonte consente di ricostruire i componenti anatomici.",
                    "evidence_ids": (evidence_id,),
                    "media_evidence_ids": (),
                },
            ),
            "omissions": (),
            "topic_omissions": (),
        })
    raise AssertionError("fixture did not receive a prepared morphology scope")


def _install_hybrid_flashcard_model(model: object) -> list[ModelRequest]:
    typed_model = cast(_FixtureModel, model)
    original = typed_model.generate
    requests: list[ModelRequest] = []

    async def generate(self: _FixtureModel, request: ModelRequest) -> ModelResponse:
        if request.metadata.get("prompt_id") == "hybrid_flashcards.v1":
            requests.append(request)
            return ModelResponse(
                "",
                None,
                ModelFinishReason.STOP,
                ModelInvocation("fixture", "1.0.0", "fixture", "fixture-flashcards"),
                structured_output=_flashcard_draft(request),
            )
        return await original(request)

    object.__setattr__(model, "generate", MethodType(generate, typed_model))
    return requests


def _install_morphology_flashcard_model(model: object) -> list[ModelRequest]:
    typed_model = cast(_FixtureModel, model)
    original = typed_model.generate
    requests: list[ModelRequest] = []

    async def generate(self: _FixtureModel, request: ModelRequest) -> ModelResponse:
        if request.metadata.get("prompt_id") == "morphology_flashcards.v1":
            requests.append(request)
            return ModelResponse(
                "",
                None,
                ModelFinishReason.STOP,
                ModelInvocation("fixture", "1.0.0", "fixture", "fixture-morphology-flashcards"),
                structured_output=_morphology_flashcard_draft(request),
            )
        return await original(request)

    object.__setattr__(model, "generate", MethodType(generate, typed_model))
    return requests


def test_repository_chat_publishes_verified_pending_flashcard_proposal(tmp_path: Path) -> None:
    root, adapters, model = _repository(tmp_path)
    flashcard_requests = _install_hybrid_flashcard_model(model)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])

    receipt = app.post(
        "/api/v1/session/turns",
        _command("create-flashcards", sequence, "Crea flashcard da queste fonti"),
    )

    assert receipt["status"] == "completed", receipt
    artifacts = app.get("/api/v1/artifacts")
    items = cast(tuple[dict[str, object], ...], artifacts["items"])
    assert len(items) == 1
    assert items[0]["status"] == "proposed"
    assert artifacts["message"] == (
        "Generated artifacts remain proposals until an explicit decision."
    )
    assert len(flashcard_requests) == 1
    assert flashcard_requests[-1].metadata["prompt_id"] == "hybrid_flashcards.v1"


def test_repository_chat_selects_morphology_first_and_persists_profile_receipt(
    tmp_path: Path,
) -> None:
    root, adapters, model = _repository(tmp_path)
    flashcard_requests = _install_morphology_flashcard_model(model)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])

    receipt = app.post(
        "/api/v1/session/turns",
        _command(
            "create-morphology-flashcards",
            sequence,
            "Crea flashcard anatomiche con ricostruzione spaziale e topologica dalle fonti",
        ),
    )
    assert receipt["status"] == "completed", receipt
    items = cast(tuple[dict[str, object], ...], app.get("/api/v1/artifacts")["items"])
    assert len(items) == 1
    provenance = cast(dict[str, object], items[0]["provenance"])
    selection = cast(dict[str, object], provenance["profile_selection"])
    profile = cast(dict[str, object], selection["profile"])
    assert profile == {"id": "morphology-first-anatomy", "version": 1}
    assert selection["selector_authority"] == "human"
    assert len(flashcard_requests) == 1
    assert flashcard_requests[0].metadata["prompt_id"] == "morphology_flashcards.v1"


def test_malformed_flashcard_inputs_fail_closed_without_unbound_fallback_state(
    tmp_path: Path,
) -> None:
    root, adapters, _model = _repository(tmp_path)
    with LocalRepository.open(root, model_adapters=adapters) as repository:
        repository.tutor_conversation(COURSE, session_id=SESSION)
        composition = repository.flashcard_composition
        assert composition is not None
        outcome = asyncio.run(
            composition.start(
                cast(JsonObject, {"query": "Crea flashcard"}),
                ExecutionContext(
                    PrincipalKind.SERVICE,
                    "fixture-malformed-input",
                    COURSE,
                    CorrelationId("fixture-malformed-input"),
                    session_id=SESSION,
                ),
            )
        )

    assert isinstance(outcome, FailedCapabilityOutcome)
