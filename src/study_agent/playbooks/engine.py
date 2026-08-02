from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from hashlib import sha256
from typing import Any, NoReturn, cast

from study_agent.domain._validation import (
    JsonObject,
    JsonValue,
    freeze_json,
    freeze_object,
)
from study_agent.domain.identifiers import RunId
from study_agent.ports import (
    ClockPort,
    ModelError,
    ModelErrorCode,
    ModelFinishReason,
    ModelPort,
    RunStore,
    StructuredOutputConstraint,
)
from study_agent.skills import (
    ArtifactReference,
    CapabilityFallback,
    NegotiationStatus,
    PromptLayer,
    SemanticVersion,
    SkillPackage,
    model_capability_names,
    negotiate_capabilities,
)

from .contracts import (
    DataBinding,
    DataReference,
    DataSourceKind,
    DialogueStep,
    ModelStep,
    PlaybookCheckpoint,
    PlaybookDefinition,
    PlaybookStep,
    ReadDependency,
    RunStatus,
    StepTrace,
    StepTraceStatus,
    ToolBehaviorPin,
    ToolStep,
    ValidateStep,
    ValidationOutcome,
    ValidatorDisposition,
    VersionPins,
)
from .runtime import (
    STRUCTURED_OUTPUT_JSON_FALLBACK,
    SUPPORTED_FALLBACK_STRATEGIES,
    CancelledRunResult,
    CompletedRunResult,
    EngineErrorCode,
    EngineFailure,
    FailedRunResult,
    InspectedRunRecord,
    PlaybookEngineError,
    PlaybookRunResult,
    PlaybookRunStatus,
    RuntimeRegistries,
    SuspendedRunResult,
    TerminatedRunResult,
    VerifiedRunRecord,
)

_CHECKPOINT_SCHEMA_VERSION = 1
_SAFE_MODEL_FAILURE_REASONS = frozenset(
    {
        ModelErrorCode.AUTHENTICATION.value,
        ModelErrorCode.MODEL_UNAVAILABLE.value,
        ModelErrorCode.ENDPOINT_INCOMPATIBLE.value,
        ModelErrorCode.RATE_LIMITED.value,
        ModelErrorCode.TIMEOUT.value,
        ModelErrorCode.PROTOCOL_ERROR.value,
        ModelErrorCode.UNAVAILABLE.value,
    }
)


class PlaybookEngine:
    def __init__(
        self,
        *,
        engine_version: SemanticVersion,
        model_adapter: ArtifactReference,
        state_contract: ArtifactReference,
        model: ModelPort,
        registries: RuntimeRegistries,
        run_store: RunStore,
        clock: ClockPort,
    ) -> None:
        self._engine_version = engine_version
        self._model_adapter = model_adapter
        self._state_contract = state_contract
        self._model = model
        self._tools = {executor.name: executor for executor in registries.tools}
        self._validators = {executor.id: executor for executor in registries.validators}
        self._prompt_composers = {
            (registration.prompt.id, str(registration.prompt.version)): registration.composer
            for registration in registries.prompt_composers
        }
        self._run_store = run_store
        self._clock = clock

    async def execute(
        self,
        *,
        run_id: RunId,
        skill: SkillPackage,
        definition: PlaybookDefinition,
        inputs: JsonObject,
        pins: VersionPins,
        read_dependencies: tuple[ReadDependency, ...] = (),
    ) -> PlaybookRunResult:
        frozen_inputs = freeze_object(inputs)
        dependencies = tuple(read_dependencies)
        activated_fallbacks = self._preflight(
            skill, definition, pins, dependencies
        )
        if set(frozen_inputs) != set(definition.input_keys):
            self._raise(
                EngineErrorCode.INVALID_INPUT,
                "run inputs must exactly match declared playbook inputs",
            )
        initial = _StoredRun(
            self._checkpoint(run_id, pins, RunStatus.RUNNING, 0, {}, dependencies),
            (),
            frozen_inputs,
            playbook_definition_fingerprint(definition),
        )
        expected = _encode_stored_run(initial)
        try:
            created = self._run_store.create(run_id, expected)
        except Exception as error:
            self._raise(
                EngineErrorCode.RUN_STORE_ERROR,
                f"run creation failed: {type(error).__name__}",
            )
        if not created:
            self._raise(EngineErrorCode.DUPLICATE_RUN, "run id already exists")
        return await self._run(
            run_id=run_id,
            definition=definition,
            pins=pins,
            run_inputs=frozen_inputs,
            outputs={},
            read_dependencies=dependencies,
            start_index=0,
            traces=(),
            expected_payload=expected,
            activated_fallbacks=activated_fallbacks,
            prompt_layers=skill.prompt_layers,
        )

    async def resume(
        self,
        *,
        run_id: RunId,
        skill: SkillPackage,
        definition: PlaybookDefinition,
        inputs: JsonObject,
        pins: VersionPins,
        read_dependencies: tuple[ReadDependency, ...],
        resume_input: JsonValue,
    ) -> PlaybookRunResult:
        frozen_inputs = freeze_object(inputs)
        dependencies = tuple(read_dependencies)
        activated_fallbacks = self._preflight(
            skill, definition, pins, dependencies
        )
        stored, suspended_payload = self._load(run_id, definition)
        checkpoint = stored.checkpoint
        if checkpoint.status is not RunStatus.SUSPENDED:
            self._raise(EngineErrorCode.INCOMPATIBLE_CHECKPOINT, "checkpoint is not suspended")
        if _pins_payload(checkpoint.pins) != _pins_payload(pins):
            self._raise(EngineErrorCode.INCOMPATIBLE_CHECKPOINT, "checkpoint pins changed")
        if checkpoint.read_dependencies != dependencies:
            self._raise(
                EngineErrorCode.STALE_READ_DEPENDENCY,
                "declared read dependencies changed since suspension",
            )
        if set(frozen_inputs) != set(definition.input_keys):
            self._raise(
                EngineErrorCode.INVALID_INPUT,
                "run inputs must exactly match declared playbook inputs",
            )
        if frozen_inputs != stored.run_inputs:
            self._raise(
                EngineErrorCode.INCOMPATIBLE_CHECKPOINT,
                "run inputs changed since suspension",
            )
        dialogue_index = checkpoint.next_step_index - 1
        if dialogue_index < 0 or dialogue_index >= len(definition.steps):
            self._raise(EngineErrorCode.INCOMPATIBLE_CHECKPOINT, "invalid resume step index")
        dialogue = definition.steps[dialogue_index]
        if not isinstance(dialogue, DialogueStep):
            self._raise(
                EngineErrorCode.INCOMPATIBLE_CHECKPOINT,
                "resume checkpoint does not follow a dialogue step",
            )
        frozen_resume = freeze_json(resume_input)
        _validate_schema(dialogue.response_schema.value, frozen_resume, "dialogue response")
        outputs = dict(checkpoint.outputs)
        outputs[dialogue.output_key] = frozen_resume
        resume_details: dict[str, JsonValue] = {
            "output_fingerprint": _json_fingerprint(frozen_resume),
            "resume_generation_fingerprint": _checkpoint_fingerprint(
                suspended_payload
            ),
            "resume_generation_updated_at": checkpoint.updated_at.isoformat(),
        }
        claimed_traces = (
            *stored.traces,
            self._trace(
                dialogue,
                StepTraceStatus.COMPLETED,
                freeze_object(resume_details),
            ),
        )
        claimed = _StoredRun(
            self._checkpoint(
                run_id,
                pins,
                RunStatus.RUNNING,
                checkpoint.next_step_index,
                outputs,
                dependencies,
            ),
            claimed_traces,
            frozen_inputs,
            stored.definition_fingerprint,
        )
        claimed_payload = _encode_stored_run(claimed)
        if not self._compare_and_set(run_id, suspended_payload, claimed_payload):
            self._raise(
                EngineErrorCode.INCOMPATIBLE_CHECKPOINT,
                "suspended run was already claimed",
            )
        return await self._run(
            run_id=run_id,
            definition=definition,
            pins=pins,
            run_inputs=frozen_inputs,
            outputs=outputs,
            read_dependencies=dependencies,
            start_index=checkpoint.next_step_index,
            traces=claimed_traces,
            expected_payload=claimed_payload,
            activated_fallbacks=activated_fallbacks,
            prompt_layers=skill.prompt_layers,
        )

    def recover(
        self,
        *,
        run_id: RunId,
        definition: PlaybookDefinition,
        inputs: JsonObject,
        pins: VersionPins,
        read_dependencies: tuple[ReadDependency, ...] = (),
    ) -> VerifiedRunRecord:
        """Verify persisted success without re-executing any playbook step."""

        stored, _, _, _ = self._inspect_expected(
            run_id=run_id,
            definition=definition,
            inputs=inputs,
            pins=pins,
            read_dependencies=read_dependencies,
        )
        self._validate_recovered_validator_registrations(definition, stored.traces)
        checkpoint = stored.checkpoint
        if checkpoint.status is not RunStatus.COMPLETED:
            self._raise(
                EngineErrorCode.INCOMPATIBLE_CHECKPOINT,
                "only successful completed checkpoints are recoverable",
            )

        termination = _recovered_termination(definition, checkpoint, stored.traces)
        if termination is None and checkpoint.next_step_index != len(definition.steps):
            self._raise(
                EngineErrorCode.INCOMPATIBLE_CHECKPOINT,
                "incomplete checkpoint has no successful deterministic termination",
            )
        status = (
            PlaybookRunStatus.TERMINATED
            if termination is not None
            else PlaybookRunStatus.COMPLETED
        )
        return VerifiedRunRecord(
            run_id,
            stored.definition_fingerprint,
            stored.run_inputs,
            checkpoint.pins,
            checkpoint.read_dependencies,
            checkpoint.outputs,
            stored.traces,
            status,
            termination,
        )

    def inspect(
        self,
        *,
        run_id: RunId,
        definition: PlaybookDefinition,
    ) -> InspectedRunRecord:
        """Validate and expose persisted bindings without executing effects."""

        stored, payload = self._load(run_id, definition)
        checkpoint = stored.checkpoint
        dialogue_step_id: str | None = None
        dialogue_request: str | None = None
        if checkpoint.status is RunStatus.SUSPENDED:
            dialogue = cast(DialogueStep, definition.steps[checkpoint.next_step_index - 1])
            dialogue_step_id = dialogue.id
            dialogue_request = dialogue.request_text
        return InspectedRunRecord(
            run_id=run_id,
            status=checkpoint.status,
            definition_fingerprint=stored.definition_fingerprint,
            checkpoint_fingerprint=_checkpoint_fingerprint(payload),
            inputs=stored.run_inputs,
            pins=checkpoint.pins,
            read_dependencies=checkpoint.read_dependencies,
            outputs=checkpoint.outputs,
            traces=stored.traces,
            next_step_index=checkpoint.next_step_index,
            dialogue_step_id=dialogue_step_id,
            dialogue_request=dialogue_request,
        )

    def _inspect_expected(
        self,
        *,
        run_id: RunId,
        definition: PlaybookDefinition,
        inputs: JsonObject,
        pins: VersionPins,
        read_dependencies: tuple[ReadDependency, ...],
    ) -> tuple[_StoredRun, bytes, JsonObject, tuple[ReadDependency, ...]]:
        frozen_inputs = freeze_object(inputs)
        dependencies = tuple(read_dependencies)
        if set(frozen_inputs) != set(definition.input_keys):
            self._raise(
                EngineErrorCode.INVALID_INPUT,
                "run inputs must exactly match declared playbook inputs",
            )
        dependency_keys = tuple((item.kind, item.id) for item in dependencies)
        if len(set(dependency_keys)) != len(dependency_keys):
            self._raise(
                EngineErrorCode.INVALID_INPUT,
                "read dependencies must be unique by kind and id",
            )
        definition_ref = ArtifactReference(definition.id, definition.version)
        if _artifact_payload(pins.playbook) != _artifact_payload(definition_ref):
            self._raise(
                EngineErrorCode.INCOMPATIBLE_PINS,
                "playbook pin does not match definition",
            )
        if _artifact_payload(pins.model_adapter) != _artifact_payload(self._model_adapter):
            self._raise(EngineErrorCode.INCOMPATIBLE_PINS, "model adapter pin changed")
        if _artifact_payload(pins.state_contract) != _artifact_payload(self._state_contract):
            self._raise(EngineErrorCode.INCOMPATIBLE_PINS, "state contract pin changed")
        stored, payload = self._load(run_id, definition)
        checkpoint = stored.checkpoint
        if _pins_payload(checkpoint.pins) != _pins_payload(pins):
            self._raise(
                EngineErrorCode.INCOMPATIBLE_CHECKPOINT,
                "checkpoint pins differ from the expected pins",
            )
        if stored.run_inputs != frozen_inputs:
            self._raise(
                EngineErrorCode.INCOMPATIBLE_CHECKPOINT,
                "checkpoint inputs differ from the expected inputs",
            )
        if checkpoint.read_dependencies != dependencies:
            self._raise(
                EngineErrorCode.STALE_READ_DEPENDENCY,
                "checkpoint read dependencies differ from expected versions",
            )
        return stored, payload, frozen_inputs, dependencies

    def _validate_recovered_validator_registrations(
        self,
        definition: PlaybookDefinition,
        traces: tuple[StepTrace, ...],
    ) -> None:
        steps = {step.id: step for step in definition.steps}
        for trace in traces:
            if trace.status is not StepTraceStatus.COMPLETED:
                continue
            step = steps[trace.step_id]
            receipts: tuple[Mapping[str, JsonValue], ...]
            if isinstance(step, ValidateStep):
                receipts = (
                    cast(Mapping[str, JsonValue], trace.details["validator"]),
                )
            elif isinstance(step, ModelStep):
                raw = cast(
                    tuple[JsonValue, ...],
                    trace.details.get("fallback_validators", ()),
                )
                receipts = tuple(cast(Mapping[str, JsonValue], item) for item in raw)
            else:
                continue
            for receipt in receipts:
                validator_id = cast(str, receipt["validator_id"])
                executor = self._validators.get(validator_id)
                if (
                    executor is None
                    or str(executor.version) != receipt["validator_version"]
                ):
                    self._raise(
                        EngineErrorCode.INCOMPATIBLE_CHECKPOINT,
                        f"recovered validator is unavailable: {validator_id}",
                    )

    def _preflight(
        self,
        skill: SkillPackage,
        definition: PlaybookDefinition,
        pins: VersionPins,
        read_dependencies: tuple[ReadDependency, ...],
    ) -> tuple[CapabilityFallback, ...]:
        if not definition.engine_compatibility.contains(self._engine_version):
            self._raise(
                EngineErrorCode.INCOMPATIBLE_ENGINE,
                "playbook does not support the configured engine version",
            )
        if not skill.engine_compatibility.contains(self._engine_version):
            self._raise(
                EngineErrorCode.INCOMPATIBLE_ENGINE,
                "skill does not support the configured engine version",
            )
        dependency_keys = tuple((item.kind, item.id) for item in read_dependencies)
        if len(set(dependency_keys)) != len(dependency_keys):
            self._raise(
                EngineErrorCode.INVALID_INPUT,
                "read dependencies must be unique by kind and id",
            )
        if _artifact_payload(pins.skill) != _artifact_payload(
            ArtifactReference(skill.id, skill.version)
        ):
            self._raise(EngineErrorCode.INCOMPATIBLE_PINS, "skill pin does not match package")
        definition_ref = ArtifactReference(definition.id, definition.version)
        if _artifact_payload(skill.playbook) != _artifact_payload(definition_ref):
            self._raise(
                EngineErrorCode.INCOMPATIBLE_PINS,
                "skill playbook reference does not match definition",
            )
        if _artifact_payload(pins.playbook) != _artifact_payload(definition_ref):
            self._raise(EngineErrorCode.INCOMPATIBLE_PINS, "playbook pin does not match definition")
        if _artifact_payload(pins.model_adapter) != _artifact_payload(self._model_adapter):
            self._raise(EngineErrorCode.INCOMPATIBLE_PINS, "model adapter pin changed")
        if _artifact_payload(pins.state_contract) != _artifact_payload(self._state_contract):
            self._raise(EngineErrorCode.INCOMPATIBLE_PINS, "state contract pin changed")

        negotiation = negotiate_capabilities(skill, self._model.capabilities)
        if negotiation.status is NegotiationStatus.UNSUPPORTED:
            names = ", ".join(negotiation.unsupported_capabilities)
            self._raise(
                EngineErrorCode.UNSUPPORTED_CAPABILITY,
                f"unsupported model capabilities: {names}",
            )
        available_capabilities = set(model_capability_names(self._model.capabilities))
        available_capabilities.update(
            fallback.missing_capability for fallback in negotiation.activated_fallbacks
        )
        declared_validators = {item.id: item.version for item in skill.validators}
        for fallback in skill.fallbacks:
            if (
                fallback.missing_capability != "structured_output"
                or fallback.strategy not in SUPPORTED_FALLBACK_STRATEGIES
            ):
                self._raise(
                    EngineErrorCode.UNSUPPORTED_FALLBACK,
                    f"unsupported fallback strategy: {fallback.strategy}",
                )
            if (
                fallback.strategy == STRUCTURED_OUTPUT_JSON_FALLBACK
                and not fallback.validator_ids
            ):
                self._raise(
                    EngineErrorCode.UNSUPPORTED_FALLBACK,
                    "structured-output fallback requires a validator",
                )
            for validator_id in fallback.validator_ids:
                version = declared_validators.get(validator_id)
                validator = self._validators.get(validator_id)
                if (
                    version is None
                    or validator is None
                    or str(validator.version) != str(version)
                ):
                    self._raise(
                        EngineErrorCode.UNSUPPORTED_VALIDATOR,
                        f"fallback validator unavailable: {validator_id}",
                    )
        for step in definition.steps:
            if isinstance(step, ModelStep):
                _validate_schema_definition(step.output_schema.value)
                missing = sorted(
                    requirement.name
                    for requirement in step.required_capabilities
                    if requirement.name not in available_capabilities
                )
                if missing:
                    self._raise(
                        EngineErrorCode.UNSUPPORTED_CAPABILITY,
                        f"unsupported model capabilities: {', '.join(missing)}",
                    )
                if _artifact_payload(step.prompt) != _artifact_payload(pins.prompt):
                    self._raise(
                        EngineErrorCode.INCOMPATIBLE_PINS,
                        "model step prompt does not match prompt pin",
                    )
                if (
                    step.prompt_bindings
                    and (step.prompt.id, str(step.prompt.version))
                    not in self._prompt_composers
                ):
                    self._raise(
                        EngineErrorCode.INCOMPATIBLE_PINS,
                        "model step prompt has no registered composer",
                    )
            elif isinstance(step, DialogueStep):
                _validate_schema_definition(step.response_schema.value)
                if step.gate is not None:
                    _validate_schema(
                        step.response_schema.value,
                        step.gate.default_response,
                        "dialogue default response",
                        step.id,
                    )

        required_tools = {item.name: item.behavior_version for item in skill.required_tools}
        pinned_tools = {item.tool_name: item.version for item in pins.tool_behaviors}
        if set(required_tools) != set(pinned_tools):
            self._raise(EngineErrorCode.INCOMPATIBLE_PINS, "tool behavior pins are incomplete")
        for name, required_version in sorted(required_tools.items()):
            executor = self._tools.get(name)
            if (
                executor is None
                or str(executor.behavior_version) != str(required_version)
                or str(pinned_tools[name]) != str(required_version)
            ):
                self._raise(
                    EngineErrorCode.UNSUPPORTED_TOOL,
                    f"tool behavior unavailable: {name}@{required_version}",
                )
        for step in definition.steps:
            if isinstance(step, ToolStep):
                step_version = required_tools.get(step.tool.id)
                if step_version is None or str(step_version) != str(step.tool.version):
                    self._raise(
                        EngineErrorCode.UNSUPPORTED_TOOL,
                        f"tool step is not declared by the skill: {step.tool.id}",
                    )
            elif isinstance(step, ValidateStep):
                declared_version = declared_validators.get(step.validator.id)
                if (
                    declared_version is None
                    or str(declared_version) != str(step.validator.version)
                ):
                    self._raise(
                        EngineErrorCode.UNSUPPORTED_VALIDATOR,
                        f"validator step is not declared by the skill: {step.validator.id}",
                    )
                validator = self._validators.get(step.validator.id)
                if validator is None or str(validator.version) != str(step.validator.version):
                    self._raise(
                        EngineErrorCode.UNSUPPORTED_VALIDATOR,
                        f"validator unavailable: {step.validator.id}@{step.validator.version}",
                    )
        return negotiation.activated_fallbacks

    async def _run(
        self,
        *,
        run_id: RunId,
        definition: PlaybookDefinition,
        pins: VersionPins,
        run_inputs: JsonObject,
        outputs: Mapping[str, JsonValue],
        read_dependencies: tuple[ReadDependency, ...],
        start_index: int,
        traces: tuple[StepTrace, ...],
        expected_payload: bytes,
        activated_fallbacks: tuple[CapabilityFallback, ...],
        prompt_layers: tuple[PromptLayer, ...],
    ) -> PlaybookRunResult:
        mutable_outputs = dict(outputs)
        mutable_traces = list(traces)
        for index in range(start_index, len(definition.steps)):
            step = definition.steps[index]
            mutable_traces.append(self._trace(step, StepTraceStatus.STARTED))
            try:
                should_suspend = isinstance(
                    step, DialogueStep
                ) and self._dialogue_should_suspend(step, mutable_outputs)
                if isinstance(step, DialogueStep):
                    value = (
                        step.gate.default_response
                        if step.gate is not None
                        else None
                    )
                    termination = None
                    trace_details = freeze_object(
                        {"dialogue_disposition": "skipped"}
                    )
                else:
                    value, termination, trace_details = await self._execute_step(
                        step,
                        run_inputs,
                        mutable_outputs,
                        activated_fallbacks,
                        prompt_layers,
                    )
            except PlaybookEngineError as error:
                cancelled = error.failure.code is EngineErrorCode.CANCELLED
                trace_details: JsonObject = {"error_code": error.failure.code.value}
                model_failure_reason = _model_failure_reason_from_failure(error.failure)
                if model_failure_reason is not None:
                    trace_details["model_failure_reason"] = model_failure_reason
                mutable_traces.append(
                    self._trace(
                        step,
                        (
                            StepTraceStatus.CANCELLED
                            if cancelled
                            else StepTraceStatus.FAILED
                        ),
                        trace_details,
                    )
                )
                failed_checkpoint = self._checkpoint(
                    run_id,
                    pins,
                    RunStatus.CANCELLED if cancelled else RunStatus.FAILED,
                    index,
                    mutable_outputs,
                    read_dependencies,
                )
                failed_payload = _encode_stored_run(
                    _StoredRun(
                        failed_checkpoint,
                        tuple(mutable_traces),
                        run_inputs,
                        playbook_definition_fingerprint(definition),
                    )
                )
                if not self._compare_and_set(run_id, expected_payload, failed_payload):
                    self._raise(
                        EngineErrorCode.RUN_STORE_ERROR,
                        "failed checkpoint could not be persisted",
                        step.id,
                    )
                if cancelled:
                    return CancelledRunResult(
                        mutable_outputs,
                        tuple(mutable_traces),
                        error.failure,
                    )
                return FailedRunResult(mutable_outputs, tuple(mutable_traces), error.failure)
            if isinstance(step, DialogueStep) and should_suspend:
                mutable_traces.append(self._trace(step, StepTraceStatus.SUSPENDED))
                checkpoint = self._checkpoint(
                    run_id,
                    pins,
                    RunStatus.SUSPENDED,
                    index + 1,
                    mutable_outputs,
                    read_dependencies,
                )
                replacement = _encode_stored_run(
                    _StoredRun(
                        checkpoint,
                        tuple(mutable_traces),
                        run_inputs,
                        playbook_definition_fingerprint(definition),
                    )
                )
                if not self._compare_and_set(run_id, expected_payload, replacement):
                    self._raise(
                        EngineErrorCode.RUN_STORE_ERROR,
                        "checkpoint advance failed after dialogue suspension",
                    )
                return SuspendedRunResult(
                    mutable_outputs,
                    tuple(mutable_traces),
                    step.request_text,
                )
            mutable_outputs[step.output_key] = value
            trace_details = freeze_object(
                {
                    **trace_details,
                    "output_fingerprint": _json_fingerprint(value),
                }
            )
            mutable_traces.append(
                self._trace(step, StepTraceStatus.COMPLETED, trace_details)
            )
            is_final = index + 1 == len(definition.steps)
            next_status = RunStatus.COMPLETED if is_final or termination else RunStatus.RUNNING
            checkpoint = self._checkpoint(
                run_id,
                pins,
                next_status,
                index + 1,
                mutable_outputs,
                read_dependencies,
            )
            replacement = _encode_stored_run(
                _StoredRun(
                    checkpoint,
                    tuple(mutable_traces),
                    run_inputs,
                    playbook_definition_fingerprint(definition),
                )
            )
            if not self._compare_and_set(run_id, expected_payload, replacement):
                self._raise(
                    EngineErrorCode.RUN_STORE_ERROR,
                    "checkpoint advance failed after step effect",
                    step.id,
                )
            expected_payload = replacement
            if termination is not None:
                return TerminatedRunResult(
                    mutable_outputs,
                    tuple(mutable_traces),
                    termination,
                )
        return CompletedRunResult(
            mutable_outputs,
            tuple(mutable_traces),
        )

    def _dialogue_should_suspend(
        self,
        step: DialogueStep,
        outputs: Mapping[str, JsonValue],
    ) -> bool:
        if step.gate is None:
            return True
        try:
            condition = _dialogue_gate_condition(step, outputs)
        except (KeyError, TypeError) as error:
            self._raise(
                EngineErrorCode.BINDING_ERROR,
                f"dialogue gate resolution failed: {type(error).__name__}",
                step.id,
            )
        return condition

    async def _execute_step(
        self,
        step: PlaybookStep,
        run_inputs: JsonObject,
        outputs: Mapping[str, JsonValue],
        activated_fallbacks: tuple[CapabilityFallback, ...],
        prompt_layers: tuple[PromptLayer, ...],
    ) -> tuple[JsonValue, ValidationOutcome | None, JsonObject]:
        if isinstance(step, ToolStep):
            arguments = self._resolve_bindings(
                step.arguments, step.bindings, run_inputs, outputs, step.id
            )
            try:
                result = await self._tools[step.tool.id].invoke(arguments)
                frozen_result = freeze_object(result)
            except Exception as error:
                self._raise(
                    EngineErrorCode.TOOL_ERROR,
                    f"tool execution failed: {type(error).__name__}",
                    step.id,
                )
            return frozen_result, None, freeze_object({})
        if isinstance(step, ModelStep):
            prompt_inputs = self._resolve_bindings(
                {}, step.prompt_bindings, run_inputs, outputs, step.id
            )
            composer = self._prompt_composers.get(
                (step.prompt.id, str(step.prompt.version))
            )
            if composer is None:
                request = step.request
                composed = None
            else:
                try:
                    composed = composer.compose(
                        prompt=step.prompt,
                        layers=prompt_layers,
                        inputs=prompt_inputs,
                        output_schema=step.output_schema,
                    )
                except Exception as error:
                    self._raise(
                        EngineErrorCode.BINDING_ERROR,
                        f"prompt composition failed: {type(error).__name__}",
                        step.id,
                    )
                constraint = step.request.structured_output
                request = replace(
                    step.request,
                    messages=composed.messages,
                    structured_output=StructuredOutputConstraint(
                        constraint.name if constraint is not None else step.output_key,
                        step.output_schema.value,
                        constraint.strict if constraint is not None else True,
                    ),
                    metadata={
                        **step.request.metadata,
                        "prompt_fingerprint": composed.fingerprint,
                        "prompt_id": composed.prompt.id,
                        "prompt_version": str(composed.prompt.version),
                    },
                )
            try:
                response = await self._model.generate(request)
            except ModelError as error:
                if error.code is ModelErrorCode.CANCELLED:
                    self._raise(
                        EngineErrorCode.CANCELLED,
                        "model execution was cancelled",
                        step.id,
                    )
                self._raise(
                    EngineErrorCode.MODEL_ERROR,
                    f"model execution failed: {_safe_model_failure_reason(error)}",
                    step.id,
                )
            except Exception:
                self._raise(
                    EngineErrorCode.MODEL_ERROR,
                    f"model execution failed: {ModelErrorCode.UNAVAILABLE.value}",
                    step.id,
                )
            if (
                response.invocation.adapter_id != self._model_adapter.id
                or response.invocation.adapter_version
                != str(self._model_adapter.version)
            ):
                self._raise(
                    EngineErrorCode.MODEL_ERROR,
                    "model invocation provenance does not match the pinned adapter",
                    step.id,
                )
            if response.finish_reason is ModelFinishReason.CANCELLED:
                self._raise(
                    EngineErrorCode.CANCELLED,
                    "model execution was cancelled",
                    step.id,
                )
            value: JsonValue
            structured_fallback = next(
                (
                    fallback
                    for fallback in activated_fallbacks
                    if fallback.missing_capability == "structured_output"
                ),
                None,
            )
            if response.structured_output is not None:
                value = response.structured_output
            elif structured_fallback is not None:
                try:
                    value = cast(JsonValue, json.loads(response.content))
                except json.JSONDecodeError:
                    self._raise(
                        EngineErrorCode.SCHEMA_ERROR,
                        "structured-output fallback returned malformed JSON",
                        step.id,
                    )
            else:
                value = response.content
            frozen_value = freeze_json(value)
            try:
                _validate_schema(step.output_schema.value, frozen_value, "model output", step.id)
            except PlaybookEngineError as error:
                if error.failure.code is not EngineErrorCode.SCHEMA_ERROR:
                    raise
                # The model completed the transport exchange but violated the
                # requested structured-output contract.  Keep that fact in
                # the closed, safe model-failure channel instead of allowing
                # the capability gateway to collapse it into an opaque 503.
                self._raise(
                    EngineErrorCode.MODEL_ERROR,
                    f"model execution failed: {ModelErrorCode.PROTOCOL_ERROR.value}",
                    step.id,
                )
            fallback_receipts: tuple[JsonObject, ...] = ()
            if structured_fallback is not None:
                fallback_receipts = await self._run_fallback_validators(
                    structured_fallback, frozen_value, step.id
                )
            details: dict[str, JsonValue] = {
                "model_invocation": {
                    "adapter_id": response.invocation.adapter_id,
                    "adapter_version": response.invocation.adapter_version,
                    "model_id": response.invocation.model_id,
                    "response_id": response.invocation.response_id,
                }
            }
            if response.usage is not None:
                details["model_usage"] = {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                }
            if composed is not None:
                details["prompt"] = {
                    "id": composed.prompt.id,
                    "version": str(composed.prompt.version),
                    "fingerprint": composed.fingerprint,
                    "layers": tuple(
                        {
                            "id": layer.id,
                            "version": layer.version,
                            "kind": layer.kind,
                            "input_fingerprint": layer.input_fingerprint,
                        }
                        for layer in composed.layers
                    ),
                }
            if fallback_receipts:
                details["fallback_validators"] = fallback_receipts
            return frozen_value, None, freeze_object(details)
        if isinstance(step, ValidateStep):
            try:
                static_inputs = {key: outputs[key] for key in step.input_keys}
            except KeyError as error:
                self._raise(
                    EngineErrorCode.BINDING_ERROR,
                    f"validation input is missing: {error.args[0]}",
                    step.id,
                )
            inputs = self._resolve_bindings(
                static_inputs, step.bindings, run_inputs, outputs, step.id
            )
            try:
                outcome = await self._validators[step.validator.id].validate(inputs)
            except Exception as error:
                self._raise(
                    EngineErrorCode.VALIDATOR_ERROR,
                    f"validator execution failed: {type(error).__name__}",
                    step.id,
                )
            termination = (
                outcome
                if outcome.disposition is ValidatorDisposition.TERMINATE
                else None
            )
            executor = self._validators[step.validator.id]
            return (
                outcome.result,
                termination,
                freeze_object(
                    {"validator": _validator_receipt(executor, outcome)}
                ),
            )
        self._raise(EngineErrorCode.BINDING_ERROR, "unsupported step at runtime", step.id)

    async def _run_fallback_validators(
        self,
        fallback: CapabilityFallback,
        output: JsonValue,
        step_id: str,
    ) -> tuple[JsonObject, ...]:
        inputs = freeze_object({"output": output})
        receipts: list[JsonObject] = []
        for validator_id in fallback.validator_ids:
            executor = self._validators[validator_id]
            try:
                outcome = await executor.validate(inputs)
            except Exception as error:
                self._raise(
                    EngineErrorCode.VALIDATOR_ERROR,
                    f"fallback validator failed: {type(error).__name__}",
                    step_id,
                )
            receipts.append(_validator_receipt(executor, outcome, include_result=True))
            if (
                not outcome.passed
                or outcome.disposition is ValidatorDisposition.TERMINATE
            ):
                self._raise(
                    EngineErrorCode.VALIDATOR_ERROR,
                    f"fallback validator rejected output: {validator_id}",
                    step_id,
                )
        return tuple(receipts)

    def _resolve_bindings(
        self,
        base: Mapping[str, JsonValue],
        bindings: tuple[DataBinding, ...],
        run_inputs: JsonObject,
        outputs: Mapping[str, JsonValue],
        step_id: str,
    ) -> JsonObject:
        resolved = dict(base)
        try:
            for binding in bindings:
                resolved[binding.target] = self._resolve_reference(
                    binding.source, run_inputs, outputs
                )
        except (KeyError, TypeError) as error:
            self._raise(
                EngineErrorCode.BINDING_ERROR,
                f"binding resolution failed: {type(error).__name__}",
                step_id,
            )
        return freeze_object(resolved)

    @staticmethod
    def _resolve_reference(
        reference: DataReference,
        run_inputs: JsonObject,
        outputs: Mapping[str, JsonValue],
    ) -> JsonValue:
        source = run_inputs if reference.source is DataSourceKind.RUN_INPUT else outputs
        value = source[reference.key]
        for part in reference.path:
            if not isinstance(value, Mapping):
                raise TypeError("binding path requires an object")
            value = value[part]
        return freeze_json(value)

    def _trace(
        self,
        step: PlaybookStep,
        status: StepTraceStatus,
        details: JsonObject | None = None,
    ) -> StepTrace:
        return StepTrace(step.id, step.kind, status, self._clock.now(), details or {})

    def _checkpoint(
        self,
        run_id: RunId,
        pins: VersionPins,
        status: RunStatus,
        next_step_index: int,
        outputs: Mapping[str, JsonValue],
        read_dependencies: tuple[ReadDependency, ...],
    ) -> PlaybookCheckpoint:
        return PlaybookCheckpoint(
            run_id,
            pins,
            status,
            next_step_index,
            outputs,
            read_dependencies,
            self._clock.now(),
        )

    def _compare_and_set(self, run_id: RunId, expected: bytes, replacement: bytes) -> bool:
        try:
            return self._run_store.compare_and_set(run_id, expected, replacement)
        except Exception as error:
            self._raise(
                EngineErrorCode.RUN_STORE_ERROR,
                f"run checkpoint compare-and-set failed: {type(error).__name__}",
            )

    def _load(
        self, run_id: RunId, definition: PlaybookDefinition
    ) -> tuple[_StoredRun, bytes]:
        try:
            payload = self._run_store.load(run_id)
        except Exception:
            self._raise(EngineErrorCode.CHECKPOINT_NOT_FOUND, "run checkpoint was not found")
        try:
            stored = _decode_stored_run(payload)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self._raise(
                EngineErrorCode.INCOMPATIBLE_CHECKPOINT,
                "run checkpoint payload is invalid",
            )
        if _encode_stored_run(stored) != payload:
            self._raise(
                EngineErrorCode.INCOMPATIBLE_CHECKPOINT,
                "run checkpoint payload is not canonical or contains unknown data",
            )
        self._validate_stored_run(run_id, definition, stored)
        return stored, payload

    def _validate_stored_run(
        self, run_id: RunId, definition: PlaybookDefinition, stored: _StoredRun
    ) -> None:
        checkpoint = stored.checkpoint
        if checkpoint.run_id != run_id:
            self._raise(EngineErrorCode.INCOMPATIBLE_CHECKPOINT, "checkpoint run id changed")
        if checkpoint.schema_version != _CHECKPOINT_SCHEMA_VERSION:
            self._raise(
                EngineErrorCode.INCOMPATIBLE_CHECKPOINT,
                "checkpoint schema version is unsupported",
            )
        if stored.definition_fingerprint != playbook_definition_fingerprint(definition):
            self._raise(
                EngineErrorCode.INCOMPATIBLE_CHECKPOINT,
                "checkpoint playbook definition changed",
            )
        _validate_checkpoint_shape(definition, stored)

    @staticmethod
    def _raise(code: EngineErrorCode, message: str, step_id: str | None = None) -> NoReturn:
        raise PlaybookEngineError(EngineFailure(code, message, step_id))


class _StoredRun:
    __slots__ = ("checkpoint", "definition_fingerprint", "run_inputs", "traces")

    def __init__(
        self,
        checkpoint: PlaybookCheckpoint,
        traces: tuple[StepTrace, ...],
        run_inputs: JsonObject,
        definition_fingerprint: str,
    ) -> None:
        self.checkpoint = checkpoint
        self.traces = traces
        self.run_inputs = freeze_object(run_inputs)
        self.definition_fingerprint = definition_fingerprint


def _artifact_payload(reference: ArtifactReference) -> tuple[str, str]:
    return reference.id, str(reference.version)


def _pins_payload(pins: VersionPins) -> tuple[object, ...]:
    return (
        _artifact_payload(pins.skill),
        _artifact_payload(pins.playbook),
        _artifact_payload(pins.prompt),
        tuple((item.tool_name, str(item.version)) for item in pins.tool_behaviors),
        _artifact_payload(pins.model_adapter),
        _artifact_payload(pins.state_contract),
    )


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _json_fingerprint(value: JsonValue) -> str:
    encoded = json.dumps(
        _plain(freeze_json(value)), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(b"study-agent-json-result-v1\0" + encoded).hexdigest()


def _checkpoint_fingerprint(payload: bytes) -> str:
    return sha256(b"study-agent-playbook-checkpoint-v1\0" + payload).hexdigest()


def _validator_receipt(
    executor: Any,
    outcome: ValidationOutcome,
    *,
    include_result: bool = False,
) -> JsonObject:
    receipt: dict[str, JsonValue] = {
        "validator_id": executor.id,
        "validator_version": str(executor.version),
        "passed": outcome.passed,
        "disposition": outcome.disposition.value,
        "result_fingerprint": _json_fingerprint(outcome.result),
        "reason": outcome.reason,
    }
    if include_result:
        receipt["result"] = outcome.result
    return freeze_object(receipt)


def _encode_stored_run(stored: _StoredRun) -> bytes:
    checkpoint = stored.checkpoint
    payload = {
        "definition_fingerprint": stored.definition_fingerprint,
        "run_inputs": _plain(stored.run_inputs),
        "checkpoint": {
            "run_id": str(checkpoint.run_id),
            "pins": {
                "skill": _artifact_payload(checkpoint.pins.skill),
                "playbook": _artifact_payload(checkpoint.pins.playbook),
                "prompt": _artifact_payload(checkpoint.pins.prompt),
                "tool_behaviors": [
                    (item.tool_name, str(item.version))
                    for item in checkpoint.pins.tool_behaviors
                ],
                "model_adapter": _artifact_payload(checkpoint.pins.model_adapter),
                "state_contract": _artifact_payload(checkpoint.pins.state_contract),
            },
            "status": checkpoint.status.value,
            "next_step_index": checkpoint.next_step_index,
            "outputs": _plain(checkpoint.outputs),
            "read_dependencies": [
                (item.kind, item.id, item.version) for item in checkpoint.read_dependencies
            ],
            "updated_at": checkpoint.updated_at.isoformat(),
            "schema_version": checkpoint.schema_version,
        },
        "traces": [
            {
                "step_id": trace.step_id,
                "step_kind": trace.step_kind,
                "status": trace.status.value,
                "occurred_at": trace.occurred_at.isoformat(),
                "details": _plain(trace.details),
            }
            for trace in stored.traces
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _decode_stored_run(payload: bytes) -> _StoredRun:
    raw = cast(dict[str, Any], json.loads(payload.decode("utf-8")))
    checkpoint_raw = cast(dict[str, Any], raw["checkpoint"])
    pins_raw = cast(dict[str, Any], checkpoint_raw["pins"])

    def artifact(value: object) -> ArtifactReference:
        identifier, version = cast(list[str], value)
        return ArtifactReference(identifier, SemanticVersion.parse(version))

    pins = VersionPins(
        artifact(pins_raw["skill"]),
        artifact(pins_raw["playbook"]),
        artifact(pins_raw["prompt"]),
        tuple(
            ToolBehaviorPin(name, SemanticVersion.parse(version))
            for name, version in cast(list[list[str]], pins_raw["tool_behaviors"])
        ),
        artifact(pins_raw["model_adapter"]),
        artifact(pins_raw["state_contract"]),
    )
    checkpoint = PlaybookCheckpoint(
        RunId(cast(str, checkpoint_raw["run_id"])),
        pins,
        RunStatus(cast(str, checkpoint_raw["status"])),
        cast(int, checkpoint_raw["next_step_index"]),
        cast(JsonObject, checkpoint_raw["outputs"]),
        tuple(
            ReadDependency(kind, identifier, version)
            for kind, identifier, version in cast(
                list[list[str]], checkpoint_raw["read_dependencies"]
            )
        ),
        datetime.fromisoformat(cast(str, checkpoint_raw["updated_at"])),
        cast(int, checkpoint_raw["schema_version"]),
    )
    traces = tuple(
        StepTrace(
            cast(str, trace["step_id"]),
            cast(str, trace["step_kind"]),
            StepTraceStatus(cast(str, trace["status"])),
            datetime.fromisoformat(cast(str, trace["occurred_at"])),
            cast(JsonObject, trace["details"]),
        )
        for trace in cast(list[dict[str, Any]], raw["traces"])
    )
    return _StoredRun(
        checkpoint,
        traces,
        cast(JsonObject, raw["run_inputs"]),
        cast(str, raw["definition_fingerprint"]),
    )


def _binding_payload(binding: DataBinding) -> object:
    return {
        "target": binding.target,
        "source": binding.source.source.value,
        "key": binding.source.key,
        "path": binding.source.path,
    }


def playbook_definition_fingerprint(definition: PlaybookDefinition) -> str:
    steps: list[object] = []
    for step in definition.steps:
        common: dict[str, object] = {
            "id": step.id,
            "kind": step.kind,
            "output_key": step.output_key,
        }
        if isinstance(step, ToolStep):
            common.update(
                {
                    "tool": _artifact_payload(step.tool),
                    "arguments": _plain(step.arguments),
                    "bindings": [_binding_payload(item) for item in step.bindings],
                }
            )
        elif isinstance(step, ModelStep):
            common.update(
                {
                    "prompt": _artifact_payload(step.prompt),
                    "request": {
                        "messages": [
                            {
                                "role": message.role.value,
                                "content": message.content,
                                "name": message.name,
                                "tool_call_id": message.tool_call_id,
                            }
                            for message in step.request.messages
                        ],
                        "structured_output": (
                            None
                            if step.request.structured_output is None
                            else {
                                "name": step.request.structured_output.name,
                                "schema": _plain(step.request.structured_output.schema),
                                "strict": step.request.structured_output.strict,
                            }
                        ),
                        "cancellation": (
                            None
                            if step.request.cancellation is None
                            else step.request.cancellation.id
                        ),
                        "max_output_tokens": step.request.max_output_tokens,
                        "temperature": step.request.temperature,
                        "metadata": _plain(step.request.metadata),
                    },
                    "output_schema": _plain(step.output_schema.value),
                    "required_capabilities": [
                        item.name for item in step.required_capabilities
                    ],
                    "bindings": [
                        _binding_payload(item) for item in step.prompt_bindings
                    ],
                }
            )
        elif isinstance(step, DialogueStep):
            common.update(
                {
                    "request_text": step.request_text,
                    "response_schema": _plain(step.response_schema.value),
                }
            )
            if step.gate is not None:
                common["gate"] = {
                    "suspend_when": {
                        "source": step.gate.suspend_when.source.value,
                        "key": step.gate.suspend_when.key,
                        "path": step.gate.suspend_when.path,
                    },
                    "default_response": _plain(step.gate.default_response),
                }
        elif isinstance(step, ValidateStep):
            common.update(
                {
                    "validator": _artifact_payload(step.validator),
                    "input_keys": step.input_keys,
                    "bindings": [_binding_payload(item) for item in step.bindings],
                }
            )
        steps.append(common)
    payload: dict[str, object] = {
        "id": definition.id,
        "version": str(definition.version),
        "engine_compatibility": (
            str(definition.engine_compatibility.minimum),
            str(definition.engine_compatibility.maximum_exclusive),
        ),
        "input_keys": definition.input_keys,
        "steps": steps,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def _validate_checkpoint_shape(
    definition: PlaybookDefinition,
    stored: _StoredRun,
) -> None:
    checkpoint = stored.checkpoint
    traces = stored.traces
    next_index = checkpoint.next_step_index
    if not 0 <= next_index <= len(definition.steps):
        _checkpoint_error("checkpoint next-step index is invalid")
    if checkpoint.status not in {
        RunStatus.RUNNING,
        RunStatus.SUSPENDED,
        RunStatus.COMPLETED,
        RunStatus.CANCELLED,
        RunStatus.FAILED,
    }:
        _checkpoint_error("checkpoint status is unsupported")

    completed_count = next_index
    suspended_dialogue = checkpoint.status is RunStatus.SUSPENDED
    if suspended_dialogue:
        if next_index == 0 or not isinstance(definition.steps[next_index - 1], DialogueStep):
            _checkpoint_error("suspended checkpoint does not follow dialogue")
        completed_count -= 1
    expected_outputs = {
        definition.steps[index].output_key for index in range(completed_count)
    }
    if not suspended_dialogue and next_index > 0:
        expected_outputs.add(definition.steps[next_index - 1].output_key)
    if set(checkpoint.outputs) != expected_outputs:
        _checkpoint_error("checkpoint output keys do not match completed steps")

    expected_trace: list[tuple[str, str, StepTraceStatus]] = []
    for index in range(completed_count):
        step = definition.steps[index]
        expected_trace.append((step.id, step.kind, StepTraceStatus.STARTED))
        if isinstance(step, DialogueStep) and (
            step.gate is None
            or _checkpoint_dialogue_gate_condition(step, checkpoint.outputs)
        ):
            expected_trace.append((step.id, step.kind, StepTraceStatus.SUSPENDED))
        expected_trace.append((step.id, step.kind, StepTraceStatus.COMPLETED))
    if suspended_dialogue:
        step = definition.steps[next_index - 1]
        if (
            isinstance(step, DialogueStep)
            and step.gate is not None
            and not _checkpoint_dialogue_gate_condition(step, checkpoint.outputs)
        ):
            _checkpoint_error("skipped dialogue cannot own a suspended checkpoint")
        expected_trace.extend(
            (
                (step.id, step.kind, StepTraceStatus.STARTED),
                (step.id, step.kind, StepTraceStatus.SUSPENDED),
            )
        )
    elif checkpoint.status in {RunStatus.CANCELLED, RunStatus.FAILED}:
        if next_index >= len(definition.steps):
            _checkpoint_error("failed or cancelled checkpoint has no current step")
        step = definition.steps[next_index]
        terminal_trace = (
            StepTraceStatus.CANCELLED
            if checkpoint.status is RunStatus.CANCELLED
            else StepTraceStatus.FAILED
        )
        expected_trace.extend(
            (
                (step.id, step.kind, StepTraceStatus.STARTED),
                (step.id, step.kind, terminal_trace),
            )
        )
    actual_trace = [(item.step_id, item.step_kind, item.status) for item in traces]
    if actual_trace != expected_trace:
        _checkpoint_error("checkpoint trace prefix does not match playbook definition")
    if checkpoint.status in {RunStatus.CANCELLED, RunStatus.FAILED}:
        terminal = traces[-1]
        try:
            error_code = EngineErrorCode(cast(str, terminal.details["error_code"]))
        except (TypeError, ValueError):
            _checkpoint_error("terminal trace error code is invalid")
        allowed_error_fields = {"error_code"}
        if error_code is EngineErrorCode.MODEL_ERROR and "model_failure_reason" in terminal.details:
            allowed_error_fields.add("model_failure_reason")
        if set(terminal.details) != allowed_error_fields:
            _checkpoint_error("terminal trace error receipt fields are invalid")
        model_failure_reason = terminal.details.get("model_failure_reason")
        if model_failure_reason is not None and (
            error_code is not EngineErrorCode.MODEL_ERROR
            or model_failure_reason not in _SAFE_MODEL_FAILURE_REASONS
        ):
            _checkpoint_error("terminal trace model failure reason is invalid")
        if checkpoint.status is RunStatus.CANCELLED:
            if error_code is not EngineErrorCode.CANCELLED:
                _checkpoint_error("cancelled checkpoint requires a cancelled trace error")
        elif error_code is EngineErrorCode.CANCELLED:
            _checkpoint_error("failed checkpoint cannot carry a cancelled trace error")
    steps_by_id = {step.id: step for step in definition.steps}
    for trace_index, trace in enumerate(traces):
        if trace.status is not StepTraceStatus.COMPLETED:
            continue
        step = steps_by_id[trace.step_id]
        if step.output_key not in checkpoint.outputs:
            _checkpoint_error("completed trace has no corresponding output")
        output = checkpoint.outputs[step.output_key]
        if trace.details.get("output_fingerprint") != _json_fingerprint(output):
            _checkpoint_error("completed trace output fingerprint does not match output")
        if isinstance(step, ModelStep):
            _validate_schema(step.output_schema.value, output, "recovered model output")
            invocation = trace.details.get("model_invocation")
            if not isinstance(invocation, Mapping) or set(invocation) != {
                "adapter_id",
                "adapter_version",
                "model_id",
                "response_id",
            }:
                _checkpoint_error("model invocation trace receipt is invalid")
            if (
                invocation["adapter_id"] != checkpoint.pins.model_adapter.id
                or invocation["adapter_version"]
                != str(checkpoint.pins.model_adapter.version)
            ):
                _checkpoint_error("model invocation trace differs from adapter pin")
            if not isinstance(invocation["model_id"], str) or not invocation[
                "model_id"
            ].strip():
                _checkpoint_error("model invocation trace has no model identity")
            response_id = invocation["response_id"]
            if response_id is not None and (
                not isinstance(response_id, str) or not response_id.strip()
            ):
                _checkpoint_error("model invocation response identity is invalid")
            prompt = trace.details.get("prompt")
            if prompt is not None:
                _validate_prompt_trace(prompt, checkpoint.pins.prompt)
            fallback_receipts = trace.details.get("fallback_validators", ())
            if not isinstance(fallback_receipts, tuple):
                _checkpoint_error("fallback validator receipts must be an array")
            for receipt in fallback_receipts:
                _validate_validator_receipt(receipt, result=None, require_result=True)
        elif isinstance(step, DialogueStep):
            if step.gate is not None:
                should_suspend = _checkpoint_dialogue_gate_condition(
                    step, checkpoint.outputs
                )
                if should_suspend:
                    if set(trace.details) != {
                        "output_fingerprint",
                        "resume_generation_fingerprint",
                        "resume_generation_updated_at",
                    }:
                        _checkpoint_error("resumed dialogue receipt fields are invalid")
                else:
                    if set(trace.details) != {
                        "dialogue_disposition",
                        "output_fingerprint",
                    } or trace.details.get("dialogue_disposition") != "skipped":
                        _checkpoint_error("skipped dialogue receipt fields are invalid")
                    if _json_fingerprint(output) != _json_fingerprint(
                        step.gate.default_response
                    ):
                        _checkpoint_error("skipped dialogue output differs from default")
            elif set(trace.details) not in (
                {"output_fingerprint"},
                {"output_fingerprint", "resume_generation_fingerprint"},
                {
                    "output_fingerprint",
                    "resume_generation_fingerprint",
                    "resume_generation_updated_at",
                },
            ):
                _checkpoint_error("dialogue trace receipt fields are invalid")
            generation = trace.details.get("resume_generation_fingerprint")
            if generation is not None and (
                not isinstance(generation, str) or not _is_sha256(generation)
            ):
                _checkpoint_error("dialogue resume generation fingerprint is invalid")
            proof_time = trace.details.get("resume_generation_updated_at")
            if proof_time is not None:
                _validate_resume_generation_proof(
                    definition,
                    stored,
                    step,
                    trace_index,
                    generation,
                    proof_time,
                )
            _validate_schema(step.response_schema.value, output, "recovered dialogue output")
        elif isinstance(step, ValidateStep):
            receipt = trace.details.get("validator")
            _validate_validator_receipt(
                receipt,
                result=output,
                expected_id=step.validator.id,
                expected_version=str(step.validator.version),
            )


def _validate_validator_receipt(
    value: JsonValue | None,
    *,
    result: JsonValue | None,
    expected_id: str | None = None,
    expected_version: str | None = None,
    require_result: bool = False,
) -> None:
    if not isinstance(value, Mapping):
        _checkpoint_error("validator trace receipt is missing")
    expected_fields = {
        "validator_id",
        "validator_version",
        "passed",
        "disposition",
        "result_fingerprint",
        "reason",
    }
    if require_result:
        expected_fields.add("result")
    if set(value) != expected_fields:
        _checkpoint_error("validator trace receipt fields are invalid")
    if expected_id is not None and value["validator_id"] != expected_id:
        _checkpoint_error("validator trace identity does not match definition")
    if expected_version is not None and value["validator_version"] != expected_version:
        _checkpoint_error("validator trace version does not match definition")
    if not isinstance(value["passed"], bool):
        _checkpoint_error("validator trace passed outcome is invalid")
    try:
        disposition = ValidatorDisposition(cast(str, value["disposition"]))
    except (TypeError, ValueError):
        _checkpoint_error("validator trace disposition is invalid")
    reason = value["reason"]
    if reason is not None and (not isinstance(reason, str) or not reason.strip()):
        _checkpoint_error("validator trace reason is invalid")
    if disposition is ValidatorDisposition.TERMINATE and reason is None:
        _checkpoint_error("terminating validator trace requires a reason")
    if disposition is ValidatorDisposition.CONTINUE and value["passed"] is not True:
        _checkpoint_error("failed validator trace cannot continue")
    fingerprinted = value["result"] if require_result else result
    if fingerprinted is None or value["result_fingerprint"] != _json_fingerprint(
        fingerprinted
    ):
        _checkpoint_error("validator result fingerprint does not match result")


def _validate_prompt_trace(value: JsonValue, expected: ArtifactReference) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "id",
        "version",
        "fingerprint",
        "layers",
    }:
        _checkpoint_error("prompt trace receipt is invalid")
    if value["id"] != expected.id or value["version"] != str(expected.version):
        _checkpoint_error("prompt trace identity differs from prompt pin")
    fingerprint = value["fingerprint"]
    if not isinstance(fingerprint, str) or not _is_sha256(fingerprint):
        _checkpoint_error("prompt trace fingerprint is invalid")
    layers = value["layers"]
    if not isinstance(layers, tuple):
        _checkpoint_error("prompt trace layers must be an array")
    for layer in layers:
        if not isinstance(layer, Mapping) or set(layer) != {
            "id",
            "version",
            "kind",
            "input_fingerprint",
        }:
            _checkpoint_error("prompt layer trace receipt is invalid")
        for field in ("id", "version", "kind"):
            identity = layer[field]
            if not isinstance(identity, str) or not identity.strip():
                _checkpoint_error("prompt layer trace identity is invalid")
        layer_fingerprint = layer["input_fingerprint"]
        if not isinstance(layer_fingerprint, str) or not _is_sha256(layer_fingerprint):
            _checkpoint_error("prompt layer trace fingerprint is invalid")


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _recovered_termination(
    definition: PlaybookDefinition,
    checkpoint: PlaybookCheckpoint,
    traces: tuple[StepTrace, ...],
) -> ValidationOutcome | None:
    if checkpoint.next_step_index == 0:
        return None
    step = definition.steps[checkpoint.next_step_index - 1]
    if not isinstance(step, ValidateStep):
        return None
    trace = traces[-1]
    receipt = cast(Mapping[str, JsonValue], trace.details["validator"])
    disposition = ValidatorDisposition(cast(str, receipt["disposition"]))
    if disposition is not ValidatorDisposition.TERMINATE:
        return None
    if receipt["passed"] is not True:
        _checkpoint_error("failed semantic termination is not recoverable")
    result = cast(JsonObject, checkpoint.outputs[step.output_key])
    reason = cast(str | None, receipt["reason"])
    return ValidationOutcome(True, disposition, result, reason)


def _checkpoint_error(message: str) -> NoReturn:
    raise PlaybookEngineError(
        EngineFailure(EngineErrorCode.INCOMPATIBLE_CHECKPOINT, message)
    )


def _dialogue_gate_condition(
    step: DialogueStep,
    outputs: Mapping[str, JsonValue],
) -> bool:
    gate = step.gate
    if gate is None:
        raise TypeError("unconditional dialogue has no gate condition")
    value = outputs[gate.suspend_when.key]
    for part in gate.suspend_when.path:
        if not isinstance(value, Mapping):
            raise TypeError("dialogue gate path requires an object")
        value = value[part]
    if type(value) is not bool:
        raise TypeError("dialogue gate condition must resolve to boolean")
    return value


def _checkpoint_dialogue_gate_condition(
    step: DialogueStep,
    outputs: Mapping[str, JsonValue],
) -> bool:
    try:
        return _dialogue_gate_condition(step, outputs)
    except (KeyError, TypeError):
        _checkpoint_error("dialogue gate condition is missing or not boolean")


def _validate_resume_generation_proof(
    definition: PlaybookDefinition,
    stored: _StoredRun,
    step: DialogueStep,
    trace_index: int,
    generation: JsonValue | None,
    proof_time: JsonValue,
) -> None:
    if not isinstance(generation, str) or not _is_sha256(generation):
        _checkpoint_error("dialogue resume generation fingerprint is invalid")
    if not isinstance(proof_time, str):
        _checkpoint_error("dialogue resume generation timestamp is invalid")
    try:
        updated_at = datetime.fromisoformat(proof_time)
        step_index = next(
            index for index, candidate in enumerate(definition.steps) if candidate.id == step.id
        )
        prior_outputs = {
            candidate.output_key: stored.checkpoint.outputs[candidate.output_key]
            for candidate in definition.steps[:step_index]
        }
        suspended = PlaybookCheckpoint(
            stored.checkpoint.run_id,
            stored.checkpoint.pins,
            RunStatus.SUSPENDED,
            step_index + 1,
            prior_outputs,
            stored.checkpoint.read_dependencies,
            updated_at,
            stored.checkpoint.schema_version,
        )
        prior = _StoredRun(
            suspended,
            stored.traces[:trace_index],
            stored.run_inputs,
            stored.definition_fingerprint,
        )
        expected = _checkpoint_fingerprint(_encode_stored_run(prior))
    except (KeyError, StopIteration, TypeError, ValueError):
        _checkpoint_error("dialogue resume generation proof is invalid")
    if generation != expected:
        _checkpoint_error("dialogue resume generation proof does not match suspension")


_SCHEMA_KEYWORDS = frozenset(
    {"type", "required", "properties", "items", "enum", "additionalProperties"}
)
_SCHEMA_TYPES = frozenset(
    {"object", "array", "string", "number", "integer", "boolean", "null"}
)


def _validate_schema_definition(schema: JsonObject, path: str = "schema") -> None:
    unsupported = sorted(set(schema) - _SCHEMA_KEYWORDS)
    if unsupported:
        _schema_error(f"unsupported schema keyword at {path}: {unsupported[0]}")
    schema_type = schema.get("type")
    if schema_type is not None:
        schema_types: tuple[str, ...]
        if isinstance(schema_type, str):
            schema_types = (schema_type,)
        elif isinstance(schema_type, tuple) and schema_type:
            if any(not isinstance(item, str) for item in schema_type):
                _schema_error(f"unsupported schema type at {path}")
            schema_types = tuple(item for item in schema_type if isinstance(item, str))
        else:
            _schema_error(f"unsupported schema type at {path}")
        if any(
            not isinstance(item, str) or item not in _SCHEMA_TYPES
            for item in schema_types
        ):
            _schema_error(f"unsupported schema type at {path}")
    required = schema.get("required")
    if required is not None and (
        not isinstance(required, tuple)
        or any(not isinstance(item, str) for item in required)
    ):
        _schema_error(f"required must be an array of strings at {path}")
    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, Mapping):
            _schema_error(f"properties must be an object at {path}")
        for name, nested in properties.items():
            if not isinstance(nested, Mapping):
                _schema_error(f"property schema must be an object at {path}.{name}")
            _validate_schema_definition(nested, f"{path}.{name}")
    items = schema.get("items")
    if items is not None:
        if not isinstance(items, Mapping):
            _schema_error(f"items must be a schema object at {path}")
        _validate_schema_definition(items, f"{path}.items")
    additional = schema.get("additionalProperties")
    if additional is not None and not isinstance(additional, (bool, Mapping)):
        _schema_error(f"additionalProperties must be boolean or schema at {path}")
    if isinstance(additional, Mapping):
        _validate_schema_definition(additional, f"{path}.additional")
    enum = schema.get("enum")
    if enum is not None and not isinstance(enum, tuple):
        _schema_error(f"enum must be an array at {path}")


def _validate_schema(
    schema: JsonObject,
    value: JsonValue,
    label: str,
    step_id: str | None = None,
    path: str = "$",
) -> None:
    _validate_schema_definition(schema)
    schema_type = schema.get("type")
    if schema_type is not None and not _matches_type(
        cast(str | tuple[str, ...], schema_type), value
    ):
        _schema_error(f"{label} has wrong type at {path}", step_id)
    enum = schema.get("enum")
    if isinstance(enum, tuple) and value not in enum:
        _schema_error(f"{label} is not an allowed enum value at {path}", step_id)
    if isinstance(value, Mapping):
        required = cast(tuple[JsonValue, ...], schema.get("required", ()))
        for name in required:
            if cast(str, name) not in value:
                _schema_error(f"{label} is missing required property {name}", step_id)
        properties = cast(Mapping[str, JsonObject], schema.get("properties", {}))
        for name, nested in properties.items():
            if name in value:
                _validate_schema(nested, value[name], label, step_id, f"{path}.{name}")
        additional = schema.get("additionalProperties", True)
        extras = set(value) - set(properties)
        if additional is False and extras:
            _schema_error(f"{label} has unexpected property {sorted(extras)[0]}", step_id)
        if isinstance(additional, Mapping):
            for name in extras:
                _validate_schema(
                    additional, value[name], label, step_id, f"{path}.{name}"
                )
    item_schema = schema.get("items")
    if isinstance(value, tuple) and isinstance(item_schema, Mapping):
        for index, item in enumerate(value):
            _validate_schema(item_schema, item, label, step_id, f"{path}[{index}]")


def _matches_type(schema_type: str | tuple[str, ...], value: JsonValue) -> bool:
    if isinstance(schema_type, tuple):
        return any(_matches_type(item, value) for item in schema_type)
    if schema_type == "object":
        return isinstance(value, Mapping)
    if schema_type == "array":
        return isinstance(value, tuple)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    return value is None


def _schema_error(message: str, step_id: str | None = None) -> NoReturn:
    raise PlaybookEngineError(
        EngineFailure(EngineErrorCode.SCHEMA_ERROR, message, step_id)
    )


def _safe_model_failure_reason(error: ModelError) -> str:
    """Persist only the closed adapter failure category, never provider text."""

    if error.code.value in _SAFE_MODEL_FAILURE_REASONS:
        return error.code.value
    return ModelErrorCode.UNAVAILABLE.value


def _model_failure_reason_from_failure(failure: EngineFailure) -> str | None:
    if failure.code is not EngineErrorCode.MODEL_ERROR:
        return None
    prefix = "model execution failed: "
    if not failure.message.startswith(prefix):
        return None
    candidate = failure.message.removeprefix(prefix)
    return candidate if candidate in _SAFE_MODEL_FAILURE_REASONS else None
