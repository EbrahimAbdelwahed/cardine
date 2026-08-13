"""Closed tutor-decision adapter over the provider-neutral model port."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

from cardine.diagnostics import record_turn_decision
from cardine.hosts import (
    TutorDecision,
    TutorHostContext,
    decision_from_bytes,
    decision_schema,
)
from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.ports import (
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
from study_agent.prompts.tutor_decision_v1 import (
    TUTOR_DECISION_PROMPT,
    tutor_decision_instruction,
)

MAX_DECISION_OUTPUT_TOKENS = 2_048


class ModelTutorDecisionError(RuntimeError):
    """Safe non-retryable failure at the closed decision boundary."""

    def __init__(self, message: str, *, failure_reason: str | None = None) -> None:
        super().__init__(message)
        self.failure_reason = failure_reason


class ModelTutorDecisionPort(TutorDecisionPort):
    """Ask one configured model for one locally validated closed decision."""

    def __init__(self, model: ModelPort) -> None:
        if not hasattr(model, "generate") or not model.capabilities.structured_output:
            raise TypeError("tutor decisions require a structured-output model")
        self._model = model

    async def decide(
        self,
        context: TutorHostContext,
        interruption: TutorInterruptionToken,
    ) -> TutorDecision:
        if not isinstance(context, TutorHostContext):
            raise TypeError("tutor decision context is invalid")
        if interruption.is_interrupted():
            raise ModelTutorDecisionError("tutor decision interrupted")
        schema = decision_schema(context)
        provider_schema = cast(
            JsonObject, _provider_strict_schema(_provider_decision_schema(schema, context))
        )
        provider_payload = json.dumps(
            {
                **json.loads(context.to_bytes()),
                "decision_schema": _plain(provider_schema),
            },
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        request = ModelRequest(
            (
                ModelMessage(
                    MessageRole.SYSTEM,
                    tutor_decision_instruction(
                        _advertised_capability_ids(schema),
                        _advertised_tool_names(schema),
                    ),
                ),
                ModelMessage(MessageRole.USER, provider_payload),
            ),
            StructuredOutputConstraint(
                "study_agent_tutor_decision",
                provider_schema,
                True,
            ),
            max_output_tokens=MAX_DECISION_OUTPUT_TOKENS,
            temperature=0,
            metadata={
                "prompt_id": TUTOR_DECISION_PROMPT.id,
                "prompt_version": str(TUTOR_DECISION_PROMPT.version),
                "context_fingerprint": context.fingerprint,
            },
        )
        try:
            response = await self._model.generate(request)
        except ModelError as error:
            if error.retryable:
                raise RetryableTutorDecisionError(
                    "provider request is retryable", failure_reason=error.code.value
                ) from None
            raise ModelTutorDecisionError(
                "provider request failed", failure_reason=error.code.value
            ) from None
        except Exception:
            raise ModelTutorDecisionError(
                "provider request failed", failure_reason=ModelErrorCode.UNAVAILABLE.value
            ) from None
        if interruption.is_interrupted():
            raise ModelTutorDecisionError("tutor decision interrupted")
        if response.finish_reason is not ModelFinishReason.STOP:
            raise ModelTutorDecisionError(
                "provider decision was incomplete",
                failure_reason=ModelErrorCode.PROTOCOL_ERROR.value,
            )
        value = response.structured_output
        if not isinstance(value, Mapping) or set(value) != {"decision"}:
            raise ModelTutorDecisionError(
                "provider decision was invalid",
                failure_reason=ModelErrorCode.PROTOCOL_ERROR.value,
            )
        raw_decision = value["decision"]
        if not isinstance(raw_decision, Mapping):
            raise ModelTutorDecisionError(
                "provider decision was invalid",
                failure_reason=ModelErrorCode.PROTOCOL_ERROR.value,
            )
        try:
            if raw_decision.get("kind") == "stop":
                raise ValueError("provider stop decision is unavailable")
            cleaned_decision = _remove_provider_null_optionals(
                raw_decision,
                cast(JsonObject, schema["properties"])["decision"],
            )
            encoded = json.dumps(
                _plain(cleaned_decision),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            decision = decision_from_bytes(encoded, context)
            record_turn_decision(decision)
            return decision
        except (TypeError, ValueError, OverflowError):
            raise ModelTutorDecisionError(
                "provider decision was invalid",
                failure_reason=ModelErrorCode.PROTOCOL_ERROR.value,
            ) from None


def _advertised_capability_ids(schema: JsonObject) -> tuple[str, ...]:
    return _advertised_operation_names(
        schema, kind_value="start_capability", name_field="capability_id"
    )


def _advertised_tool_names(schema: JsonObject) -> tuple[str, ...]:
    return _advertised_operation_names(
        schema, kind_value="invoke_tool", name_field="tool_name"
    )


def _advertised_operation_names(
    schema: JsonObject, *, kind_value: str, name_field: str
) -> tuple[str, ...]:
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return ()
    decision = properties.get("decision")
    if not isinstance(decision, Mapping):
        return ()
    branches = decision.get("anyOf")
    if not isinstance(branches, tuple):
        return ()
    names: list[str] = []
    for branch in branches:
        if not isinstance(branch, Mapping):
            continue
        branch_properties = branch.get("properties")
        if not isinstance(branch_properties, Mapping):
            continue
        kind_schema = branch_properties.get("kind")
        operation_name = branch_properties.get(name_field)
        if (
            not isinstance(kind_schema, Mapping)
            or kind_schema.get("enum") != (kind_value,)
        ):
            continue
        if not isinstance(operation_name, Mapping):
            continue
        enum = operation_name.get("enum")
        if (
            isinstance(enum, tuple)
            and len(enum) == 1
            and isinstance(enum[0], str)
        ):
            names.append(enum[0])
    return tuple(sorted(set(names)))


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _provider_strict_schema(value: object) -> object:
    """Project local schemas into OpenAI strict Structured Outputs schemas.

    The local command contract permits optional object fields.  OpenAI strict
    schemas require every declared property to be listed in ``required``;
    optional fields are therefore represented as required nullable fields and
    nulls are removed again before the local contract is validated.
    """

    if isinstance(value, Mapping):
        projected = {str(key): _provider_strict_schema(item) for key, item in value.items()}
        properties = projected.get("properties")
        required = projected.get("required")
        if isinstance(properties, dict) and isinstance(required, tuple):
            required_names = set(required)
            for name, item in tuple(properties.items()):
                if name not in required_names:
                    properties[name] = {"anyOf": (item, {"type": "null"})}
                    required_names.add(name)
            projected["required"] = tuple(sorted(required_names))
        return projected
    if isinstance(value, tuple):
        return tuple(_provider_strict_schema(item) for item in value)
    return value


def _provider_decision_schema(
    schema: Mapping[str, object], context: TutorHostContext
) -> JsonObject:
    """Remove the generic stop branch from Cardine's provider schema."""

    if context.pending_continuation is not None:
        return cast(JsonObject, schema)
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return cast(JsonObject, schema)
    decision = properties.get("decision")
    if not isinstance(decision, Mapping):
        return cast(JsonObject, schema)
    branches = decision.get("anyOf")
    if not isinstance(branches, tuple):
        return cast(JsonObject, schema)
    projected = cast(dict[str, JsonValue], dict(schema))
    projected_properties = cast(dict[str, JsonValue], dict(properties))
    projected_decision = cast(dict[str, JsonValue], dict(decision))
    projected_decision["anyOf"] = tuple(
        branch for branch in branches if not _is_stop_branch(branch)
    )
    projected_properties["decision"] = projected_decision
    projected["properties"] = projected_properties
    return projected


def _is_stop_branch(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    properties = value.get("properties")
    if not isinstance(properties, Mapping):
        return False
    kind = properties.get("kind")
    return isinstance(kind, Mapping) and kind.get("enum") == ("stop",)


def _remove_provider_null_optionals(value: object, schema: object) -> object:
    """Undo the nullable projection for optional local fields only."""

    if not isinstance(schema, Mapping):
        return value
    variants = schema.get("anyOf")
    if isinstance(variants, tuple):
        schema = _matching_variant(value, variants)
        if not isinstance(schema, Mapping):
            return value
    if isinstance(value, Mapping):
        properties = schema.get("properties")
        required = schema.get("required")
        if not isinstance(properties, Mapping) or not isinstance(required, tuple):
            return value
        required_names = set(required)
        cleaned: dict[str, object] = {}
        for key, item in value.items():
            name = str(key)
            if name not in properties:
                cleaned[name] = item
            elif item is None and name not in required_names:
                continue
            else:
                cleaned[name] = _remove_provider_null_optionals(item, properties[name])
        return cleaned
    if isinstance(value, tuple):
        items = schema.get("items")
        return tuple(_remove_provider_null_optionals(item, items) for item in value)
    if isinstance(value, list):
        items = schema.get("items")
        return [_remove_provider_null_optionals(item, items) for item in value]
    return value


def _matching_variant(value: object, variants: tuple[object, ...]) -> object:
    """Choose the closed decision branch using its required discriminator."""

    if not isinstance(value, Mapping):
        return variants[0] if variants else {}
    kind = value.get("kind")
    for variant in variants:
        if not isinstance(variant, Mapping):
            continue
        properties = variant.get("properties")
        if not isinstance(properties, Mapping):
            continue
        discriminator = properties.get("kind")
        if isinstance(discriminator, Mapping) and kind in discriminator.get("enum", ()):
            return variant
    return variants[0] if variants else {}


__all__ = [
    "MAX_DECISION_OUTPUT_TOKENS",
    "ModelTutorDecisionError",
    "ModelTutorDecisionPort",
]
