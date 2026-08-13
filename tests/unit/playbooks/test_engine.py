from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

import pytest

from study_agent.domain import RunId
from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.playbooks import (
    STRUCTURED_OUTPUT_JSON_FALLBACK,
    CompletedRunResult,
    DataBinding,
    DataReference,
    DataSourceKind,
    EngineErrorCode,
    FailedRunResult,
    ModelStep,
    PlaybookDefinition,
    PlaybookEngine,
    PlaybookEngineError,
    PlaybookRunStatus,
    PromptComposerRegistration,
    ReadDependency,
    RuntimeRegistries,
    SuspendedRunResult,
    TerminatedRunResult,
    ToolBehaviorPin,
    ToolStep,
    ValidateStep,
    ValidationOutcome,
    ValidatorDisposition,
    VersionPins,
)
from study_agent.ports import (
    CancellationToken,
    MessageRole,
    ModelCapabilities,
    ModelError,
    ModelErrorCode,
    ModelFinishReason,
    ModelInvocation,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelStreamEvent,
    ModelUsage,
)
from study_agent.prompts import ComposedPrompt, PromptLayerRecord
from study_agent.skills import (
    ArtifactReference,
    CapabilityFallback,
    CapabilityRequirement,
    GroundingPolicy,
    JsonSchema,
    PromptLayer,
    PromptLayerKind,
    SemanticVersion,
    SkillPackage,
    StateWritePolicy,
    ToolRequirement,
    ValidatorDefinition,
    VersionRange,
)

V1 = SemanticVersion.parse("1.0.0")
V2 = SemanticVersion.parse("2.0.0")
MODEL_ADAPTER = ArtifactReference("scripted_adapter", V1)
MODEL_INVOCATION = ModelInvocation("scripted_adapter", "1.0.0", "scripted")
STATE_CONTRACT = ArtifactReference("event_state", V1)
PROMPT = ArtifactReference("grounded_prompt", V1)
STRUCTURED_OUTPUT_CAPABILITIES = ModelCapabilities(structured_output=True)


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 10, 12, 0, tzinfo=UTC)


class MemoryRunStore:
    def __init__(self, *, fail_cas_number: int | None = None) -> None:
        self.data: dict[RunId, bytes] = {}
        self.cas_calls = 0
        self.fail_cas_number = fail_cas_number

    def create(self, run_id: RunId, payload: bytes) -> bool:
        if run_id in self.data:
            return False
        self.data[run_id] = payload
        return True

    def compare_and_set(self, run_id: RunId, expected: bytes, replacement: bytes) -> bool:
        self.cas_calls += 1
        if self.cas_calls == self.fail_cas_number:
            return False
        if self.data.get(run_id) != expected:
            return False
        self.data[run_id] = replacement
        return True

    def load(self, run_id: RunId) -> bytes:
        return self.data[run_id]


class ScriptedTool:
    def __init__(
        self,
        name: str,
        result: JsonObject | Exception,
        calls: list[tuple[str, object]],
        version: SemanticVersion = V1,
    ) -> None:
        self._name = name
        self._result = result
        self._calls = calls
        self._version = version

    @property
    def name(self) -> str:
        return self._name

    @property
    def behavior_version(self) -> SemanticVersion:
        return self._version

    async def invoke(self, arguments: JsonObject) -> JsonObject:
        self._calls.append((self.name, arguments))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class ScriptedValidator:
    def __init__(
        self,
        identifier: str,
        outcome: ValidationOutcome | Exception,
        calls: list[tuple[str, object]],
    ) -> None:
        self._id = identifier
        self._outcome = outcome
        self._calls = calls

    @property
    def id(self) -> str:
        return self._id

    @property
    def version(self) -> SemanticVersion:
        return V1

    async def validate(self, inputs: JsonObject) -> ValidationOutcome:
        self._calls.append((self.id, inputs))
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class ScriptedModel:
    def __init__(
        self,
        capabilities: ModelCapabilities,
        calls: list[tuple[str, object]],
        response: ModelResponse | Exception | None = None,
    ) -> None:
        self._capabilities = capabilities
        self.calls = calls
        self.response = response or ModelResponse(
            "",
            ModelUsage(1, 1),
            ModelFinishReason.STOP,
            MODEL_INVOCATION,
            structured_output={"answer": "supported"},
        )

    @property
    def capabilities(self) -> ModelCapabilities:
        return self._capabilities

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(("model", request))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        raise NotImplementedError

    async def cancel(self, token: CancellationToken) -> None:
        raise NotImplementedError


class FixturePromptComposer:
    def compose(
        self,
        *,
        prompt: ArtifactReference,
        layers: tuple[PromptLayer, ...],
        inputs: JsonObject,
        output_schema: JsonSchema,
    ) -> ComposedPrompt:
        del layers, output_schema
        return ComposedPrompt(
            prompt,
            (ModelMessage(MessageRole.USER, f"evidence={inputs['evidence']}"),),
            (PromptLayerRecord("fixture", "1.0.0", "task_instruction", "a" * 64),),
            "b" * 64,
        )


def make_definition() -> PlaybookDefinition:
    question = DataReference(DataSourceKind.RUN_INPUT, "question")
    evidence = DataReference(DataSourceKind.STEP_OUTPUT, "evidence")
    draft = DataReference(DataSourceKind.STEP_OUTPUT, "draft")
    validated = DataReference(DataSourceKind.STEP_OUTPUT, "validated")
    return PlaybookDefinition(
        "grounded_flow",
        V1,
        VersionRange(V1, V2),
        (
            ToolStep(
                "search",
                ArtifactReference("source.search", V1),
                {},
                "evidence",
                (DataBinding("query", question),),
            ),
            ModelStep(
                "answer",
                PROMPT,
                ModelRequest((ModelMessage(MessageRole.USER, "Use prompt inputs."),)),
                JsonSchema(
                    {
                        "type": "object",
                        "required": ("answer",),
                        "properties": {"answer": {"type": "string"}},
                        "additionalProperties": False,
                    }
                ),
                "draft",
                (CapabilityRequirement("structured_output"),),
                (DataBinding("evidence", evidence),),
            ),
            ValidateStep(
                "validate",
                ArtifactReference("citation_validator", V1),
                ("draft",),
                "validated",
                (DataBinding("answer", draft),),
            ),
            ToolStep(
                "commit",
                ArtifactReference("session.commit", V1),
                {},
                "committed",
                (DataBinding("answer", validated),),
            ),
        ),
        ("question",),
    )


def make_skill(definition: PlaybookDefinition) -> SkillPackage:
    return SkillPackage(
        "grounded_answer",
        V1,
        "Grounded answer test skill.",
        VersionRange(V1, V2),
        JsonSchema({"type": "object"}),
        JsonSchema({"type": "object"}),
        (
            PromptLayer(
                "policy",
                V1,
                PromptLayerKind.STUDY_SECURITY_POLICY,
                "Use supplied evidence.",
            ),
        ),
        (),
        GroundingPolicy(True, "insufficient_evidence"),
        StateWritePolicy(),
        (CapabilityRequirement("structured_output"),),
        (
            ToolRequirement("source.search", V1),
            ToolRequirement("session.commit", V1),
        ),
        ArtifactReference(definition.id, definition.version),
        validators=(ValidatorDefinition("citation_validator", V1, "Check citations."),),
    )


def make_pins(skill: SkillPackage, definition: PlaybookDefinition) -> VersionPins:
    return VersionPins(
        ArtifactReference(skill.id, skill.version),
        ArtifactReference(definition.id, definition.version),
        PROMPT,
        (
            ToolBehaviorPin("source.search", V1),
            ToolBehaviorPin("session.commit", V1),
        ),
        MODEL_ADAPTER,
        STATE_CONTRACT,
    )


def make_engine(
    calls: list[tuple[str, object]],
    *,
    capabilities: ModelCapabilities = STRUCTURED_OUTPUT_CAPABILITIES,
    search_version: SemanticVersion = V1,
    validator_outcome: ValidationOutcome | Exception | None = None,
    engine_version: SemanticVersion = V1,
    model_response: ModelResponse | Exception | None = None,
    run_store: MemoryRunStore | None = None,
    search_result: JsonObject | Exception | None = None,
    include_validator: bool = True,
) -> PlaybookEngine:
    outcome = (
        validator_outcome
        if validator_outcome is not None
        else ValidationOutcome(
            True,
            ValidatorDisposition.CONTINUE,
            {"answer": "validated"},
        )
    )
    resolved_search_result = (
        search_result if search_result is not None else {"items": ("evidence",)}
    )
    return PlaybookEngine(
        engine_version=engine_version,
        model_adapter=MODEL_ADAPTER,
        state_contract=STATE_CONTRACT,
        model=ScriptedModel(capabilities, calls, model_response),
        registries=RuntimeRegistries(
            (
                ScriptedTool(
                    "source.search", resolved_search_result, calls, search_version
                ),
                ScriptedTool("session.commit", {"saved": True}, calls),
            ),
            (
                (ScriptedValidator("citation_validator", outcome, calls),)
                if include_validator
                else ()
            ),
            (PromptComposerRegistration(PROMPT, FixturePromptComposer()),),
        ),
        run_store=run_store or MemoryRunStore(),
        clock=FixedClock(),
    )


def test_complete_sequence_resolves_immutable_bindings_in_order() -> None:
    calls: list[tuple[str, object]] = []
    definition = make_definition()
    skill = make_skill(definition)

    result = asyncio.run(
        make_engine(calls).execute(
            run_id=RunId("run-complete"),
            skill=skill,
            definition=definition,
            inputs={"question": "What is supported?"},
            pins=make_pins(skill, definition),
        )
    )

    assert result.status is PlaybookRunStatus.COMPLETED
    assert [name for name, _ in calls] == [
        "source.search",
        "model",
        "citation_validator",
        "session.commit",
    ]
    model_request = calls[1][1]
    assert isinstance(model_request, ModelRequest)
    assert model_request.messages[0].content == "evidence={'items': ('evidence',)}"
    assert model_request.metadata == {
        "prompt_fingerprint": "b" * 64,
        "prompt_id": PROMPT.id,
        "prompt_version": str(PROMPT.version),
    }
    assert result.outputs["committed"] == {"saved": True}
    validator_receipt = cast(
        Mapping[str, JsonValue], result.traces[5].details["validator"]
    )
    assert validator_receipt == {
        "validator_id": "citation_validator",
        "validator_version": "1.0.0",
        "passed": True,
        "disposition": "continue",
        "result_fingerprint": validator_receipt["result_fingerprint"],
        "reason": None,
    }
    assert len(cast(str, validator_receipt["result_fingerprint"])) == 64


def test_mismatched_model_invocation_provenance_fails_before_model_output_checkpoint() -> None:
    calls: list[tuple[str, object]] = []
    definition = make_definition()
    skill = make_skill(definition)
    store = MemoryRunStore()
    response = ModelResponse(
        "",
        None,
        ModelFinishReason.STOP,
        ModelInvocation("forged-adapter", "9.9.9", "untrusted-model"),
        structured_output={"answer": "must-not-be-consumed"},
    )

    result = asyncio.run(
        make_engine(calls, model_response=response, run_store=store).execute(
            run_id=RunId("run-provenance-mismatch"),
            skill=skill,
            definition=definition,
            inputs={"question": "What is supported?"},
            pins=make_pins(skill, definition),
        )
    )

    assert isinstance(result, FailedRunResult)
    assert result.failure.code is EngineErrorCode.MODEL_ERROR
    assert result.failure.message == (
        "model invocation provenance does not match the pinned adapter"
    )
    assert result.outputs == {"evidence": {"items": ("evidence",)}}
    assert [item.status.value for item in result.traces[-2:]] == ["started", "failed"]
    assert result.traces[-1].step_id == "answer"
    assert result.traces[-1].details == {"error_code": "model_error"}
    persisted = json.loads(store.load(RunId("run-provenance-mismatch")))
    assert persisted["checkpoint"]["status"] == "failed"
    assert persisted["checkpoint"]["next_step_index"] == 1
    assert set(persisted["checkpoint"]["outputs"]) == {"evidence"}
    with pytest.raises(TypeError):
        result.outputs["new"] = True  # type: ignore[index]


@pytest.mark.parametrize(
    ("model_failure", "expected_reason"),
    (
        (RuntimeError("transport failed"), ModelErrorCode.UNAVAILABLE.value),
        (ModelError(ModelErrorCode.TIMEOUT, "provider timeout"), ModelErrorCode.TIMEOUT.value),
        (
            ModelError(ModelErrorCode.PROTOCOL_ERROR, "provider protocol failure"),
            ModelErrorCode.PROTOCOL_ERROR.value,
        ),
    ),
)
def test_model_transport_faults_persist_safe_model_error_receipts(
    model_failure: Exception,
    expected_reason: str,
) -> None:
    calls: list[tuple[str, object]] = []
    definition = make_definition()
    skill = make_skill(definition)
    store = MemoryRunStore()
    run_id = RunId(f"run-model-failure-{expected_reason}")

    result = asyncio.run(
        make_engine(calls, model_response=model_failure, run_store=store).execute(
            run_id=run_id,
            skill=skill,
            definition=definition,
            inputs={"question": "Question"},
            pins=make_pins(skill, definition),
        )
    )

    assert isinstance(result, FailedRunResult)
    assert result.failure.code is EngineErrorCode.MODEL_ERROR
    assert result.failure.message == f"model execution failed: {expected_reason}"
    assert result.traces[-1].details == {
        "error_code": "model_error",
        "model_failure_reason": expected_reason,
    }
    persisted = json.loads(store.load(run_id))
    assert persisted["traces"][-1]["details"] == {
        "error_code": "model_error",
        "model_failure_reason": expected_reason,
    }
    inspected = make_engine(calls, run_store=store).inspect(
        run_id=run_id,
        definition=definition,
    )
    assert inspected.status.value == "failed"


def test_validator_termination_prevents_later_effects() -> None:
    calls: list[tuple[str, object]] = []
    definition = make_definition()
    skill = make_skill(definition)
    outcome = ValidationOutcome(
        False,
        ValidatorDisposition.TERMINATE,
        {"status": "invalid"},
        "Citation check failed.",
    )

    result = asyncio.run(
        make_engine(calls, validator_outcome=outcome).execute(
            run_id=RunId("run-terminated"),
            skill=skill,
            definition=definition,
            inputs={"question": "Question"},
            pins=make_pins(skill, definition),
        )
    )

    assert result.status is PlaybookRunStatus.TERMINATED
    assert [name for name, _ in calls] == [
        "source.search",
        "model",
        "citation_validator",
    ]
    assert "committed" not in result.outputs


@pytest.mark.parametrize(
    ("capabilities", "tool_version", "expected_code"),
    [
        (ModelCapabilities(), V1, EngineErrorCode.UNSUPPORTED_CAPABILITY),
        (
            ModelCapabilities(structured_output=True),
            SemanticVersion.parse("1.1.0"),
            EngineErrorCode.UNSUPPORTED_TOOL,
        ),
    ],
)
def test_preflight_rejects_incompatible_runtime_before_effects(
    capabilities: ModelCapabilities,
    tool_version: SemanticVersion,
    expected_code: EngineErrorCode,
) -> None:
    calls: list[tuple[str, object]] = []
    definition = make_definition()
    skill = make_skill(definition)

    with pytest.raises(PlaybookEngineError) as caught:
        asyncio.run(
            make_engine(
                calls,
                capabilities=capabilities,
                search_version=tool_version,
            ).execute(
                run_id=RunId("run-preflight"),
                skill=skill,
                definition=definition,
                inputs={"question": "Question"},
                pins=make_pins(skill, definition),
            )
        )

    assert caught.value.failure.code is expected_code
    assert calls == []


def test_engine_and_pin_preflight_reject_before_effects() -> None:
    definition = make_definition()
    skill = make_skill(definition)
    pins = make_pins(skill, definition)

    for engine_version, candidate_pins, expected_code in (
        (
            V2,
            pins,
            EngineErrorCode.INCOMPATIBLE_ENGINE,
        ),
        (
            V1,
            replace(pins, model_adapter=ArtifactReference("changed_adapter", V1)),
            EngineErrorCode.INCOMPATIBLE_PINS,
        ),
    ):
        calls: list[tuple[str, object]] = []
        engine = make_engine(calls, engine_version=engine_version)
        with pytest.raises(PlaybookEngineError) as caught:
            asyncio.run(
                engine.execute(
                    run_id=RunId("run-other-preflight"),
                    skill=skill,
                    definition=definition,
                    inputs={"question": "Question"},
                    pins=candidate_pins,
                )
            )
        assert caught.value.failure.code is expected_code
        assert calls == []


@pytest.mark.parametrize(
    "response",
    [
        ModelResponse(
            "raw text", ModelUsage(1, 1), ModelFinishReason.STOP, MODEL_INVOCATION
        ),
        ModelResponse(
            "",
            ModelUsage(1, 1),
            ModelFinishReason.STOP,
            MODEL_INVOCATION,
            structured_output={},
        ),
    ],
)
def test_model_output_must_match_declared_schema(response: ModelResponse) -> None:
    calls: list[tuple[str, object]] = []
    definition = make_definition()
    skill = make_skill(definition)

    result = asyncio.run(
        make_engine(calls, model_response=response).execute(
            run_id=RunId("run-invalid-model"),
            skill=skill,
            definition=definition,
            inputs={"question": "Question"},
            pins=make_pins(skill, definition),
        )
    )

    assert result.status is PlaybookRunStatus.FAILED
    assert isinstance(result, FailedRunResult)
    assert result.failure.code is EngineErrorCode.SCHEMA_ERROR
    assert [name for name, _ in calls] == ["source.search", "model"]


def test_declared_structured_output_fallback_parses_and_validates_json() -> None:
    calls: list[tuple[str, object]] = []
    definition = make_definition()
    skill = replace(
        make_skill(definition),
        fallbacks=(
            CapabilityFallback(
                "structured_output",
                STRUCTURED_OUTPUT_JSON_FALLBACK,
                validator_ids=("citation_validator",),
            ),
        ),
    )
    response = ModelResponse(
        '{"answer":"fallback"}',
        ModelUsage(1, 1),
        ModelFinishReason.STOP,
        MODEL_INVOCATION,
    )

    result = asyncio.run(
        make_engine(
            calls,
            capabilities=ModelCapabilities(),
            model_response=response,
        ).execute(
            run_id=RunId("run-fallback"),
            skill=skill,
            definition=definition,
            inputs={"question": "Question"},
            pins=make_pins(skill, definition),
        )
    )

    assert result.status is PlaybookRunStatus.COMPLETED
    assert result.outputs["draft"] == {"answer": "fallback"}
    assert [name for name, _ in calls] == [
        "source.search",
        "model",
        "citation_validator",
        "citation_validator",
        "session.commit",
    ]
    fallback_receipts = cast(
        tuple[JsonValue, ...], result.traces[3].details["fallback_validators"]
    )
    fallback_receipt = cast(Mapping[str, JsonValue], fallback_receipts[0])
    assert fallback_receipt["validator_id"] == "citation_validator"
    assert fallback_receipt["validator_version"] == "1.0.0"
    assert fallback_receipt["passed"] is True
    assert fallback_receipt["disposition"] == "continue"
    assert len(cast(str, fallback_receipt["result_fingerprint"])) == 64


@pytest.mark.parametrize(
    ("strategy", "include_validator", "code"),
    [
        ("unknown_fallback", True, EngineErrorCode.UNSUPPORTED_FALLBACK),
        (
            STRUCTURED_OUTPUT_JSON_FALLBACK,
            False,
            EngineErrorCode.UNSUPPORTED_VALIDATOR,
        ),
    ],
)
def test_fallback_strategy_and_validators_are_preflighted_before_creation(
    strategy: str,
    include_validator: bool,
    code: EngineErrorCode,
) -> None:
    calls: list[tuple[str, object]] = []
    definition = make_definition()
    skill = replace(
        make_skill(definition),
        fallbacks=(
            CapabilityFallback(
                "structured_output",
                strategy,
                validator_ids=("citation_validator",),
            ),
        ),
    )
    store = MemoryRunStore()

    with pytest.raises(PlaybookEngineError) as caught:
        asyncio.run(
            make_engine(
                calls,
                capabilities=ModelCapabilities(),
                include_validator=include_validator,
                run_store=store,
            ).execute(
                run_id=RunId("run-invalid-fallback"),
                skill=skill,
                definition=definition,
                inputs={"question": "Question"},
                pins=make_pins(skill, definition),
            )
        )

    assert caught.value.failure.code is code
    assert store.data == {}
    assert calls == []


def test_duplicate_execute_and_failed_cas_never_repeat_an_effect() -> None:
    calls: list[tuple[str, object]] = []
    definition = make_definition()
    skill = make_skill(definition)
    store = MemoryRunStore(fail_cas_number=1)
    engine = make_engine(calls, run_store=store)
    run_id = RunId("run-cas-failure")

    with pytest.raises(PlaybookEngineError) as failed_advance:
        asyncio.run(
            engine.execute(
                run_id=run_id,
                skill=skill,
                definition=definition,
                inputs={"question": "Question"},
                pins=make_pins(skill, definition),
            )
        )
    assert failed_advance.value.failure.code is EngineErrorCode.RUN_STORE_ERROR
    assert [name for name, _ in calls] == ["source.search"]

    with pytest.raises(PlaybookEngineError) as duplicate:
        asyncio.run(
            engine.execute(
                run_id=run_id,
                skill=skill,
                definition=definition,
                inputs={"question": "Question"},
                pins=make_pins(skill, definition),
            )
        )
    assert duplicate.value.failure.code is EngineErrorCode.DUPLICATE_RUN
    assert [name for name, _ in calls] == ["source.search"]


def test_skill_range_and_duplicate_dependencies_fail_before_run_creation() -> None:
    calls: list[tuple[str, object]] = []
    definition = make_definition()
    base_skill = make_skill(definition)
    pins = make_pins(base_skill, definition)
    store = MemoryRunStore()
    engine = make_engine(calls, run_store=store)

    cases = (
        (
            replace(
                base_skill,
                engine_compatibility=VersionRange(
                    V2, SemanticVersion.parse("3.0.0")
                ),
            ),
            (),
            EngineErrorCode.INCOMPATIBLE_ENGINE,
        ),
        (
            base_skill,
            (
                ReadDependency("source", "source-1", "v1"),
                ReadDependency("source", "source-1", "v2"),
            ),
            EngineErrorCode.INVALID_INPUT,
        ),
    )
    for index, (skill, dependencies, code) in enumerate(cases):
        with pytest.raises(PlaybookEngineError) as caught:
            asyncio.run(
                engine.execute(
                    run_id=RunId(f"run-preflight-{index}"),
                    skill=skill,
                    definition=definition,
                    inputs={"question": "Question"},
                    pins=pins,
                    read_dependencies=dependencies,
                )
            )
        assert caught.value.failure.code is code
    assert store.data == {}
    assert calls == []


def test_run_result_variants_reject_invalid_status_payload_combinations() -> None:
    completed = CompletedRunResult({}, ())
    assert completed.status is PlaybookRunStatus.COMPLETED
    with pytest.raises(TypeError):
        CompletedRunResult({}, (), dialogue_request="not allowed")  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="dialogue request"):
        SuspendedRunResult({}, (), " ")
    continued = ValidationOutcome(
        True, ValidatorDisposition.CONTINUE, {"status": "valid"}
    )
    with pytest.raises(ValueError, match="terminate disposition"):
        TerminatedRunResult({}, (), continued)


@pytest.mark.parametrize("failure_kind", ["tool", "model", "schema", "validator"])
def test_handled_failures_are_atomically_persisted_as_failed(failure_kind: str) -> None:
    calls: list[tuple[str, object]] = []
    definition = make_definition()
    skill = make_skill(definition)
    store = MemoryRunStore()
    response: ModelResponse | Exception | None = None
    search_result: JsonObject | Exception | None = None
    validator_outcome: ValidationOutcome | Exception | None = None
    if failure_kind == "tool":
        search_result = RuntimeError("tool failed")
    elif failure_kind == "model":
        response = RuntimeError("model failed")
    elif failure_kind == "schema":
        response = ModelResponse(
            "",
            ModelUsage(1, 1),
            ModelFinishReason.STOP,
            MODEL_INVOCATION,
            structured_output={},
        )
    else:
        validator_outcome = RuntimeError("validator failed")
    run_id = RunId(f"run-failed-{failure_kind}")

    result = asyncio.run(
        make_engine(
            calls,
            model_response=response,
            search_result=search_result,
            validator_outcome=validator_outcome,
            run_store=store,
        ).execute(
            run_id=run_id,
            skill=skill,
            definition=definition,
            inputs={"question": "Question"},
            pins=make_pins(skill, definition),
        )
    )

    assert isinstance(result, FailedRunResult)
    persisted = json.loads(store.data[run_id])
    assert persisted["checkpoint"]["status"] == "failed"
    assert persisted["traces"][-1]["status"] == "failed"


def test_failure_persistence_cas_loss_raises_store_error_and_leaves_running() -> None:
    calls: list[tuple[str, object]] = []
    definition = make_definition()
    skill = make_skill(definition)
    store = MemoryRunStore(fail_cas_number=1)
    run_id = RunId("run-failure-cas-loss")

    with pytest.raises(PlaybookEngineError) as caught:
        asyncio.run(
            make_engine(
                calls,
                search_result=RuntimeError("tool failed"),
                run_store=store,
            ).execute(
                run_id=run_id,
                skill=skill,
                definition=definition,
                inputs={"question": "Question"},
                pins=make_pins(skill, definition),
            )
        )

    assert caught.value.failure.code is EngineErrorCode.RUN_STORE_ERROR
    persisted = json.loads(store.data[run_id])
    assert persisted["checkpoint"]["status"] == "running"


def test_completed_run_recovery_is_verified_immutable_and_effect_free() -> None:
    calls: list[tuple[str, object]] = []
    definition = make_definition()
    skill = make_skill(definition)
    pins = make_pins(skill, definition)
    store = MemoryRunStore()
    engine = make_engine(calls, run_store=store)
    run_id = RunId("run-recover-complete")
    inputs = {"question": "Question"}
    dependencies = (ReadDependency("source", "source-1", "revision-1"),)

    asyncio.run(
        engine.execute(
            run_id=run_id,
            skill=skill,
            definition=definition,
            inputs=inputs,
            pins=pins,
            read_dependencies=dependencies,
        )
    )
    effects = list(calls)

    recovered = engine.recover(
        run_id=run_id,
        definition=definition,
        inputs=inputs,
        pins=pins,
        read_dependencies=dependencies,
    )

    assert recovered.status is PlaybookRunStatus.COMPLETED
    assert recovered.run_id == run_id
    assert recovered.inputs == inputs
    assert recovered.read_dependencies == dependencies
    assert recovered.outputs["committed"] == {"saved": True}
    assert calls == effects
    with pytest.raises(TypeError):
        recovered.outputs["forged"] = True  # type: ignore[index]


def test_successful_deterministic_termination_is_recoverable_but_failure_is_not() -> None:
    definition = make_definition()
    skill = make_skill(definition)
    pins = make_pins(skill, definition)
    for passed, run_suffix in ((True, "success"), (False, "failure")):
        calls: list[tuple[str, object]] = []
        store = MemoryRunStore()
        outcome = ValidationOutcome(
            passed,
            ValidatorDisposition.TERMINATE,
            {"status": "insufficient_evidence" if passed else "failed"},
            "deterministic termination",
        )
        engine = make_engine(
            calls, validator_outcome=outcome, run_store=store
        )
        run_id = RunId(f"run-recover-termination-{run_suffix}")
        asyncio.run(
            engine.execute(
                run_id=run_id,
                skill=skill,
                definition=definition,
                inputs={"question": "Question"},
                pins=pins,
            )
        )
        effects = list(calls)
        if passed:
            recovered = engine.recover(
                run_id=run_id,
                definition=definition,
                inputs={"question": "Question"},
                pins=pins,
            )
            assert recovered.status is PlaybookRunStatus.TERMINATED
            assert recovered.termination == outcome
        else:
            with pytest.raises(PlaybookEngineError) as caught:
                engine.recover(
                    run_id=run_id,
                    definition=definition,
                    inputs={"question": "Question"},
                    pins=pins,
                )
            assert caught.value.failure.code is EngineErrorCode.INCOMPATIBLE_CHECKPOINT
        assert calls == effects


@pytest.mark.parametrize(
    "tamper",
    ("output", "validator_identity", "trace", "status", "unknown"),
)
def test_recovery_rejects_corrupt_checkpoint_without_effects(tamper: str) -> None:
    calls: list[tuple[str, object]] = []
    definition = make_definition()
    skill = make_skill(definition)
    pins = make_pins(skill, definition)
    store = MemoryRunStore()
    engine = make_engine(calls, run_store=store)
    run_id = RunId(f"run-recover-tampered-{tamper}")
    asyncio.run(
        engine.execute(
            run_id=run_id,
            skill=skill,
            definition=definition,
            inputs={"question": "Question"},
            pins=pins,
        )
    )
    payload = json.loads(store.data[run_id])
    if tamper == "output":
        payload["checkpoint"]["outputs"]["validated"] = {"answer": "forged"}
    elif tamper == "validator_identity":
        payload["traces"][5]["details"]["validator"]["validator_id"] = "forged"
    elif tamper == "trace":
        payload["traces"].pop()
    elif tamper == "status":
        payload["checkpoint"]["status"] = "running"
    else:
        payload["unknown"] = True
    store.data[run_id] = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode()
    effects = list(calls)

    with pytest.raises(PlaybookEngineError) as caught:
        engine.recover(
            run_id=run_id,
            definition=definition,
            inputs={"question": "Question"},
            pins=pins,
        )

    assert caught.value.failure.code is EngineErrorCode.INCOMPATIBLE_CHECKPOINT
    assert calls == effects


def test_recovery_requires_exact_inputs_pins_and_read_dependencies() -> None:
    calls: list[tuple[str, object]] = []
    definition = make_definition()
    skill = make_skill(definition)
    pins = make_pins(skill, definition)
    dependency = (ReadDependency("source", "source-1", "v1"),)
    store = MemoryRunStore()
    engine = make_engine(calls, run_store=store)
    run_id = RunId("run-recover-exact-expectations")
    asyncio.run(
        engine.execute(
            run_id=run_id,
            skill=skill,
            definition=definition,
            inputs={"question": "Question"},
            pins=pins,
            read_dependencies=dependency,
        )
    )
    effects = list(calls)

    candidates = (
        (
            {"question": "Changed"},
            pins,
            dependency,
            EngineErrorCode.INCOMPATIBLE_CHECKPOINT,
        ),
        (
            {"question": "Question"},
            replace(
                pins,
                tool_behaviors=(
                    ToolBehaviorPin("source.search", V1),
                    ToolBehaviorPin("session.commit", V2),
                ),
            ),
            dependency,
            EngineErrorCode.INCOMPATIBLE_CHECKPOINT,
        ),
        (
            {"question": "Question"},
            pins,
            (ReadDependency("source", "source-1", "v2"),),
            EngineErrorCode.STALE_READ_DEPENDENCY,
        ),
    )
    for inputs, candidate_pins, dependencies, code in candidates:
        with pytest.raises(PlaybookEngineError) as caught:
            engine.recover(
                run_id=run_id,
                definition=definition,
                inputs=inputs,
                pins=candidate_pins,
                read_dependencies=dependencies,
            )
        assert caught.value.failure.code is code
    assert calls == effects
