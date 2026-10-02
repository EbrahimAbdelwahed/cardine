from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine, Sequence
from dataclasses import replace

import pytest

from study_agent.capabilities import CapabilityContinuation, TutorCapabilityId
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
    ToolBehaviorPin,
    ValidatorDisposition,
    VerifiedRunRecord,
    VersionPins,
)
from study_agent.skills import ArtifactReference, SemanticVersion
from study_agent.state import canonical_json_bytes
from study_agent.workers import (
    ChildCapabilityObservation,
    GenerationWorkerConflictError,
    GenerationWorkerReceipt,
    GenerationWorkerService,
    GenerationWorkerStatus,
    GenerationWorkerTask,
    GenerationWorkerTaskKind,
    ObservedValidationReceipt,
    ValidationExpectation,
    ValidationReceiptSource,
    VerifiedPromptReceipt,
    fingerprint_output_schema,
    generation_worker_child_context,
)

V1 = SemanticVersion.parse("1.0.0")
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
OUTPUT = {"cards": ({"front": "Valve?", "back": "Three cusps."},)}
SCHEMA: JsonObject = {
    "type": "object",
    "required": ("cards",),
    "properties": {
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ("front", "back"),
                "properties": {
                    "front": {"type": "string"},
                    "back": {"type": "string"},
                },
                "additionalProperties": False,
            },
        }
    },
    "additionalProperties": False,
}


def _pins() -> VersionPins:
    return VersionPins(
        ArtifactReference("hybrid_flashcards", V1),
        ArtifactReference("hybrid_flashcards_flow", V1),
        ArtifactReference("hybrid_flashcards.v1", V1),
        (ToolBehaviorPin("source.prepare_flashcard_scope", V1),),
        ArtifactReference("model-adapter", V1),
        ArtifactReference("event-state", V1),
    )


def _expected() -> tuple[ValidationExpectation, ...]:
    return (
        ValidationExpectation(
            "integrity",
            ValidationReceiptSource.VALIDATE_STEP,
            "hybrid_flashcard_integrity",
            "1.0.0",
        ),
        ValidationExpectation(
            "fallback",
            ValidationReceiptSource.STRUCTURED_OUTPUT_FALLBACK,
            "structured_output_schema",
            "1.0.0",
        ),
    )


def _task(**changes: object) -> GenerationWorkerTask:
    values: dict[str, object] = {
        "task_id": "lesson-1:hybrid",
        "task_kind": GenerationWorkerTaskKind.FLASHCARD_BUNDLE,
        "capability_id": TutorCapabilityId.PROPOSE_FLASHCARDS,
        "capability_version": V1,
        "manifest_fingerprint": SHA_A,
        "required_authority": ("source.read",),
        "pins": _pins(),
        "definition_fingerprint": SHA_B,
        "language": "it",
        "preferences": {"exam_format": "oral"},
        "continuation_summary": {"known": ("valves",)},
        "index_references": ("scope:lesson-1",),
        "evidence_references": ("evidence:chunk-1",),
        "payload": {"query": "Generate cards", "scope": "lesson-1"},
        "output_schema": SCHEMA,
        "output_schema_fingerprint": fingerprint_output_schema(SCHEMA),
        "expected_validations": _expected(),
    }
    values.update(changes)
    return GenerationWorkerTask(**values)  # type: ignore[arg-type]


def _parent(
    *, principal: str = "tutor-service", grants: frozenset[str] = frozenset({"source.read"})
) -> ExecutionContext:
    return ExecutionContext(
        PrincipalKind.SERVICE,
        principal,
        CourseId("course-1"),
        CorrelationId("parent-correlation"),
        grants,
        SessionId("session-1"),
        idempotency_key="parent-retry",
    )


def _continuation(
    task: GenerationWorkerTask, *, checkpoint: str, step: int
) -> CapabilityContinuation:
    return CapabilityContinuation(
        RunId("child-run-1"),
        task.capability_id,
        task.capability_version,
        task.manifest_fingerprint,
        SHA_A,
        SHA_C,
        task.definition_fingerprint,
        checkpoint,
        f"clarify-{step}",
        step,
        task.capability_inputs(),
        task.pins,
        (),
    )


def _observed_validations() -> tuple[ObservedValidationReceipt, ...]:
    return tuple(
        ObservedValidationReceipt(
            expected.step_id,
            expected.source,
            expected.validator_id,
            expected.validator_version,
            True,
            SHA_C if index == 0 else SHA_D,
        )
        for index, expected in enumerate(_expected())
    )


def _base_observation(
    status: GenerationWorkerStatus,
    *,
    task: GenerationWorkerTask | None = None,
    continuation: CapabilityContinuation | None = None,
    failure_code: str | None = None,
) -> ChildCapabilityObservation:
    selected = task or _task()
    return ChildCapabilityObservation(
        status,
        selected.capability_id,
        selected.capability_version,
        selected.manifest_fingerprint,
        RunId("child-run-1"),
        selected.pins,
        selected.definition_fingerprint,
        selected.output_schema_fingerprint,
        continuation=continuation,
        failure_code=failure_code,
    )


def _completed(**changes: object) -> ChildCapabilityObservation:
    task = _task()
    values: dict[str, object] = {
        "status": GenerationWorkerStatus.COMPLETED,
        "capability_id": task.capability_id,
        "capability_version": task.capability_version,
        "manifest_fingerprint": task.manifest_fingerprint,
        "run_id": RunId("child-run-1"),
        "pins": task.pins,
        "definition_fingerprint": task.definition_fingerprint,
        "output_schema_fingerprint": task.output_schema_fingerprint,
        "validations": _observed_validations(),
        "prompt": VerifiedPromptReceipt(
            task.pins.prompt.id, str(task.pins.prompt.version), SHA_B, (SHA_C,)
        ),
        "verified_run": VerifiedRunRecord(
            RunId("child-run-1"),
            task.definition_fingerprint,
            task.capability_inputs(),
            task.pins,
            (),
            {"proposal": OUTPUT},
            (),
            PlaybookRunStatus.COMPLETED,
        ),
        "output": OUTPUT,
    }
    values.update(changes)
    return ChildCapabilityObservation(**values)  # type: ignore[arg-type]


class MemoryStore:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    def create(self, task_id: str, payload: bytes) -> bool:
        if task_id in self.values:
            return False
        self.values[task_id] = payload
        return True

    def compare_and_set(self, task_id: str, expected: bytes, replacement: bytes) -> bool:
        if self.values[task_id] != expected:
            return False
        self.values[task_id] = replacement
        return True

    def load(self, task_id: str) -> bytes:
        return self.values[task_id]


class WinningRaceStore(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.race_once = True

    def compare_and_set(self, task_id: str, expected: bytes, replacement: bytes) -> bool:
        if self.race_once:
            self.race_once = False
            assert self.values[task_id] == expected
            self.values[task_id] = replacement
            return False
        return super().compare_and_set(task_id, expected, replacement)


class RecordingRuns:
    def __init__(self, observations: Sequence[ChildCapabilityObservation | BaseException]) -> None:
        self.observations = list(observations)
        self.starts: list[tuple[GenerationWorkerTask, ExecutionContext]] = []
        self.resumes: list[
            tuple[GenerationWorkerTask, CapabilityContinuation, JsonValue, ExecutionContext]
        ] = []

    def _next(self) -> ChildCapabilityObservation:
        result = self.observations.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    async def start(
        self, task: GenerationWorkerTask, context: ExecutionContext
    ) -> ChildCapabilityObservation:
        self.starts.append((task, context))
        return self._next()

    async def resume(
        self,
        task: GenerationWorkerTask,
        continuation: CapabilityContinuation,
        response: JsonValue,
        context: ExecutionContext,
    ) -> ChildCapabilityObservation:
        self.resumes.append((task, continuation, response, context))
        return self._next()


def _service(
    observations: Sequence[ChildCapabilityObservation | BaseException],
    *,
    store: MemoryStore | None = None,
) -> tuple[GenerationWorkerService, MemoryStore, RecordingRuns]:
    selected_store = store or MemoryStore()
    runs = RecordingRuns(observations)
    return GenerationWorkerService(store=selected_store, isolated_runs=runs), selected_store, runs


def _run[T](awaitable: Coroutine[object, object, T]) -> T:
    return asyncio.run(awaitable)


def test_start_delegates_only_allowlisted_task_and_fresh_deterministic_child_context() -> None:
    service, _, runs = _service([_completed()])
    task = _task()
    view = _run(service.start(task, _parent()))
    assert view.status is GenerationWorkerStatus.COMPLETED
    delegated, child = runs.starts[0]
    assert delegated.capability_inputs() == task.payload
    serialized = delegated.to_bytes().decode()
    for forbidden in (
        "tutor history",
        "sibling output",
        "api_key",
        "provider",
        "messages",
        "principal_id",
        "session_id",
    ):
        assert forbidden not in serialized
    assert child.requested_capabilities == frozenset({"source.read"})
    assert child.principal_id == _parent().principal_id
    assert child.course_id == _parent().course_id
    assert child.session_id == _parent().session_id
    assert child.correlation_id != _parent().correlation_id
    assert child.idempotency_key != _parent().idempotency_key
    assert child.model_run_id is None

    repeat_service, _, repeat_runs = _service([_completed()])
    _run(repeat_service.start(task, _parent()))
    assert repeat_runs.starts[0][1] == child


def test_public_child_context_preserves_parent_identity_and_narrows_authority() -> None:
    task = _task()
    parent = _parent(grants=frozenset({"source.read", "course.read"}))

    child = generation_worker_child_context(task, parent)

    assert child == ExecutionContext(
        principal_kind=PrincipalKind.SERVICE,
        principal_id="tutor-service",
        course_id=CourseId("course-1"),
        correlation_id=CorrelationId(
            "worker-correlation-sha256:"
            "033798a80bbef02965207a56d3de9bcd4e266277fa5289be71a796c1956e38c9"
        ),
        requested_capabilities=frozenset({"source.read"}),
        session_id=SessionId("session-1"),
        model_run_id=None,
        idempotency_key=(
            "worker-child-sha256:fb4c6aa739cb5b7f849fd733496f125166ee4ed9ddf6ab4325ffb9280e5dd37f"
        ),
    )
    assert generation_worker_child_context(task, parent) == child


def test_public_child_context_identity_changes_with_task_not_parent_retry_metadata() -> None:
    task = _task()
    parent = _parent()
    changed_parent_retry = replace(
        parent,
        correlation_id=CorrelationId("another-parent-correlation"),
        idempotency_key="another-parent-retry",
    )

    original = generation_worker_child_context(task, parent)
    parent_retry = generation_worker_child_context(task, changed_parent_retry)
    other_task = generation_worker_child_context(replace(task, task_id="lesson-2:hybrid"), parent)

    assert parent_retry == original
    assert other_task.correlation_id != original.correlation_id
    assert other_task.idempotency_key != original.idempotency_key


def test_two_suspend_resume_cycles_reuse_one_child_run_and_bound_responses() -> None:
    task = _task()
    first = _continuation(task, checkpoint=SHA_C, step=1)
    second = _continuation(task, checkpoint=SHA_D, step=2)
    service, _, runs = _service(
        [
            _base_observation(GenerationWorkerStatus.SUSPENDED, continuation=first),
            _base_observation(GenerationWorkerStatus.SUSPENDED, continuation=second),
            _completed(),
        ]
    )
    initial = _run(service.start(task, _parent()))
    assert (initial.status, initial.generation) == (GenerationWorkerStatus.SUSPENDED, 0)
    next_view = _run(service.resume(task.task_id, 0, {"answer": "first"}, _parent()))
    assert (next_view.status, next_view.generation) == (GenerationWorkerStatus.SUSPENDED, 1)
    final = _run(service.resume(task.task_id, 1, {"answer": "second"}, _parent()))
    assert final.status is GenerationWorkerStatus.COMPLETED
    assert [item[0] for item in runs.resumes] == [task, task]
    assert [item[1].run_id for item in runs.resumes] == [RunId("child-run-1")] * 2
    assert [item[2] for item in runs.resumes] == [{"answer": "first"}, {"answer": "second"}]
    assert runs.starts[0][1] == runs.resumes[0][3] == runs.resumes[1][3]


def test_resume_rejects_changed_run_and_preserves_original_continuation_run() -> None:
    task = _task()
    first = _continuation(task, checkpoint=SHA_C, step=1)
    changed = replace(
        _continuation(task, checkpoint=SHA_D, step=2),
        run_id=RunId("child-run-2"),
    )
    changed_observation = replace(
        _base_observation(GenerationWorkerStatus.SUSPENDED, continuation=first),
        run_id=changed.run_id,
        continuation=changed,
    )
    service, _, runs = _service(
        [
            _base_observation(GenerationWorkerStatus.SUSPENDED, continuation=first),
            changed_observation,
        ]
    )
    _run(service.start(task, _parent()))
    view = _run(service.resume(task.task_id, 0, {"answer": "first"}, _parent()))
    assert view.status is GenerationWorkerStatus.FAILED
    assert view.failure_code == "child_run_mismatch"
    assert runs.resumes[0][0] == task
    assert runs.resumes[0][1].run_id == first.run_id


def test_continuations_and_verified_runs_cannot_contaminate_task_inputs() -> None:
    task = _task()
    contaminated = replace(
        _continuation(task, checkpoint=SHA_C, step=1),
        inputs={"query": "forged", "ambient": {"history": "stolen"}},
    )
    suspended = _base_observation(GenerationWorkerStatus.SUSPENDED, continuation=contaminated)
    service, _, _ = _service([suspended])
    view = _run(service.start(task, _parent()))
    assert view.status is GenerationWorkerStatus.FAILED
    assert view.failure_code == "child_continuation_mismatch"

    completed = _completed()
    assert completed.verified_run is not None
    contaminated_run = replace(
        completed.verified_run,
        inputs={"query": "forged", "scope": "sibling"},
    )
    run_service, _, _ = _service([replace(completed, verified_run=contaminated_run)])
    run_view = _run(run_service.start(task, _parent()))
    assert run_view.status is GenerationWorkerStatus.FAILED
    assert run_view.failure_code == "child_validation_provenance_invalid"


def test_canonical_stored_continuation_rejects_contaminated_inputs() -> None:
    task = _task()
    continuation = _continuation(task, checkpoint=SHA_C, step=1)
    service, store, _ = _service(
        [_base_observation(GenerationWorkerStatus.SUSPENDED, continuation=continuation)]
    )
    _run(service.start(task, _parent()))
    state = json.loads(store.values[task.task_id])
    state["continuation"]["inputs"] = {"query": "forged", "history": "ambient"}
    store.values[task.task_id] = canonical_json_bytes(state)
    with pytest.raises(ValueError, match="stored continuation"):
        _run(service.start(task, _parent()))


def test_claimed_response_recovers_after_crash_and_rejects_different_response() -> None:
    task = _task()
    continuation = _continuation(task, checkpoint=SHA_C, step=1)
    service, _, runs = _service(
        [
            _base_observation(GenerationWorkerStatus.SUSPENDED, continuation=continuation),
            RuntimeError("crash after durable claim"),
            _completed(),
        ]
    )
    _run(service.start(task, _parent()))
    with pytest.raises(RuntimeError, match="crash"):
        _run(service.resume(task.task_id, 0, {"answer": "stored"}, _parent()))
    with pytest.raises(GenerationWorkerConflictError, match="different response"):
        _run(service.resume(task.task_id, 0, {"answer": "forged"}, _parent()))
    recovered = _run(service.resume(task.task_id, 0, {"answer": "stored"}, _parent()))
    assert recovered.status is GenerationWorkerStatus.COMPLETED
    assert [call[0] for call in runs.resumes] == [task, task]
    assert [call[2] for call in runs.resumes] == [{"answer": "stored"}] * 2


def test_terminal_retry_and_detail_do_not_delegate_and_are_authority_scoped() -> None:
    service, _, runs = _service([_completed()])
    task = _task()
    first = _run(service.start(task, _parent()))
    retry = _run(service.start(task, _parent()))
    resumed = _run(service.resume(task.task_id, 0, {"ignored": True}, _parent()))
    assert retry == first == resumed
    assert len(runs.starts) == 1
    assert runs.resumes == []
    detail = service.detail(task.task_id, _parent())
    assert detail.output == OUTPUT
    assert detail.receipt.prompt_fingerprint == SHA_B
    assert not hasattr(detail, "model_metadata")
    with pytest.raises(GenerationWorkerConflictError, match="authority changed"):
        service.detail(task.task_id, _parent(principal="another-service"))


def test_changed_task_pins_and_missing_or_changed_authority_fail_without_delegation() -> None:
    task = _task()
    continuation = _continuation(task, checkpoint=SHA_C, step=1)
    service, _, runs = _service(
        [_base_observation(GenerationWorkerStatus.SUSPENDED, continuation=continuation)]
    )
    _run(service.start(task, _parent()))
    with pytest.raises(GenerationWorkerConflictError, match="task bytes or pins changed"):
        _run(service.start(replace(task, language="en"), _parent()))
    changed_pins = replace(task.pins, model_adapter=ArtifactReference("other-adapter", V1))
    with pytest.raises(GenerationWorkerConflictError, match="task bytes or pins changed"):
        _run(service.start(replace(task, pins=changed_pins), _parent()))
    with pytest.raises(GenerationWorkerConflictError, match="lacks worker-required authority"):
        _run(service.start(_task(task_id="other"), _parent(grants=frozenset())))
    with pytest.raises(GenerationWorkerConflictError, match="authority changed"):
        _run(service.resume(task.task_id, 0, {"answer": "x"}, _parent(principal="other")))
    assert len(runs.starts) == 1


def test_direct_pending_completion_failure_and_running_observation_are_sanitized() -> None:
    complete_service, _, _ = _service([_completed()])
    complete = _run(complete_service.start(_task(), _parent()))
    assert complete.verified_detail_available
    assert not hasattr(complete, "output")

    failed = _base_observation(GenerationWorkerStatus.FAILED, failure_code="safe_failure")
    failed_service, _, _ = _service([failed])
    failed_view = _run(failed_service.start(_task(), _parent()))
    assert failed_view.status is GenerationWorkerStatus.FAILED
    assert failed_view.failure_code == "safe_failure"
    assert not failed_view.verified_detail_available
    with pytest.raises(GenerationWorkerConflictError, match="detail is unavailable"):
        failed_service.detail(_task().task_id, _parent())

    running_service, _, runs = _service(
        [_base_observation(GenerationWorkerStatus.RUNNING), _completed()]
    )
    running = _run(running_service.start(_task(), _parent()))
    assert running.status is GenerationWorkerStatus.RUNNING
    assert running.child_run_id == RunId("child-run-1")
    completed = _run(running_service.start(_task(), _parent()))
    assert completed.status is GenerationWorkerStatus.COMPLETED
    assert len(runs.starts) == 2


def test_child_private_machine_failure_code_collapses_before_compact_view() -> None:
    private = _base_observation(GenerationWorkerStatus.FAILED, failure_code="openai_overloaded")
    service, _, _ = _service([private])
    view = _run(service.start(_task(), _parent()))
    assert view.status is GenerationWorkerStatus.FAILED
    assert view.failure_code == "failed"
    assert "openai" not in repr(view)


@pytest.mark.parametrize(
    ("status", "failure"),
    (
        (GenerationWorkerStatus.FAILED, "failed"),
        (GenerationWorkerStatus.CANCELLED, "cancelled"),
        (GenerationWorkerStatus.STALE, "stale_generation"),
    ),
)
def test_non_success_observations_never_expose_detail(
    status: GenerationWorkerStatus, failure: str
) -> None:
    service, _, _ = _service([_base_observation(status, failure_code=failure)])
    view = _run(service.start(_task(), _parent()))
    assert view.status is status
    assert view.failure_code == failure
    assert not view.verified_detail_available
    with pytest.raises(GenerationWorkerConflictError):
        service.detail(_task().task_id, _parent())


@pytest.mark.parametrize(
    ("change", "failure"),
    (
        ({"manifest_fingerprint": SHA_D}, "child_binding_mismatch"),
        ({"definition_fingerprint": SHA_D}, "child_binding_mismatch"),
        ({"output_schema_fingerprint": SHA_D}, "child_binding_mismatch"),
        (
            {
                "prompt": VerifiedPromptReceipt("forged-prompt", "1.0.0", SHA_B),
            },
            "child_prompt_provenance_invalid",
        ),
        (
            {
                "validations": tuple(reversed(_observed_validations())),
            },
            "child_validation_provenance_invalid",
        ),
        (
            {
                "validations": (
                    replace(_observed_validations()[0], passed=False),
                    _observed_validations()[1],
                )
            },
            "child_validation_provenance_invalid",
        ),
    ),
)
def test_tampered_provenance_fails_closed_before_detail(
    change: dict[str, object], failure: str
) -> None:
    service, _, _ = _service([_completed(**change)])
    view = _run(service.start(_task(), _parent()))
    assert view.status is GenerationWorkerStatus.FAILED
    assert view.failure_code == failure
    with pytest.raises(GenerationWorkerConflictError):
        service.detail(_task().task_id, _parent())


def test_completion_rejects_terminating_validation_disposition() -> None:
    invalid = (
        replace(_observed_validations()[0], disposition=ValidatorDisposition.TERMINATE),
        _observed_validations()[1],
    )
    service, _, _ = _service([_completed(validations=invalid)])
    view = _run(service.start(_task(), _parent()))
    assert view.status is GenerationWorkerStatus.FAILED
    assert view.failure_code == "child_validation_provenance_invalid"


@pytest.mark.parametrize(
    ("field", "forged"),
    (
        ("task_id", "other-task"),
        ("pins_fingerprint", SHA_D),
        ("input_fingerprint", SHA_D),
        ("child_run_id", "other-run"),
        ("output_fingerprint", SHA_D),
        ("validator_fingerprint", SHA_D),
        ("run_fingerprint", SHA_D),
        ("prompt_fingerprint", SHA_D),
    ),
)
def test_canonical_terminal_state_rejects_forged_receipt_bindings(field: str, forged: str) -> None:
    service, store, _ = _service([_completed()])
    task = _task()
    _run(service.start(task, _parent()))
    state = json.loads(store.values[task.task_id])
    state["receipt"][field] = forged
    receipt = GenerationWorkerReceipt.from_json(state["receipt"])
    state["receipt_fingerprint"] = receipt.fingerprint
    store.values[task.task_id] = canonical_json_bytes(state)
    with pytest.raises(ValueError, match=r"stored (receipt|verified output)"):
        _run(service.start(task, _parent()))


def test_identical_cas_winner_is_observed_without_duplicate_effect() -> None:
    store = WinningRaceStore()
    service, _, runs = _service([_completed()], store=store)
    view = _run(service.start(_task(), _parent()))
    assert view.status is GenerationWorkerStatus.COMPLETED
    assert len(runs.starts) == 1


def test_stale_resume_generation_is_rejected_after_next_suspension() -> None:
    task = _task()
    first = _continuation(task, checkpoint=SHA_C, step=1)
    second = _continuation(task, checkpoint=SHA_D, step=2)
    service, _, runs = _service(
        [
            _base_observation(GenerationWorkerStatus.SUSPENDED, continuation=first),
            _base_observation(GenerationWorkerStatus.SUSPENDED, continuation=second),
        ]
    )
    _run(service.start(task, _parent()))
    _run(service.resume(task.task_id, 0, {"answer": "generation-zero"}, _parent()))
    with pytest.raises(GenerationWorkerConflictError, match="generation"):
        _run(service.resume(task.task_id, 0, {"answer": "generation-zero"}, _parent()))
    assert len(runs.resumes) == 1


@pytest.mark.parametrize(
    "code",
    [
        "gateway_authentication",
        "gateway_authorization",
        "gateway_model_unavailable",
        "gateway_endpoint_incompatible",
        "gateway_schema_incompatible",
        "gateway_rate_limited",
        "gateway_timeout",
        "gateway_protocol_error",
        "gateway_unavailable",
    ],
)
def test_typed_gateway_failure_survives_compact_worker_sanitization(code: str) -> None:
    observation = _base_observation(GenerationWorkerStatus.FAILED, failure_code=code)
    service, _, _ = _service([observation])
    view = _run(service.start(_task(), _parent()))
    assert view.failure_code == code
    assert not view.verified_detail_available
