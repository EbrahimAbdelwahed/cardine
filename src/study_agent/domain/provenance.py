from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256

from ._validation import JsonObject, require_text
from .identifiers import ChunkId, RevisionId, RunId, SourceId


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


def _require_fingerprint(value: str, name: str) -> None:
    require_text(value, name)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 fingerprint")
