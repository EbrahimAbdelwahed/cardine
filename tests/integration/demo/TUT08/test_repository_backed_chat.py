from __future__ import annotations

import json
import platform
import re
import shutil
import sys
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
from tests.receipt_assertions import without_transient_activity

COURSE = CourseId("cardine-course")
SESSION = SessionId("cardine-session")
_EVIDENCE_ID = re.compile(r'"evidence_id":"([^"]+)"')



_VERIFIED_ANYDOC_WORKER = (
    sys.platform == "darwin"
    and platform.machine() == "arm64"
    and sys.version_info[:2] in {(3, 12), (3, 13)}
    and shutil.which("sandbox-exec") == "/usr/bin/sandbox-exec"
)
_requires_verified_worker = pytest.mark.skipif(
    not _VERIFIED_ANYDOC_WORKER,
    reason="verified AnyDoc containment requires macOS arm64 with sandbox-exec",
)


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
                    "kind": "start_capability",
                    "capability_id": "explain_concept",
                    "inputs": {
                        "query": "aortic valve",
                        "target": "aortic valve",
                        "language": "en",
                        "learner_goal": None,
                        "continuation_summary_json": None,
                    },
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
            {"kind": "assistant_message", "message": "Corso creato."},
            {
                "kind": "invoke_tool",
                "tool_name": "session.start",
                "arguments": {"session_id": str(SESSION)},
            },
            {"kind": "assistant_message", "message": "Sessione avviata."},
            {
                "kind": "invoke_tool",
                "tool_name": "source.ingest",
                "arguments": {
                    "filename": "tutor-notes.md",
                    "title": "Tutor notes",
                    "content": "Il ventricolo sinistro genera pressione sistemica.",
                },
            },
            {"kind": "assistant_message", "message": "Fonte registrata."},
            {"kind": "invoke_tool", "tool_name": "evidence.get", "arguments": {}},
            {
                "kind": "assistant_message",
                "message": "Connessione verificata.",
            },
            {"kind": "assistant_message", "message": "Modello pronto."},
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


def test_repository_source_viewer_returns_the_bound_markdown_revision(tmp_path: Path) -> None:
    root, adapters, _model = _repository(tmp_path)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    materials = cast(tuple[dict[str, object], ...], app.get("/api/v1/materials")["items"])
    material = next(item for item in materials if item["title"] == "Valve notes")
    assert material["viewer"] == {"kind": "markdown", "page_count": None}

    document = app.read_source_document(
        cast(str, material["source_id"]),
        cast(str, material["revision_id"]),
    )

    assert document.title == "Valve notes"
    assert document.viewer_kind == "markdown"
    assert document.media_type == "text/markdown; charset=utf-8"
    assert document.content == b"The aortic valve has three cusps."

    with pytest.raises(UiRequestError, match="source revision"):
        app.read_source_document(cast(str, material["source_id"]), "revision-mismatch")


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


