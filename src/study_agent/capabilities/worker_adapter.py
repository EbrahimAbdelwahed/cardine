"""Gateway-backed adapter for isolated generation workers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from study_agent.domain import ExecutionContext, RunId
from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.pedagogy import ProfileSelectionReceipt
from study_agent.playbooks import (
    ModelStep,
    PlaybookRunStatus,
    StepTraceStatus,
    ToolStep,
    ValidateStep,
    ValidatorDisposition,
    VerifiedRunRecord,
    playbook_definition_fingerprint,
)
from study_agent.workers.contracts import (
    ChildCapabilityObservation,
    GenerationWorkerReceipt,
    GenerationWorkerStatus,
    GenerationWorkerTask,
    ObservedValidationReceipt,
    ValidationReceiptSource,
    VerifiedPromptReceipt,
    fingerprint_execution_inputs,
    fingerprint_output,
    fingerprint_run,
    fingerprint_validations,
)
from study_agent.workers.proof import (
    TechnicalModelReceipt,
    VerifiedChildExecutionProof,
    VerifiedChildProofOwner,
    VerifiedToolOutput,
    verified_child_value_fingerprint,
)

from .bindings import (
    CapabilityBinding,
    ProfiledCapabilityBinding,
    profiled_execution_inputs,
)
from .contracts import (
    CancelledCapabilityOutcome,
    CapabilityContinuation,
    CapabilityGatewayError,
    CapabilityGatewayErrorCode,
    CapabilityOutcome,
    CompletedCapabilityOutcome,
    FailedCapabilityOutcome,
    StaleCapabilityOutcome,
    SuspendedCapabilityOutcome,
    TerminatedCapabilityOutcome,
)
from .gateway import StudyCapabilityGateway


@dataclass(frozen=True, slots=True)
class ProfiledWorkerExecutionDescriptor:
    """Request-scoped trusted inputs for one profiled worker execution."""

    binding: ProfiledCapabilityBinding
    selection_receipt: ProfileSelectionReceipt
    profile_expectation_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.binding, ProfiledCapabilityBinding):
            raise TypeError("profiled worker binding is invalid")
        if not isinstance(self.selection_receipt, ProfileSelectionReceipt):
            raise TypeError("profiled worker selection receipt is invalid")
        if self.selection_receipt.profile != self.binding.profile:
            raise ValueError("profiled worker receipt differs from binding profile")
        value = self.profile_expectation_fingerprint
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("profile expectation fingerprint must be lowercase sha256")

    def execution_inputs(self, task: GenerationWorkerTask) -> JsonObject:
        reference = f"profile-sha256:{self.profile_expectation_fingerprint}"
        if reference not in task.index_references:
            raise ValueError("worker task does not commit the profile expectation")
        return profiled_execution_inputs(task.capability_inputs(), self.selection_receipt)


class GatewayIsolatedCapabilityRunAdapter:
    """Drive one trusted capability binding and preserve its sanitized proof."""

    def __init__(
        self,
        *,
        gateway: StudyCapabilityGateway,
        bindings: tuple[CapabilityBinding | ProfiledWorkerExecutionDescriptor, ...],
        proof_owner: VerifiedChildProofOwner,
    ) -> None:
        if not isinstance(gateway, StudyCapabilityGateway):
            raise TypeError("worker adapter gateway must be StudyCapabilityGateway")
        values = tuple(bindings)
        if not values or not all(
            isinstance(item, (CapabilityBinding, ProfiledWorkerExecutionDescriptor))
            for item in values
        ):
            raise ValueError("worker adapter requires trusted capability bindings")
        identities = tuple(
            (
                _descriptor_binding(item).manifest.id,
                _descriptor_binding(item).manifest.version,
                _descriptor_binding(item).manifest_fingerprint,
                playbook_definition_fingerprint(_descriptor_binding(item).playbook),
            )
            for item in values
        )
        if len(set(identities)) != len(values):
            raise ValueError("worker adapter bindings must be pairwise unique")
        if not isinstance(proof_owner, VerifiedChildProofOwner):
            raise TypeError("worker adapter proof owner is invalid")
        self._gateway = gateway
        self._bindings = values
        self._proof_owner = proof_owner

    async def start(
        self, task: GenerationWorkerTask, context: ExecutionContext
    ) -> ChildCapabilityObservation:
        binding, execution_inputs = self._selection(task)
        try:
            outcome = await self._gateway._start_bound(
                binding,
                task.capability_inputs(),
                execution_inputs,
                context,
            )
        except CapabilityGatewayError as error:
            return self._gateway_failure(task, None, error, execution_inputs)
        return self._observe(task, binding, outcome, context, execution_inputs)

    async def resume(
        self,
        task: GenerationWorkerTask,
        continuation: CapabilityContinuation,
        response: JsonValue,
        context: ExecutionContext,
    ) -> ChildCapabilityObservation:
        binding, execution_inputs = self._selection(task)
        self._require_continuation(task, continuation, execution_inputs)
        try:
            outcome = await self._gateway._resume_bound(binding, continuation, response, context)
        except CapabilityGatewayError as error:
            return self._gateway_failure(task, continuation.run_id, error, execution_inputs)
        return self._observe(task, binding, outcome, context, execution_inputs)

    def _selection(
        self, task: GenerationWorkerTask
    ) -> tuple[CapabilityBinding | ProfiledCapabilityBinding, JsonObject]:
        if not isinstance(task, GenerationWorkerTask):
            raise TypeError("worker task must be GenerationWorkerTask")
        matches = tuple(
            item
            for item in self._bindings
            if _descriptor_binding(item).manifest.id is task.capability_id
            and _descriptor_binding(item).manifest.version == task.capability_version
            and _descriptor_binding(item).manifest_fingerprint == task.manifest_fingerprint
            and _descriptor_binding(item).pins == task.pins
            and playbook_definition_fingerprint(_descriptor_binding(item).playbook)
            == task.definition_fingerprint
            and _descriptor_binding(item).manifest.output_schema == task.output_schema
            and _descriptor_binding(item).manifest.required_authority == task.required_authority
        )
        if len(matches) != 1:
            raise ValueError("worker task does not select one exact trusted binding")
        selected = matches[0]
        binding = _descriptor_binding(selected)
        execution_inputs = (
            selected.execution_inputs(task)
            if isinstance(selected, ProfiledWorkerExecutionDescriptor)
            else task.capability_inputs()
        )
        return binding, execution_inputs

    @staticmethod
    def _require_continuation(
        task: GenerationWorkerTask,
        continuation: CapabilityContinuation,
        execution_inputs: JsonObject,
    ) -> None:
        if not isinstance(continuation, CapabilityContinuation):
            raise TypeError("worker continuation is invalid")
        if (
            continuation.capability_id is not task.capability_id
            or continuation.capability_version != task.capability_version
            or continuation.manifest_fingerprint != task.manifest_fingerprint
            or continuation.definition_fingerprint != task.definition_fingerprint
            or continuation.pins != task.pins
            or continuation.inputs != execution_inputs
        ):
            raise ValueError("worker task and continuation bindings differ")

    def _observe(
        self,
        task: GenerationWorkerTask,
        binding: CapabilityBinding | ProfiledCapabilityBinding,
        outcome: CapabilityOutcome,
        context: ExecutionContext,
        execution_inputs: JsonObject,
    ) -> ChildCapabilityObservation:
        if isinstance(outcome, SuspendedCapabilityOutcome):
            return _base(
                task,
                GenerationWorkerStatus.SUSPENDED,
                outcome.run_id,
                continuation=outcome.continuation,
                execution_inputs=execution_inputs,
            )
        if isinstance(outcome, CancelledCapabilityOutcome):
            return _base(
                task,
                GenerationWorkerStatus.CANCELLED,
                outcome.run_id,
                failure_code="gateway_cancelled",
                execution_inputs=execution_inputs,
            )
        if isinstance(outcome, StaleCapabilityOutcome):
            return _base(
                task,
                GenerationWorkerStatus.STALE,
                outcome.run_id,
                failure_code="gateway_stale",
                execution_inputs=execution_inputs,
            )
        if isinstance(outcome, FailedCapabilityOutcome):
            return _base(
                task,
                GenerationWorkerStatus.FAILED,
                outcome.run_id,
                failure_code=(
                    f"gateway_{outcome.failure_reason}"
                    if outcome.failure_reason is not None
                    else "gateway_failed"
                ),
                execution_inputs=execution_inputs,
            )
        if isinstance(outcome, TerminatedCapabilityOutcome):
            return _base(
                task,
                GenerationWorkerStatus.TERMINATED,
                outcome.run.run_id,
                verified_run=outcome.run,
                failure_code="gateway_terminated",
                execution_inputs=execution_inputs,
            )
        if not isinstance(outcome, CompletedCapabilityOutcome):
            raise TypeError("gateway returned an unknown capability outcome")
        try:
            observation, _ = _completed_observation(task, binding, outcome, execution_inputs)
            receipt = _expected_completed_receipt(task, observation)
            self._proof_owner.create(
                task,
                receipt,
                outcome.run,
                binding.playbook,
                outcome.output,
                context,
                execution_inputs,
            )
            return observation
        except Exception:
            return _base(
                task,
                GenerationWorkerStatus.FAILED,
                outcome.run.run_id,
                failure_code="child_proof_invalid",
                execution_inputs=execution_inputs,
            )

    @staticmethod
    def _gateway_failure(
        task: GenerationWorkerTask,
        run_id: RunId | None,
        error: CapabilityGatewayError,
        execution_inputs: JsonObject,
    ) -> ChildCapabilityObservation:
        selected = run_id or _failed_run_id(task)
        if error.code is CapabilityGatewayErrorCode.IN_PROGRESS and error.retryable:
            return _base(
                task,
                GenerationWorkerStatus.RUNNING,
                selected,
                execution_inputs=execution_inputs,
            )
        return _base(
            task,
            GenerationWorkerStatus.FAILED,
            selected,
            failure_code=f"gateway_{error.code.value}",
            execution_inputs=execution_inputs,
        )


def _completed_observation(
    task: GenerationWorkerTask,
    binding: CapabilityBinding | ProfiledCapabilityBinding,
    outcome: CompletedCapabilityOutcome,
    execution_inputs: JsonObject,
) -> tuple[ChildCapabilityObservation, VerifiedChildExecutionProof]:
    run = outcome.run
    if (
        run.status is not PlaybookRunStatus.COMPLETED
        or run.run_id != outcome.run.run_id
        or run.definition_fingerprint != task.definition_fingerprint
        or run.pins != task.pins
        or run.inputs != execution_inputs
    ):
        raise ValueError("verified gateway run differs from worker task")
    steps = {step.id: step for step in binding.playbook.steps}
    tools: list[VerifiedToolOutput] = []
    validations: list[ObservedValidationReceipt] = []
    models: list[TechnicalModelReceipt] = []
    prompts: list[VerifiedPromptReceipt] = []
    for trace in run.traces:
        if trace.status is not StepTraceStatus.COMPLETED:
            continue
        step = steps.get(trace.step_id)
        if step is None or trace.step_kind != step.kind:
            raise ValueError("verified trace does not match bound playbook")
        if isinstance(step, ToolStep):
            value = run.outputs[step.output_key]
            tools.append(
                VerifiedToolOutput(
                    step.id,
                    step.output_key,
                    step.tool.id,
                    str(step.tool.version),
                    value,
                    verified_child_value_fingerprint(value),
                )
            )
        elif isinstance(step, ModelStep):
            models.append(_technical_model(trace.details))
            prompt = _prompt(trace.details)
            if prompt is not None:
                prompts.append(prompt)
            validations.extend(_fallback_validations(step.id, trace.details))
        elif isinstance(step, ValidateStep):
            validations.append(_validation(step, trace.details))
    if len(models) != 1 or len(prompts) != 1:
        raise ValueError("completed child requires one model and prompt receipt")
    observed = tuple(validations)
    expected = tuple(
        (item.step_id, item.source, item.validator_id, item.validator_version)
        for item in task.expected_validations
    )
    actual = tuple(
        (item.step_id, item.source, item.validator_id, item.validator_version) for item in observed
    )
    if actual != expected or not all(
        item.passed and item.disposition is ValidatorDisposition.CONTINUE for item in observed
    ):
        raise ValueError("verified validation provenance differs from worker task")
    prompt = prompts[0]
    observation = ChildCapabilityObservation(
        GenerationWorkerStatus.COMPLETED,
        task.capability_id,
        task.capability_version,
        task.manifest_fingerprint,
        run.run_id,
        task.pins,
        task.definition_fingerprint,
        task.output_schema_fingerprint,
        observed,
        prompt,
        verified_run=run,
        output=outcome.output,
        execution_input_fingerprint=fingerprint_execution_inputs(execution_inputs),
    )
    proof = VerifiedChildExecutionProof(
        run.run_id,
        GenerationWorkerStatus.COMPLETED,
        run.definition_fingerprint,
        run.pins,
        task.payload_fingerprint,
        outcome.output,
        fingerprint_output(outcome.output),
        run.read_dependencies,
        tuple(tools),
        models[0],
        prompt,
        observed,
        (
            task.payload_fingerprint
            if execution_inputs == task.capability_inputs()
            else fingerprint_execution_inputs(execution_inputs)
        ),
    )
    return observation, proof


def _technical_model(details: Mapping[str, JsonValue]) -> TechnicalModelReceipt:
    invocation = _mapping(details.get("model_invocation"), "model invocation")
    if set(invocation) != {"adapter_id", "adapter_version", "model_id", "response_id"}:
        raise ValueError("model invocation fields are not exact")
    usage_value = details.get("model_usage")
    input_tokens: int | None = None
    output_tokens: int | None = None
    if usage_value is not None:
        usage = _mapping(usage_value, "model usage")
        if set(usage) != {"input_tokens", "output_tokens"}:
            raise ValueError("model usage fields are not exact")
        input_tokens = _integer(usage["input_tokens"], "input_tokens")
        output_tokens = _integer(usage["output_tokens"], "output_tokens")
    response = invocation["response_id"]
    if response is not None and not isinstance(response, str):
        raise ValueError("model response id must be text or null")
    return TechnicalModelReceipt(
        _string(invocation, "adapter_id"),
        _string(invocation, "adapter_version"),
        _string(invocation, "model_id"),
        response,
        input_tokens,
        output_tokens,
    )


def _prompt(details: Mapping[str, JsonValue]) -> VerifiedPromptReceipt | None:
    value = details.get("prompt")
    if value is None:
        return None
    prompt = _mapping(value, "prompt receipt")
    if set(prompt) != {"id", "version", "fingerprint", "layers"}:
        raise ValueError("prompt receipt fields are not exact")
    layers_value = prompt["layers"]
    if not isinstance(layers_value, tuple):
        raise ValueError("prompt layers must be an array")
    layers: list[str] = []
    for item in layers_value:
        layer = _mapping(item, "prompt layer")
        if set(layer) != {"id", "version", "kind", "input_fingerprint"}:
            raise ValueError("prompt layer fields are not exact")
        layers.append(_string(layer, "input_fingerprint"))
    return VerifiedPromptReceipt(
        _string(prompt, "id"),
        _string(prompt, "version"),
        _string(prompt, "fingerprint"),
        tuple(layers),
    )


def _fallback_validations(
    step_id: str, details: Mapping[str, JsonValue]
) -> tuple[ObservedValidationReceipt, ...]:
    value = details.get("fallback_validators", ())
    if not isinstance(value, tuple):
        raise ValueError("fallback validator receipts must be an array")
    return tuple(
        _validation_receipt(
            step_id,
            ValidationReceiptSource.STRUCTURED_OUTPUT_FALLBACK,
            _mapping(item, "fallback validation receipt"),
        )
        for item in value
    )


def _validation(step: ValidateStep, details: Mapping[str, JsonValue]) -> ObservedValidationReceipt:
    receipt = _mapping(details.get("validator"), "validation receipt")
    observed = _validation_receipt(step.id, ValidationReceiptSource.VALIDATE_STEP, receipt)
    if observed.validator_id != step.validator.id or observed.validator_version != str(
        step.validator.version
    ):
        raise ValueError("validation receipt differs from declared step")
    return observed


def _validation_receipt(
    step_id: str,
    source: ValidationReceiptSource,
    receipt: Mapping[str, JsonValue],
) -> ObservedValidationReceipt:
    allowed = {
        "validator_id",
        "validator_version",
        "passed",
        "disposition",
        "result_fingerprint",
        "reason",
    }
    if set(receipt) not in (allowed, {*allowed, "result"}):
        raise ValueError("validation receipt fields are not exact")
    passed = receipt["passed"]
    if type(passed) is not bool:
        raise ValueError("validation passed must be boolean")
    return ObservedValidationReceipt(
        step_id,
        source,
        _string(receipt, "validator_id"),
        _string(receipt, "validator_version"),
        passed,
        _string(receipt, "result_fingerprint"),
        ValidatorDisposition(_string(receipt, "disposition")),
    )


def _expected_completed_receipt(
    task: GenerationWorkerTask, observation: ChildCapabilityObservation
) -> GenerationWorkerReceipt:
    assert observation.prompt is not None
    return GenerationWorkerReceipt(
        task.task_id,
        task.task_kind,
        GenerationWorkerStatus.COMPLETED,
        observation.run_id,
        task.fingerprint,
        task.pins_fingerprint,
        task.payload_fingerprint,
        fingerprint_output(observation.output),
        fingerprint_validations(observation.validations),
        fingerprint_run(observation),
        observation.prompt.composition_fingerprint,
    )


def _base(
    task: GenerationWorkerTask,
    status: GenerationWorkerStatus,
    run_id: RunId,
    *,
    continuation: CapabilityContinuation | None = None,
    verified_run: VerifiedRunRecord | None = None,
    failure_code: str | None = None,
    execution_inputs: JsonObject | None = None,
) -> ChildCapabilityObservation:
    return ChildCapabilityObservation(
        status,
        task.capability_id,
        task.capability_version,
        task.manifest_fingerprint,
        run_id,
        task.pins,
        task.definition_fingerprint,
        task.output_schema_fingerprint,
        continuation=continuation,
        verified_run=verified_run,
        failure_code=failure_code,
        execution_input_fingerprint=fingerprint_execution_inputs(
            execution_inputs if execution_inputs is not None else task.capability_inputs()
        ),
    )


def _descriptor_binding(
    value: CapabilityBinding | ProfiledWorkerExecutionDescriptor,
) -> CapabilityBinding | ProfiledCapabilityBinding:
    return value.binding if isinstance(value, ProfiledWorkerExecutionDescriptor) else value


def _failed_run_id(task: GenerationWorkerTask) -> RunId:
    return RunId(f"worker-failed:{task.fingerprint}")


def _mapping(value: JsonValue | None, name: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _string(value: Mapping[str, JsonValue], key: str) -> str:
    item = value[key]
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{key} must be text")
    return item


def _integer(value: JsonValue, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value
