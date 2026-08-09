from __future__ import annotations

import json
from collections.abc import Mapping
from hashlib import sha256

from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.ports import MessageRole, ModelMessage
from study_agent.skills import (
    ArtifactReference,
    JsonSchema,
    PromptLayer,
    PromptLayerKind,
)

from .contracts import (
    ComposedPrompt,
    PromptCompositionError,
    PromptLayerRecord,
)

_EXPECTED_ORDER = tuple(PromptLayerKind)
_INTERNAL_FIELDS = frozenset({"output_schema"})
_PROFILE_LAYER_INPUT_FINGERPRINT_VERSION = "profile-layer-input@1"


def canonical_json(value: JsonValue) -> str:
    """Render frozen JSON data deterministically without changing its meaning."""

    return json.dumps(
        _plain_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _plain_json(value: JsonValue) -> object:
    if isinstance(value, Mapping):
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_json(item) for item in value]
    return value


class CanonicalPromptComposer:
    """Compose portable prompt layers without inspecting any model or provider."""

    def __init__(self, *, allow_profile_layers: bool = False) -> None:
        self._allow_profile_layers = allow_profile_layers

    def compose(
        self,
        *,
        prompt: ArtifactReference,
        layers: tuple[PromptLayer, ...],
        inputs: JsonObject,
        output_schema: JsonSchema,
    ) -> ComposedPrompt:
        kinds = tuple(layer.kind for layer in layers)
        if self._allow_profile_layers:
            _validate_profile_layer_order(kinds)
        elif kinds != _EXPECTED_ORDER:
            raise PromptCompositionError(
                "prompt layers must contain the six canonical kinds exactly once and in order"
            )
        declared = {
            field
            for layer in layers
            for field in layer.input_fields
            if field not in _INTERNAL_FIELDS
        }
        supplied = set(inputs)
        if supplied != declared:
            missing = sorted(declared - supplied)
            unused = sorted(supplied - declared)
            raise PromptCompositionError(
                f"prompt inputs must exactly match declarations; missing={missing}, unused={unused}"
            )

        messages: list[ModelMessage] = []
        records: list[PromptLayerRecord] = []
        for layer in layers:
            layer_data: dict[str, JsonValue] = {}
            for field in layer.input_fields:
                layer_data[field] = (
                    output_schema.value if field == "output_schema" else inputs[field]
                )
            rendered_data = canonical_json(layer_data)
            content = (
                f"[prompt-layer:{layer.kind.value}:{layer.id}@{layer.version}]\n"
                f"{layer.template}\n"
                f"<layer-data>{rendered_data}</layer-data>"
            )
            role = (
                MessageRole.SYSTEM
                if layer.kind
                in {
                    PromptLayerKind.STUDY_SECURITY_POLICY,
                    PromptLayerKind.OUTPUT_SCHEMA,
                }
                else MessageRole.USER
            )
            messages.append(ModelMessage(role, content))
            records.append(
                PromptLayerRecord(
                    layer.id,
                    str(layer.version),
                    layer.kind.value,
                    _layer_input_fingerprint(
                        layer,
                        rendered_data,
                        include_identity=self._allow_profile_layers,
                    ),
                )
            )

        fingerprint_payload = canonical_json(
            {
                "prompt": {"id": prompt.id, "version": str(prompt.version)},
                "messages": tuple(
                    {"role": message.role.value, "content": message.content}
                    for message in messages
                ),
                "layers": tuple(
                    {
                        "id": record.id,
                        "version": record.version,
                        "kind": record.kind,
                        "input_fingerprint": record.input_fingerprint,
                    }
                    for record in records
                ),
            }
        )
        return ComposedPrompt(
            prompt,
            tuple(messages),
            tuple(records),
            sha256(fingerprint_payload.encode()).hexdigest(),
        )


def _layer_input_fingerprint(
    layer: PromptLayer, rendered_data: str, *, include_identity: bool
) -> str:
    if not include_identity:
        # Preserve the original canonical prompt contract byte-for-byte.
        return sha256(rendered_data.encode()).hexdigest()
    return sha256(
        canonical_json(
            {
                "fingerprint_version": _PROFILE_LAYER_INPUT_FINGERPRINT_VERSION,
                "id": layer.id,
                "version": str(layer.version),
                "kind": layer.kind.value,
                "data": rendered_data,
            }
        ).encode()
    ).hexdigest()


def _validate_profile_layer_order(kinds: tuple[PromptLayerKind, ...]) -> None:
    """Accept registered profile variants while keeping their canonical order."""

    if not kinds or kinds[0] is not PromptLayerKind.STUDY_SECURITY_POLICY:
        raise PromptCompositionError("profile prompt layers must start with security policy")
    if kinds[-1] is not PromptLayerKind.OUTPUT_SCHEMA:
        raise PromptCompositionError("profile prompt layers must end with output schema")
    ranks = {kind: index for index, kind in enumerate(_EXPECTED_ORDER)}
    non_task = tuple(kind for kind in kinds if kind is not PromptLayerKind.TASK_INSTRUCTION)
    ordered = tuple(ranks[kind] for kind in non_task)
    if ordered != tuple(sorted(ordered)):
        raise PromptCompositionError("profile prompt layers must remain in canonical order")
    if any(
        kinds.count(kind) > 1
        for kind in _EXPECTED_ORDER
        if kind is not PromptLayerKind.TASK_INSTRUCTION
    ):
        raise PromptCompositionError("profile prompt layers may repeat only task instructions")
