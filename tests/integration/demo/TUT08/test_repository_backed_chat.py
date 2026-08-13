from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread
from typing import cast

import pytest

from cardine.cli.repository import (
    LocalRepository,
    ModelAdapterRegistry,
    initialize_local_repository,
)
from cardine.demo.browser import create_server
from cardine.demo.ui_application import RepositoryUiApplication, UiRequestError
from study_agent.adapters.model import (
    GPT_5_6_LUNA_ADAPTER_ID,
    GPT_5_6_LUNA_ADAPTER_VERSION,
    HttpResponse,
    OpenAIGpt56LunaConfig,
    OpenAIGpt56LunaModel,
)
from study_agent.domain import (
    Citation,
    CorrelationId,
    CourseId,
    CourseProfile,
    ExecutionContext,
    PrincipalKind,
    SessionId,
    SourceId,
)
from study_agent.domain._validation import JsonObject
from study_agent.ports import (
    CancellationToken,
    ModelCapabilities,
    ModelError,
    ModelErrorCode,
    ModelFinishReason,
    ModelInvocation,
    ModelRequest,
    ModelResponse,
    ModelStreamEvent,
    ModelStreamEventKind,
)
from study_agent.repository_config import LocalRepositoryConfig, ModelAdapterConfig

COURSE = CourseId("cardine-course")
SESSION = SessionId("cardine-session")
_EVIDENCE_ID = re.compile(r'"evidence_id":"([^"]+)"')


class _LunaWireTransport:
    """Chat Completions-shaped offline response for the pinned Luna adapter."""

    def __init__(self) -> None:
        self.calls: list[bytes] = []

    def post(
        self,
        _url: str,
        _headers: Mapping[str, str],
        body: bytes,
        _timeout_seconds: float,
    ) -> HttpResponse:
        self.calls.append(body)
        request = json.loads(body)
        schema_name = request["response_format"]["json_schema"]["name"]
        content: JsonObject
        if schema_name == "explain_concept_draft":
            rendered = "\n".join(str(message["content"]) for message in request["messages"])
            evidence_id = _EVIDENCE_ID.search(rendered)
            assert evidence_id is not None
            content = {
                "status": "answered",
                "segments": (
                    {
                        "kind": "supported_claim",
                        "text": "The aortic valve has three cusps.",
                        "evidence_ids": (evidence_id.group(1),),
                    },
                ),
                "unsupported_information_note": None,
            }
        else:
            content = {
                "decision": {
                    "kind": "assistant_message",
                    "message": "Partiamo dal concetto che vuoi chiarire.",
                }
            }
        return HttpResponse(
            200,
            json.dumps(
                {
                    "id": "luna-e2e-decision",
                    "choices": [
                        {
                            "message": {"content": json.dumps(content)},
                            "finish_reason": "stop",
                        }
                    ],
                }
            ).encode(),
        )


class _FixtureModel:
    """Deterministic host decision model over canonical redacted context."""

    capabilities = ModelCapabilities(structured_output=True)

    def __init__(
        self,
        calls: list[ModelRequest],
        decisions: tuple[JsonObject, ...] | None = None,
        *,
        explain_error: ModelError | None = None,
        explain_output: JsonObject | None = None,
    ) -> None:
        self._calls = calls
        self._decisions = decisions
        self._explain_error = explain_error
        self._explain_output = explain_output
        self._decision_calls = 0

    @property
    def requests(self) -> tuple[ModelRequest, ...]:
        return tuple(self._calls)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self._calls.append(request)
        if request.metadata.get("prompt_id") == "explain_concept.v1":
            if self._explain_error is not None:
                raise self._explain_error
            rendered = "\n".join(message.content for message in request.messages)
            match = _EVIDENCE_ID.search(rendered)
            if match is None:
                raise AssertionError("fixture expected canonical evidence")
            explain_output = self._explain_output
            if explain_output == {"fixture": "long-supported-answer"}:
                explain_output = {
                    "status": "answered",
                    "segments": (
                        {
                            "kind": "supported_claim",
                            "text": "A" * 4_001,
                            "evidence_ids": (match.group(1),),
                        },
                    ),
                    "unsupported_information_note": None,
                }
            elif explain_output == {"fixture": "long-cited-lesson-answer"}:
                evidence_ids = tuple(dict.fromkeys(_EVIDENCE_ID.findall(rendered)))
                assert len(evidence_ids) >= 10
                explain_output = {
                    "status": "answered",
                    "segments": tuple(
                        {
                            "kind": "supported_claim",
                            "text": f"Punto didattico {index}: "
                            + ("contenuto verificato " * 9).strip(),
                            "evidence_ids": tuple(
                                dict.fromkeys((evidence_ids[0], evidence_id))
                            ),
                        }
                        for index, evidence_id in enumerate(evidence_ids[:10], start=1)
                    ),
                    "unsupported_information_note": None,
                }
            return ModelResponse(
                "",
                None,
                ModelFinishReason.STOP,
                ModelInvocation("fixture", "1.0.0", "fixture", "fixture-explain"),
                structured_output=explain_output
                or {
                    "status": "answered",
                    "segments": (
                        {
                            "kind": "supported_claim",
                            "text": "The aortic valve has three cusps.",
                            "evidence_ids": (match.group(1),),
                        },
                    ),
                    "unsupported_information_note": None,
                },
            )
        decision = (
            self._decisions[self._decision_calls]
            if self._decisions is not None
            else {
                "kind": "assistant_message",
                "message": "Quale aspetto della valvola aortica vuoi studiare?",
            }
        )
        self._decision_calls += 1
        if decision.get("kind") == "__answer_pending":
            context = json.loads(request.messages[-1].content)
            pending = context["pending_continuation"]
            decision = {
                "kind": "answer_dialogue",
                "continuation_fingerprint": pending["fingerprint"],
                "response": {
                    "provided": True,
                    "text": "La morfologia delle cuspidi",
                },
            }
        return ModelResponse(
            "",
            None,
            ModelFinishReason.STOP,
            ModelInvocation("fixture", "1.0.0", "fixture", "fixture-response"),
            structured_output={"decision": decision},
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        del request
        if False:  # pragma: no cover - makes this an async generator
            yield ModelStreamEvent(ModelStreamEventKind.CANCELLED)
        raise AssertionError("repository tutor decisions do not stream")

    async def cancel(self, token: CancellationToken) -> None:
        del token
        raise AssertionError("fixture cancellation is not supported")


def _repository(
    tmp_path: Path,
    decisions: tuple[JsonObject, ...] | None = None,
    *,
    explain_error: ModelError | None = None,
    explain_output: JsonObject | None = None,
    credential_env: str | None = None,
    source_content: bytes = b"The aortic valve has three cusps.",
) -> tuple[Path, ModelAdapterRegistry, _FixtureModel]:
    root = tmp_path / "repository"
    initialize_local_repository(
        root,
        LocalRepositoryConfig(ModelAdapterConfig("fixture", {}, credential_env)),
    )
    calls: list[ModelRequest] = []
    model = _FixtureModel(
        calls,
        decisions,
        explain_error=explain_error,
        explain_output=explain_output,
    )
    adapters = ModelAdapterRegistry(
        {"fixture": lambda _config, _credential: model}, versions={"fixture": "1.0.0"}
    )
    with LocalRepository.open(root, model_adapters=adapters) as repository:
        repository.course_service.create(
            CourseProfile(COURSE, "Cardine Fixture", "en", learning_goals=("Learn",)),
            ExecutionContext(
                PrincipalKind.SERVICE,
                "fixture-course",
                COURSE,
                CorrelationId("fixture-course-create"),
            ),
        )
        repository.for_course(COURSE).ingestion.ingest(
            filename="valves.md",
            content=source_content,
            source_id=SourceId("valves"),
            title="Valve notes",
            trust_level=90,
            source_role="primary",
            context=ExecutionContext(
                PrincipalKind.SERVICE,
                "fixture-ingest",
                COURSE,
                CorrelationId("fixture-source-ingest"),
            ),
        )
        repository.session_service.start(
            ExecutionContext(
                PrincipalKind.HUMAN,
                "fixture-session",
                COURSE,
                CorrelationId("fixture-session-start"),
                session_id=SESSION,
            )
        )
        repository.provider_consent_service.grant(
            ExecutionContext(
                PrincipalKind.HUMAN,
                "fixture-consent",
                COURSE,
                CorrelationId("fixture-provider-consent"),
            ),
            "fixture-provider-consent",
        )
    return root, adapters, model


def _event_count(root: Path, adapters: ModelAdapterRegistry) -> int:
    with LocalRepository.open(root, model_adapters=adapters) as repository:
        return len(repository.events.read(COURSE))


def _assert_provider_strict_schema(schema: object) -> None:
    if isinstance(schema, Mapping):
        properties = schema.get("properties")
        if isinstance(properties, Mapping):
            assert set(properties).issubset(set(schema.get("required", ())))
        for value in schema.values():
            _assert_provider_strict_schema(value)
    elif isinstance(schema, (tuple, list)):
        for value in schema:
            _assert_provider_strict_schema(value)


def _command(request_id: str, expected_sequence: int, content: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "request_id": request_id,
        "expected_sequence": expected_sequence,
        "payload": {"content": content},
    }


def _continuation_command(
    request_id: str, expected_sequence: int, response: str
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "request_id": request_id,
        "expected_sequence": expected_sequence,
        "payload": {"response": response},
    }


def _workspace_command(
    request_id: str, expected_sequence: int, payload: Mapping[str, object]
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "request_id": request_id,
        "expected_sequence": expected_sequence,
        "payload": dict(payload),
    }


def test_repository_workspace_lists_selects_and_manages_course_sessions(tmp_path: Path) -> None:
    root, adapters, _model = _repository(tmp_path)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)

    workspace = app.get("/api/v1/workspace")
    courses = cast(tuple[dict[str, object], ...], workspace["courses"])
    assert workspace["selected"] == {
        "course_id": str(COURSE),
        "session_id": str(SESSION),
    }
    assert courses[0]["id"] == str(COURSE)
    sessions = cast(tuple[dict[str, object], ...], courses[0]["sessions"])
    assert sessions[0]["id"] == str(SESSION)
    assert sessions[0]["selected"] is True

    created = app.post(
        "/api/v1/workspace/courses",
        _workspace_command(
            "workspace-course-create",
            0,
            {
                "course_id": "second-course",
                "title": "Second Fixture Course",
                "language": "en",
                "learning_goals": ["Practice recall"],
            },
        ),
    )
    assert created["status"] == "created"
    assert cast(dict[str, object], created["course"])["id"] == "second-course"

    started = app.post(
        "/api/v1/workspace/sessions",
        _workspace_command(
            "workspace-session-start",
            0,
            {"course_id": "second-course", "session_id": "second-session"},
        ),
    )
    assert started["status"] == "started"
    assert started["selected"] == {
        "course_id": "second-course",
        "session_id": "second-session",
    }


def test_tutor_invokes_the_same_harness_source_adapter_and_records_timeline(tmp_path: Path) -> None:
    root, adapters, model = _repository(
        tmp_path,
        decisions=(
            {
                "kind": "invoke_tool",
                "tool_name": "course.create",
                "arguments": {
                    "title": "Corso creato dal tutor",
                    "language": "it",
                    "learning_goals": ("Impostare il percorso",),
                },
            },
            {
                "kind": "invoke_tool",
                "tool_name": "session.start",
                "arguments": {"session_id": str(SESSION)},
            },
            {
                "kind": "invoke_tool",
                "tool_name": "source.ingest",
                "arguments": {
                    "filename": "tutor-notes.md",
                    "title": "Tutor notes",
                    "content": "Il ventricolo sinistro genera pressione sistemica.",
                },
            },
            {"kind": "invoke_tool", "tool_name": "evidence.get", "arguments": {}},
            {
                "kind": "assistant_message",
                "message": "Connessione verificata.",
            },
        ),
    )
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    receipts = []
    for request_id, content in (
        ("tutor-tool-course", "Crea il corso"),
        ("tutor-tool-session", "Avvia una sessione"),
        ("tutor-tool-source", "Registra questa fonte"),
        ("tutor-tool-evidence", "Controlla le evidenze"),
    ):
        before = app.get("/api/v1/session")
        receipts.append(
            app.post(
                "/api/v1/session/turns",
                _command(request_id, cast(int, before["high_water_sequence"]), content),
            )
        )
    receipt = receipts[-1]

    assert receipt["status"] == "assistant_message"
    materials = app.get("/api/v1/materials")
    material_items = cast(tuple[dict[str, object], ...], materials["items"])
    assert any(item["title"] == "Tutor notes" for item in material_items)
    session = app.get("/api/v1/session")
    timeline = cast(tuple[dict[str, object], ...], session["timeline"])
    assert any(item["kind"] == "assistant_message" for item in timeline)
    workspace = app.get("/api/v1/workspace")
    courses = cast(tuple[dict[str, object], ...], workspace["courses"])
    assert any(item["title"] == "Corso creato dal tutor" for item in courses)
    course_sessions = cast(tuple[dict[str, object], ...], courses[0]["sessions"])
    assert any(item["id"] == str(SESSION) for item in course_sessions)

    selected = app.post(
        "/api/v1/workspace/select",
        _workspace_command(
            "workspace-select",
            0,
            {"course_id": str(COURSE), "session_id": str(SESSION)},
        ),
    )
    assert selected["status"] == "selected"
    assert app.course_id == COURSE
    assert app.session_id == SESSION

    model_check = app.post(
        "/api/v1/settings/model/check",
        _workspace_command("workspace-model-check", 0, {}),
    )
    assert model_check["status"] == "ok"
    assert model_check["adapter_id"] == "fixture"
    assert model.requests[-1].metadata["prompt_id"] == "tutor_decision.v1"
    assert model.requests[-1].structured_output is not None
    assert model.requests[-1].structured_output.strict is True
    assert model.requests[-1].structured_output.schema["additionalProperties"] is False
    _assert_provider_strict_schema(model.requests[-1].structured_output.schema)


def test_chat_course_creation_creates_starts_selects_and_reconciles_retry(tmp_path: Path) -> None:
    root, adapters, _model = _repository(tmp_path)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    command = _workspace_command(
        "chat-course-create",
        0,
        {
            "confirmed": True,
            "course_id": "physiology",
            "title": "Fisiologia umana",
            "language": "it",
            "learning_goals": ["Collegare funzione e fisiopatologia"],
            "session_id": "physiology-initial",
        },
    )

    receipt = app.post("/api/v1/chat/course-creation", command)

    assert receipt["status"] == "created"
    assert receipt["selected"] == {
        "course_id": "physiology",
        "session_id": "physiology-initial",
    }
    assert cast(dict[str, object], receipt["course"])["title"] == "Fisiologia umana"
    assert cast(dict[str, object], receipt["session"])["status"] == "active"
    assert app.course_id == CourseId("physiology")
    assert app.session_id == SessionId("physiology-initial")

    assert app.post("/api/v1/chat/course-creation", command) == receipt


def test_chat_course_creation_requires_explicit_confirmation(tmp_path: Path) -> None:
    root, adapters, _model = _repository(tmp_path)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)

    with pytest.raises(UiRequestError, match="confirmation"):
        app.post(
            "/api/v1/chat/course-creation",
            _workspace_command(
                "chat-course-unconfirmed",
                0,
                {
                    "confirmed": False,
                    "course_id": "physiology",
                    "title": "Fisiologia umana",
                    "language": "it",
                    "learning_goals": ["Collegare funzione e fisiopatologia"],
                    "session_id": "physiology-initial",
                },
            ),
        )


def test_repository_source_upload_ingests_text_and_reconciles_retry(tmp_path: Path) -> None:
    root, adapters, _model = _repository(tmp_path)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    command = _workspace_command(
        "source-upload-lecture-one",
        _event_count(root, adapters),
        {
            "filename": "lecture-one.md",
            "title": "Lezione uno",
            "content": "# Fisiologia\n\nLa pressione arteriosa dipende dalla gittata.",
        },
    )

    receipt = app.post("/api/v1/sources/upload", command)

    assert receipt["status"] == "emitted"
    assert cast(dict[str, object], receipt["source"])["title"] == "Lezione uno"
    assert cast(int, cast(dict[str, object], receipt["source"])["chunk_count"]) >= 1
    queued = cast(dict[str, object], receipt["indexing"])
    assert queued["status"] == "queued"
    assert queued["phase"] == "queued"
    assert isinstance(queued["target_fingerprint"], str)
    visible = cast(dict[str, object], app.get("/api/v1/indexing/status")["indexing"])
    assert visible["status"] in {"queued", "indexing", "ready", "degraded"}
    repeated = app.post("/api/v1/sources/upload", command)
    assert repeated["status"] == "idempotent"
    assert repeated["high_water_sequence"] == receipt["high_water_sequence"]
    materials = cast(tuple[dict[str, object], ...], app.get("/api/v1/materials")["items"])
    assert {item["title"] for item in materials} == {"Valve notes", "Lezione uno"}


def test_repository_source_upload_rejects_unsupported_files(tmp_path: Path) -> None:
    root, adapters, _model = _repository(tmp_path)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)

    with pytest.raises(UiRequestError, match=r"only \.txt and \.md"):
        app.post(
            "/api/v1/sources/upload",
            _workspace_command(
                "source-upload-pdf",
                _event_count(root, adapters),
                {"filename": "lecture.pdf", "title": "Lezione", "content": "not a PDF"},
            ),
        )


def test_repository_pdf_import_is_canonical_and_restart_safe(tmp_path: Path) -> None:
    from tests.integration.adapters.workarounds.test_pdf_markdown_real import (
        _minimal_text_pdf,
    )

    root, adapters, _model = _repository(tmp_path)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    pdf = _minimal_text_pdf()
    input_path = tmp_path / "lesson.pdf"
    input_path.write_bytes(pdf)

    receipt = app.import_pdf(
        input_path=input_path,
        pdf_sha256=sha256(pdf).hexdigest(),
        byte_size=len(pdf),
        filename="lesson.pdf",
        title="Lezione PDF",
        request_id="pdf-import-one",
    )

    assert receipt["status"] == "emitted"
    assert cast(dict[str, object], receipt["conversion"])["page_count"] == 1
    restarted = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    materials = cast(tuple[dict[str, object], ...], restarted.get("/api/v1/materials")["items"])
    assert "Lezione PDF" in {item["title"] for item in materials}
    with LocalRepository.open(root, model_adapters=adapters) as repository:
        source_id = SourceId("source-pdf-sha256:" + sha256(pdf).hexdigest())
        source = next(
            item
            for item in repository.for_course(COURSE).content.catalog()
            if item.source.source_id == source_id
        )
        assert source.source.content_origin.value == "extracted"
        assert source.source.conversion_provenance is not None
        assert source.source.conversion_provenance.pdf_sha256 == sha256(pdf).hexdigest()
        assert tuple(
            (span.page, span.start_offset, span.end_offset)
            for span in source.source.conversion_provenance.page_spans
        ) == ((1, 0, len(source.text)),)
        chunk = source.chunks[0]
        resolved = repository.for_course(COURSE).content.resolve(
            Citation(
                source_id,
                source.source.revision_id,
                chunk.chunk_id,
                chunk.start_offset,
                chunk.end_offset,
                "client locator ignored",
                source.text[chunk.start_offset : chunk.end_offset],
            )
        )
        assert "page 1" in resolved.citation.locator
        assert repository.blobs.get(source.source.blob) == pdf


class _CountingUiApplication:
    mode = "local_repository"

    def __init__(self, delegate: RepositoryUiApplication) -> None:
        self._delegate = delegate
        self.post_calls = 0

    def get(self, path: str) -> JsonObject:
        return self._delegate.get(path)

    def post(self, path: str, command: Mapping[str, object]) -> JsonObject:
        self.post_calls += 1
        return self._delegate.post(path, command)


def test_repository_chat_is_durable_idempotent_and_stale_safe(tmp_path: Path) -> None:
    root, adapters, model = _repository(tmp_path)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)

    bootstrap = app.get("/api/v1/bootstrap")
    assert bootstrap["mode"] == "local_repository"
    initial_sequence = cast(int, bootstrap["high_water_sequence"])
    materials = app.get("/api/v1/materials")
    material = cast(tuple[dict[str, object], ...], materials["items"])[0]
    assert material["title"] == "Valve notes"
    assert material["source_id"] == "valves"
    assert "filename" not in material
    assert "byte_size" not in material

    command = _command("browser-request-1", initial_sequence, "aortic")
    initial_events = _event_count(root, adapters)
    receipt = app.post("/api/v1/session/turns", command)
    assert receipt["status"] == "assistant_message"
    assert cast(int, receipt["high_water_sequence"]) > initial_sequence
    assert len(model.requests) == 1
    assert _event_count(root, adapters) > initial_events

    decision_before_retry = app.turn_traces.snapshot()
    assert app.post("/api/v1/session/turns", command) == receipt
    assert app.turn_traces.snapshot() == decision_before_retry
    assert len(model.requests) == 1

    fresh = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    session = fresh.get("/api/v1/session")
    timeline = cast(tuple[dict[str, object], ...], session["timeline"])
    assert tuple(item["role"] for item in timeline) == ("learner", "assistant")
    assert timeline[0]["content"] == "aortic"
    assert timeline[1]["role"] == "assistant"
    assert timeline[1]["status"] == "completed"
    assert timeline[1]["content"] == "Quale aspetto della valvola aortica vuoi studiare?"

    calls_before_retry = len(model.requests)
    events_before_retry = _event_count(root, adapters)
    retry = fresh.post("/api/v1/session/turns", command)
    assert retry == receipt
    assert len(model.requests) == calls_before_retry
    assert _event_count(root, adapters) == events_before_retry

    calls_before_stale = len(model.requests)
    events_before_stale = _event_count(root, adapters)
    with pytest.raises(UiRequestError, match="stale") as stale:
        fresh.post(
            "/api/v1/session/turns",
            _command("browser-request-2", initial_sequence, "A new question"),
        )
    assert stale.value.status_code == 409
    assert len(model.requests) == calls_before_stale
    assert _event_count(root, adapters) == events_before_stale

    calls_before_changed = len(model.requests)
    events_before_changed = _event_count(root, adapters)
    with pytest.raises(UiRequestError, match="conflicts") as changed:
        fresh.post(
            "/api/v1/session/turns",
            _command("browser-request-1", initial_sequence, "Changed content"),
        )
    assert changed.value.status_code == 409
    assert len(model.requests) == calls_before_changed
    assert _event_count(root, adapters) == events_before_changed


def test_workspace_selection_failure_keeps_the_last_canonical_pair(tmp_path: Path) -> None:
    root, adapters, _model = _repository(tmp_path)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)

    with pytest.raises(UiRequestError) as rejected:
        app.post(
            "/api/v1/workspace/select",
            _workspace_command(
                "bad-selection",
                0,
                {"course_id": str(COURSE), "session_id": "missing-session"},
            ),
        )

    assert rejected.value.status_code == 404
    assert app.course_id == COURSE
    assert app.session_id == SESSION
    assert app.get("/api/v1/workspace")["selected"] == {
        "course_id": str(COURSE),
        "session_id": str(SESSION),
    }


def test_grounded_completion_is_recovered_and_persisted_as_canonical_presentation(
    tmp_path: Path,
) -> None:
    root, adapters, model = _repository(
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
    )
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    receipt = app.post(
        "/api/v1/session/turns",
        _command("grounded-completion", sequence, "Spiegami la valvola aortica"),
    )
    assert receipt["status"] == "completed"
    session = app.get("/api/v1/session")
    timeline = cast(tuple[dict[str, object], ...], session["timeline"])
    assert timeline[-1]["role"] == "assistant"
    assert "three cusps" in str(timeline[-1]["content"])
    assert "Valve notes" in str(timeline[-1]["content"])
    assert "chars " in str(timeline[-1]["content"])
    assert len(model.requests) == 2

    restarted = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    reloaded = cast(tuple[dict[str, object], ...], restarted.get("/api/v1/session")["timeline"])
    assert reloaded[-1]["content"] == timeline[-1]["content"]


def test_unrenderable_grounded_completion_still_returns_a_visible_chat_message(
    tmp_path: Path,
) -> None:
    root, adapters, model = _repository(
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
        explain_output={"fixture": "long-supported-answer"},
    )
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])

    receipt = app.post(
        "/api/v1/session/turns",
        _command("grounded-fallback", sequence, "Spiegami la valvola aortica"),
    )

    assert receipt["status"] == "completed"
    assert receipt["presentation_id"] is not None
    timeline = cast(tuple[dict[str, object], ...], app.get("/api/v1/session")["timeline"])
    assert timeline[-1]["role"] == "assistant"
    assert "Non sono riuscito" in str(timeline[-1]["content"])
    assert len(model.requests) == 2


def test_attached_long_lesson_publishes_complete_answer_with_compact_sources(
    tmp_path: Path,
) -> None:
    lesson = "# Lezione 1\n\n" + "\n\n".join(
        f"Concetto canonico {index}. " * 30
        for index in range(1, 13)
    ) + "\n\n# Lezione 2\n\nContenuto estraneo.\n"
    root, adapters, _model = _repository(
        tmp_path,
        (
            {
                "kind": "start_capability",
                "capability_id": "explain_concept",
                "inputs": {
                    "query": "avvia una spiegazione",
                    "target": "avvia una spiegazione",
                    "language": "it",
                    "learner_goal": None,
                    "continuation_summary_json": None,
                },
            },
        ),
        explain_output={"fixture": "long-cited-lesson-answer"},
        source_content=lesson.encode(),
    )
    with LocalRepository.open(root, model_adapters=adapters) as repository:
        repository.queue_indexing()
        repository.reconcile_indexing()
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    search = app.post(
        "/api/v1/lessons/search",
        _workspace_command("long-lesson-search", sequence, {"query": "Lezione 1"}),
    )
    candidate = cast(tuple[dict[str, object], ...], search["candidates"])[0]
    selected = app.post(
        "/api/v1/lessons/select",
        _workspace_command(
            "long-lesson-select",
            sequence,
            {"query": "Lezione 1", "candidate_id": candidate["candidate_id"]},
        ),
    )
    command = _command("long-pinned-turn", sequence, "Avvia una spiegazione")
    cast(dict[str, object], command["payload"])["lesson_pin"] = selected["pin"]

    receipt = app.post("/api/v1/session/turns", command)

    assert receipt["status"] == "completed"
    timeline = cast(tuple[dict[str, object], ...], app.get("/api/v1/session")["timeline"])
    answer = str(timeline[-1]["content"])
    expected_body = "\n\n".join(
        f"Punto didattico {index}: " + ("contenuto verificato " * 9).strip()
        for index in range(1, 11)
    )
    body, sources = answer.split("\n\nFonti verificate:", 1)
    assert body == expected_body
    source_lines = tuple(
        line.removeprefix("- ")
        for line in sources.splitlines()
        if line.startswith("- ") and not line.startswith("- Altre ")
    )
    assert source_lines
    assert len(source_lines) == len(set(source_lines))
    assert "Non sono riuscito" not in answer
    assert answer.count("Fonti verificate:") == 1
    assert len(answer) <= 4_000


@pytest.mark.parametrize(
    "decision",
    (
        {"kind": "assistant_message", "message": "ok"},
        {"kind": "ask_learner", "question": "Vuoi che proceda?"},
    ),
)
def test_source_directed_question_cannot_end_without_grounded_content(
    tmp_path: Path, decision: JsonObject
) -> None:
    """An explicit source request is grounded regardless of the model's first decision."""

    root, adapters, model = _repository(
        tmp_path,
        (decision,),
    )
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])

    receipt = app.post(
        "/api/v1/session/turns",
        _command(
            "source-directed-question",
            sequence,
            "Read and explain the source: what does it say about the aortic valve cusps?",
        ),
    )

    decision_context = json.loads(model.requests[0].messages[-1].content)
    assert decision_context["tutor_snapshot"]["timeline"][-1]["kind"] == "learner"
    assert "source" in decision_context["tutor_snapshot"]["timeline"][-1]["content"]
    assert receipt["status"] == "completed"
    timeline = cast(tuple[dict[str, object], ...], app.get("/api/v1/session")["timeline"])
    answer = str(timeline[-1]["content"])
    assert answer not in {"ok", "Vuoi che proceda?"}
    assert "three cusps" in answer
    assert "Valve notes" in answer
    assert "chars " in answer
    assert [request.metadata.get("prompt_id") for request in model.requests] == [
        "tutor_decision.v1",
        "explain_concept.v1",
    ]


def test_read_request_with_course_materials_enters_the_grounded_flow(
    tmp_path: Path,
) -> None:
    """A concise Italian source-first request cannot depend on model guesswork."""

    root, adapters, model = _repository(
        tmp_path,
        ({"kind": "assistant_message", "message": "ok"},),
        source_content=b"The aortic valve has three cusps.",
    )
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])

    receipt = app.post(
        "/api/v1/session/turns",
        _command("read-course-material", sequence, "Leggi Valve notes"),
    )

    assert receipt["status"] == "completed"
    timeline = cast(tuple[dict[str, object], ...], app.get("/api/v1/session")["timeline"])
    assert "three cusps" in str(timeline[-1]["content"])
    assert [request.metadata.get("prompt_id") for request in model.requests] == [
        "tutor_decision.v1",
        "explain_concept.v1",
    ]
    diagnostics = app.turn_traces.snapshot()
    trace = cast(tuple[dict[str, object], ...], diagnostics["turn_traces"])[-1]
    # Diagnostics report the validated model decision, not the host-side
    # source-grounding rewrite that subsequently enforces capability routing.
    assert trace["decision"] == {"kind": "assistant_message"}
    assert {
        "events",
        "final_status",
        "learner_persisted",
        "attempt_count",
        "started_unix",
        "updated_unix",
        "payload",
    }.isdisjoint(trace)
    encoded_trace = json.dumps(trace, sort_keys=True)
    assert "Leggi Valve notes" not in encoded_trace
    assert "The aortic valve has three cusps" not in encoded_trace


def test_invalid_tutor_decision_returns_a_visible_safe_fallback(
    tmp_path: Path,
) -> None:
    root, adapters, _model = _repository(
        tmp_path,
        (
            {
                "kind": "not-a-real-decision",
            },
        ),
    )
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])

    receipt = app.post(
        "/api/v1/session/turns",
        _command("invalid-tutor-decision", sequence, "Leggi biochimica"),
    )

    assert receipt["status"] == "failed"
    assert receipt["presentation_id"] is not None
    timeline = cast(tuple[dict[str, object], ...], app.get("/api/v1/session")["timeline"])
    assert timeline[-1]["role"] == "assistant"
    assert "Non sono riuscito" in str(timeline[-1]["content"])


def test_luna_wire_response_completes_a_repository_backed_chat_turn(
    tmp_path: Path,
) -> None:
    """Exercise UI -> repository -> pinned adapter -> decision parser with no network."""

    root = tmp_path / "luna-repository"
    initialize_local_repository(
        root,
        LocalRepositoryConfig(
            ModelAdapterConfig(
                GPT_5_6_LUNA_ADAPTER_ID,
                {"timeout_seconds": 60},
                "OPENAI_API_KEY",
            )
        ),
    )
    transport = _LunaWireTransport()
    model = OpenAIGpt56LunaModel(OpenAIGpt56LunaConfig("fixture-openai-key"), transport=transport)
    adapters = ModelAdapterRegistry(
        {GPT_5_6_LUNA_ADAPTER_ID: lambda _config, _credential: model},
        versions={GPT_5_6_LUNA_ADAPTER_ID: GPT_5_6_LUNA_ADAPTER_VERSION},
    )
    with LocalRepository.open(root, model_adapters=adapters) as repository:
        repository.course_service.create(
            CourseProfile(COURSE, "Luna Fixture", "it", learning_goals=("Studiare",)),
            ExecutionContext(
                PrincipalKind.SERVICE,
                "luna-course",
                COURSE,
                CorrelationId("luna-course-create"),
            ),
        )
        repository.for_course(COURSE).ingestion.ingest(
            filename="luna-source.md",
            content=b"Biochimica: the aortic valve has three cusps.",
            source_id=SourceId("luna-source"),
            title="Luna source",
            trust_level=90,
            source_role="primary",
            context=ExecutionContext(
                PrincipalKind.SERVICE,
                "luna-source-ingest",
                COURSE,
                CorrelationId("luna-source-ingest"),
            ),
        )
        repository.session_service.start(
            ExecutionContext(
                PrincipalKind.HUMAN,
                "luna-session",
                COURSE,
                CorrelationId("luna-session-start"),
                session_id=SESSION,
            )
        )
        repository.provider_consent_service.grant(
            ExecutionContext(
                PrincipalKind.HUMAN,
                "luna-consent",
                COURSE,
                CorrelationId("luna-provider-consent"),
            ),
            "luna-provider-consent",
        )
    app = RepositoryUiApplication(
        root,
        COURSE,
        SESSION,
        model_adapters=adapters,
        environment={"OPENAI_API_KEY": "fixture-openai-key"},
    )
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])

    receipt = app.post(
        "/api/v1/session/turns",
        _command("luna-wire-turn", sequence, "Leggi biochimica"),
    )

    assert receipt["status"] == "completed"
    sent = json.loads(transport.calls[0])
    assert sent["response_format"]["json_schema"]["strict"] is True
    assert sent["model"] == "gpt-5.6-luna"
    grounded = json.loads(transport.calls[1])
    assert grounded["response_format"]["json_schema"]["name"] == "explain_concept_draft"
    assert grounded["response_format"]["json_schema"]["strict"] is True
    timeline = cast(tuple[dict[str, object], ...], app.get("/api/v1/session")["timeline"])
    assert "three cusps" in str(timeline[-1]["content"])


def test_model_check_reports_the_same_decision_protocol_failure_as_chat(
    tmp_path: Path,
) -> None:
    root, adapters, _model = _repository(
        tmp_path,
        ({"kind": "not-a-real-decision"},),
    )
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)

    result = app.post(
        "/api/v1/settings/model/check",
        _workspace_command("model-check-invalid-decision", 0, {}),
    )

    assert result["status"] == "error"
    assert result["reason"] == "provider_protocol_error"


def test_missing_runtime_key_has_a_configuration_diagnostic_not_a_generic_503(
    tmp_path: Path,
) -> None:
    root, adapters, _model = _repository(
        tmp_path,
        credential_env="CARDINE_TEST_MISSING_RUNTIME_KEY",
    )
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])

    with pytest.raises(UiRequestError) as rejected:
        app.post(
            "/api/v1/session/turns",
            _command("missing-runtime-key", sequence, "Spiegami biochimica"),
        )

    assert rejected.value.status_code == 503
    assert rejected.value.diagnostic_code == "tutor_configuration"


def test_source_grounding_provider_rejection_returns_a_visible_safe_fallback(
    tmp_path: Path,
) -> None:
    root, adapters, _model = _repository(
        tmp_path,
        (
            {
                "kind": "start_capability",
                "capability_id": "explain_concept",
                "inputs": {
                    "query": "aortic",
                    "target": "aortic valve",
                    "language": "it",
                    "learner_goal": None,
                    "continuation_summary_json": None,
                },
            },
            {
                "kind": "start_capability",
                "capability_id": "explain_concept",
                "inputs": {
                    "query": "aortic",
                    "target": "aortic valve",
                    "language": "it",
                    "learner_goal": None,
                    "continuation_summary_json": None,
                },
            },
        ),
        explain_error=ModelError(ModelErrorCode.AUTHENTICATION, "fixture-secret"),
    )
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])

    command = _command(
        "grounding-provider-rejected", sequence, "Leggi e spiega le cuspidi aortiche"
    )
    rejected = app.post("/api/v1/session/turns", command)

    assert rejected["status"] == "failed"
    assert rejected["presentation_id"] is not None
    first_timeline = cast(tuple[dict[str, object], ...], app.get("/api/v1/session")["timeline"])
    assert first_timeline[-1]["role"] == "assistant"
    assert "Non sono riuscito" in str(first_timeline[-1]["content"])
    assert "fixture-secret" not in str(rejected)
    assert "fixture-secret" not in str(first_timeline)

    _model._explain_error = None
    retry_sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    retry = app.post(
        "/api/v1/session/turns",
        _command(
            "grounding-provider-retry",
            retry_sequence,
            "Leggi e spiega le cuspidi aortiche",
        ),
    )
    assert retry["status"] == "completed"
    timeline = cast(tuple[dict[str, object], ...], app.get("/api/v1/session")["timeline"])
    assert tuple(item["role"] for item in timeline) == (
        "learner",
        "assistant",
        "learner",
        "assistant",
    )


def test_source_grounding_schema_rejection_returns_a_visible_safe_fallback(
    tmp_path: Path,
) -> None:
    """A malformed second structured response is not an opaque tutor failure."""

    root, adapters, _model = _repository(
        tmp_path,
        (
            {
                "kind": "start_capability",
                "capability_id": "explain_concept",
                "inputs": {
                    "query": "aortic",
                    "target": "aortic valve",
                    "language": "it",
                    "learner_goal": None,
                    "continuation_summary_json": None,
                },
            },
        ),
        explain_output={"status": "answered", "segments": (), "unexpected": True},
    )
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])

    receipt = app.post(
        "/api/v1/session/turns",
        _command("grounding-schema-rejected", sequence, "Leggi e spiega le cuspidi aortiche"),
    )

    assert receipt["status"] == "failed"
    assert receipt["presentation_id"] is not None
    timeline = cast(tuple[dict[str, object], ...], app.get("/api/v1/session")["timeline"])
    assert timeline[-1]["role"] == "assistant"
    assert "Non sono riuscito" in str(timeline[-1]["content"])


def test_materials_are_not_presented_as_groundable_when_source_text_is_missing(
    tmp_path: Path,
) -> None:
    root, adapters, _model = _repository(tmp_path)
    with LocalRepository.open(root, model_adapters=adapters) as repository:
        record = repository.for_course(COURSE).content.catalog()[0]
        digest = record.source.normalized_blob.checksum_sha256
    blob = root / "blobs" / "objects" / digest[:2] / digest[2:4] / digest
    blob.unlink()

    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)

    materials = app.get("/api/v1/materials")
    assert materials["status"] == "unavailable"
    assert materials["items"] == ()


def test_repository_chat_serializes_new_requests_at_one_sequence(tmp_path: Path) -> None:
    root, adapters, model = _repository(tmp_path)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    initial_sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    initial_events = _event_count(root, adapters)

    def submit(request_id: str) -> tuple[str, object]:
        try:
            return "completed", app.post(
                "/api/v1/session/turns",
                _command(
                    request_id,
                    initial_sequence,
                    "aortic",
                ),
            )
        except UiRequestError as error:
            return "error", error

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(submit, ("concurrent-a", "concurrent-b")))

    completed = tuple(value for kind, value in outcomes if kind == "completed")
    conflicts = tuple(value for kind, value in outcomes if kind == "error")
    assert len(completed) == 1
    assert len(conflicts) == 1
    assert isinstance(conflicts[0], UiRequestError)
    assert conflicts[0].status_code == 409
    assert len(model.requests) == 1
    assert _event_count(root, adapters) > initial_events


def test_second_tutor_decision_receives_redacted_canonical_presentation_history(
    tmp_path: Path,
) -> None:
    root, adapters, model = _repository(tmp_path)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    initial = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    first = app.post(
        "/api/v1/session/turns",
        _command("history-first", initial, "aortic"),
    )
    app.post(
        "/api/v1/session/turns",
        _command(
            "history-second",
            cast(int, first["high_water_sequence"]),
            "cuspidi",
        ),
    )

    second_context = json.loads(model.requests[1].messages[-1].content)
    presentations = second_context["tutor_snapshot"]["tutor_presentations"]
    assert presentations == [
        {
            "kind": "assistant_message",
            "content": "Quale aspetto della valvola aortica vuoi studiare?",
            "course_sequence": cast(int, first["high_water_sequence"]),
            "in_reply_to_interaction_id": presentations[0]["in_reply_to_interaction_id"],
        }
    ]
    assert presentations[0]["in_reply_to_interaction_id"]
    assert "idempotency_key" not in json.dumps(second_context)


def test_repository_continuation_is_restored_resolved_and_exactly_retryable(
    tmp_path: Path,
) -> None:
    root, adapters, model = _repository(
        tmp_path,
        (
            {
                "kind": "start_capability",
                "capability_id": "explain_concept",
                "inputs": {
                    "query": "aortic",
                    "target": None,
                    "language": "en",
                    "learner_goal": None,
                    "continuation_summary_json": None,
                },
            },
            {
                "kind": "__answer_pending",
            },
        ),
    )
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    initial = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])

    suspended = app.post(
        "/api/v1/session/turns",
        _command("clarify-start", initial, "Spiegami la valvola aortica"),
    )

    assert suspended["status"] == "suspended"
    continuation = cast(
        dict[str, object], cast(dict[str, object], suspended["result"])["continuation"]
    )
    fingerprint = cast(str, continuation["fingerprint"])
    assert continuation["prompt"] == "Which concept or aspect should the explanation target?"
    response_schema = cast(dict[str, object], continuation["response_schema"])
    assert isinstance(response_schema, dict)
    assert isinstance(response_schema["required"], list)
    assert isinstance(response_schema["properties"], dict)

    restarted = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    restored = restarted.get("/api/v1/session")
    assert cast(dict[str, object], restored["continuation"])["fingerprint"] == fingerprint

    response_command = _continuation_command(
        "clarify-response",
        cast(int, suspended["high_water_sequence"]),
        "Spiega la morfologia delle cuspidi dalle fonti",
    )
    resumed = restarted.post(
        f"/api/v1/session/continuations/{fingerprint}/responses",
        response_command,
    )

    assert resumed["status"] == "completed"
    assert cast(dict[str, object], resumed["result"])["continuation"] is None
    assert restarted.get("/api/v1/session")["continuation"] is None
    assert len(model.requests) == 3


def test_wrong_continuation_routes_fail_before_provider_or_canonical_write(
    tmp_path: Path,
) -> None:
    root, adapters, model = _repository(tmp_path)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    before_events = _event_count(root, adapters)
    command = _continuation_command("wrong-continuation", sequence, "response")

    with pytest.raises(UiRequestError, match="fingerprint") as malformed:
        app.post("/api/v1/session/continuations/bad/responses", command)
    assert malformed.value.status_code == 400

    with pytest.raises(UiRequestError) as missing:
        app.post(
            f"/api/v1/session/continuations/{'f' * 64}/responses",
            command,
        )
    assert missing.value.status_code == 404
    assert model.requests == ()
    assert _event_count(root, adapters) == before_events


def test_source_change_invalidates_suspended_capability_dependencies(
    tmp_path: Path,
) -> None:
    root, adapters, model = _repository(
        tmp_path,
        (
            {
                "kind": "start_capability",
                "capability_id": "explain_concept",
                "inputs": {
                    "query": "aortic",
                    "target": None,
                    "language": "en",
                    "learner_goal": None,
                    "continuation_summary_json": None,
                },
            },
            {"kind": "__answer_pending"},
            {
                "kind": "assistant_message",
                "message": "Le fonti sono cambiate: riformuliamo la richiesta.",
            },
        ),
    )
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    initial = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    suspended = app.post(
        "/api/v1/session/turns",
        _command("source-stale-start", initial, "Spiegami la valvola aortica"),
    )
    continuation = cast(
        dict[str, object], cast(dict[str, object], suspended["result"])["continuation"]
    )
    fingerprint = cast(str, continuation["fingerprint"])

    with LocalRepository.open(root, model_adapters=adapters) as repository:
        repository.for_course(COURSE).ingestion.ingest(
            filename="new.md",
            content=b"New canonical source revision.",
            source_id=SourceId("new-source"),
            title="New source",
            trust_level=90,
            source_role="primary",
            context=ExecutionContext(
                PrincipalKind.SERVICE,
                "source-update",
                COURSE,
                CorrelationId("source-update"),
            ),
        )
    current = cast(int, app.get("/api/v1/session")["high_water_sequence"])
    result = app.post(
        f"/api/v1/session/continuations/{fingerprint}/responses",
        _continuation_command("source-stale-response", current, "Le cuspidi"),
    )

    assert result["status"] == "assistant_message"
    assert cast(dict[str, object], result["result"])["continuation"] is None
    assert len(model.requests) == 3
    assert not any(
        request.metadata.get("prompt_id") == "explain_concept.v1" for request in model.requests
    )


def test_repository_application_is_reachable_over_http(tmp_path: Path) -> None:
    root, adapters, _model = _repository(tmp_path)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    try:
        server = create_server("127.0.0.1", 0, ui_application=app)
    except PermissionError as error:
        pytest.skip(f"local sockets are unavailable in this test sandbox: {error}")
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = cast(tuple[str, int], server.server_address)
        connection = HTTPConnection(host, port, timeout=2)
        connection.request("GET", "/api/v1/bootstrap")
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert response.status == 200
        assert payload["mode"] == "local_repository"
        connection.close()

        connection = HTTPConnection(host, port, timeout=2)
        connection.request("GET", "/health")
        health_response = connection.getresponse()
        health = json.loads(health_response.read())
        assert health_response.status == 200
        assert health == {
            "mode": "local_repository",
            "status": "ok",
            "runtime_id": "cardine-local-source-grounding-v2",
        }
        connection.close()
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_repository_http_rejects_unsafe_posts_before_application(tmp_path: Path) -> None:
    root, adapters, model = _repository(tmp_path)
    application = _CountingUiApplication(
        RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    )
    try:
        server = create_server("127.0.0.1", 0, ui_application=application)
    except PermissionError as error:
        pytest.skip(f"local sockets are unavailable in this test sandbox: {error}")
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = cast(tuple[str, int], server.server_address)
        connection = HTTPConnection(host, port, timeout=2)
        connection.request("GET", "/api/v1/bootstrap")
        bootstrap_response = connection.getresponse()
        initial_sequence = json.loads(bootstrap_response.read())["high_water_sequence"]
        connection.close()

        body = json.dumps(_command("security-request", initial_sequence, "aortic")).encode()
        connection = HTTPConnection(host, port, timeout=2)
        connection.request(
            "POST",
            "/api/v1/session/turns",
            body=body,
            headers={
                "Content-Type": "text/plain",
                "Content-Length": str(len(body)),
            },
        )
        content_type_response = connection.getresponse()
        content_type_response.read()
        assert content_type_response.status == 415
        connection.close()

        connection = HTTPConnection(host, port, timeout=2)
        connection.request(
            "POST",
            "/api/v1/session/turns",
            body=body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Content-Length": str(len(body)),
                "Origin": "https://foreign.example",
            },
        )
        origin_response = connection.getresponse()
        origin_response.read()
        assert origin_response.status == 403
        connection.close()

        assert application.post_calls == 0
        assert model.requests == ()

        connection = HTTPConnection(host, port, timeout=2)
        connection.request(
            "POST",
            "/api/v1/session/turns",
            body=body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Content-Length": str(len(body)),
            },
        )
        direct_response = connection.getresponse()
        direct_payload = json.loads(direct_response.read())
        assert direct_response.status == 200
        assert direct_payload["status"] == "assistant_message"
        assert application.post_calls == 1
        connection.close()
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
