"""Bounded semantic routing with the existing tutor validation authority.

This is an opt-in decision port. Production composition remains unchanged until
shadow benchmarks and policy calibration pass. No provider SDK belongs here.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from hashlib import sha256
from time import monotonic
from typing import cast

from study_agent.domain._validation import JsonObject, JsonValue, freeze_object
from study_agent.domain.features import FeatureMode
from study_agent.ports.judgement import (
    ChoiceJudgementPort,
    ChoiceJudgementRequest,
    ChoiceOption,
    ChoiceProbability,
    validate_judgement,
)
from study_agent.ports.model import (
    MessageRole,
    ModelError,
    ModelErrorCode,
    ModelFinishReason,
    ModelMessage,
    ModelPort,
    ModelRequest,
    StructuredOutputConstraint,
)
from study_agent.ports.tutor_host import (
    RetryableTutorDecisionError,
    TutorDecisionPort,
    TutorInterruptionToken,
)
from study_agent.tools.schema import validate_json

from .contracts import (
    MAX_HOST_TEXT,
    AnswerDialogueDecision,
    AskLearnerDecision,
    AssistantMessageDecision,
    StartCapabilityDecision,
    StopDecision,
    TutorDecision,
    TutorDecisionKind,
    TutorHostContext,
    TutorStopReason,
    validate_decision,
)


@dataclass(frozen=True, slots=True)
class RoutingThreshold:
    """Explicitly calibrated consumer policy, never an adapter default."""

    minimum_probability: float
    minimum_margin: float

    def __post_init__(self) -> None:
        for value in (self.minimum_probability, self.minimum_margin):
            if isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("routing thresholds must be finite probabilities")


@dataclass(frozen=True, slots=True)
class TutorRoutingPolicy:
    version: str
    route: RoutingThreshold
    capability: RoutingThreshold
    boolean_dialogue: RoutingThreshold
    enum_dialogue: RoutingThreshold
    mode: FeatureMode = FeatureMode.SHADOW
    emergency_fallback: bool = True
    maximum_utterance_characters: int = MAX_HOST_TEXT
    maximum_output_tokens: int = 1_024

    def __post_init__(self) -> None:
        if not self.version or self.version != self.version.strip():
            raise ValueError("routing policy version must be non-blank trimmed text")
        if any(
            not isinstance(item, RoutingThreshold)
            for item in (self.route, self.capability, self.boolean_dialogue, self.enum_dialogue)
        ):
            raise TypeError("routing policy thresholds must be immutable RoutingThreshold values")
        if not isinstance(self.mode, FeatureMode):
            raise TypeError("routing mode must be a FeatureMode")
        if not isinstance(self.emergency_fallback, bool):
            raise TypeError("emergency_fallback must be boolean")
        if (
            type(self.maximum_utterance_characters) is not int
            or not 1 <= self.maximum_utterance_characters <= MAX_HOST_TEXT
            or type(self.maximum_output_tokens) is not int
            or self.maximum_output_tokens < 1
        ):
            raise ValueError("routing payload bounds must be positive and bounded")

    @property
    def fingerprint(self) -> str:
        return _fingerprint(
            {
                "version": self.version,
                "mode": self.mode.value,
                "emergency_fallback": self.emergency_fallback,
                "maximum_utterance_characters": self.maximum_utterance_characters,
                "maximum_output_tokens": self.maximum_output_tokens,
                "thresholds": tuple(
                    (threshold.minimum_probability, threshold.minimum_margin)
                    for threshold in (
                        self.route,
                        self.capability,
                        self.boolean_dialogue,
                        self.enum_dialogue,
                    )
                ),
            }
        )


@dataclass(frozen=True, slots=True)
class RoutingJudgementReceipt:
    use_case: str
    input_fingerprint: str
    options: tuple[str, ...]
    selected_key: str | None
    probabilities: tuple[ChoiceProbability, ...]
    confidence: float | None
    margin: float | None
    accepted: bool
    latency_ms: float
    model_id: str | None
    producer_id: str | None
    producer_version: str | None
    usage: JsonObject
    failure_reason: str | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "usage", freeze_object(self.usage))


@dataclass(frozen=True, slots=True)
class TutorRoutingReceipt:
    """Derived telemetry: no utterance, payload, source text, or raw errors."""

    mode: FeatureMode
    context_fingerprint: str
    policy_version: str
    policy_fingerprint: str
    judgements: tuple[RoutingJudgementReceipt, ...]
    candidate_kind: TutorDecisionKind | None
    candidate_validated: bool
    selected_capability_id: str | None
    legacy_kind: TutorDecisionKind | None
    disagreement: bool | None
    narrow_generation_used: bool
    narrow_generation_latency_ms: float
    legacy_latency_ms: float | None
    fallback_reason: str | None


@dataclass(slots=True)
class _Trace:
    judgements: list[RoutingJudgementReceipt] = field(default_factory=list)
    generation_used: bool = False
    generation_latency_ms: float = 0
    capability_id: str | None = None
    candidate_kind: TutorDecisionKind | None = None
    candidate_validated: bool = False


class _RoutingFailure(RuntimeError):
    pass


class _RoutingInterrupted(RetryableTutorDecisionError):
    pass


class RoutingTutorDecisionPort:
    """Choose only legal routes, bind narrow payloads, validate, then return.

    OFF and SHADOW require a legacy port. ON fallback is optional but explicit.
    The optional receipt callback is for operational telemetry only; it must not
    write canonical events. Callback failures cannot change the chosen decision.
    """

    def __init__(
        self,
        judgement: ChoiceJudgementPort,
        model: ModelPort,
        policy: TutorRoutingPolicy,
        *,
        legacy: TutorDecisionPort | None = None,
        record_receipt: Callable[[TutorRoutingReceipt], None] | None = None,
    ) -> None:
        if policy.mode in {FeatureMode.OFF, FeatureMode.SHADOW} and legacy is None:
            raise ValueError("OFF and SHADOW routing require a legacy decision port")
        self._judgement = judgement
        self._model = model
        self._policy = policy
        self._legacy = legacy
        self._record_receipt = record_receipt

    async def decide(
        self, context: TutorHostContext, interruption: TutorInterruptionToken
    ) -> TutorDecision:
        _check_interruption(interruption)
        trace = _Trace()
        candidate: TutorDecision | None = None
        failure: str | None = None
        if self._policy.mode is not FeatureMode.OFF:
            try:
                candidate = await self._candidate(context, interruption, trace)
                trace.candidate_kind = candidate.kind
                _check_interruption(interruption)
                try:
                    validate_decision(candidate, context)
                    trace.candidate_validated = True
                except (TypeError, ValueError) as error:
                    raise _RoutingFailure("validation_failure") from error
            except _RoutingInterrupted:
                raise
            except ModelError as error:
                if error.code is ModelErrorCode.CANCELLED:
                    raise _RoutingInterrupted("tutor routing interrupted") from error
                candidate = None
                failure = "payload_provider_failure"
            except Exception as error:
                _check_interruption(interruption)
                candidate = None
                failure = str(error) if isinstance(error, _RoutingFailure) else "provider_failure"
        _check_interruption(interruption)
        legacy: TutorDecision | None = None
        legacy_latency: float | None = None
        if self._policy.mode in {FeatureMode.OFF, FeatureMode.SHADOW} or candidate is None:
            if self._legacy is None or (
                self._policy.mode is FeatureMode.ON and not self._policy.emergency_fallback
            ):
                self._record(context, trace, candidate, None, None, failure)
                raise RetryableTutorDecisionError(
                    "bounded tutor routing failed", failure_reason=failure
                )
            started = monotonic()
            # One call, outside the candidate exception handler: never fallback
            # again when the legacy port itself fails.
            legacy = await self._legacy.decide(context, interruption)
            legacy_latency = (monotonic() - started) * 1_000
            _check_interruption(interruption)
            validate_decision(legacy, context)
        self._record(context, trace, candidate, legacy, legacy_latency, failure)
        if legacy is not None:
            return legacy
        assert candidate is not None
        return candidate

    def _record(
        self,
        context: TutorHostContext,
        trace: _Trace,
        candidate: TutorDecision | None,
        legacy: TutorDecision | None,
        legacy_latency: float | None,
        failure: str | None,
    ) -> None:
        if self._record_receipt is None:
            return
        receipt = TutorRoutingReceipt(
            self._policy.mode,
            context.fingerprint,
            self._policy.version,
            self._policy.fingerprint,
            tuple(trace.judgements),
            trace.candidate_kind,
            trace.candidate_validated,
            trace.capability_id,
            legacy.kind if legacy is not None else None,
            candidate != legacy if candidate is not None and legacy is not None else None,
            trace.generation_used,
            trace.generation_latency_ms,
            legacy_latency,
            failure,
        )
        # Telemetry is derived state and never decision authority.
        with suppress(Exception):
            self._record_receipt(receipt)

    async def _candidate(
        self, context: TutorHostContext, interruption: TutorInterruptionToken, trace: _Trace
    ) -> TutorDecision:
        state: JsonObject = {
            "latest_learner_utterance": _latest_utterance(
                context, self._policy.maximum_utterance_characters
            )
        }
        pending = context.pending_continuation
        if pending is not None:
            # Existing host validation admits only ANSWER_DIALOGUE here.
            state = {**state, "dialogue_request": pending.dialogue_request}
            values = _closed_values(pending.response_schema)
            if values is not None:
                options = tuple(
                    ChoiceOption(f"response_{index}", _json(value))
                    for index, value in enumerate(values)
                )
                threshold = (
                    self._policy.boolean_dialogue
                    if pending.response_schema.get("type") == "boolean"
                    else self._policy.enum_dialogue
                )
                key = await self._choose(
                    "closed_dialogue", state, options, threshold, interruption, trace
                )
                response = values[next(i for i, option in enumerate(options) if option.key == key)]
            else:
                payload = await self._generate(
                    "dialogue_response",
                    state,
                    _wrapper("response", pending.response_schema),
                    interruption,
                    trace,
                )
                response = payload["response"]
            return AnswerDialogueDecision(pending.fingerprint, response)
        routes = [
            ChoiceOption(TutorDecisionKind.ASK_LEARNER.value, "Ask the learner a clarification"),
            ChoiceOption(TutorDecisionKind.ASSISTANT_MESSAGE.value, "Give a tutor message"),
            ChoiceOption(TutorDecisionKind.STOP.value, "Finish this tutor turn"),
        ]
        if context.advertised_capabilities:
            routes.append(
                ChoiceOption(
                    TutorDecisionKind.START_CAPABILITY.value, "Start an advertised capability"
                )
            )
            state = {
                **state,
                "capabilities": tuple(item.id for item in context.advertised_capabilities),
            }
        route = await self._choose(
            "route", state, tuple(routes), self._policy.route, interruption, trace
        )
        if route == TutorDecisionKind.START_CAPABILITY.value:
            capability_id = await self._choose(
                "capability",
                state,
                tuple(ChoiceOption(item.id, item.id) for item in context.advertised_capabilities),
                self._policy.capability,
                interruption,
                trace,
            )
            descriptor = next(
                item for item in context.advertised_capabilities if item.id == capability_id
            )
            trace.capability_id = capability_id
            inputs = _fixed_inputs(descriptor.input_schema)
            if inputs is None:
                # No other capability schema, host snapshot or authority token.
                inputs = await self._generate(
                    "capability_inputs",
                    {
                        "latest_learner_utterance": state["latest_learner_utterance"],
                        "capability_id": descriptor.id,
                    },
                    descriptor.input_schema,
                    interruption,
                    trace,
                )
            return StartCapabilityDecision(descriptor.id, inputs)
        if route == TutorDecisionKind.STOP.value:
            reason = await self._choose(
                "stop_reason",
                {"latest_learner_utterance": state["latest_learner_utterance"]},
                tuple(ChoiceOption(item.value, item.value) for item in TutorStopReason),
                self._policy.route,
                interruption,
                trace,
            )
            return StopDecision(TutorStopReason(reason))
        field_name = "question" if route == TutorDecisionKind.ASK_LEARNER.value else "message"
        # Dataclass constructors enforce the upper text bound; the core schema
        # subset intentionally has no maxLength keyword.
        payload = await self._generate(
            field_name,
            {"latest_learner_utterance": state["latest_learner_utterance"]},
            _wrapper(field_name, {"type": "string", "minLength": 1}),
            interruption,
            trace,
        )
        text = payload[field_name]
        if not isinstance(text, str):
            raise _RoutingFailure("invalid_payload")
        return (
            AskLearnerDecision(text) if field_name == "question" else AssistantMessageDecision(text)
        )

    async def _choose(
        self,
        use_case: str,
        state: JsonObject,
        options: tuple[ChoiceOption, ...],
        threshold: RoutingThreshold,
        interruption: TutorInterruptionToken,
        trace: _Trace,
    ) -> str:
        _check_interruption(interruption)
        if len(options) == 1:
            return options[0].key
        request = ChoiceJudgementRequest(
            "Select exactly one legal option for this tutor turn.",
            state,
            options,
            {"use_case": use_case, "policy_version": self._policy.version},
        )
        fingerprint = _fingerprint(
            {
                "state": state,
                "options": tuple(
                    {"key": item.key, "description": item.description} for item in options
                ),
                "instruction": request.instruction,
                "policy_fingerprint": self._policy.fingerprint,
            }
        )
        started = monotonic()
        try:
            result = await self._judgement.judge(request)
            _check_interruption(interruption)
            try:
                validate_judgement(request, result)
            except (TypeError, ValueError) as error:
                raise _RoutingFailure("malformed_judgement") from error
        except _RoutingInterrupted:
            raise
        except Exception as error:
            _check_interruption(interruption)
            if isinstance(error, ModelError) and error.code is ModelErrorCode.CANCELLED:
                raise _RoutingInterrupted("tutor routing interrupted") from error
            reason = (
                str(error) if isinstance(error, _RoutingFailure) else "judgement_provider_failure"
            )
            trace.judgements.append(
                RoutingJudgementReceipt(
                    use_case,
                    fingerprint,
                    tuple(item.key for item in options),
                    None,
                    (),
                    None,
                    None,
                    False,
                    (monotonic() - started) * 1_000,
                    None,
                    None,
                    None,
                    {},
                    reason,
                )
            )
            raise _RoutingFailure(reason) from error
        ranked = sorted((item.probability for item in result.probabilities), reverse=True)
        selected = next(
            item.probability for item in result.probabilities if item.key == result.selected_key
        )
        margin = ranked[0] - ranked[1]
        accepted = (
            selected == ranked[0]
            and selected >= threshold.minimum_probability
            and margin >= threshold.minimum_margin
        )
        trace.judgements.append(
            RoutingJudgementReceipt(
                use_case,
                fingerprint,
                tuple(item.key for item in options),
                result.selected_key,
                result.probabilities,
                result.confidence,
                margin,
                accepted,
                (monotonic() - started) * 1_000,
                result.model_id,
                result.producer_id,
                result.producer_version,
                _numeric_usage(result.usage),
                None if accepted else "insufficient_separation",
            )
        )
        if not accepted:
            raise _RoutingFailure("insufficient_separation")
        return result.selected_key

    async def _generate(
        self,
        name: str,
        state: JsonObject,
        schema: JsonObject,
        interruption: TutorInterruptionToken,
        trace: _Trace,
    ) -> JsonObject:
        _check_interruption(interruption)
        if not self._model.capabilities.structured_output:
            raise _RoutingFailure("structured_output_unavailable")
        trace.generation_used = True
        started = monotonic()
        try:
            response = await self._model.generate(
                ModelRequest(
                    (
                        ModelMessage(
                            MessageRole.SYSTEM,
                            f"Generate only the selected {name} payload. "
                            "Do not choose routes, capabilities or authority identifiers.",
                        ),
                        ModelMessage(MessageRole.USER, _json(state)),
                    ),
                    structured_output=StructuredOutputConstraint(name, schema),
                    max_output_tokens=self._policy.maximum_output_tokens,
                    metadata={"policy_version": self._policy.version},
                )
            )
        finally:
            trace.generation_latency_ms += (monotonic() - started) * 1_000
        _check_interruption(interruption)
        if response.finish_reason is ModelFinishReason.CANCELLED:
            raise _RoutingInterrupted("tutor routing interrupted")
        if (
            response.finish_reason is not ModelFinishReason.STOP
            or response.tool_calls
            or response.structured_output is None
        ):
            raise _RoutingFailure("invalid_payload")
        try:
            validate_json(response.structured_output, schema)
        except (TypeError, ValueError) as error:
            raise _RoutingFailure("invalid_payload") from error
        return response.structured_output


def _check_interruption(interruption: TutorInterruptionToken) -> None:
    if interruption.is_interrupted():
        raise _RoutingInterrupted("tutor routing interrupted", failure_reason="interrupted")


def _latest_utterance(context: TutorHostContext, limit: int) -> str:
    timeline = context.tutor_snapshot.get("timeline", ())
    if isinstance(timeline, tuple):
        for item in reversed(timeline):
            if isinstance(item, Mapping) and item.get("kind") == "learner":
                content = item.get("content")
                return content[:limit] if isinstance(content, str) else ""
    return ""


def _closed_values(schema: JsonObject) -> tuple[JsonValue, ...] | None:
    enum = schema.get("enum")
    values = (
        enum
        if isinstance(enum, tuple)
        else (
            (False, True)
            if schema.get("type") == "boolean"
            else (None,)
            if schema.get("type") == "null"
            else None
        )
    )
    if values is None:
        return None
    valid: list[JsonValue] = []
    for value in values:
        try:
            validate_json(value, schema)
        except (TypeError, ValueError):
            continue
        valid.append(value)
    if not valid:
        raise _RoutingFailure("no_legal_response_values")
    return tuple(valid)


def _fixed_inputs(schema: JsonObject) -> JsonObject | None:
    try:
        validate_json({}, schema)
        return {}
    except (TypeError, ValueError):
        pass
    properties, required = schema.get("properties"), schema.get("required")
    if not isinstance(properties, Mapping) or not isinstance(required, tuple):
        return None
    values: dict[str, JsonValue] = {}
    for name in required:
        child = properties.get(name) if isinstance(name, str) else None
        if not isinstance(child, Mapping):
            return None
        options = _closed_values(child)
        if options is None or len(options) != 1:
            return None
        values[cast(str, name)] = options[0]
    try:
        validate_json(values, schema)
    except (TypeError, ValueError):
        return None
    return values


def _wrapper(name: str, schema: JsonObject) -> JsonObject:
    return {
        "type": "object",
        "properties": {name: schema},
        "required": (name,),
        "additionalProperties": False,
    }


def _plain(value: JsonValue) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _json(value: JsonValue) -> str:
    return json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fingerprint(value: JsonObject) -> str:
    return sha256(b"cardine-tutor-routing-v1\0" + _json(value).encode()).hexdigest()


def _numeric_usage(usage: JsonObject) -> JsonObject:
    # Do not let an adapter's arbitrary usage extensions log source text.
    return {
        key: value
        for key, value in usage.items()
        if type(value) in (int, float) and math.isfinite(cast(float, value))
    }


__all__ = [
    "RoutingJudgementReceipt",
    "RoutingThreshold",
    "RoutingTutorDecisionPort",
    "TutorRoutingPolicy",
    "TutorRoutingReceipt",
]
