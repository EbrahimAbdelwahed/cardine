from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from study_agent.domain import AnswerStatus, SegmentKind
from study_agent.domain._validation import JsonObject, JsonValue, freeze_object
from study_agent.skills import JsonSchema

from .evidence import GroundingContractError

_STATUSES = tuple(status.value for status in AnswerStatus)
_KINDS = tuple(kind.value for kind in SegmentKind)

GROUNDED_ANSWER_DRAFT_SCHEMA = JsonSchema(
    {
        "type": "object",
        "required": ("status", "segments", "unsupported_information_note"),
        "additionalProperties": False,
        "properties": {
            "status": {"type": "string", "enum": _STATUSES},
            "segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ("kind", "text", "evidence_ids"),
                    "additionalProperties": False,
                    "properties": {
                        "kind": {"type": "string", "enum": _KINDS},
                        "text": {"type": "string"},
                        "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "unsupported_information_note": {"type": ("string", "null")},
        },
    }
)


@dataclass(frozen=True, slots=True)
class DraftSegment:
    kind: SegmentKind
    text: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GroundedAnswerDraft:
    status: AnswerStatus
    segments: tuple[DraftSegment, ...]
    unsupported_information_note: str | None

    @classmethod
    def from_json(cls, value: JsonValue) -> GroundedAnswerDraft:
        if not isinstance(value, Mapping) or set(value) != {
            "status",
            "segments",
            "unsupported_information_note",
        }:
            raise GroundingContractError("answer must contain only the canonical draft fields")
        status_raw = value["status"]
        if not isinstance(status_raw, str):
            raise GroundingContractError("answer.status is unsupported")
        try:
            status = AnswerStatus(status_raw)
        except (TypeError, ValueError) as error:
            raise GroundingContractError("answer.status is unsupported") from error
        raw_segments = value["segments"]
        if not isinstance(raw_segments, tuple):
            raise GroundingContractError("answer.segments must be an array")
        segments = tuple(_parse_segment(item, index) for index, item in enumerate(raw_segments))
        note_raw = value["unsupported_information_note"]
        if note_raw is not None and (
            not isinstance(note_raw, str) or not note_raw or note_raw != note_raw.strip()
        ):
            raise GroundingContractError("unsupported_information_note must be null or text")
        return cls(status, segments, note_raw)


def validated_answer_json(
    draft: GroundedAnswerDraft, citations: tuple[tuple[JsonObject, ...], ...]
) -> JsonObject:
    return freeze_object(
        {
            "status": draft.status.value,
            "segments": tuple(
                {
                    "kind": segment.kind.value,
                    "text": segment.text,
                    "citations": segment_citations,
                }
                for segment, segment_citations in zip(draft.segments, citations, strict=True)
            ),
            "unsupported_information_note": draft.unsupported_information_note,
        }
    )


def _parse_segment(value: JsonValue, index: int) -> DraftSegment:
    if not isinstance(value, Mapping) or set(value) != {"kind", "text", "evidence_ids"}:
        raise GroundingContractError(f"answer.segments[{index}] has unexpected fields")
    kind_raw = value["kind"]
    if not isinstance(kind_raw, str):
        raise GroundingContractError(f"answer.segments[{index}].kind is unsupported")
    try:
        kind = SegmentKind(kind_raw)
    except (TypeError, ValueError) as error:
        raise GroundingContractError(f"answer.segments[{index}].kind is unsupported") from error
    text = value["text"]
    if not isinstance(text, str) or not text or text != text.strip():
        raise GroundingContractError(f"answer.segments[{index}].text must be non-empty")
    handles = value["evidence_ids"]
    if not isinstance(handles, tuple) or any(not isinstance(item, str) for item in handles):
        raise GroundingContractError(f"answer.segments[{index}].evidence_ids must be strings")
    typed_handles = tuple(item for item in handles if isinstance(item, str))
    if len(set(typed_handles)) != len(typed_handles):
        raise GroundingContractError(f"answer.segments[{index}] repeats an evidence handle")
    return DraftSegment(kind, text, typed_handles)
