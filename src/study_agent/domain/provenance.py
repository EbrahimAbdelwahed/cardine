from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256

from ._validation import JsonObject, require_aware, require_text
from .artifact import LessonMaterialVariant
from .identifiers import ArtifactRevisionId, ChunkId, EventId, RevisionId, RunId, SourceId


class ContentOrigin(StrEnum):
    ORIGINAL = "original"
    EXTRACTED = "extracted"
    REWORKED = "reworked"
    GENERATED = "generated"
    INFERRED = "inferred"


class ClaimOrigin(StrEnum):
    DECLARED = "declared"
    OBSERVED = "observed"
    INFERRED = "inferred"


class StructureOrigin(StrEnum):
    SOURCE_AUTHORED = "source_authored"
    MECHANICALLY_EXTRACTED = "mechanically_extracted"
    MODEL_PROPOSED = "model_proposed"
    HUMAN_APPROVED = "human_approved"


@dataclass(frozen=True, slots=True)
class DocumentPageSpan:
    """One source-PDF page bound to exact normalized Markdown offsets."""

    page: int
    start_offset: int
    end_offset: int

    def __post_init__(self) -> None:
        if type(self.page) is not int or self.page < 1:
            raise ValueError("page must be positive")
        if type(self.start_offset) is not int or type(self.end_offset) is not int:
            raise ValueError("page offsets must be integers")
        if self.start_offset < 0 or self.end_offset <= self.start_offset:
            raise ValueError("page offsets must describe a non-empty span")


@dataclass(frozen=True, slots=True)
class DocumentConversionProvenance:
    """Lineage for Markdown mechanically extracted from one binary PDF."""

    pdf_sha256: str
    markdown_sha256: str
    adapter_identity: str
    adapter_version: str
    manifest_fingerprint: str
    normalizer_policy: str
    limitations: tuple[str, ...]
    assets_omitted: bool = True
    page_count: int | None = None
    page_spans: tuple[DocumentPageSpan, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        for name in ("adapter_identity", "adapter_version", "normalizer_policy"):
            require_text(getattr(self, name), name)
        for name in ("pdf_sha256", "markdown_sha256", "manifest_fingerprint"):
            _require_fingerprint(getattr(self, name), name)
        if self.assets_omitted is not True:
            raise ValueError("assets_omitted must remain true")
        if self.page_count is not None and (
            type(self.page_count) is not int or self.page_count < 1
        ):
            raise ValueError("page_count must be positive when present")
        object.__setattr__(self, "page_spans", tuple(self.page_spans))
        if self.page_spans:
            if self.page_count != len(self.page_spans):
                raise ValueError("page_count must match page_spans")
            previous_end = 0
            for expected_page, span in enumerate(self.page_spans, 1):
                if span.page != expected_page or span.start_offset < previous_end:
                    raise ValueError("page_spans must be ordered and non-overlapping")
                previous_end = span.end_offset
        if type(self.schema_version) is not int or self.schema_version < 1:
            raise ValueError("schema_version must be positive")
        object.__setattr__(self, "limitations", tuple(self.limitations))
        if not self.limitations or len(set(self.limitations)) != len(self.limitations):
            raise ValueError("limitations must be non-empty and unique")
        for item in self.limitations:
            require_text(item, "limitation")

    @property
    def fingerprint(self) -> str:
        from study_agent.state.serialization import canonical_json_bytes

        payload: JsonObject = {
            "adapter_identity": self.adapter_identity,
            "adapter_version": self.adapter_version,
            "assets_omitted": self.assets_omitted,
            "limitations": self.limitations,
            "manifest_fingerprint": self.manifest_fingerprint,
            "markdown_sha256": self.markdown_sha256,
            "normalizer_policy": self.normalizer_policy,
            "page_count": self.page_count,
            "pdf_sha256": self.pdf_sha256,
            "page_spans": tuple(
                {
                    "page": span.page,
                    "start_offset": span.start_offset,
                    "end_offset": span.end_offset,
                }
                for span in self.page_spans
            ),
            "schema_version": self.schema_version,
        }
        return sha256(canonical_json_bytes(payload)).hexdigest()


@dataclass(frozen=True, slots=True)
class GeneratedDocumentProvenance:
    """Lineage proof for a generated Markdown source admitted after review.

    Provider and prompt receipts remain owned by the generated artifact
    provenance.  This compact source-side proof carries only the identities
    required to bind a published document to its root source, artifact
    revision, material run, and exact human decision.
    """

    root_source_id: SourceId
    root_revision_id: RevisionId
    root_normalized_blob_sha256: str
    artifact_revision_id: ArtifactRevisionId
    artifact_provenance_sha256: str
    material_run_id: RunId
    variant: LessonMaterialVariant
    direct_parent_blob_sha256: str
    human_decision_event_id: EventId
    human_decision_at: datetime
    schema_version: int = 1

    def __post_init__(self) -> None:
        for identifier, expected, name in (
            (self.root_source_id, SourceId, "root_source_id"),
            (self.root_revision_id, RevisionId, "root_revision_id"),
            (self.artifact_revision_id, ArtifactRevisionId, "artifact_revision_id"),
            (self.material_run_id, RunId, "material_run_id"),
            (self.human_decision_event_id, EventId, "human_decision_event_id"),
        ):
            if not isinstance(identifier, expected):
                raise TypeError(f"{name} has an invalid identifier type")
        for digest, name in (
            (self.root_normalized_blob_sha256, "root_normalized_blob_sha256"),
            (self.artifact_provenance_sha256, "artifact_provenance_sha256"),
            (self.direct_parent_blob_sha256, "direct_parent_blob_sha256"),
        ):
            _require_fingerprint(digest, name)
        if not isinstance(self.variant, LessonMaterialVariant):
            raise TypeError("variant must be a LessonMaterialVariant")
        require_aware(self.human_decision_at, "human_decision_at")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("generated document provenance schema_version must equal 1")

@dataclass(frozen=True, slots=True)
class PromptProvenance:
    prompt_id: str
    version: str
    composition_fingerprint: str | None = None
    layer_fingerprints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_text(self.prompt_id, "prompt_id")
        require_text(self.version, "version")
        object.__setattr__(self, "layer_fingerprints", tuple(self.layer_fingerprints))
        if self.composition_fingerprint is not None:
            _require_fingerprint(self.composition_fingerprint, "composition_fingerprint")
        for fingerprint in self.layer_fingerprints:
            _require_fingerprint(fingerprint, "layer_fingerprints item")
        if len(set(self.layer_fingerprints)) != len(self.layer_fingerprints):
            raise ValueError("layer_fingerprints must be ordered and unique")


@dataclass(frozen=True, slots=True)
class ModelUsageProvenance:
    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("model usage token counts must be non-negative")


@dataclass(frozen=True, slots=True)
class ModelProvenance:
    adapter_id: str
    adapter_version: str
    model_id: str
    response_id: str | None
    run_id: RunId
    usage: ModelUsageProvenance | None = None

    def __post_init__(self) -> None:
        for name in (
            "adapter_id",
            "adapter_version",
            "model_id",
        ):
            require_text(getattr(self, name), name)
        if self.response_id is not None:
            require_text(self.response_id, "response_id")


@dataclass(frozen=True, slots=True)
class RetrievalProvenance:
    strategy_id: str
    strategy_version: str
    query_fingerprint: str
    index_version: str
    read_set_fingerprint: str

    def __post_init__(self) -> None:
        for name in ("strategy_id", "strategy_version", "index_version"):
            require_text(getattr(self, name), name)
        _require_fingerprint(self.query_fingerprint, "query_fingerprint")
        _require_fingerprint(self.read_set_fingerprint, "read_set_fingerprint")


@dataclass(frozen=True, slots=True)
class ValidatorProvenance:
    validator_id: str
    version: str
    passed: bool
    disposition: str
    result_fingerprint: str

    def __post_init__(self) -> None:
        require_text(self.validator_id, "validator_id")
        require_text(self.version, "version")
        require_text(self.disposition, "disposition")
        _require_fingerprint(self.result_fingerprint, "result_fingerprint")


@dataclass(frozen=True, slots=True)
class SourceCommitment:
    source_id: SourceId
    revision_id: RevisionId
    chunk_id: ChunkId
    start_offset: int
    end_offset: int

    def __post_init__(self) -> None:
        if self.start_offset < 0 or self.end_offset <= self.start_offset:
            raise ValueError("source commitment offsets must describe a forward span")


@dataclass(frozen=True, slots=True)
class VersionPins:
    skill: str
    playbook: str
    prompt: str
    model_adapter: str | None
    state_contract: str
    tool_behavior: str

    def __post_init__(self) -> None:
        for name in ("skill", "playbook", "prompt", "state_contract", "tool_behavior"):
            require_text(getattr(self, name), name)
        if self.model_adapter is not None:
            require_text(self.model_adapter, "model_adapter")


@dataclass(frozen=True, slots=True)
class AnswerProvenance:
    source_commitments: tuple[SourceCommitment, ...]
    prompt: PromptProvenance
    model: ModelProvenance | None
    retrieval: RetrievalProvenance
    validators: tuple[ValidatorProvenance, ...]
    pins: VersionPins
    playbook_run_id: RunId
    event_schema_version: int = 1
    reducer_schema_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_commitments", tuple(self.source_commitments))
        object.__setattr__(self, "validators", tuple(self.validators))
        if not self.validators:
            raise ValueError("validators must contain at least one validator")
        if any(not validator.passed for validator in self.validators):
            raise ValueError("persisted answer provenance cannot contain failed validators")
        if len(set(self.source_commitments)) != len(self.source_commitments):
            raise ValueError("source_commitments must be ordered and unique")
        validator_keys = tuple((item.validator_id, item.version) for item in self.validators)
        if len(set(validator_keys)) != len(validator_keys):
            raise ValueError("validators must be ordered and unique by identity")
        if self.event_schema_version < 1 or self.reducer_schema_version < 1:
            raise ValueError("event and reducer schema versions must be positive")
        if self.model is None:
            if self.prompt.composition_fingerprint is not None:
                raise ValueError("prompt composition provenance requires a model invocation")
            if self.prompt.layer_fingerprints:
                raise ValueError("prompt layer provenance requires a model invocation")
            if self.pins.model_adapter is not None:
                raise ValueError("model adapter pin must be absent without model provenance")
        else:
            expected_pin = f"{self.model.adapter_id}@{self.model.adapter_version}"
            if self.pins.model_adapter != expected_pin:
                raise ValueError("model provenance must match the model adapter pin")
            if self.model.run_id != self.playbook_run_id:
                raise ValueError("model run must match the playbook run")


def generated_document_provenance_to_json(value: GeneratedDocumentProvenance) -> JsonObject:
    if not isinstance(value, GeneratedDocumentProvenance):
        raise TypeError("generated document provenance must use its canonical type")
    return {
        "artifact_provenance_sha256": value.artifact_provenance_sha256,
        "artifact_revision_id": str(value.artifact_revision_id),
        "direct_parent_blob_sha256": value.direct_parent_blob_sha256,
        "human_decision_at": value.human_decision_at.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z"),
        "human_decision_event_id": str(value.human_decision_event_id),
        "material_run_id": str(value.material_run_id),
        "root_normalized_blob_sha256": value.root_normalized_blob_sha256,
        "root_revision_id": str(value.root_revision_id),
        "root_source_id": str(value.root_source_id),
        "schema_version": value.schema_version,
        "variant": value.variant.value,
    }


def generated_document_provenance_to_bytes(value: GeneratedDocumentProvenance) -> bytes:
    from study_agent.state.serialization import canonical_json_bytes

    return canonical_json_bytes(generated_document_provenance_to_json(value))


def generated_document_provenance_from_json(
    value: JsonObject,
) -> GeneratedDocumentProvenance:
    if set(value) != {
        "artifact_provenance_sha256",
        "artifact_revision_id",
        "direct_parent_blob_sha256",
        "human_decision_at",
        "human_decision_event_id",
        "material_run_id",
        "root_normalized_blob_sha256",
        "root_revision_id",
        "root_source_id",
        "schema_version",
        "variant",
    }:
        raise ValueError("generated document provenance fields are not canonical")
    raw_time = value.get("human_decision_at")
    if not isinstance(raw_time, str):
        raise ValueError("human_decision_at must be an ISO-8601 timestamp")
    try:
        decision_at = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("human_decision_at must be an ISO-8601 timestamp") from error
    return GeneratedDocumentProvenance(
        root_source_id=SourceId(_string(value, "root_source_id")),
        root_revision_id=RevisionId(_string(value, "root_revision_id")),
        root_normalized_blob_sha256=_string(value, "root_normalized_blob_sha256"),
        artifact_revision_id=ArtifactRevisionId(_string(value, "artifact_revision_id")),
        artifact_provenance_sha256=_string(value, "artifact_provenance_sha256"),
        material_run_id=RunId(_string(value, "material_run_id")),
        variant=LessonMaterialVariant(_string(value, "variant")),
        direct_parent_blob_sha256=_string(value, "direct_parent_blob_sha256"),
        human_decision_event_id=EventId(_string(value, "human_decision_event_id")),
        human_decision_at=decision_at,
        schema_version=_integer(value, "schema_version"),
    )


def generated_document_provenance_from_bytes(data: bytes) -> GeneratedDocumentProvenance:
    import json

    from study_agent.domain._validation import freeze_object

    try:
        decoded = json.loads(data)
        if not isinstance(decoded, dict):
            raise ValueError("generated document provenance must be an object")
        value = generated_document_provenance_from_json(freeze_object(decoded))
    except (TypeError, ValueError) as error:
        raise ValueError("generated document provenance bytes are not canonical") from error
    if generated_document_provenance_to_bytes(value) != data:
        raise ValueError("generated document provenance bytes are not canonical")
    return value


def _string(value: JsonObject, name: str) -> str:
    item = value.get(name)
    if not isinstance(item, str) or not item or item != item.strip():
        raise ValueError(f"{name} must be non-empty trimmed text")
    return item


def _integer(value: JsonObject, name: str) -> int:
    item = value.get(name)
    if type(item) is not int:
        raise ValueError(f"{name} must be an integer")
    return item


def _require_fingerprint(value: str, name: str) -> None:
    require_text(value, name)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 fingerprint")
