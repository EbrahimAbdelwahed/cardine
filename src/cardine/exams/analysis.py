"""Capability binding, validators, and task factory for exam analysis."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from hashlib import sha256

from study_agent.capabilities.bindings import CapabilityBinding, CapabilityDependencyResolver
from study_agent.capabilities.builtin import ANALYZE_EXAM_SAMPLE_MANIFEST
from study_agent.domain._validation import JsonObject, JsonValue, freeze_object
from study_agent.exams.contracts import (
    ExamAnalysisProposal,
    ExamAnalysisRequest,
    ExamObservation,
    PreparedExamSampleScope,
)
from study_agent.grounding import GroundingContractError
from study_agent.playbooks import (
    ToolBehaviorPin,
    ValidationOutcome,
    ValidatorDisposition,
    VersionPins,
    playbook_definition_fingerprint,
)
from study_agent.playbooks.builtin.analyze_exam_sample_flow import ANALYZE_EXAM_SAMPLE_FLOW
from study_agent.ports import EvidenceStatus, SourceContentPort
from study_agent.prompts.exam_sample_analysis_v1 import EXAM_SAMPLE_ANALYSIS_PROMPT
from study_agent.skills import ArtifactReference, SemanticVersion
from study_agent.skills.builtin.analyze_exam_sample import ANALYZE_EXAM_SAMPLE_SKILL
from study_agent.workers import (
    GenerationWorkerTask,
    GenerationWorkerTaskKind,
    ValidationExpectation,
    ValidationReceiptSource,
    fingerprint_output_schema,
)

VERSION = SemanticVersion.parse("1.0.0")
_INJECTION = re.compile(
    r"(?i)(ignore\s+(all\s+)?(prior|previous|above)\s+instructions|system\s*prompt|"
    r"reveal\s+.*(credential|secret|prompt)|<\|/?(?:system|assistant|user)\|>)"
)
_PREDICTIVE = re.compile(
    r"(?i)\b(likely|likelihood|probability|expected\s+(?:next|future)|will\s+appear|frequency)\b"
)


class ExamSampleReadinessValidator:
    id = "exam_sample_readiness"
    version = VERSION

    async def validate(self, inputs: JsonObject) -> ValidationOutcome:
        try:
            if set(inputs) != {"prepared_scope"} or not isinstance(
                inputs["prepared_scope"], Mapping
            ):
                raise ValueError("exam readiness input is not exact")
            scope = PreparedExamSampleScope.from_json(inputs["prepared_scope"])
            if scope.evidence.status is EvidenceStatus.INSUFFICIENT:
                raise GroundingContractError("exam sample evidence is insufficient")
            for item in scope.evidence.items:
                normalized = unicodedata.normalize("NFKC", item.evidence.text)
                if _INJECTION.search(normalized):
                    raise GroundingContractError("exam sample contains instruction injection")
        except (GroundingContractError, KeyError, TypeError, ValueError) as error:
            return _failure(error)
        return ValidationOutcome(
            True,
            ValidatorDisposition.CONTINUE,
            {"sample_size": len(scope.samples), "scope_fingerprint": scope.scope_fingerprint},
        )


class ExamBlueprintIntegrityValidator:
    id = "exam_blueprint_integrity"
    version = VERSION

    def __init__(self, content: SourceContentPort) -> None:
        self._content = content

    async def validate(self, inputs: JsonObject) -> ValidationOutcome:
        try:
            if set(inputs) == {"output"}:
                _draft(inputs["output"])
                return ValidationOutcome(
                    True, ValidatorDisposition.CONTINUE, {"schema_valid": True}
                )
            if set(inputs) != {"prepared_scope", "draft"} or not isinstance(
                inputs["prepared_scope"], Mapping
            ):
                raise GroundingContractError("exam integrity inputs are not exact")
            scope = PreparedExamSampleScope.from_json(inputs["prepared_scope"])
            topics, formats = _draft(inputs["draft"])
            trusted = scope.evidence.by_handle()
            seen: set[str] = set()
            for observation in (*topics, *formats):
                normalized = _normalize(observation.value)
                if (
                    not normalized
                    or normalized in seen
                    or _PREDICTIVE.search(observation.value)
                    or _INJECTION.search(unicodedata.normalize("NFKC", observation.value))
                ):
                    raise GroundingContractError(
                        "exam observation is duplicate, predictive, or instruction-shaped"
                    )
                seen.add(normalized)
                if not set(observation.evidence_ids) <= set(trusted):
                    raise GroundingContractError("exam observation cites unknown evidence")
                for handle in observation.evidence_ids:
                    evidence = trusted[handle]
                    resolved = self._content.resolve(evidence.citation)
                    if resolved.citation != evidence.citation or resolved.text != evidence.text:
                        raise GroundingContractError("exam evidence changed")
            limitations = [
                "observational_only_not_predictive",
                "coverage_limited_to_selected_samples",
            ]
            if len(scope.samples) < 3:
                limitations.append("sparse_sample_fewer_than_three")
            if scope.evidence.status is EvidenceStatus.CONFLICTING:
                limitations.append("conflicting_source_evidence")
            proposal = ExamAnalysisProposal(len(scope.samples), topics, formats, tuple(limitations))
        except (GroundingContractError, KeyError, TypeError, ValueError) as error:
            return _failure(error)
        return ValidationOutcome(True, ValidatorDisposition.CONTINUE, proposal.to_json())


def analyze_exam_sample_binding(
    *,
    dependency_resolver: CapabilityDependencyResolver,
    model_adapter: ArtifactReference,
    state_contract: ArtifactReference,
) -> CapabilityBinding:
    pins = VersionPins(
        ArtifactReference(ANALYZE_EXAM_SAMPLE_SKILL.id, ANALYZE_EXAM_SAMPLE_SKILL.version),
        ArtifactReference(ANALYZE_EXAM_SAMPLE_FLOW.id, ANALYZE_EXAM_SAMPLE_FLOW.version),
        EXAM_SAMPLE_ANALYSIS_PROMPT,
        (ToolBehaviorPin("source.prepare_exam_sample_scope", VERSION),),
        model_adapter,
        state_contract,
    )
    return CapabilityBinding(
        ANALYZE_EXAM_SAMPLE_MANIFEST,
        ANALYZE_EXAM_SAMPLE_MANIFEST.fingerprint,
        ANALYZE_EXAM_SAMPLE_SKILL,
        ANALYZE_EXAM_SAMPLE_FLOW,
        pins,
        "proposal",
        dependency_resolver,
    )


class ExamAnalysisTaskFactory:
    def __init__(self, binding: CapabilityBinding) -> None:
        if binding.manifest != ANALYZE_EXAM_SAMPLE_MANIFEST:
            raise ValueError("exam task factory requires analyze_exam_sample@1")
        self._binding = binding

    def build(self, request: ExamAnalysisRequest, opaque_request_key: str) -> GenerationWorkerTask:
        if not opaque_request_key or not opaque_request_key.replace("-", "").isalnum():
            raise ValueError("opaque request key must be portable")
        identity = sha256(
            b"exam-analysis-task@1\0" + request.to_bytes() + b"\0" + opaque_request_key.encode()
        ).hexdigest()
        inputs = request.to_json()
        binding = self._binding
        return GenerationWorkerTask(
            f"exam-analysis-sha256:{identity}",
            GenerationWorkerTaskKind.EXAM_ANALYSIS,
            binding.manifest.id,
            binding.manifest.version,
            binding.manifest_fingerprint,
            binding.manifest.required_authority,
            binding.pins,
            playbook_definition_fingerprint(binding.playbook),
            request.language,
            {},
            None,
            (),
            tuple(str(item) for item in request.sample_revision_ids),
            inputs,
            binding.manifest.output_schema,
            fingerprint_output_schema(binding.manifest.output_schema),
            exam_analysis_validation_expectations(),
        )


def exam_analysis_validation_expectations() -> tuple[ValidationExpectation, ...]:
    return (
        ValidationExpectation(
            "check_exam_sample_readiness",
            ValidationReceiptSource.VALIDATE_STEP,
            "exam_sample_readiness",
            "1.0.0",
        ),
        ValidationExpectation(
            "analyze_exam_samples",
            ValidationReceiptSource.STRUCTURED_OUTPUT_FALLBACK,
            "exam_blueprint_integrity",
            "1.0.0",
        ),
        ValidationExpectation(
            "validate_exam_blueprint",
            ValidationReceiptSource.VALIDATE_STEP,
            "exam_blueprint_integrity",
            "1.0.0",
        ),
    )


def _draft(value: JsonValue) -> tuple[tuple[ExamObservation, ...], tuple[ExamObservation, ...]]:
    if not isinstance(value, Mapping) or set(value) != {
        "observed_topics",
        "observed_formats",
    }:
        raise GroundingContractError("exam draft fields are not exact")
    result: list[tuple[ExamObservation, ...]] = []
    for key in ("observed_topics", "observed_formats"):
        raw = value[key]
        if not isinstance(raw, tuple) or not 1 <= len(raw) <= 64:
            raise GroundingContractError(f"{key} must contain 1..64 observations")
        result.append(
            tuple(ExamObservation.from_json(item) for item in raw if isinstance(item, Mapping))
        )
        if len(result[-1]) != len(raw):
            raise GroundingContractError(f"{key} contains a non-object")
    return result[0], result[1]


def _normalize(value: str) -> str:
    folded = unicodedata.normalize("NFKC", value).casefold()
    return " ".join("".join(char if char.isalnum() else " " for char in folded).split())


def _failure(error: Exception) -> ValidationOutcome:
    return ValidationOutcome(
        False,
        ValidatorDisposition.TERMINATE,
        freeze_object({"status": "failed", "code": "exam_analysis_validation_failed"}),
        str(error).strip() or "exam analysis validation failed",
    )


__all__ = [
    "ExamAnalysisTaskFactory",
    "ExamBlueprintIntegrityValidator",
    "ExamSampleReadinessValidator",
    "analyze_exam_sample_binding",
    "exam_analysis_validation_expectations",
]
