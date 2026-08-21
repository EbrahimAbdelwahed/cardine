"""Mechanical validators for provider responses and material ancestry."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256

from study_agent.ports.model import ModelFinishReason, ModelResponse

from .generation_contracts import MAX_OUTPUT_BYTES, GenerationPipelinePins

_UNCERTAINTY = re.compile(
    r"\b(?:unclear|uncertain|not sure|possibly|maybe|might|may|could)\b", re.I
)
_EMPHASIS = re.compile(
    r"\b(?:important|key|remember|emphasized|attenzione|ricorda|fondamentale)\b", re.I
)
_NUMERIC = re.compile(
    r"(?<![A-Za-z])\d+(?:[.,]\d+)?(?:\s?%|\s?(?:mg|ml|cm|mm|kg|hz|s|min|h))?\b", re.I
)


class MaterialValidationError(ValueError):
    """A provider response cannot be admitted to a material stage."""

    def __init__(self, message: str, *, code: str = "malformed_output") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ValidatedText:
    text: str
    fingerprint: str
    character_length: int
    limitations: tuple[str, ...]


def parse_material_output(response: ModelResponse, *, stage: str) -> tuple[str, tuple[str, ...]]:
    """Decode the strict structured response used by text-producing stages."""

    output = response.structured_output
    if output is None or not isinstance(output, Mapping):
        raise MaterialValidationError(f"{stage} response must contain structured JSON")
    if set(output) != {"markdown", "limitations"}:
        raise MaterialValidationError(f"{stage} structured output fields are not canonical")
    markdown = output.get("markdown")
    raw_limitations = output.get("limitations")
    if not isinstance(markdown, str) or not markdown.strip():
        raise MaterialValidationError(f"{stage} structured markdown is empty")
    if not isinstance(raw_limitations, tuple) or not raw_limitations:
        raise MaterialValidationError(f"{stage} limitations must be a non-empty array")
    limitations: list[str] = []
    for item in raw_limitations:
        if not isinstance(item, str) or not item.strip():
            raise MaterialValidationError(f"{stage} limitations contain invalid text")
        if len(item) > 1_000 or "\x00" in item:
            raise MaterialValidationError(f"{stage} limitations are oversized")
        if unicodedata.normalize("NFC", item) != item:
            raise MaterialValidationError(f"{stage} limitations are not NFC-normalized")
        limitations.append(item)
    return markdown, tuple(limitations)


def validate_provider_response(
    response: ModelResponse,
    *,
    pins: GenerationPipelinePins,
    structured: bool = True,
) -> ModelResponse:
    """Prove the configured Luna invocation and portable response envelope."""

    invocation = response.invocation
    expected_adapter, expected_version = pins.model_adapter.split("@", 1)
    if invocation.adapter_id != expected_adapter or invocation.adapter_version != expected_version:
        raise MaterialValidationError("provider response used an unpinned adapter")
    if invocation.model_id != pins.model_id:
        raise MaterialValidationError("provider response used an unpinned model")
    if response.tool_calls or response.finish_reason is ModelFinishReason.TOOL_CALLS:
        raise MaterialValidationError("material generation forbids tool calls")
    if response.finish_reason is not ModelFinishReason.STOP:
        raise MaterialValidationError("material response did not finish with stop")
    if structured and response.structured_output is None:
        raise MaterialValidationError("boundary response lacks structured output")
    if not structured and not response.content:
        raise MaterialValidationError("material response lacks Markdown content")
    return response


def validate_markdown(
    text: str,
    *,
    source_text: str,
    stage: str,
    max_characters: int,
    parent_text: str | None = None,
    limitations: tuple[str, ...] | None = None,
) -> ValidatedText:
    if not isinstance(text, str) or not text.strip():
        raise MaterialValidationError(f"{stage} output is empty")
    if "\x00" in text:
        raise MaterialValidationError(f"{stage} output contains NUL")
    try:
        encoded = text.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise MaterialValidationError(f"{stage} output is not valid UTF-8") from error
    if len(encoded) > MAX_OUTPUT_BYTES or len(text) > max_characters:
        raise MaterialValidationError(f"{stage} output exceeds its bound", code="oversize")
    if unicodedata.normalize("NFC", text) != text:
        raise MaterialValidationError(f"{stage} output is not NFC-normalized")
    if re.search(r"(?m)^\s{0,3}#{1,6}\s+\S", text) is None:
        raise MaterialValidationError(f"{stage} output lacks a Markdown heading")

    source_markers = _markers(source_text)
    output_markers = _markers(text)
    if source_markers[0] and not source_markers[0].intersection(output_markers[0]):
        raise MaterialValidationError(f"{stage} output dropped uncertainty markers")
    if source_markers[1] and not source_markers[1].intersection(output_markers[1]):
        raise MaterialValidationError(f"{stage} output dropped teacher-emphasis markers")
    missing_numbers = source_markers[2] - output_markers[2]
    if missing_numbers:
        raise MaterialValidationError(
            f"{stage} output dropped numeric anchors: {sorted(missing_numbers)}"
        )
    if parent_text is not None and not parent_text.strip():
        raise MaterialValidationError(f"{stage} has an empty parent")
    resolved_limitations = limitations or _limitations(text, source_text)
    _validate_limitations(resolved_limitations, source_text, stage)
    return ValidatedText(text, sha256(encoded).hexdigest(), len(text), resolved_limitations)


def validate_complete_markdown(
    text: str,
    *,
    transcript_text: str,
    segment_texts: tuple[str, ...],
    max_characters: int = 500_000,
    limitations: tuple[str, ...] | None = None,
) -> ValidatedText:
    if not segment_texts or any(not item.strip() for item in segment_texts):
        raise MaterialValidationError("complete output requires every completed segment")
    result = validate_markdown(
        text,
        source_text=transcript_text,
        stage="complete merge",
        max_characters=max_characters,
        limitations=limitations,
    )
    # The merge need not be byte-identical to each segment, but it must retain
    # every segment's numeric and uncertainty/emphasis commitments.
    for segment in segment_texts:
        _ensure_commitments(segment, result.text, "complete merge")
    return result


def validate_study_markdown(
    text: str,
    *,
    complete_text: str,
    max_characters: int = 300_000,
    limitations: tuple[str, ...] | None = None,
) -> ValidatedText:
    return validate_markdown(
        text,
        source_text=complete_text,
        stage="study material",
        max_characters=max_characters,
        parent_text=complete_text,
        limitations=limitations,
    )


def _ensure_commitments(source: str, output: str, stage: str) -> None:
    markers = _markers(source)
    target = _markers(output)
    if markers[0] and not markers[0].intersection(target[0]):
        raise MaterialValidationError(f"{stage} dropped a segment uncertainty marker")
    if markers[1] and not markers[1].intersection(target[1]):
        raise MaterialValidationError(f"{stage} dropped a segment emphasis marker")
    missing = markers[2] - target[2]
    if missing:
        raise MaterialValidationError(f"{stage} dropped segment numeric anchors")


def _markers(text: str) -> tuple[set[str], set[str], set[str]]:
    return (
        {item.lower() for item in _UNCERTAINTY.findall(text)},
        {item.lower() for item in _EMPHASIS.findall(text)},
        {item for item in _NUMERIC.findall(text)},
    )


def _limitations(text: str, source: str) -> tuple[str, ...]:
    if _UNCERTAINTY.search(source) and not re.search(
        r"\b(?:uncertain|unclear|limitation|not established)\b", text, re.I
    ):
        return (
            "The transcript contains uncertainty markers; the generated material preserves only "
            "what was explicit.",
        )
    return (
        "Generated from the pinned transcript; semantic completeness is mechanically validated "
        "but not independently proven.",
    )


def _validate_limitations(limitations: tuple[str, ...], source: str, stage: str) -> None:
    if not limitations or any(not item.strip() for item in limitations):
        raise MaterialValidationError(f"{stage} limitations are missing")
    if _UNCERTAINTY.search(source) and not any(
        re.search(
            r"\b(?:uncertain(?:ty)?|unclear|limitation|not established|ambiguous)\b",
            item,
            re.I,
        )
        for item in limitations
    ):
        raise MaterialValidationError(f"{stage} limitations are not truthful about uncertainty")


__all__ = [
    "MaterialValidationError",
    "ValidatedText",
    "parse_material_output",
    "validate_complete_markdown",
    "validate_markdown",
    "validate_provider_response",
    "validate_study_markdown",
]
