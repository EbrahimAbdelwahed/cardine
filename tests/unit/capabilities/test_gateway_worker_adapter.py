from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from study_agent.capabilities import (
    CancelledCapabilityOutcome,
    CapabilityContinuation,
    CapabilityGatewayError,
    CapabilityGatewayErrorCode,
    CompletedCapabilityOutcome,
    FailedCapabilityOutcome,
    StaleCapabilityOutcome,
    StudyCapabilityGateway,
    SuspendedCapabilityOutcome,
    TerminatedCapabilityOutcome,
    explain_concept_binding,
)
from study_agent.capabilities.bindings import CapabilityBinding, ProfiledCapabilityBinding
from study_agent.capabilities.contracts import CapabilityOutcome
from study_agent.capabilities.worker_adapter import GatewayIsolatedCapabilityRunAdapter
from study_agent.domain import (
    CorrelationId,
    CourseId,
    ExecutionContext,
    PrincipalKind,
    RunId,
    SessionId,
)
from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.playbooks import (
    PlaybookRunStatus,
    ReadDependency,
    StepTrace,
    StepTraceStatus,
    ValidationOutcome,
    ValidatorDisposition,
    VerifiedRunRecord,
    playbook_definition_fingerprint,
)
from study_agent.skills import ArtifactReference, SemanticVersion
from study_agent.workers import (
    GenerationWorkerStatus,
    GenerationWorkerTask,
    GenerationWorkerTaskKind,
    ValidationExpectation,
    ValidationReceiptSource,
    fingerprint_output_schema,
    generation_worker_authority_fingerprint,
)
from study_agent.workers.proof import VerifiedChildProofOwner

V1 = SemanticVersion.parse("1.0.0")
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
NOW = datetime(2026, 7, 16, 12, tzinfo=UTC)
INPUTS: JsonObject = {
    "query": "Explain the aortic valve.",
    "target": "cusps",
    "language": "en",
    "learner_goal": None,
    "continuation_summary_json": None,
}
OUTPUT: JsonObject = {
    "status": "answered",
    "segments": (),
    "unsupported_information_note": None,
}


class MemoryProofStore:
    def __init__(self) -> None:
        self.values: dict[RunId, bytes] = {}

    def create(self, run_id: RunId, payload: bytes) -> bool:
        if run_id in self.values:
            return False
        self.values[run_id] = payload
        return True

    def load(self, run_id: RunId) -> bytes:
        return self.values[run_id]


class RecordingGateway(StudyCapabilityGateway):
    def __init__(self, result: CapabilityOutcome | BaseException) -> None:
        self.result = result
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    async def _start_bound(
        self,
        binding: CapabilityBinding | ProfiledCapabilityBinding,
        public_inputs: JsonObject,
        execution_inputs: JsonObject,
        context: ExecutionContext,
    ) -> CapabilityOutcome:
        self.calls.append(("start", (binding, public_inputs, execution_inputs, context)))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result

    async def _resume_bound(
        self,
        binding: CapabilityBinding | ProfiledCapabilityBinding,
        continuation: CapabilityContinuation,
        response: JsonValue,
        context: ExecutionContext,
    ) -> CapabilityOutcome:
        self.calls.append(("resume", (binding, continuation, response, context)))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def _binding() -> CapabilityBinding:
    return explain_concept_binding(
        dependency_resolver=lambda *, context, inputs: (
            ReadDependency("course", str(context.course_id), "sequence-1"),
        ),
        model_adapter=ArtifactReference("model-adapter", V1),
        state_contract=ArtifactReference("event-state", V1),
    )


def _expected() -> tuple[ValidationExpectation, ...]:
    return (
        ValidationExpectation(
            "check_evidence",
            ValidationReceiptSource.VALIDATE_STEP,
            "tutor_evidence_gate",
            "1.0.0",
        ),
        ValidationExpectation(
            "check_readiness",
            ValidationReceiptSource.VALIDATE_STEP,
            "explain_concept_readiness",
            "1.0.0",
        ),
        ValidationExpectation(
            "generate_explanation",
            ValidationReceiptSource.STRUCTURED_OUTPUT_FALLBACK,
            "explain_concept_integrity",
            "1.0.0",
        ),
        ValidationExpectation(
            "validate_explanation",
            ValidationReceiptSource.VALIDATE_STEP,
            "explain_concept_integrity",
            "1.0.0",
        ),
    )


def _task() -> GenerationWorkerTask:
    binding = _binding()
    return GenerationWorkerTask(
        "lesson-1:explain",
        GenerationWorkerTaskKind.FLASHCARD_BUNDLE,
        binding.manifest.id,
        binding.manifest.version,
        binding.manifest_fingerprint,
        binding.manifest.required_authority,
        binding.pins,
        playbook_definition_fingerprint(binding.playbook),
        "en",
        {},
        None,
        ("scope:lesson-1",),
        ("evidence:chunk-1",),
        INPUTS,
        binding.manifest.output_schema,
        fingerprint_output_schema(binding.manifest.output_schema),
        _expected(),
    )


def _parent() -> ExecutionContext:
    return ExecutionContext(
        PrincipalKind.SERVICE,
        "worker-host",
        CourseId("course-1"),
        CorrelationId("child-correlation"),
        frozenset({"course:read"}),
        SessionId("session-1"),
        idempotency_key="child-retry",
    )


def _validator_details(validator_id: str) -> JsonObject:
    return {
        "validator": {
            "validator_id": validator_id,
            "validator_version": "1.0.0",
            "passed": True,
            "disposition": "continue",
            "result_fingerprint": SHA_A,
            "reason": None,
        }
    }


def _completed_run(*, traces: tuple[StepTrace, ...] | None = None) -> VerifiedRunRecord:
    task = _task()
    values = (
        StepTrace("search_sources", "tool", StepTraceStatus.COMPLETED, NOW, {}),
        StepTrace(
            "check_evidence",
            "validate",
            StepTraceStatus.COMPLETED,
            NOW,
            _validator_details("tutor_evidence_gate"),
        ),
        StepTrace(
            "check_readiness",
            "validate",
            StepTraceStatus.COMPLETED,
            NOW,
            _validator_details("explain_concept_readiness"),
        ),
        StepTrace("clarify_target", "dialogue", StepTraceStatus.COMPLETED, NOW, {}),
        StepTrace(
            "generate_explanation",
            "model",
            StepTraceStatus.COMPLETED,
            NOW,
            {
                "model_invocation": {
                    "adapter_id": "model-adapter",
                    "adapter_version": "1.0.0",
                    "model_id": "fixture-model",
                    "response_id": None,
                },
                "model_usage": {"input_tokens": 12, "output_tokens": 7},
                "prompt": {
                    "id": task.pins.prompt.id,
                    "version": str(task.pins.prompt.version),
                    "fingerprint": SHA_B,
                    "layers": (
                        {
                            "id": "task",
                            "version": "1.0.0",
                            "kind": "task_instruction",
                            "input_fingerprint": SHA_C,
                        },
                    ),
                },
                "fallback_validators": (
                    {
                        "validator_id": "explain_concept_integrity",
                        "validator_version": "1.0.0",
                        "passed": True,
                        "disposition": "continue",
                        "result_fingerprint": SHA_A,
                        "reason": None,
                        "result": OUTPUT,
                    },
                ),
            },
        ),
        StepTrace(
            "validate_explanation",
            "validate",
            StepTraceStatus.COMPLETED,
            NOW,
            _validator_details("explain_concept_integrity"),
        ),
    )
    return VerifiedRunRecord(
        RunId("child-run-1"),
        task.definition_fingerprint,
        task.capability_inputs(),
        task.pins,
        (ReadDependency("course", "course-1", "sequence-1"),),
        {
            "evidence": {"items": ()},
            "evidence_gate": {"passed": True},
            "readiness": {"needs_clarification": False},
            "clarification": {"provided": False, "text": ""},
            "draft": OUTPUT,
            "explanation": OUTPUT,
        },
        traces or values,
        PlaybookRunStatus.COMPLETED,
    )


def _continuation() -> CapabilityContinuation:
    task = _task()
    return CapabilityContinuation(
        RunId("child-run-1"),
        task.capability_id,
        task.capability_version,
        task.manifest_fingerprint,
        SHA_A,
        SHA_B,
        task.definition_fingerprint,
        SHA_C,
        "clarify_target",
        4,
        task.capability_inputs(),
        task.pins,
        (),
    )


def _adapter(
    result: CapabilityOutcome | BaseException,
) -> tuple[GatewayIsolatedCapabilityRunAdapter, RecordingGateway]:
    return _adapter_with_store(result, MemoryProofStore())


def _adapter_with_store(
    result: CapabilityOutcome | BaseException, store: MemoryProofStore
) -> tuple[GatewayIsolatedCapabilityRunAdapter, RecordingGateway]:
    gateway = RecordingGateway(result)
    adapter = GatewayIsolatedCapabilityRunAdapter(
        gateway=gateway,
        bindings=(_binding(),),
        proof_owner=VerifiedChildProofOwner(store),
    )
    return adapter, gateway


def _run[T](awaitable: Coroutine[object, object, T]) -> T:
    return asyncio.run(awaitable)


def test_public_definition_and_worker_authority_helpers_preserve_golden_bytes() -> None:
    assert playbook_definition_fingerprint(_binding().playbook) == (
        "5db0d32dd3d19d302c59dd67237fc7e254366a2bef2ca986daee4712672b9565"
    )
    assert generation_worker_authority_fingerprint(_task(), _parent()) == (
        "ccbfde52476ca8a702fe2779622c9bb9a1ae8f947838706e87725e46ec21f204"
    )


def test_completed_gateway_run_is_called_once_and_converted_with_sanitized_provenance() -> None:
    run = _completed_run()
    adapter, gateway = _adapter(CompletedCapabilityOutcome(run, OUTPUT))
    observed = _run(adapter.start(_task(), _parent()))
    assert observed.status is GenerationWorkerStatus.COMPLETED
    assert observed.output == OUTPUT
    assert observed.verified_run is run
    assert observed.prompt is not None and observed.prompt.composition_fingerprint == SHA_B
    assert [item.step_id for item in observed.validations] == [
        item.step_id for item in _expected()
    ]
    assert len(gateway.calls) == 1


def test_resume_passes_the_exact_task_and_continuation_to_one_gateway_call() -> None:
    run = _completed_run()
    continuation = _continuation()
    task = _task()
    adapter, gateway = _adapter(CompletedCapabilityOutcome(run, OUTPUT))
    observed = _run(adapter.resume(task, continuation, {"provided": False}, _parent()))
    assert observed.status is GenerationWorkerStatus.COMPLETED
    assert len(gateway.calls) == 1
    assert gateway.calls[0][0] == "resume"
    assert gateway.calls[0][1][1] is continuation

    changed = replace(
        task,
        payload={**task.payload, "query": "A different durable request."},
    )
    with pytest.raises(ValueError, match="task and continuation"):
        _run(adapter.resume(changed, continuation, {}, _parent()))
    assert len(gateway.calls) == 1


@pytest.mark.parametrize(
    ("outcome", "status"),
    (
        (
            CancelledCapabilityOutcome(RunId("child-run-1"), "cancelled"),
            GenerationWorkerStatus.CANCELLED,
        ),
        (StaleCapabilityOutcome(RunId("child-run-1"), "stale"), GenerationWorkerStatus.STALE),
        (FailedCapabilityOutcome(RunId("child-run-1"), "failed"), GenerationWorkerStatus.FAILED),
    ),
)
def test_closed_non_success_gateway_outcomes_map_without_direct_effect(
    outcome: CapabilityOutcome, status: GenerationWorkerStatus
) -> None:
    adapter, gateway = _adapter(outcome)
    observed = _run(adapter.start(_task(), _parent()))
    assert observed.status is status
    assert len(gateway.calls) == 1


def test_suspended_terminated_and_retryable_in_progress_map_exactly() -> None:
    continuation = _continuation()
    suspended, calls = _adapter(
        SuspendedCapabilityOutcome(
            continuation.run_id,
            "clarify",
            continuation,
            {
                "type": "object",
                "properties": {},
                "required": (),
                "additionalProperties": False,
            },
        )
    )
    assert _run(suspended.start(_task(), _parent())).status is GenerationWorkerStatus.SUSPENDED
    assert len(calls.calls) == 1

    run = _completed_run()
    terminated_run = replace(
        run,
        status=PlaybookRunStatus.TERMINATED,
        termination=ValidationOutcome(
            False, ValidatorDisposition.TERMINATE, {}, "insufficient evidence"
        ),
    )
    terminated, calls = _adapter(TerminatedCapabilityOutcome(terminated_run))
    assert _run(terminated.start(_task(), _parent())).status is GenerationWorkerStatus.TERMINATED
    assert len(calls.calls) == 1

    pending, calls = _adapter(
        CapabilityGatewayError(
            CapabilityGatewayErrorCode.IN_PROGRESS, "owned", retryable=True
        )
    )
    assert _run(pending.start(_task(), _parent())).status is GenerationWorkerStatus.RUNNING
    assert len(calls.calls) == 1


@pytest.mark.parametrize("mutation", ("missing", "extra", "duplicate", "reordered", "tampered"))
def test_changed_validation_or_prompt_provenance_fails_without_second_gateway_call(
    mutation: str,
) -> None:
    traces = list(_completed_run().traces)
    if mutation == "missing":
        traces.pop(1)
    elif mutation == "extra":
        traces.append(traces[-1])
    elif mutation == "duplicate":
        traces.insert(2, traces[1])
    elif mutation == "reordered":
        traces[1], traces[2] = traces[2], traces[1]
    else:
        details = dict(traces[4].details)
        prompt = dict(details["prompt"])  # type: ignore[arg-type]
        prompt["id"] = "forged-prompt"
        details["prompt"] = prompt
        traces[4] = replace(traces[4], details=details)
    run = _completed_run(traces=tuple(traces))
    adapter, gateway = _adapter(CompletedCapabilityOutcome(run, OUTPUT))
    observed = _run(adapter.start(_task(), _parent()))
    assert observed.status is GenerationWorkerStatus.FAILED
    assert observed.failure_code == "child_proof_invalid"
    assert len(gateway.calls) == 1


@pytest.mark.parametrize(
    "mutation",
    ("dependency", "tool_value", "model_id", "response_id", "usage"),
)
def test_retry_cannot_replace_the_sanitized_proof_derived_by_the_first_run(
    mutation: str,
) -> None:
    store = MemoryProofStore()
    exact_run = _completed_run()
    exact, exact_gateway = _adapter_with_store(
        CompletedCapabilityOutcome(exact_run, OUTPUT), store
    )
    exact_observation = _run(exact.start(_task(), _parent()))
    assert exact_observation.status is GenerationWorkerStatus.COMPLETED
    exact_payload = store.values[RunId("child-run-1")]

    if mutation == "dependency":
        changed_run = replace(
            exact_run,
            read_dependencies=(ReadDependency("course", "course-1", "sequence-2"),),
        )
    elif mutation == "tool_value":
        outputs = dict(exact_run.outputs)
        outputs["evidence"] = {"items": ({"text": "fabricated"},)}
        changed_run = replace(exact_run, outputs=outputs)
    else:
        traces = list(exact_run.traces)
        details = dict(traces[4].details)
        if mutation == "usage":
            details["model_usage"] = {"input_tokens": 99, "output_tokens": 101}
        else:
            invocation = dict(details["model_invocation"])  # type: ignore[arg-type]
            invocation[mutation] = f"fabricated-{mutation}"
            details["model_invocation"] = invocation
        traces[4] = replace(traces[4], details=details)
        changed_run = replace(exact_run, traces=tuple(traces))

    retry, retry_gateway = _adapter_with_store(
        CompletedCapabilityOutcome(changed_run, OUTPUT), store
    )
    rejected = _run(retry.start(_task(), _parent()))
    assert rejected.status is GenerationWorkerStatus.FAILED
    assert rejected.failure_code == "child_proof_invalid"
    assert store.values[RunId("child-run-1")] == exact_payload
    assert len(exact_gateway.calls) == len(retry_gateway.calls) == 1


def test_fabricated_tool_step_is_rejected_before_proof_persistence() -> None:
    run = _completed_run()
    traces = list(run.traces)
    traces[0] = replace(traces[0], step_id="fabricated-step")
    store = MemoryProofStore()
    adapter, gateway = _adapter_with_store(
        CompletedCapabilityOutcome(replace(run, traces=tuple(traces)), OUTPUT),
        store,
    )
    rejected = _run(adapter.start(_task(), _parent()))
    assert rejected.status is GenerationWorkerStatus.FAILED
    assert rejected.failure_code == "child_proof_invalid"
    assert store.values == {}
    assert len(gateway.calls) == 1
