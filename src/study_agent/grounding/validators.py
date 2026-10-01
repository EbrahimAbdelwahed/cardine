from __future__ import annotations

from study_agent.domain import AnswerStatus, Citation, SegmentKind
from study_agent.domain._validation import JsonObject, JsonValue, freeze_object
from study_agent.playbooks import (
    ValidationOutcome,
    ValidatorDisposition,
)
from study_agent.ports import EvidenceStatus, SourceContentPort
from study_agent.skills import SemanticVersion

from .draft import GroundedAnswerDraft, validated_answer_json
from .evidence import EvidenceEnvelope, GroundingContractError

VERSION = SemanticVersion.parse("1.0.0")
INSUFFICIENT_NOTE = "The supplied sources do not contain enough evidence to answer."


class EvidenceSufficiencyValidator:
    id = "evidence_sufficiency"
    version = VERSION

    async def validate(self, inputs: JsonObject) -> ValidationOutcome:
        try:
            if set(inputs) != {"evidence"}:
                raise GroundingContractError("evidence validator requires exactly evidence")
            envelope = EvidenceEnvelope.from_json(inputs["evidence"])
        except (KeyError, GroundingContractError, ValueError) as error:
            return _failure(error)
        if envelope.status is EvidenceStatus.INSUFFICIENT:
            return ValidationOutcome(
                True,
                ValidatorDisposition.TERMINATE,
                {
                    "status": AnswerStatus.INSUFFICIENT_EVIDENCE.value,
                    "segments": (),
                    "unsupported_information_note": INSUFFICIENT_NOTE,
                },
                "retrieval returned insufficient evidence",
            )
        return ValidationOutcome(
            True,
            ValidatorDisposition.CONTINUE,
            {"evidence_status": envelope.status.value},
        )


class GroundedAnswerIntegrityValidator:
    id = "grounded_answer_integrity"
    version = VERSION

    def __init__(self, content: SourceContentPort) -> None:
        self._content = content

    async def validate(self, inputs: JsonObject) -> ValidationOutcome:
        try:
            if set(inputs) == {"output"}:
                GroundedAnswerDraft.from_json(inputs["output"])
                return ValidationOutcome(
                    True,
                    ValidatorDisposition.CONTINUE,
                    {"schema_valid": True},
                )
            if set(inputs) != {"answer", "evidence"}:
                raise GroundingContractError(
                    "answer validator requires output or answer and evidence"
                )
            envelope = EvidenceEnvelope.from_json(inputs["evidence"])
            draft = GroundedAnswerDraft.from_json(inputs["answer"])
            result = self._validate(draft, envelope)
        except (KeyError, GroundingContractError, ValueError) as error:
            return _failure(error)
        return ValidationOutcome(True, ValidatorDisposition.CONTINUE, result)

    def _validate(self, draft: GroundedAnswerDraft, envelope: EvidenceEnvelope) -> JsonObject:
        # The model draft is untrusted. It must preserve the retrieval outcome
        # instead of turning missing or conflicting evidence into a confident answer.
        if envelope.status is EvidenceStatus.INSUFFICIENT:
            raise GroundingContractError("model output cannot consume insufficient evidence")
        if (
            envelope.status is EvidenceStatus.CONFLICTING
            and draft.status is not AnswerStatus.CONFLICTING_EVIDENCE
        ):
            raise GroundingContractError("conflicting evidence must remain conflicting")
        if (
            envelope.status is not EvidenceStatus.CONFLICTING
            and draft.status is AnswerStatus.CONFLICTING_EVIDENCE
        ):
            raise GroundingContractError("answer cannot invent an evidence conflict")

        # Answer-level semantics are checked before individual citations: an
        # answered result needs a claim, while non-answers must explain their limit.
        claim_kinds = {SegmentKind.SUPPORTED_CLAIM, SegmentKind.SYNTHESIS}
        has_claim = any(segment.kind in claim_kinds for segment in draft.segments)
        if draft.status is AnswerStatus.ANSWERED and not has_claim:
            raise GroundingContractError("answered output requires a supported claim")
        if draft.status in {AnswerStatus.INSUFFICIENT_EVIDENCE, AnswerStatus.FAILED} and has_claim:
            raise GroundingContractError("non-grounded status cannot contain supported claims")
        if draft.status is not AnswerStatus.ANSWERED and draft.unsupported_information_note is None:
            raise GroundingContractError("non-answered output requires an unsupported note")

        # Evidence identities come only from the trusted retrieval envelope.
        # The model may reference those handles, but it cannot create new evidence.
        trusted = envelope.by_handle()
        all_citations: list[tuple[JsonObject, ...]] = []
        for segment in draft.segments:
            if segment.kind in claim_kinds and not segment.evidence_ids:
                raise GroundingContractError("supported claims and synthesis require evidence")
            if segment.kind is SegmentKind.STUDY_GUIDANCE and segment.evidence_ids:
                raise GroundingContractError("study guidance cannot cite domain evidence")
            citations: list[JsonObject] = []
            for handle in segment.evidence_ids:
                evidence = trusted.get(handle)
                if evidence is None:
                    raise GroundingContractError("answer references unknown evidence")
                # Re-resolve every citation against canonical storage. A stale
                # revision or changed excerpt fails closed before publication.
                try:
                    resolved = self._content.resolve(evidence.citation)
                except Exception as error:
                    raise GroundingContractError(
                        "evidence citation does not resolve to canonical content"
                    ) from error
                if resolved.citation != evidence.citation or resolved.text != evidence.text:
                    raise GroundingContractError("evidence no longer matches canonical content")
                citations.append(_citation_json(evidence.citation))
            all_citations.append(tuple(citations))
        # Only this canonicalized representation may cross the trust boundary.
        return validated_answer_json(draft, tuple(all_citations))


def _citation_json(citation: Citation) -> JsonObject:
    return freeze_object(
        {
            "source_id": str(citation.source_id),
            "revision_id": str(citation.revision_id),
            "chunk_id": str(citation.chunk_id),
            "start_offset": citation.start_offset,
            "end_offset": citation.end_offset,
            "locator": citation.locator,
            "quoted_snippet": citation.quoted_snippet,
        }
    )


def _failure(error: Exception) -> ValidationOutcome:
    reason = str(error).strip() or "grounding validation failed"
    result: dict[str, JsonValue] = {
        "status": AnswerStatus.FAILED.value,
        "code": "grounding_validation_failed",
    }
    return ValidationOutcome(
        False,
        ValidatorDisposition.TERMINATE,
        result,
        reason,
    )
