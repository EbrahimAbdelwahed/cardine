"""Strict exporter-neutral study-artifact content contracts and codecs."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, assert_never, cast

from study_agent.domain._validation import JsonObject, JsonValue, freeze_object, require_text
from study_agent.domain.artifact import (
    AssessmentFormat,
    HybridFlashcardRole,
    LessonMaterialVariant,
    MorphologyCognitiveFunction,
    MorphologyFamily,
    MorphologyFlashcardRole,
    RetrievalForm,
    StudyArtifactKind,
    VerifiedMediaRef,
    require_sha256,
)
from study_agent.domain.identifiers import BlobId
from study_agent.domain.source import BlobRef
from study_agent.pedagogy import (
    HYBRID_MACRO_DETAIL_V1,
    MORPHOLOGY_FIRST_ANATOMY_V1,
    PedagogicalProfileRef,
)

_MAX_COLLECTION = 64
_FORBIDDEN_KEYS = frozenset(
    {
        "artifact_id",
        "revision_id",
        "batch_id",
        "candidate_id",
        "candidate_key",
        "decision",
        "status",
        "accepted",
        "deck",
        "deck_name",
        "tag",
        "tags",
        "template",
        "template_name",
        "html",
        "raw_html",
        "media_name",
        "filename",
        "provider",
        "provider_id",
        "model",
        "model_id",
        "api_key",
        "credential",
        "credentials",
        "password",
        "secret",
        "attempt",
        "grade",
        "mastery",
        "schedule",
        "learner_model",
    }
)


@dataclass(frozen=True, slots=True)
class AnswerBlock:
    label: str
    text: str
    key_points: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_text(self.label, "answer block label")
        require_text(self.text, "answer block text")
        object.__setattr__(
            self,
            "key_points",
            _bounded_texts(self.key_points, "answer block key_points", allow_empty=True),
        )
        _reject_markup(self.label, "answer block label")
        _reject_markup(self.text, "answer block text")


@dataclass(frozen=True, slots=True)
class HybridFlashcardContent:
    retrieval_form: RetrievalForm
    prompt: str
    answer_blocks: tuple[AnswerBlock, ...]
    role: HybridFlashcardRole
    rationale: str
    source_commitment_indices: tuple[int, ...]
    parent_ordinal: int | None = None
    media: tuple[VerifiedMediaRef, ...] = ()
    profile: PedagogicalProfileRef = HYBRID_MACRO_DETAIL_V1

    def __post_init__(self) -> None:
        _validate_flashcard_common(self)
        if self.profile != HYBRID_MACRO_DETAIL_V1:
            raise ValueError("hybrid flashcard requires hybrid-macro-detail@1")
        if not isinstance(self.role, HybridFlashcardRole):
            raise TypeError("hybrid flashcard role is invalid")


@dataclass(frozen=True, slots=True)
class MorphologyFlashcardContent:
    retrieval_form: RetrievalForm
    prompt: str
    answer_blocks: tuple[AnswerBlock, ...]
    role: MorphologyFlashcardRole
    family: MorphologyFamily
    cognitive_function: MorphologyCognitiveFunction
    rationale: str
    source_commitment_indices: tuple[int, ...]
    parent_ordinal: int | None = None
    media: tuple[VerifiedMediaRef, ...] = ()
    profile: PedagogicalProfileRef = MORPHOLOGY_FIRST_ANATOMY_V1

    def __post_init__(self) -> None:
        _validate_flashcard_common(self)
        if self.profile != MORPHOLOGY_FIRST_ANATOMY_V1:
            raise ValueError("morphology flashcard requires morphology-first-anatomy@1")
        if not isinstance(self.role, MorphologyFlashcardRole):
            raise TypeError("morphology flashcard role is invalid")
        if not isinstance(self.family, MorphologyFamily):
            raise TypeError("morphology family is invalid")
        if not isinstance(self.cognitive_function, MorphologyCognitiveFunction):
            raise TypeError("morphology cognitive function is invalid")


type FlashcardContent = HybridFlashcardContent | MorphologyFlashcardContent


@dataclass(frozen=True, slots=True)
class AssessmentItemContent:
    format: AssessmentFormat
    prompt: str
    options: tuple[str, ...]
    expected_response: str
    evaluation_criteria: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.format, AssessmentFormat):
            raise TypeError("assessment format is invalid")
        require_text(self.prompt, "assessment prompt")
        require_text(self.expected_response, "assessment expected_response")
        object.__setattr__(
            self, "evaluation_criteria", _bounded_texts(self.evaluation_criteria, "criteria")
        )
        options = _bounded_texts(self.options, "assessment options", allow_empty=True)
        if self.format is AssessmentFormat.FREE_RESPONSE and options:
            raise ValueError("free-response assessment cannot contain options")
        if self.format is not AssessmentFormat.FREE_RESPONSE and len(options) < 2:
            raise ValueError("choice assessment requires at least two options")
        object.__setattr__(self, "options", options)
        _reject_markup(self.prompt, "assessment prompt")
        _reject_markup(self.expected_response, "assessment expected_response")


@dataclass(frozen=True, slots=True)
class EvidenceObservation:
    value: str
    source_commitment_indices: tuple[int, ...]

    def __post_init__(self) -> None:
        require_text(self.value, "observation value")
        _reject_markup(self.value, "observation value")
        object.__setattr__(
            self,
            "source_commitment_indices",
            _bounded_indices(self.source_commitment_indices, "observation evidence"),
        )


@dataclass(frozen=True, slots=True)
class ExamBlueprintContent:
    sample_size: int
    observed_topics: tuple[EvidenceObservation, ...]
    observed_formats: tuple[EvidenceObservation, ...]
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.sample_size) is not int or self.sample_size < 1:
            raise ValueError("exam blueprint sample_size must be positive")
        _require_members(self.observed_topics, EvidenceObservation, "observed_topics")
        _require_members(self.observed_formats, EvidenceObservation, "observed_formats")
        object.__setattr__(self, "observed_topics", _bounded_unique(self.observed_topics, "topics"))
        object.__setattr__(
            self, "observed_formats", _bounded_unique(self.observed_formats, "formats")
        )
        object.__setattr__(self, "limitations", _bounded_texts(self.limitations, "limitations"))


@dataclass(frozen=True, slots=True)
class StudyBriefSection:
    heading: str
    summary: str
    key_points: tuple[str, ...]

    def __post_init__(self) -> None:
        require_text(self.heading, "section heading")
        require_text(self.summary, "section summary")
        _reject_markup(self.heading, "section heading")
        _reject_markup(self.summary, "section summary")
        object.__setattr__(self, "key_points", _bounded_texts(self.key_points, "key_points"))


@dataclass(frozen=True, slots=True)
class StudyBriefContent:
    title: str
    objective: str
    sections: tuple[StudyBriefSection, ...]
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        require_text(self.title, "study brief title")
        require_text(self.objective, "study brief objective")
        _reject_markup(self.title, "study brief title")
        _reject_markup(self.objective, "study brief objective")
        _require_members(self.sections, StudyBriefSection, "sections")
        object.__setattr__(self, "sections", _bounded_unique(self.sections, "sections"))
        object.__setattr__(self, "limitations", _bounded_texts(self.limitations, "limitations"))


@dataclass(frozen=True, slots=True)
class LessonMaterialContent:
    """A long generated lesson body addressed by an existing content blob."""

    variant: LessonMaterialVariant
    title: str
    markdown_blob: BlobRef
    markdown_character_length: int
    direct_parent_blob_sha256: str
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.variant, LessonMaterialVariant):
            raise TypeError("lesson material variant is invalid")
        require_text(self.title, "lesson material title")
        _reject_markup(self.title, "lesson material title")
        if not isinstance(self.markdown_blob, BlobRef):
            raise TypeError("lesson material markdown_blob must be BlobRef")
        if str(self.markdown_blob.id) != f"sha256:{self.markdown_blob.checksum_sha256}":
            raise ValueError("lesson material blob id must match its checksum")
        if self.markdown_blob.byte_length < 1:
            raise ValueError("lesson material markdown blob must be non-empty")
        if type(self.markdown_character_length) is not int or self.markdown_character_length < 1:
            raise ValueError("lesson material character length must be positive")
        require_sha256(self.direct_parent_blob_sha256, "lesson material direct parent")
        object.__setattr__(
            self, "limitations", _bounded_texts(self.limitations, "lesson material limitations")
        )


type ArtifactContent = (
    FlashcardContent
    | AssessmentItemContent
    | ExamBlueprintContent
    | StudyBriefContent
    | LessonMaterialContent
)


@dataclass(frozen=True, slots=True)
class StudyArtifactEnvelope:
    kind: StudyArtifactKind
    content: ArtifactContent
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("artifact content schema_version must equal 1")
        expected = {
            StudyArtifactKind.FLASHCARD: (HybridFlashcardContent, MorphologyFlashcardContent),
            StudyArtifactKind.ASSESSMENT_ITEM: (AssessmentItemContent,),
            StudyArtifactKind.EXAM_BLUEPRINT: (ExamBlueprintContent,),
            StudyArtifactKind.STUDY_BRIEF: (StudyBriefContent,),
            StudyArtifactKind.LESSON_MATERIAL: (LessonMaterialContent,),
        }
        if not isinstance(self.kind, StudyArtifactKind) or not isinstance(
            self.content, expected[self.kind]
        ):
            raise ValueError("artifact kind and content codec do not match")

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "kind": self.kind.value,
                "schema_version": self.schema_version,
                "content": _content_json(self.content),
            }
        )

    def to_bytes(self) -> bytes:
        return _canonical_bytes(self.to_json())

    @classmethod
    def from_json(cls, value: Mapping[str, JsonValue]) -> StudyArtifactEnvelope:
        _reject_reserved(value)
        _exact(value, {"kind", "schema_version", "content"}, "artifact envelope")
        kind = StudyArtifactKind(_string(value, "kind"))
        version = _integer(value, "schema_version")
        content = _mapping(value, "content")
        return cls(kind=kind, schema_version=version, content=_decode_content(kind, content))

    @classmethod
    def from_bytes(cls, data: bytes) -> StudyArtifactEnvelope:
        try:
            decoded: Any = json.loads(data)
            if not isinstance(decoded, dict):
                raise ValueError("artifact content must be a JSON object")
            result = cls.from_json(cast(dict[str, JsonValue], decoded))
        except (TypeError, ValueError) as exc:
            raise ValueError("artifact content bytes are not canonical") from exc
        if result.to_bytes() != data:
            raise ValueError("artifact content bytes are not canonical")
        return result


def _validate_flashcard_common(value: HybridFlashcardContent | MorphologyFlashcardContent) -> None:
    if not isinstance(value.retrieval_form, RetrievalForm):
        raise TypeError("flashcard retrieval form is invalid")
    require_text(value.prompt, "flashcard prompt")
    require_text(value.rationale, "flashcard rationale")
    _reject_markup(value.prompt, "flashcard prompt")
    _reject_markup(value.rationale, "flashcard rationale")
    _require_members(value.answer_blocks, AnswerBlock, "answer_blocks")
    object.__setattr__(
        value, "answer_blocks", _bounded_unique(value.answer_blocks, "answer_blocks")
    )
    object.__setattr__(
        value,
        "source_commitment_indices",
        _bounded_indices(value.source_commitment_indices, "source commitment indices"),
    )
    if value.parent_ordinal is not None and (
        type(value.parent_ordinal) is not int or value.parent_ordinal < 0
    ):
        raise ValueError("parent_ordinal must be non-negative")
    media = tuple(value.media)
    _require_members(media, VerifiedMediaRef, "media")
    if len(media) > _MAX_COLLECTION or len(set(media)) != len(media):
        raise ValueError("media must be bounded and unique")
    if any(item.source_commitment_index not in value.source_commitment_indices for item in media):
        raise ValueError("verified media must link to a flashcard source commitment")
    object.__setattr__(value, "media", media)


def _content_json(content: ArtifactContent) -> dict[str, JsonValue]:
    if isinstance(content, HybridFlashcardContent):
        return _flashcard_json(content, {"role": content.role.value})
    if isinstance(content, MorphologyFlashcardContent):
        return _flashcard_json(
            content,
            {
                "role": content.role.value,
                "family": content.family.value,
                "cognitive_function": content.cognitive_function.value,
            },
        )
    if isinstance(content, AssessmentItemContent):
        return {
            "format": content.format.value,
            "prompt": content.prompt,
            "options": content.options,
            "expected_response": content.expected_response,
            "evaluation_criteria": content.evaluation_criteria,
        }
    if isinstance(content, ExamBlueprintContent):
        return {
            "sample_size": content.sample_size,
            "observed_topics": tuple(_observation_json(item) for item in content.observed_topics),
            "observed_formats": tuple(_observation_json(item) for item in content.observed_formats),
            "limitations": content.limitations,
        }
    if isinstance(content, LessonMaterialContent):
        return {
            "variant": content.variant.value,
            "title": content.title,
            "markdown_blob": _blob_json(content.markdown_blob),
            "markdown_character_length": content.markdown_character_length,
            "direct_parent_blob_sha256": content.direct_parent_blob_sha256,
            "limitations": content.limitations,
        }
    if isinstance(content, StudyBriefContent):
        return {
            "title": content.title,
            "objective": content.objective,
            "sections": tuple(
                {"heading": item.heading, "summary": item.summary, "key_points": item.key_points}
                for item in content.sections
            ),
            "limitations": content.limitations,
        }
    assert_never(content)


def _flashcard_json(
    content: HybridFlashcardContent | MorphologyFlashcardContent,
    discriminators: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    return {
        "profile": {"id": content.profile.id.value, "version": content.profile.version},
        "retrieval_form": content.retrieval_form.value,
        "prompt": content.prompt,
        "answer_blocks": tuple(
            {"label": item.label, "text": item.text, "key_points": item.key_points}
            for item in content.answer_blocks
        ),
        **discriminators,
        "rationale": content.rationale,
        "source_commitment_indices": content.source_commitment_indices,
        "parent_ordinal": content.parent_ordinal,
        "media": tuple(_media_json(item) for item in content.media),
    }


def _decode_content(kind: StudyArtifactKind, value: Mapping[str, JsonValue]) -> ArtifactContent:
    if kind is StudyArtifactKind.FLASHCARD:
        profile = _profile(_mapping(value, "profile"))
        common = _decode_flashcard_common(value)
        if profile == HYBRID_MACRO_DETAIL_V1:
            _exact(value, _FLASH_COMMON | {"role"}, "hybrid flashcard")
            return HybridFlashcardContent(
                profile=profile, role=HybridFlashcardRole(_string(value, "role")), **common
            )
        if profile == MORPHOLOGY_FIRST_ANATOMY_V1:
            _exact(
                value,
                _FLASH_COMMON | {"role", "family", "cognitive_function"},
                "morphology flashcard",
            )
            return MorphologyFlashcardContent(
                profile=profile,
                role=MorphologyFlashcardRole(_string(value, "role")),
                family=MorphologyFamily(_string(value, "family")),
                cognitive_function=MorphologyCognitiveFunction(
                    _string(value, "cognitive_function")
                ),
                **common,
            )
        raise ValueError("unknown flashcard profile")
    if kind is StudyArtifactKind.ASSESSMENT_ITEM:
        _exact(
            value,
            {"format", "prompt", "options", "expected_response", "evaluation_criteria"},
            "assessment item",
        )
        return AssessmentItemContent(
            format=AssessmentFormat(_string(value, "format")),
            prompt=_string(value, "prompt"),
            options=_strings(value, "options"),
            expected_response=_string(value, "expected_response"),
            evaluation_criteria=_strings(value, "evaluation_criteria"),
        )
    if kind is StudyArtifactKind.EXAM_BLUEPRINT:
        _exact(
            value,
            {"sample_size", "observed_topics", "observed_formats", "limitations"},
            "exam blueprint",
        )
        return ExamBlueprintContent(
            sample_size=_integer(value, "sample_size"),
            observed_topics=_observations(value, "observed_topics"),
            observed_formats=_observations(value, "observed_formats"),
            limitations=_strings(value, "limitations"),
        )
    if kind is StudyArtifactKind.LESSON_MATERIAL:
        _exact(
            value,
            {
                "variant",
                "title",
                "markdown_blob",
                "markdown_character_length",
                "direct_parent_blob_sha256",
                "limitations",
            },
            "lesson material",
        )
        return LessonMaterialContent(
            variant=LessonMaterialVariant(_string(value, "variant")),
            title=_string(value, "title"),
            markdown_blob=_blob(_mapping(value, "markdown_blob")),
            markdown_character_length=_integer(value, "markdown_character_length"),
            direct_parent_blob_sha256=_string(value, "direct_parent_blob_sha256"),
            limitations=_strings(value, "limitations"),
        )
    if kind is StudyArtifactKind.STUDY_BRIEF:
        _exact(value, {"title", "objective", "sections", "limitations"}, "study brief")
        return StudyBriefContent(
            title=_string(value, "title"),
            objective=_string(value, "objective"),
            sections=_sections(value, "sections"),
            limitations=_strings(value, "limitations"),
        )
    assert_never(kind)


_FLASH_COMMON = {
    "profile",
    "retrieval_form",
    "prompt",
    "answer_blocks",
    "rationale",
    "source_commitment_indices",
    "parent_ordinal",
    "media",
}


def _decode_flashcard_common(value: Mapping[str, JsonValue]) -> dict[str, Any]:
    parent = value.get("parent_ordinal")
    if parent is not None and type(parent) is not int:
        raise ValueError("parent_ordinal has invalid type")
    return {
        "retrieval_form": RetrievalForm(_string(value, "retrieval_form")),
        "prompt": _string(value, "prompt"),
        "answer_blocks": _answer_blocks(value, "answer_blocks"),
        "rationale": _string(value, "rationale"),
        "source_commitment_indices": _integers(value, "source_commitment_indices"),
        "parent_ordinal": parent,
        "media": _media_items(value, "media"),
    }


def _profile(value: Mapping[str, JsonValue]) -> PedagogicalProfileRef:
    from study_agent.pedagogy import PedagogicalProfileId

    _exact(value, {"id", "version"}, "profile")
    return PedagogicalProfileRef(
        PedagogicalProfileId(_string(value, "id")), _integer(value, "version")
    )


def _media_json(item: VerifiedMediaRef) -> dict[str, JsonValue]:
    return {
        "blob_id": str(item.blob_id),
        "sha256": item.sha256,
        "source_commitment_index": item.source_commitment_index,
        "verifier_id": item.verifier_id,
        "verifier_version": item.verifier_version,
        "verifier_fingerprint": item.verifier_fingerprint,
        "alt_text": item.alt_text,
    }


def _blob_json(item: BlobRef) -> dict[str, JsonValue]:
    return {
        "id": str(item.id),
        "checksum_sha256": item.checksum_sha256,
        "byte_length": item.byte_length,
    }


def _blob(value: Mapping[str, JsonValue]) -> BlobRef:
    _exact(value, {"id", "checksum_sha256", "byte_length"}, "lesson material blob")
    checksum = _string(value, "checksum_sha256")
    blob_id = _string(value, "id")
    if blob_id != f"sha256:{checksum}":
        raise ValueError("lesson material blob id must match its checksum")
    return BlobRef(BlobId(blob_id), checksum, _integer(value, "byte_length"))


def _media_items(value: Mapping[str, JsonValue], key: str) -> tuple[VerifiedMediaRef, ...]:
    return tuple(
        VerifiedMediaRef(
            blob_id=BlobId(_string(item, "blob_id")),
            sha256=_string(item, "sha256"),
            source_commitment_index=_integer(item, "source_commitment_index"),
            verifier_id=_string(item, "verifier_id"),
            verifier_version=_string(item, "verifier_version"),
            verifier_fingerprint=_string(item, "verifier_fingerprint"),
            alt_text=_string(item, "alt_text"),
        )
        for item in _objects_exact(
            value,
            key,
            {
                "blob_id",
                "sha256",
                "source_commitment_index",
                "verifier_id",
                "verifier_version",
                "verifier_fingerprint",
                "alt_text",
            },
        )
    )


def _answer_blocks(value: Mapping[str, JsonValue], key: str) -> tuple[AnswerBlock, ...]:
    return tuple(
        AnswerBlock(_string(item, "label"), _string(item, "text"), _strings(item, "key_points"))
        for item in _objects_exact(value, key, {"label", "text", "key_points"})
    )


def _observation_json(item: EvidenceObservation) -> dict[str, JsonValue]:
    return {"value": item.value, "source_commitment_indices": item.source_commitment_indices}


def _observations(value: Mapping[str, JsonValue], key: str) -> tuple[EvidenceObservation, ...]:
    return tuple(
        EvidenceObservation(_string(item, "value"), _integers(item, "source_commitment_indices"))
        for item in _objects_exact(value, key, {"value", "source_commitment_indices"})
    )


def _sections(value: Mapping[str, JsonValue], key: str) -> tuple[StudyBriefSection, ...]:
    return tuple(
        StudyBriefSection(
            _string(item, "heading"), _string(item, "summary"), _strings(item, "key_points")
        )
        for item in _objects_exact(value, key, {"heading", "summary", "key_points"})
    )


def _objects_exact(
    value: Mapping[str, JsonValue], key: str, fields: set[str]
) -> tuple[Mapping[str, JsonValue], ...]:
    raw = value.get(key)
    if not isinstance(raw, tuple) and not isinstance(raw, list):
        raise ValueError(f"{key} must be an array")
    result = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ValueError(f"{key} items must be objects")
        _exact(item, fields, f"{key} item")
        result.append(item)
    return tuple(result)


def _mapping(value: Mapping[str, JsonValue], key: str) -> Mapping[str, JsonValue]:
    item = value.get(key)
    if not isinstance(item, Mapping):
        raise ValueError(f"{key} must be an object")
    return item


def _string(value: Mapping[str, JsonValue], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise ValueError(f"{key} must be a string")
    return item


def _integer(value: Mapping[str, JsonValue], key: str) -> int:
    item = value.get(key)
    if type(item) is not int:
        raise ValueError(f"{key} must be an integer")
    return item


def _strings(value: Mapping[str, JsonValue], key: str) -> tuple[str, ...]:
    raw = value.get(key)
    if not isinstance(raw, (tuple, list)) or any(not isinstance(item, str) for item in raw):
        raise ValueError(f"{key} must be an array of strings")
    return tuple(cast(str, item) for item in raw)


def _integers(value: Mapping[str, JsonValue], key: str) -> tuple[int, ...]:
    raw = value.get(key)
    if not isinstance(raw, (tuple, list)) or any(type(item) is not int for item in raw):
        raise ValueError(f"{key} must be an array of integers")
    return tuple(cast(int, item) for item in raw)


def _exact(value: Mapping[str, JsonValue], expected: set[str], name: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{name} fields must be exactly {sorted(expected)}")


def _reject_reserved(value: JsonValue, path: str = "artifact") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = key.lower().replace("-", "_")
            if normalized in _FORBIDDEN_KEYS:
                raise ValueError(f"{path}.{key} is forbidden in artifact content")
            _reject_reserved(item, f"{path}.{key}")
    elif isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            _reject_reserved(item, f"{path}[{index}]")


def _require_members(values: tuple[Any, ...], expected: type[object], name: str) -> None:
    if not all(isinstance(item, expected) for item in values):
        raise TypeError(f"{name} items must use {expected.__name__}")


def _bounded_texts(
    values: tuple[str, ...], name: str, *, allow_empty: bool = False
) -> tuple[str, ...]:
    result = tuple(values)
    if (
        (not result and not allow_empty)
        or len(result) > _MAX_COLLECTION
        or len(set(result)) != len(result)
    ):
        raise ValueError(f"{name} must be non-empty, bounded, and unique")
    for item in result:
        require_text(item, f"{name} item")
        _reject_markup(item, f"{name} item")
    return result


def _bounded_indices(values: tuple[int, ...], name: str) -> tuple[int, ...]:
    result = tuple(values)
    if (
        not result
        or len(result) > _MAX_COLLECTION
        or len(set(result)) != len(result)
        or any(type(item) is not int or item < 0 for item in result)
    ):
        raise ValueError(f"{name} must be non-empty, bounded, unique non-negative integers")
    return result


def _bounded_unique(values: tuple[Any, ...], name: str) -> tuple[Any, ...]:
    result = tuple(values)
    if not result or len(result) > _MAX_COLLECTION or len(set(result)) != len(result):
        raise ValueError(f"{name} must be non-empty, bounded, and unique")
    return result


def _reject_markup(value: str, name: str) -> None:
    if "<" in value or ">" in value:
        raise ValueError(f"{name} cannot contain HTML")


def _canonical_bytes(value: JsonObject) -> bytes:
    def plain(item: JsonValue) -> object:
        if isinstance(item, Mapping):
            return {key: plain(child) for key, child in item.items()}
        if isinstance(item, tuple):
            return [plain(child) for child in item]
        return item

    return json.dumps(
        plain(value), ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode()


__all__ = [
    "AnswerBlock",
    "ArtifactContent",
    "AssessmentItemContent",
    "EvidenceObservation",
    "ExamBlueprintContent",
    "FlashcardContent",
    "HybridFlashcardContent",
    "LessonMaterialContent",
    "MorphologyFlashcardContent",
    "StudyArtifactEnvelope",
    "StudyBriefContent",
    "StudyBriefSection",
]
