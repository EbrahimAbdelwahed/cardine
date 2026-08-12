from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import cast

from study_agent.adapters.model import (
    ADAPTER_ID,
    HttpResponse,
    OpenAICompatibleConfig,
    OpenAICompatibleModel,
    ScriptedExchange,
    ScriptedModel,
)
from study_agent.adapters.sqlite import SQLiteEventStore
from cardine.courses import register_course_events
from study_agent.domain import (
    ChunkId,
    Citation,
    CorrelationId,
    CourseId,
    ExecutionContext,
    PrincipalKind,
    ResolvedCitation,
    RevisionId,
    RunId,
    SessionId,
    SourceChunk,
    SourceId,
)
from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.grounding import (
    EvidenceEnvelope,
    EvidenceSufficiencyValidator,
    GroundedAnswerIntegrityValidator,
)
from study_agent.playbooks import (
    ModelStep,
    PlaybookEngine,
    PlaybookRunResult,
    PlaybookRunStatus,
    PromptComposerRegistration,
    RuntimeRegistries,
    ToolBehaviorPin,
    VersionPins,
)
from study_agent.playbooks.builtin import GROUNDED_ANSWER_FLOW
from study_agent.ports import (
    EvidenceStatus,
    ModelCapabilities,
    ModelFinishReason,
    ModelInvocation,
    ModelPort,
    ModelRequest,
    ModelResponse,
    RetrievalEvidence,
    RetrievalEvidenceSet,
    retrieval_read_set_fingerprint,
)
from study_agent.prompts import GROUNDED_ANSWER_PROMPT, CanonicalPromptComposer
from study_agent.sessions import (
    GroundedSessionFinalizer,
    ProjectionSessionView,
    SessionService,
    register_session_events,
)
from study_agent.skills import ArtifactReference, SemanticVersion
from study_agent.skills.builtin import GROUNDED_ANSWER_SKILL
from study_agent.state import EventRegistry
from tests.course_fixtures import create_canonical_course

V1 = SemanticVersion.parse("1.0.0")
STATE = ArtifactReference("event_state", V1)
SCRIPTED = ArtifactReference("scripted-model", V1)


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 7, 11, 15, tzinfo=UTC)


class Store:
    def __init__(self) -> None:
        self.values: dict[RunId, bytes] = {}

    def create(self, run_id: RunId, payload: bytes) -> bool:
        if run_id in self.values:
            return False
        self.values[run_id] = payload
        return True

    def compare_and_set(self, run_id: RunId, expected: bytes, replacement: bytes) -> bool:
        if self.values.get(run_id) != expected:
            return False
        self.values[run_id] = replacement
        return True

    def load(self, run_id: RunId) -> bytes:
        return self.values[run_id]


class Tool:
    behavior_version = V1

    def __init__(self, name: str, result: JsonObject) -> None:
        self.name = name
        self.result = result
        self.calls: list[JsonObject] = []

    async def invoke(self, arguments: JsonObject) -> JsonObject:
        self.calls.append(arguments)
        return self.result


class Content:
    def __init__(self, citation: Citation, text: str) -> None:
        self.citation = citation
        self.text = text

    def get_text(self, revision_id: RevisionId) -> str:
        return self.text

    def resolve(self, citation: Citation) -> ResolvedCitation:
        if citation != self.citation:
            raise ValueError("citation is not canonical")
        return ResolvedCitation(self.citation, self.text)


class FakeTransport:
    def __init__(self, response_content: JsonObject | str) -> None:
        self.response_content = response_content
        self.calls: list[bytes] = []

    def post(
        self,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> HttpResponse:
        del url, headers, timeout_seconds
        self.calls.append(body)
        content = (
            self.response_content
            if isinstance(self.response_content, str)
            else json.dumps(_plain(self.response_content))
        )
        payload = {
            "id": "response-1",
            "choices": [
                {
                    "message": {"content": content},
                    "finish_reason": "stop",
                }
            ],
        }
        return HttpResponse(200, json.dumps(payload).encode())


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def evidence(status: EvidenceStatus = EvidenceStatus.SUFFICIENT) -> tuple[JsonObject, Content]:
    text = "La valvola aortica ha tre cuspidi."
    source = SourceId("source-1")
    revision = RevisionId("revision-1")
    chunk = SourceChunk(
        ChunkId("chunk-1"),
        source,
        revision,
        0,
        len(text),
        (),
        0,
        sha256(text.encode()).hexdigest(),
        "chunker-v1",
    )
    citation = Citation(source, revision, chunk.chunk_id, 0, len(text), "Heart · 1", text)
    items = () if status is EvidenceStatus.INSUFFICIENT else (
        RetrievalEvidence(chunk, citation, text, 0.8),
    )
    envelope = EvidenceEnvelope.from_retrieval(
        RetrievalEvidenceSet(
            status,
            items,
            "a" * 64,
            "fixture_lexical",
            "1.0.0",
            "fixture-index-v1",
            retrieval_read_set_fingerprint(items),
        )
    )
    return envelope.to_json(), Content(citation, text)


def draft(handle: str, *, status: str = "answered") -> JsonObject:
    return {
        "status": status,
        "segments": (
            {
                "kind": "supported_claim",
                "text": "La valvola aortica ha tre cuspidi.",
                "evidence_ids": (handle,),
            },
        ),
        "unsupported_information_note": (
            "Le fonti sono in conflitto." if status == "conflicting_evidence" else None
        ),
    }


def context() -> JsonObject:
    return {
        "course_profile": {
            "language": "it",
            "terminology_policy": {"valve": "valvola"},
        },
        "continuation_summary": "Previous question only.",
        "source_policy": {"minimum_trust_level": 80},
    }


def pins(adapter: ArtifactReference) -> VersionPins:
    return VersionPins(
        ArtifactReference(GROUNDED_ANSWER_SKILL.id, GROUNDED_ANSWER_SKILL.version),
        ArtifactReference(GROUNDED_ANSWER_FLOW.id, GROUNDED_ANSWER_FLOW.version),
        GROUNDED_ANSWER_PROMPT,
        (
            ToolBehaviorPin("session.get_context", V1),
            ToolBehaviorPin("source.search", V1),
        ),
        adapter,
        STATE,
    )


def expected_request(evidence_json: JsonObject) -> ModelRequest:
    step = cast(ModelStep, GROUNDED_ANSWER_FLOW.steps[3])
    composer = CanonicalPromptComposer()
    composed = composer.compose(
        prompt=GROUNDED_ANSWER_PROMPT,
        layers=GROUNDED_ANSWER_SKILL.prompt_layers,
        inputs={
            "question": "Quante cuspidi?",
            "course_profile": context()["course_profile"],
            "continuation_summary": context()["continuation_summary"],
            "evidence": evidence_json,
        },
        output_schema=step.output_schema,
    )
    request = step.request
    return replace(
        request,
        messages=composed.messages,
        metadata={
            "prompt_fingerprint": composed.fingerprint,
            "prompt_id": composed.prompt.id,
            "prompt_version": str(composed.prompt.version),
        },
    )


def engine(
    model: ModelPort,
    adapter: ArtifactReference,
    evidence_json: JsonObject,
    content: Content,
) -> PlaybookEngine:
    return PlaybookEngine(
        engine_version=V1,
        model_adapter=adapter,
        state_contract=STATE,
        model=model,
        registries=RuntimeRegistries(
            (
                Tool("session.get_context", context()),
                Tool("source.search", evidence_json),
            ),
            (
                EvidenceSufficiencyValidator(),
                GroundedAnswerIntegrityValidator(content),
            ),
            (
                PromptComposerRegistration(
                    GROUNDED_ANSWER_PROMPT, CanonicalPromptComposer()
                ),
            ),
        ),
        run_store=Store(),
        clock=Clock(),
    )


def execute(
    runtime: PlaybookEngine, run_id: str, adapter: ArtifactReference
) -> PlaybookRunResult:
    return asyncio.run(
        runtime.execute(
            run_id=RunId(run_id),
            skill=GROUNDED_ANSWER_SKILL,
            definition=GROUNDED_ANSWER_FLOW,
            inputs={
                "course_id": "course-1",
                "session_id": "session-1",
                "question": "Quante cuspidi?",
            },
            pins=pins(adapter),
        )
    )


def first_handle(envelope: JsonObject) -> str:
    items = cast(tuple[JsonValue, ...], envelope["items"])
    item = cast(Mapping[str, JsonValue], items[0])
    return cast(str, item["evidence_id"])


def test_exact_builtin_flow_composes_and_validates_through_scripted_model() -> None:
    envelope, content = evidence()
    handle = first_handle(envelope)
    request = expected_request(envelope)
    scripted = ScriptedModel(
        (
            ScriptedExchange(
                request,
                ModelResponse(
                    "",
                    None,
                    ModelFinishReason.STOP,
                    ModelInvocation("scripted-model", "1.0.0", "fixture-model", "r1"),
                    structured_output=draft(handle),
                ),
            ),
        ),
        ModelCapabilities(structured_output=True),
        adapter_id=SCRIPTED.id,
        adapter_version=str(SCRIPTED.version),
        model_id="fixture-model",
    )

    result = execute(engine(scripted, SCRIPTED, envelope, content), "run-scripted", SCRIPTED)

    assert result.status is PlaybookRunStatus.COMPLETED
    validated = cast(Mapping[str, JsonValue], result.outputs["validated_answer"])
    assert validated["status"] == "answered"
    model_trace = result.traces[7]
    prompt = cast(Mapping[str, JsonValue], model_trace.details["prompt"])
    invocation = cast(Mapping[str, JsonValue], model_trace.details["model_invocation"])
    assert prompt["fingerprint"] == request.metadata["prompt_fingerprint"]
    assert invocation["response_id"] == "r1"
    scripted.assert_exhausted()


def test_verified_supported_run_finalizes_with_complete_trusted_provenance(
    tmp_path: Path,
) -> None:
    envelope, content = evidence()
    handle = first_handle(envelope)
    request = expected_request(envelope)
    scripted = ScriptedModel(
        (
            ScriptedExchange(
                request,
                ModelResponse(
                    "",
                    None,
                    ModelFinishReason.STOP,
                    ModelInvocation("scripted-model", "1.0.0", "fixture-model", "r1"),
                    structured_output=draft(handle),
                ),
            ),
        ),
        ModelCapabilities(structured_output=True),
        adapter_id=SCRIPTED.id,
        adapter_version=str(SCRIPTED.version),
        model_id="fixture-model",
    )
    runtime = engine(scripted, SCRIPTED, envelope, content)
    run_id = RunId("run-session-finalize")
    execute(runtime, str(run_id), SCRIPTED)
    inputs = {
        "course_id": "course-1",
        "session_id": "session-1",
        "question": "Quante cuspidi?",
    }
    registry = EventRegistry()
    register_course_events(registry)
    register_session_events(registry)
    events = SQLiteEventStore(tmp_path / "events.sqlite3", registry)
    courses = create_canonical_course(events, CourseId("course-1"))
    view = ProjectionSessionView(events.projection)
    execution_context = ExecutionContext(
        PrincipalKind.SERVICE,
        "test-service",
        CourseId("course-1"),
        CorrelationId("correlation-session-finalize"),
        session_id=SessionId("session-1"),
    )
    SessionService(events, Clock(), view, courses).start(execution_context)
    record = GroundedSessionFinalizer(
        events,
        Clock(),
        view,
        content,
        GROUNDED_ANSWER_SKILL.state_write_policy,
    ).finalize_grounded_run(
        context=execution_context,
        engine=runtime,
        run_id=run_id,
        definition=GROUNDED_ANSWER_FLOW,
        inputs=inputs,
        pins=pins(SCRIPTED),
        idempotency_key="retry-session-finalize",
    )

    provenance = record.answer.provenance
    assert record.answer.status.value == "answered"
    assert provenance.model is not None
    assert provenance.model.response_id == "r1"
    assert provenance.prompt.composition_fingerprint == request.metadata["prompt_fingerprint"]
    assert provenance.retrieval.read_set_fingerprint == envelope["read_set_fingerprint"]
    assert {item.validator_id for item in provenance.validators} == {
        "evidence_sufficiency",
        "grounded_answer_integrity",
    }
    assert len(provenance.source_commitments) == 1
    assert len(events.read(CourseId("course-1"))) == 5
    assert events.verify_projection(CourseId("course-1"))


def test_identical_composed_request_runs_through_openai_fake_without_metadata() -> None:
    envelope, content = evidence()
    handle = first_handle(envelope)
    transport = FakeTransport(draft(handle))
    adapter_ref = ArtifactReference(ADAPTER_ID, V1)
    model = OpenAICompatibleModel(
        OpenAICompatibleConfig(
            "https://example.invalid/v1/chat/completions",
            "fixture-model",
            "secret-sentinel",
            capabilities=ModelCapabilities(structured_output=True),
        ),
        transport,
    )

    result = execute(engine(model, adapter_ref, envelope, content), "run-http", adapter_ref)
    sent = json.loads(transport.calls[0])
    expected = expected_request(envelope)

    assert result.status is PlaybookRunStatus.COMPLETED
    assert sent["messages"] == [
        {"role": item.role.value, "content": item.content} for item in expected.messages
    ]
    assert "metadata" not in sent
    assert "prompt_fingerprint" not in transport.calls[0].decode()
    assert "secret-sentinel" not in transport.calls[0].decode()


def test_insufficient_evidence_terminates_before_model_call() -> None:
    envelope, content = evidence(EvidenceStatus.INSUFFICIENT)
    scripted = ScriptedModel(
        (),
        ModelCapabilities(structured_output=True),
        adapter_id=SCRIPTED.id,
        adapter_version=str(SCRIPTED.version),
        model_id="fixture-model",
    )

    result = execute(
        engine(scripted, SCRIPTED, envelope, content), "run-insufficient", SCRIPTED
    )

    assert result.status is PlaybookRunStatus.TERMINATED
    gate = cast(Mapping[str, JsonValue], result.outputs["evidence_gate"])
    assert gate["status"] == "insufficient_evidence"
    assert scripted.requests == ()


def test_malformed_model_output_fails_before_integrity_result() -> None:
    envelope, content = evidence()
    request = expected_request(envelope)
    scripted = ScriptedModel(
        (
            ScriptedExchange(
                request,
                ModelResponse(
                    "",
                    None,
                    ModelFinishReason.STOP,
                    ModelInvocation("scripted-model", "1.0.0", "fixture-model"),
                    structured_output={"status": "answered"},
                ),
            ),
        ),
        ModelCapabilities(structured_output=True),
        adapter_id=SCRIPTED.id,
        adapter_version=str(SCRIPTED.version),
        model_id="fixture-model",
    )

    result = execute(engine(scripted, SCRIPTED, envelope, content), "run-invalid", SCRIPTED)

    assert result.status is PlaybookRunStatus.FAILED
    assert "validated_answer" not in result.outputs


def test_scripted_invocation_mismatch_fails_before_draft_or_validation() -> None:
    envelope, content = evidence()
    request = expected_request(envelope)
    scripted = ScriptedModel(
        (
            ScriptedExchange(
                request,
                ModelResponse(
                    "",
                    None,
                    ModelFinishReason.STOP,
                    ModelInvocation("ignored", "0.0.0", "ignored", "response-1"),
                    structured_output=draft(first_handle(envelope)),
                ),
            ),
        ),
        ModelCapabilities(structured_output=True),
        adapter_id="mismatched-scripted-adapter",
        adapter_version="9.9.9",
        model_id="fixture-model",
    )

    result = execute(
        engine(scripted, SCRIPTED, envelope, content),
        "run-scripted-provenance-mismatch",
        SCRIPTED,
    )

    assert result.status is PlaybookRunStatus.FAILED
    assert "draft" not in result.outputs
    assert "validated_answer" not in result.outputs
    assert result.traces[-1].step_id == "generate_answer"
    assert result.traces[-1].details == {"error_code": "model_error"}
    scripted.assert_exhausted()


def test_content_json_fallback_is_identical_and_validates_through_both_adapters() -> None:
    envelope, content = evidence()
    candidate = draft(first_handle(envelope))
    request = expected_request(envelope)
    scripted = ScriptedModel(
        (
            ScriptedExchange(
                request,
                ModelResponse(
                    json.dumps(_plain(candidate)),
                    None,
                    ModelFinishReason.STOP,
                    ModelInvocation("ignored", "0.0.0", "ignored", "scripted-r1"),
                ),
            ),
        ),
        ModelCapabilities(structured_output=False),
        adapter_id=SCRIPTED.id,
        adapter_version=str(SCRIPTED.version),
        model_id="fixture-model",
    )
    scripted_result = execute(
        engine(scripted, SCRIPTED, envelope, content), "run-scripted-fallback", SCRIPTED
    )

    transport = FakeTransport(candidate)
    http_ref = ArtifactReference(ADAPTER_ID, V1)
    http = OpenAICompatibleModel(
        OpenAICompatibleConfig(
            "https://example.invalid/v1/chat/completions",
            "fixture-model",
            "secret-sentinel",
            capabilities=ModelCapabilities(structured_output=False),
        ),
        transport,
    )
    http_result = execute(
        engine(http, http_ref, envelope, content), "run-http-fallback", http_ref
    )
    sent = json.loads(transport.calls[0])

    assert scripted_result.status is PlaybookRunStatus.COMPLETED
    assert http_result.status is PlaybookRunStatus.COMPLETED
    assert cast(Mapping[str, JsonValue], scripted_result.outputs["validated_answer"])[
        "status"
    ] == "answered"
    assert cast(Mapping[str, JsonValue], http_result.outputs["validated_answer"])[
        "status"
    ] == "answered"
    assert sent["messages"] == [
        {"role": message.role.value, "content": message.content}
        for message in scripted.requests[0].messages
    ]
    assert scripted.requests[0].messages == request.messages
    assert "response_format" not in sent
    assert scripted.requests[0].structured_output is not None
    scripted.assert_exhausted()


def test_malformed_and_nonconforming_fallback_json_fail_before_validated_output() -> None:
    envelope, content = evidence()
    request = expected_request(envelope)
    payloads = ("{malformed", '{"status":"answered"}')

    for index, payload in enumerate(payloads):
        scripted = ScriptedModel(
            (
                ScriptedExchange(
                    request,
                    ModelResponse(
                        payload,
                        None,
                        ModelFinishReason.STOP,
                        ModelInvocation("ignored", "0.0.0", "ignored"),
                    ),
                ),
            ),
            ModelCapabilities(structured_output=False),
            adapter_id=SCRIPTED.id,
            adapter_version=str(SCRIPTED.version),
            model_id="fixture-model",
        )
        scripted_result = execute(
            engine(scripted, SCRIPTED, envelope, content),
            f"run-scripted-fallback-invalid-{index}",
            SCRIPTED,
        )

        transport = FakeTransport(payload)
        http_ref = ArtifactReference(ADAPTER_ID, V1)
        http = OpenAICompatibleModel(
            OpenAICompatibleConfig(
                "https://example.invalid/v1/chat/completions",
                "fixture-model",
                "secret-sentinel",
                capabilities=ModelCapabilities(structured_output=False),
            ),
            transport,
        )
        http_result = execute(
            engine(http, http_ref, envelope, content),
            f"run-http-fallback-invalid-{index}",
            http_ref,
        )

        assert scripted_result.status is PlaybookRunStatus.FAILED
        assert http_result.status is PlaybookRunStatus.FAILED
        assert "validated_answer" not in scripted_result.outputs
        assert "validated_answer" not in http_result.outputs
        assert "response_format" not in json.loads(transport.calls[0])
