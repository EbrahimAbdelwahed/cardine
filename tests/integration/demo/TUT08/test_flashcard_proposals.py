from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, replace
from pathlib import Path
from types import MethodType
from typing import TYPE_CHECKING, Protocol, cast

import pytest

if TYPE_CHECKING:
    from tests.integration.demo.TUT08.test_repository_backed_chat import _command, _repository
    from tests.integration.demo.TUT08.test_repository_backed_chat import (
        _FixtureModel as DecisionModel,
    )
else:
    try:
        from tests.integration.demo.TUT08.test_repository_backed_chat import _command, _repository
        from tests.integration.demo.TUT08.test_repository_backed_chat import (
            _FixtureModel as DecisionModel,
        )
    except ModuleNotFoundError:
        from test_repository_backed_chat import _command, _repository
        from test_repository_backed_chat import _FixtureModel as DecisionModel

from cardine.adapters.model.openai_luna import OpenAIGpt56LunaConfig, OpenAIGpt56LunaModel
from cardine.cli.repository import LocalRepository
from cardine.demo.ui_application import RepositoryUiApplication as _RepositoryUiApplication
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


class RepositoryUiApplication(_RepositoryUiApplication):
    """Use this test's explicitly injected offline Jev opener for every UI read/write."""
    def __init__(self, root: Path, course: CourseId, session: SessionId,
                 **kwargs: object) -> None:
        kwargs.setdefault("repository_opener", LocalRepository.open)
        initialize = cast(Callable[..., None], super().__init__)
        initialize(root, course, session, **kwargs)


class _FixtureModel(Protocol):
    async def generate(self, request: ModelRequest) -> ModelResponse: ...


def _fixture_flashcard_scope(text: str, state: JsonObject) -> tuple[str, str]:
    """Deterministic fake Jev decisions; no claim of provider language accuracy."""
    lowered = text.casefold()
    if any(marker in lowered for marker in ("quesot", "su questo", "about this", "on this")):
        return "latest_explanation", "latest explanation"
    if "lezione" in lowered or state.get("selected_lesson_available"):
        references = re.findall(r"lezione\s+(\d+)", lowered)
        if not references:
            recent = state.get("recent_conversation", ())
            for item in reversed(recent if isinstance(recent, (tuple, list)) else ()):
                if isinstance(item, Mapping):
                    references = re.findall(r"lezione\s+(\d+)", str(item.get("content", "")))
                    if references:
                        break
        return "selected_lesson", f"Lezione {references[-1]}" if references else "Valve notes"
    for topic in (
        "oldmarker", "absentmarker", "aortic valve", "cusps", "mitosi", "pompa sodio-potassio"
    ):
        if topic in lowered:
            return "explicit_topic", topic
    return "conversation", "aortic valve"


def _install_semantic_repository_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    from study_agent.domain.features import FeatureMode
    from study_agent.ports.judgement import (
        ChoiceJudgement,
        ChoiceJudgementRequest,
        ChoiceProbability,
    )
    from study_agent.repository_config import JudgementAdapterConfig

    class FixtureJudge:
        async def judge(self, request: ChoiceJudgementRequest) -> ChoiceJudgement:
            state = cast(JsonObject, request.state)
            text = str(state.get("latest_learner_utterance", ""))
            card = "flash" in text.casefold() or "cards" in text.casefold()
            use = request.metadata["use_case"]
            if use == "route":
                selected = "start_capability"
            elif use == "capability":
                selected = "propose_flashcards" if card else "explain_concept"
            elif use == "flashcard_scope":
                selected = _fixture_flashcard_scope(text, state)[0]
            elif use == "flashcard_profile":
                selected = ("morphology" if any(word in text.casefold()
                    for word in ("anatom", "topologic", "ricostru")) else "default")
            else:
                selected = request.options[0].key
            return ChoiceJudgement(selected, tuple(ChoiceProbability(
                item.key, 1.0 if item.key == selected else 0.0) for item in request.options),
                1.0, "offline-fixture", "1", "fake-jev", 0.0)

    original_open = cast(Callable[..., LocalRepository], LocalRepository.open)
    def open_repository(cls: type[LocalRepository], /, root: str | Path,
                        **kwargs: object) -> LocalRepository:
        del cls
        kwargs.setdefault("judgement", FixtureJudge())
        repo = original_open(root, **kwargs)
        repo.config = replace(repo.config, judgement=JudgementAdapterConfig(),
            features=replace(repo.config.features, tutor_routing_mode=FeatureMode.ON,
                             emergency_fallback=False))
        return repo
    monkeypatch.setattr(LocalRepository, "open", classmethod(open_repository))

    original_generate = DecisionModel.generate
    async def generate(self: DecisionModel, request: ModelRequest) -> ModelResponse:
        constraint = request.structured_output
        if constraint is None or constraint.name != "capability_inputs":
            return await original_generate(self, request)
        self._calls.append(request)
        state = cast(JsonObject, json.loads(request.messages[-1].content))
        text = str(state["latest_learner_utterance"])
        props = cast(Mapping[str, JsonObject], constraint.schema["properties"])
        if "scope" in props:
            query = _fixture_flashcard_scope(text, state)[1]
            fixed_query = props["query"].get("enum")
            if isinstance(fixed_query, tuple):
                query = str(fixed_query[0])
            payload: JsonObject = {"query": query,
                "scope": cast(tuple[str, ...], props["scope"]["enum"])[0],
                "language": "it", "candidate_ceiling": 24, "continuation_summary_json": None}
        else:
            query = next((marker for marker in ("oldmarker", "cusps")
                          if marker in text.casefold()), text)
            payload = {"query": query, "target": query, "language": "it",
                       "learner_goal": None, "continuation_summary_json": None}
        return ModelResponse("", None, ModelFinishReason.STOP,
            ModelInvocation("fixture", "1.0.0", "fixture"), structured_output=payload)
    monkeypatch.setattr(DecisionModel, "generate", generate)


@pytest.fixture(autouse=True)
def _semantic_repository_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_semantic_repository_fixture(monkeypatch)


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


@pytest.mark.parametrize("scope,query,request_text", (
    ("latest_explanation", "quesot", "genera una flashcard su quesot"),
    ("explicit_topic", "oldmarker", "genera una flashcard su oldmarker"),
    ("conversation", "cusps", "crea una flashcard sui temi di questa conversazione"),
    ("selected_lesson", "Aortic valve", "crea una flashcard sulla lezione Aortic valve"),
))
@pytest.mark.parametrize("prior_failed_request", (False, True))
@pytest.mark.parametrize("with_pin", (False, True))
def test_primary_jev_flashcard_path_owns_scope_without_downstream_language_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope: str, query: str, request_text: str,
    prior_failed_request: bool, with_pin: bool,
) -> None:
    from study_agent.domain.features import FeatureMode
    from study_agent.ports.judgement import (
        ChoiceJudgement,
        ChoiceJudgementRequest,
        ChoiceProbability,
    )
    from study_agent.repository_config import JudgementAdapterConfig
    from tests.unit.hosts.test_routing import Judge

    root, adapters, model = _repository(tmp_path, source_content=(
        b"# Old topic\nOldmarker facts about the old topic.\n"
        b"# Aortic valve\nThe aortic valve has three cusps.\n"
    ))
    calls = _install_hybrid_flashcard_model(model)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/session")["high_water_sequence"])
    explained = app.post("/api/v1/session/turns", _command("explain", sequence, "Spiegami cusps"))
    assert explained["status"] == "completed"
    sequence = cast(int, explained["high_water_sequence"])
    if prior_failed_request:
        failure = app.post("/api/v1/session/turns", _command(
            "failed-old-card-request", sequence, "Create a flashcard about absentmarker"))
        assert failure["status"] == "failed"
        sequence = cast(int, failure["high_water_sequence"])
        assert calls == []
    pin_json: dict[str, object] | None = None
    if with_pin:
        with LocalRepository.open(root, model_adapters=adapters) as repo:
            found = repo.search_lessons(COURSE, "Aortic valve")
            pin_json = asdict(repo.select_lesson(COURSE, "Aortic valve",
                                               found.candidates[0].candidate_id))
    judge = Judge("start_capability", "propose_flashcards", scope, "default")
    original_judge = judge.judge

    async def judgement(request: ChoiceJudgementRequest) -> ChoiceJudgement:
        if judge.selections:
            return await original_judge(request)
        selected = "stop" if request.metadata["use_case"] == "route" else request.options[0].key
        return ChoiceJudgement(selected, tuple(ChoiceProbability(
            option.key, 1.0 if option.key == selected else 0.0) for option in request.options),
            1.0, "fake", "1", "fake", 0.0)

    monkeypatch.setattr(judge, "judge", judgement)
    original = model.generate

    async def generate(request: ModelRequest) -> ModelResponse:
        constraint = request.structured_output
        if constraint is not None and constraint.name == "capability_inputs":
            props = cast(Mapping[str, JsonObject], constraint.schema["properties"])
            fixed_scope = cast(tuple[str, ...], props["scope"]["enum"])[0]
            fixed_queries = props["query"].get("enum")
            selected_query = fixed_queries[0] if isinstance(fixed_queries, tuple) else query
            return ModelResponse("", None, ModelFinishReason.STOP,
                ModelInvocation("fixture", "1", "fixture"), structured_output={
                    "query": selected_query, "scope": fixed_scope, "language": "it",
                    "candidate_ceiling": 24, "continuation_summary_json": None})
        return await original(request)

    monkeypatch.setattr(model, "generate", generate)

    def open_repository(path: str | Path, **kwargs: object) -> LocalRepository:
        del kwargs
        repo = LocalRepository.open(path, model_adapters=adapters, judgement=judge)
        repo.config = replace(repo.config, judgement=JudgementAdapterConfig(),
                              features=replace(repo.config.features,
            tutor_routing_mode=FeatureMode.ON, emergency_fallback=False))
        return repo

    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters,
                                  repository_opener=open_repository)
    command = _command("semantic-cards", sequence, request_text)
    if pin_json is not None:
        cast(dict[str, object], command["payload"])["lesson_pin"] = pin_json
    result = app.post("/api/v1/session/turns", command)
    scope_state = cast(Mapping[str, object], judge.requests[2].state)
    assert scope_state["selected_lesson_available"] == with_pin
    if with_pin and scope == "explicit_topic":
        assert result["status"] == "failed"
        assert calls == [] and not app.get("/api/v1/artifacts")["items"]
        return
    assert result["status"] == "completed", (json.dumps(result, default=str),
        [(item.metadata, item.state) for item in judge.requests], model.requests)
    assert len(calls) == 1
    prompt = "\n".join(message.content for message in calls[0].messages)
    assert ("Oldmarker" in prompt) == (scope == "explicit_topic")
    assert ("aortic valve has three cusps" in prompt) == (scope != "explicit_topic")
    assert [item.metadata["use_case"] for item in judge.requests][:4] == [
        "route", "capability", "flashcard_scope", "flashcard_profile"]
    assert app.get("/api/v1/artifacts")["items"]


@pytest.mark.parametrize("raw_scope", (None, "free-form scope", "stale", "wrong-turn"))
def test_primary_gateway_rejects_missing_or_stale_semantic_scope(
    tmp_path: Path, raw_scope: str | None,
) -> None:
    from cardine.application.flashcard_scope import FlashcardScope, learner_fingerprint
    from cardine.cli.repository import _RepositoryTutorGateway

    root, adapters, model = _repository(tmp_path)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/session")["high_water_sequence"])
    app.post("/api/v1/session/turns", _command("human", sequence, "Spiegami cusps"))
    with LocalRepository.open(root, model_adapters=adapters) as repo:
        human = repo.sessions.interactions(COURSE, SESSION)[0]
        if raw_scope in {"stale", "wrong-turn"}:
            raw_scope = FlashcardScope("explicit_topic", "default",
                learner_fingerprint("old topic" if raw_scope == "stale" else human.content),
                str(human.id) if raw_scope == "stale" else "previous-turn").encode()
        gateway = _RepositoryTutorGateway(repo, COURSE, SESSION, model,
            repo._model_adapters.artifact("fixture"))
        with pytest.raises(ValueError, match="scope"):
            gateway._flashcard_lesson_pin({"query": "cusps", "scope": raw_scope})
