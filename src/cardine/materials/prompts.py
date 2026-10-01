"""Versioned, provider-neutral prompts for the material pipeline."""

from __future__ import annotations

from hashlib import sha256

from study_agent.domain._validation import JsonObject
from study_agent.ports.model import (
    MessageRole,
    ModelMessage,
    ModelRequest,
    StructuredOutputConstraint,
)
from study_agent.state import canonical_json_bytes

from .generation_contracts import MAX_SEGMENTS, GenerationPipelinePins
from .planning import SegmentBoundary, UnitManifest, units_for_boundary

MATERIAL_BOUNDARIES_PROMPT = "material-boundaries@1"
COMPLETE_SEGMENT_PROMPT = "complete-segment@1"
COMPLETE_MERGE_PROMPT = "complete-merge@1"
STUDY_FROM_COMPLETE_PROMPT = "study-from-complete@1"

BOUNDARY_SCHEMA: JsonObject = {
    "type": "object",
    "additionalProperties": False,
    "required": ("schema_version", "manifest_fingerprint", "segments"),
    "properties": {
        "schema_version": {"type": "integer", "const": 1},
        "manifest_fingerprint": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "segments": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_SEGMENTS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ("start_unit", "end_unit", "title"),
                "properties": {
                    "start_unit": {"type": "integer", "minimum": 0},
                    "end_unit": {"type": "integer", "minimum": 1},
                    "title": {"type": "string", "minLength": 1, "maxLength": 240},
                },
            },
        },
    },
}

MATERIAL_OUTPUT_SCHEMA: JsonObject = {
    "type": "object",
    "additionalProperties": False,
    "required": ("markdown", "limitations"),
    "properties": {
        "markdown": {"type": "string", "minLength": 1, "maxLength": 500_000},
        "limitations": {
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "items": {"type": "string", "minLength": 1, "maxLength": 1_000},
        },
    },
}


def boundary_request(manifest: UnitManifest, pins: GenerationPipelinePins) -> ModelRequest:
    manifest_json = manifest.to_bytes().decode("utf-8")
    return ModelRequest(
        messages=(
            ModelMessage(
                MessageRole.SYSTEM,
                "You are the Cardine material-boundary planner. Return only the strict "
                "JSON schema. "
                "Partition every unit exactly once, in order, with no gaps or overlaps. "
                "Do not invent content.",
            ),
            ModelMessage(
                MessageRole.USER,
                f"prompt={pins.boundaries_prompt}\nunit_manifest={manifest_json}",
            ),
        ),
        structured_output=StructuredOutputConstraint(
            "material_boundaries_v1", BOUNDARY_SCHEMA, strict=True
        ),
        max_output_tokens=4_096,
        temperature=0,
        metadata={
            "material_prompt": pins.boundaries_prompt,
            "model_id": pins.model_id,
            "tool_calls": False,
        },
    )


def complete_segment_request(
    manifest: UnitManifest,
    boundary: SegmentBoundary,
    *,
    pins: GenerationPipelinePins,
) -> ModelRequest:
    source = units_for_boundary(manifest, boundary)
    return ModelRequest(
        messages=(
            ModelMessage(
                MessageRole.SYSTEM,
                "You are the Cardine complete-segment writer. Produce only faithful Markdown prose "
                "for the supplied transcript segment. Preserve uncertainty, teacher emphasis, and "
                "numeric facts. Do not add facts absent from the transcript. Return only the "
                "strict {markdown,limitations} JSON object and state truthful limitations.",
            ),
            ModelMessage(
                MessageRole.USER,
                f"prompt={pins.complete_segment_prompt}\nsegment_title={boundary.title}\n"
                f"transcript_segment:\n{source}\n[end transcript_segment]",
            ),
        ),
        structured_output=StructuredOutputConstraint(
            "material_complete_segment_v1", MATERIAL_OUTPUT_SCHEMA, strict=True
        ),
        max_output_tokens=16_384,
        temperature=0,
        metadata={
            "material_prompt": pins.complete_segment_prompt,
            "model_id": pins.model_id,
            "tool_calls": False,
        },
    )


def complete_merge_request(
    segments: tuple[str, ...],
    *,
    title: str,
    pins: GenerationPipelinePins,
) -> ModelRequest:
    joined = "\n\n".join(f"[segment {index + 1}]\n{value}" for index, value in enumerate(segments))
    return ModelRequest(
        messages=(
            ModelMessage(
                MessageRole.SYSTEM,
                "You are the Cardine complete-material editor. Merge the supplied ordered "
                "segments into one coherent Markdown lesson. Keep every supported fact and "
                "limitation; do not introduce new facts. Return only the strict "
                "{markdown,limitations} JSON object.",
            ),
            ModelMessage(
                MessageRole.USER,
                f"prompt={pins.complete_merge_prompt}\ntitle={title}\n"
                f"ordered_segments:\n{joined}\n[end ordered_segments]",
            ),
        ),
        structured_output=StructuredOutputConstraint(
            "material_complete_merge_v1", MATERIAL_OUTPUT_SCHEMA, strict=True
        ),
        max_output_tokens=65_536,
        temperature=0,
        metadata={
            "material_prompt": pins.complete_merge_prompt,
            "model_id": pins.model_id,
            "tool_calls": False,
        },
    )


def study_from_complete_request(
    complete_markdown: str,
    *,
    title: str,
    pins: GenerationPipelinePins,
) -> ModelRequest:
    return ModelRequest(
        messages=(
            ModelMessage(
                MessageRole.SYSTEM,
                "You are the Cardine study-material editor. Derive a concise study guide "
                "from the complete lesson below, retaining uncertainty, teacher emphasis, "
                "numeric anchors, and truthful limitations. Return only the strict "
                "{markdown,limitations} JSON object and use the complete lesson as the sole "
                "source.",
            ),
            ModelMessage(
                MessageRole.USER,
                f"prompt={pins.study_prompt}\ntitle={title}\n"
                f"complete_lesson:\n{complete_markdown}\n[end complete_lesson]",
            ),
        ),
        structured_output=StructuredOutputConstraint(
            "material_study_from_complete_v1", MATERIAL_OUTPUT_SCHEMA, strict=True
        ),
        max_output_tokens=32_768,
        temperature=0,
        metadata={
            "material_prompt": pins.study_prompt,
            "model_id": pins.model_id,
            "tool_calls": False,
        },
    )


def request_fingerprint(request: ModelRequest) -> str:
    payload: JsonObject = {
        "messages": tuple(
            {"role": item.role.value, "content": item.content} for item in request.messages
        ),
        "structured_output": (
            {
                "name": request.structured_output.name,
                "schema": request.structured_output.schema,
                "strict": request.structured_output.strict,
            }
            if request.structured_output
            else None
        ),
        "max_output_tokens": request.max_output_tokens,
        "temperature": request.temperature,
        "metadata": request.metadata,
    }
    return sha256(b"material-model-request@1\0" + canonical_json_bytes(payload)).hexdigest()


__all__ = [
    "BOUNDARY_SCHEMA",
    "COMPLETE_MERGE_PROMPT",
    "COMPLETE_SEGMENT_PROMPT",
    "MATERIAL_BOUNDARIES_PROMPT",
    "MATERIAL_OUTPUT_SCHEMA",
    "STUDY_FROM_COMPLETE_PROMPT",
    "boundary_request",
    "complete_merge_request",
    "complete_segment_request",
    "request_fingerprint",
    "study_from_complete_request",
]
