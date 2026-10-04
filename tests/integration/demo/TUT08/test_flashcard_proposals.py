from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from types import MethodType
from typing import TYPE_CHECKING, Protocol, cast

import pytest

if TYPE_CHECKING:
    from tests.integration.demo.TUT08.test_repository_backed_chat import _command, _repository
else:
    try:
        from tests.integration.demo.TUT08.test_repository_backed_chat import _command, _repository
    except ModuleNotFoundError:
        from test_repository_backed_chat import _command, _repository

from cardine.adapters.model.openai_luna import OpenAIGpt56LunaConfig, OpenAIGpt56LunaModel
from cardine.cli.repository import LocalRepository
from cardine.demo.ui_application import RepositoryUiApplication
from study_agent.adapters.model.openai_compatible import HttpResponse
from study_agent.capabilities import FailedCapabilityOutcome
from study_agent.domain import CorrelationId, CourseId, ExecutionContext, PrincipalKind, SessionId
from study_agent.domain._validation import JsonObject
from study_agent.ports import (
    ModelError,
    ModelErrorCode,
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
        topic_keys = tuple(str(item) for item in active_topics)
        evidence_ids = tuple(
            str(cast(tuple[object, ...] | list[object], item["evidence_handles"])[0])
            for item in index
        )
        return cast(JsonObject, {
            "topic_plan": tuple(
                {
                    "topic_key": key,
                    "disposition": "generate",
                    "candidate_keys": (f"candidate-{position + 1}",),
                    "omission_reason": None,
                }
                for position, key in enumerate(topic_keys)
            ),
            "candidates": tuple(
                {
                    "candidate_key": f"candidate-{position + 1}",
                    "parent_candidate_key": None,
                    "retrieval_form": "direct_recall",
                    "prompt": f"Quante cuspidi ha la valvola aortica? (topic {position + 1})",
                    "answer_blocks": (
                        {
                            "label": "Risposta",
                            "text": f"Tre cuspidi nel topic {position + 1}.",
                            "key_points": (),
                        },
                    ),
                    "pedagogical_role": "section",
                    "morphology_family": None,
                    "cognitive_function": None,
                    "rationale": "La fonte dichiara il numero di cuspidi.",
                    "evidence_ids": (evidence_ids[position],),
                    "media_evidence_ids": (),
                }
                for position in range(len(topic_keys))
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
    activity = cast(dict[str, object], receipt["activity"])
    assert activity["kind"] == "flashcard_generation"
    assert activity["status"] == "completed"
    assert activity["request_id"] == "create-flashcards"
    assert activity["proposal_count"] == 1
    assert activity["destination"] == "proposte"
    artifacts = app.get("/api/v1/artifacts")
    items = cast(tuple[dict[str, object], ...], artifacts["items"])
    assert len(items) == 1
    assert activity["proposal_revision_ids"] == (items[0]["revision_id"],)
    assert items[0]["status"] == "proposed"
    assert artifacts["message"] == (
        "Generated artifacts remain proposals until an explicit decision."
    )
    assert len(flashcard_requests) == 1
    assert flashcard_requests[-1].metadata["prompt_id"] == "hybrid_flashcards.v1"


@pytest.mark.parametrize("request_text", (
    "Crea una flashcard sulla aortic valve",
    "Fammi una flashcard sulla aortic valve per favore",
    "Make a flashcard about aortic valve please",
    "Create a flashcard about aortic valve " + "valve " * 150,
))
def test_topic_flashcard_does_not_plan_the_entire_large_course(
    tmp_path: Path, request_text: str,
) -> None:
    source = (
        "\n".join(f"# Unrelated {i}\nUnrelated background {i}." for i in range(300))
        + "\n# Aortic valve\nThe aortic valve has three cusps."
    )
    root, adapters, model = _repository(tmp_path, source_content=source.encode())
    calls = _install_hybrid_flashcard_model(model)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/session")["high_water_sequence"])
    result = app.post(
        "/api/v1/session/turns",
        _command("topic-large-course", sequence, request_text),
    )
    assert result["status"] == "completed", result["status"]
    assert len(calls) == 1
    prompt = "\n".join(message.content for message in calls[0].messages)
    assert "Unrelated background" not in prompt
    assert "aortic valve has three cusps" in prompt


@pytest.mark.parametrize("request_text,long_heading", (
    ("genera una flashcard su questo", False),
    ("Create a flashcard about this", False),
    ("genera una flashcard su questo", True),
    ("Create a flashcard about this", True),
    ("Make a flashcard about this please", False),
    ("Fammi una flashcard su questo per favore", False),
))
def test_flashcard_about_this_uses_latest_explanation_sources(
    tmp_path: Path, request_text: str, long_heading: bool,
) -> None:
    heading = "Aortic valve" + (" long section" * 200 if long_heading else "")
    root, adapters, model = _repository(tmp_path, source_content=(
        "# Old topic\nOldmarker facts about the old topic.\n"
        f"# {heading}\nThe aortic valve has three cusps.\n"
    ).encode())
    calls = _install_hybrid_flashcard_model(model)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/session")["high_water_sequence"])
    for position, content in enumerate(("Spiegami oldmarker", "Spiegami cusps")):
        receipt = app.post("/api/v1/session/turns", _command(
            f"explanation-{position}", sequence, content
        ))
        assert receipt["status"] == "completed"
        sequence = cast(int, receipt["high_water_sequence"])
    receipt = app.post("/api/v1/session/turns", _command(
        "flashcard-latest-explanation", sequence, request_text
    ))
    assert receipt["status"] == "completed", receipt["status"]
    assert len(calls) == 1
    prompt = "\n".join(message.content for message in calls[0].messages)
    assert "aortic valve has three cusps" in prompt
    assert "Oldmarker" not in prompt


def test_retired_chunks_cannot_hide_active_flashcard_topic(tmp_path: Path) -> None:
    from study_agent.domain import SourceId
    from study_agent.ports.retrieval import RetrievalQuery

    source = "\n".join(
        f"# Retired {i}\nAortic valve. Aortic valve. Retiredmarker {i}." for i in range(10)
    )
    root, adapters, model = _repository(tmp_path, source_content=source.encode())
    calls = _install_hybrid_flashcard_model(model)
    with LocalRepository.open(root, model_adapters=adapters) as repository:
        context = ExecutionContext(
            PrincipalKind.HUMAN, "fixture-active", COURSE, CorrelationId("fixture-active")
        )
        repository.for_course(COURSE).ingestion.ingest(
            filename="active.md", content=b"The aortic valve has three cusps. Activemarker.",
            source_id=SourceId("active"), title="Current lesson", trust_level=90,
            source_role="primary", context=context,
        )
        course = repository.for_course(COURSE)
        repository.reconcile_indexing()
        baseline = course.retrieval.search(RetrievalQuery(COURSE, "aortic valve", limit=8))
        assert len(baseline.evidence) == 8
        assert all(item.chunk.source_id == SourceId("valves") for item in baseline.evidence)
        repository.source_lifetime_service.retire(
            context, SourceId("valves"), "retire-old-topic",
            expected_sequence=repository.events.projection(COURSE).sequence,
        )
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/session")["high_water_sequence"])
    receipt = app.post("/api/v1/session/turns", _command(
        "active-topic", sequence, "Create a flashcard about aortic valve"
    ))
    assert receipt["status"] == "completed", receipt["status"]
    assert len(calls) == 1
    prompt = "\n".join(message.content for message in calls[0].messages)
    assert "Activemarker" in prompt and "Retiredmarker" not in prompt


def test_topic_with_no_evidence_cannot_fall_back_to_the_old_course_topic(tmp_path: Path) -> None:
    root, adapters, model = _repository(tmp_path)
    calls = _install_hybrid_flashcard_model(model)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/session")["high_water_sequence"])
    receipt = app.post("/api/v1/session/turns", _command(
        "unknown-topic", sequence, "genera una flashcard su absentmarker"
    ))
    assert receipt["status"] == "failed"
    assert not calls
    assert not app.get("/api/v1/artifacts")["items"]


def test_repository_chat_recovers_live_flashcard_promise_into_lesson_one_proposals(
    tmp_path: Path,
) -> None:
    promise = "Ok, allora preparo le flashcard richieste."
    source = (
        b"# Lezione 1\nLa glicolisi converte il glucosio in piruvato.\n"
        b"# Lezione 2\nIl ciclo di Krebs produce equivalenti riducenti."
    )
    root, adapters, model = _repository(
        tmp_path,
        decisions=(
            {"kind": "assistant_message", "message": promise},
        ),
        source_content=source,
    )
    # Keep the fixture's provider output identical to the live failure: the
    # host router, rather than the model, must select the capability.
    flashcard_requests = _install_hybrid_flashcard_model(model)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])

    receipt = app.post(
        "/api/v1/session/turns",
        _command(
            "live-event-183",
            sequence,
            "voglio che generi 15 flashcards sulla lezione 1 di biochimica unificato",
        ),
    )

    assert receipt["status"] == "completed", receipt
    activity = cast(dict[str, object], receipt["activity"])
    assert activity["kind"] == "flashcard_generation"
    assert activity["status"] == "completed"
    assert cast(int, activity["proposal_count"]) >= 1
    assert len(flashcard_requests) == 1
    prompt = "\n".join(message.content for message in flashcard_requests[0].messages)
    assert "Lezione 1" in prompt
    assert "Lezione 2" not in prompt

    artifacts = cast(tuple[dict[str, object], ...], app.get("/api/v1/artifacts")["items"])
    assert artifacts
    assert all(item["status"] == "proposed" for item in artifacts)
    timeline = cast(tuple[dict[str, object], ...], app.get("/api/v1/session")["timeline"])
    assert not any(item.get("content") == promise for item in timeline)


def test_repository_chat_flashcards_respect_the_attached_lesson_scope(tmp_path: Path) -> None:
    source = "\n".join(
        f"# Lezione {position}\nLa valvola della lezione {position} ha tre cuspidi."
        for position in range(1, 261)
    ).encode()
    root, adapters, model = _repository(tmp_path, source_content=source)
    flashcard_requests = _install_hybrid_flashcard_model(model)
    with LocalRepository.open(root, model_adapters=adapters) as repository:
        result = repository.search_lessons(COURSE, "Lezione 1")
        candidate = next(
            item for item in result.candidates if item.section_title == "Lezione 1"
        )
        pin = repository.select_lesson(COURSE, "Lezione 1", candidate.candidate_id)

    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    command = _command(
        "create-pinned-flashcard",
        sequence,
        "Crea una flashcard sulla lezione allegata",
    )
    command["payload"] = {
        "content": "Crea una flashcard sulla lezione allegata",
        "lesson_pin": {
            "course_id": pin.course_id,
            "source_id": pin.source_id,
            "revision_id": pin.revision_id,
            "section_title": pin.section_title,
            "start_offset": pin.start_offset,
            "end_offset": pin.end_offset,
            "content_sha256": pin.content_sha256,
            "catalog_fingerprint": pin.catalog_fingerprint,
        },
    }

    receipt = app.post("/api/v1/session/turns", command)

    assert receipt["status"] == "completed", receipt
    activity = cast(dict[str, object], receipt["activity"])
    assert activity["kind"] == "flashcard_generation"
    assert cast(int, activity["proposal_count"]) >= 1
    assert len(flashcard_requests) == 1
    prompt = "\n".join(message.content for message in flashcard_requests[0].messages)
    assert "Lezione 1" in prompt
    assert "Lezione 2" not in prompt


def test_repository_chat_flashcards_reuse_the_recent_resolved_lesson_scope(
    tmp_path: Path,
) -> None:
    source = "\n".join(
        f"# Lezione {position}\nLa valvola della lezione {position} ha tre cuspidi."
        for position in range(1, 261)
    ).encode()
    root, adapters, model = _repository(
        tmp_path,
        decisions=(
            {"kind": "assistant_message", "message": "Leggo la lezione."},
            {
                "kind": "start_capability",
                "capability_id": "propose_flashcards",
                "inputs": {
                    "query": "questa lezione",
                    "scope": "questa lezione",
                    "language": "it",
                    "candidate_ceiling": 15,
                    "continuation_summary_json": None,
                },
            },
        ),
        source_content=source,
    )
    flashcard_requests = _install_hybrid_flashcard_model(model)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)

    before = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    studied = app.post(
        "/api/v1/session/turns",
        _command("study-lesson-one", before, "Leggi la lezione 1"),
    )
    assert studied["status"] == "completed", studied

    receipt = app.post(
        "/api/v1/session/turns",
        _command(
            "create-recent-lesson-flashcards",
            cast(int, studied["high_water_sequence"]),
            "Genera 15 flashcard su questa lezione",
        ),
    )

    assert receipt["status"] == "completed", receipt
    activity = cast(dict[str, object], receipt["activity"])
    assert cast(int, activity["proposal_count"]) >= 1
    assert len(flashcard_requests) == 1
    prompt = "\n".join(message.content for message in flashcard_requests[0].messages)
    assert "Lezione 1" in prompt
    assert "Lezione 2" not in prompt


def test_direct_selected_lesson_flashcards_create_human_interaction_before_generation(
    tmp_path: Path,
) -> None:
    root, adapters, model = _repository(
        tmp_path,
        source_content=b"# Lezione 1\nLa valvola aortica ha tre cuspidi.\n# Lezione 2\nAltro.",
    )
    flashcard_requests = _install_hybrid_flashcard_model(model)
    with LocalRepository.open(root, model_adapters=adapters) as repository:
        result = repository.search_lessons(COURSE, "Lezione 1")
        pin = repository.select_lesson(COURSE, "Lezione 1", result.candidates[0].candidate_id)
        source_record = repository.validate_lesson_pin(pin)
        assert source_record.chunks, (pin, source_record)
        context = ExecutionContext(
            PrincipalKind.HUMAN,
            "direct-selected-lesson",
            COURSE,
            CorrelationId("direct-selected-lesson"),
            session_id=SESSION,
            idempotency_key="direct-selected-lesson",
        )
        receipt = asyncio.run(
            repository.propose_flashcards_for_pin(
                COURSE, SESSION, pin, "Crea flashcard da queste fonti", context
            )
        )
        assert receipt.run_id
        human = tuple(
            item
            for item in repository.sessions.interactions(COURSE, SESSION)
            if item.kind.value == "human" and item.content == "Crea flashcard da queste fonti"
        )
        assert len(human) == 1
        assert len(flashcard_requests) == 1
        repository.session_turn_service.record_learner_turn(
            "Una domanda intermedia",
            replace(
                context,
                correlation_id=CorrelationId("interleaved-human-turn"),
                idempotency_key="interleaved-human-turn",
            ),
            repository.events.projection(COURSE).sequence,
        )
        retried = asyncio.run(
            repository.propose_flashcards_for_pin(
                COURSE, SESSION, pin, "Crea flashcard da queste fonti", context
            )
        )
        assert retried == receipt
        assert len(flashcard_requests) == 1


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


def test_repository_flashcard_provider_failure_is_not_missing_evidence(tmp_path: Path) -> None:
    root, adapters, model = _repository(tmp_path)
    typed_model = cast(_FixtureModel, model)
    original = typed_model.generate
    attempts: list[ModelRequest] = []

    async def generate(self: _FixtureModel, request: ModelRequest) -> ModelResponse:
        if request.metadata.get("prompt_id") == "hybrid_flashcards.v1":
            attempts.append(request)
            raise ModelError(ModelErrorCode.PROTOCOL_ERROR, "private-provider-error")
        return await original(request)

    object.__setattr__(model, "generate", MethodType(generate, typed_model))
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    receipt = app.post(
        "/api/v1/session/turns",
        _command("failed-flashcards", sequence, "Crea flashcard da queste fonti"),
    )
    assert attempts
    assert receipt["status"] == "failed"
    assert "evidenze sufficienti" not in json.dumps(receipt, ensure_ascii=False)
    assert "private-provider-error" not in json.dumps(receipt)


def test_repository_new_flashcard_turn_retries_after_provider_failure(tmp_path: Path) -> None:
    root, adapters, model = _repository(tmp_path)
    typed_model = cast(_FixtureModel, model)
    original = typed_model.generate

    async def generate(self: _FixtureModel, request: ModelRequest) -> ModelResponse:
        if request.metadata.get("prompt_id") == "hybrid_flashcards.v1":
            raise ModelError(ModelErrorCode.PROTOCOL_ERROR, "private-provider-error")
        return await original(request)

    object.__setattr__(model, "generate", MethodType(generate, typed_model))
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    app.post(
        "/api/v1/session/turns",
        _command("failed-flashcards", sequence, "Crea flashcard da queste fonti"),
    )
    requests = _install_hybrid_flashcard_model(model)
    # Replaying the original request remains idempotent after a restart.
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    app.post(
        "/api/v1/session/turns",
        _command("failed-flashcards", sequence, "Crea flashcard da queste fonti"),
    )
    assert requests == []
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    receipt = app.post(
        "/api/v1/session/turns",
        _command("retry-flashcards", sequence, "Crea flashcard da queste fonti"),
    )
    assert requests, receipt
    activity = cast(dict[str, object], receipt["activity"])
    assert activity["proposal_count"] == 1


def test_repository_flashcards_cross_luna_wire_and_publish_verified_proposals(
    tmp_path: Path,
) -> None:
    root, adapters, model = _repository(tmp_path)
    typed_model = cast(_FixtureModel, model)
    original = typed_model.generate
    calls: list[bytes] = []

    class Transport:
        response: bytes = b""

        def post(
            self, url: str, headers: Mapping[str, str], body: bytes, timeout_seconds: float,
        ) -> HttpResponse:
            del url, headers, timeout_seconds
            schema = json.loads(body)["response_format"]["json_schema"]["schema"]

            def check(value: object) -> None:
                if isinstance(value, dict):
                    if value.get("type") == "array":
                        assert "items" in value
                    if value.get("type") == "object":
                        assert value.get("additionalProperties") is False
                        assert set(value["properties"]) == set(value["required"])
                    assert "uniqueItems" not in value
                    for child in value.values():
                        check(child)
                elif isinstance(value, list):
                    for child in value:
                        check(child)

            check(schema)
            calls.append(body)
            return HttpResponse(200, self.response)

    transport = Transport()
    luna = OpenAIGpt56LunaModel(OpenAIGpt56LunaConfig("offline-fixture"), transport=transport)

    async def generate(self: _FixtureModel, request: ModelRequest) -> ModelResponse:
        if request.metadata.get("prompt_id") != "hybrid_flashcards.v1":
            return await original(request)
        transport.response = json.dumps({"choices": [{
            "message": {"content": json.dumps(_flashcard_draft(request))},
            "finish_reason": "stop",
        }]}).encode()
        # The repository fixture pins the outer model as "fixture"; exercise
        # Luna's wire and parsing beneath that registered fixture adapter.
        return replace(
            await luna.generate(request),
            invocation=ModelInvocation("fixture", "1.0.0", "fixture", "fixture-luna-wire"),
        )

    object.__setattr__(model, "generate", MethodType(generate, typed_model))
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    receipt = app.post(
        "/api/v1/session/turns",
        _command("luna-flashcards", sequence, "Crea flashcard da queste fonti"),
    )
    assert calls
    assert receipt["status"] == "completed", receipt
    activity = cast(dict[str, object], receipt["activity"])
    assert activity["proposal_count"] == 1
    items = cast(tuple[dict[str, object], ...], app.get("/api/v1/artifacts")["items"])
    assert items[0]["status"] == "proposed"
