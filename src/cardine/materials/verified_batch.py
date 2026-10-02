"""Verified generated-artifact recovery for the final paired proposal."""

from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
from unicodedata import normalize

from study_agent.artifacts import (
    ArtifactProposal,
    GeneratedBatchProofReceipt,
    VerifiedGeneratedArtifactBatch,
    artifact_batch_id_for,
    artifact_id_for,
    artifact_provenance_to_bytes,
    artifact_revision_id_for,
)
from study_agent.artifacts.content import LessonMaterialContent, StudyArtifactEnvelope
from study_agent.artifacts.identity import (
    GeneratedArtifactProvenance,
)
from study_agent.domain import (
    ArtifactReadDependency,
    BlobRef,
    ExecutionContext,
    LessonMaterialVariant,
    ModelProvenance,
    ModelUsageProvenance,
    PrincipalKind,
    PromptProvenance,
    RetrievalProvenance,
    RunId,
    SourceCommitment,
    StudyArtifactKind,
    ValidatorProvenance,
    VersionPins,
)
from study_agent.ports import BlobStore, VerifiedGeneratedBatchPort
from study_agent.ports.model import ModelRequest
from study_agent.state import canonical_json_bytes

from .generation_contracts import (
    GenerationPipelinePins,
    MaterialGenerationStage,
    MaterialGenerationState,
    PinnedTranscriptInput,
    StageReceipt,
    limitations_fingerprint,
)
from .planning import SegmentBoundaries, UnitManifest
from .prompts import (
    boundary_request,
    complete_merge_request,
    complete_segment_request,
    request_fingerprint,
    study_from_complete_request,
)


class VerifiedBatchError(ValueError):
    """A completed material job cannot be converted into a proposal batch."""


StateLoader = Callable[[RunId], MaterialGenerationState]
SourceCommitmentFactory = Callable[[PinnedTranscriptInput], tuple[SourceCommitment, ...]]


class MaterialVerifiedBatchAdapter(VerifiedGeneratedBatchPort):
    """Turn only a fully verified state into the existing artifact port contract."""

    def __init__(
        self,
        *,
        blobs: BlobStore,
        load_state: StateLoader,
        source_commitments: SourceCommitmentFactory,
    ) -> None:
        self._blobs = blobs
        self._load_state = load_state
        self._source_commitments = source_commitments

    def recover(self, run_id: RunId, context: ExecutionContext) -> VerifiedGeneratedArtifactBatch:
        state = self._state(run_id, context)
        return self._batch(state)

    def _state(self, run_id: RunId, context: ExecutionContext) -> MaterialGenerationState:
        state = self._load_state(run_id)
        if context.principal_kind is not PrincipalKind.SERVICE:
            raise VerifiedBatchError("generated material recovery requires SERVICE authority")
        if state.run_id != run_id or state.request.pin.course_id != context.course_id:
            raise VerifiedBatchError("material run does not belong to the execution context")
        if context.session_id is None or state.request.pin.session_id != context.session_id:
            raise VerifiedBatchError("material run does not belong to the execution session")
        if state.stage not in (MaterialGenerationStage.PROPOSAL, MaterialGenerationStage.PROPOSED):
            raise VerifiedBatchError("paired material outputs are not complete")
        if state.complete is None or state.study is None:
            raise VerifiedBatchError("paired material outputs are missing")
        return state

    def _batch(self, state: MaterialGenerationState) -> VerifiedGeneratedArtifactBatch:
        assert state.complete is not None and state.study is not None
        complete_bytes = self._blobs.get(state.complete)
        study_bytes = self._blobs.get(state.study)
        try:
            complete_text = complete_bytes.decode("utf-8")
            study_text = study_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            raise VerifiedBatchError("material output blob is not UTF-8") from error
        if (
            normalize("NFC", complete_text) != complete_text
            or normalize("NFC", study_text) != study_text
        ):
            raise VerifiedBatchError("material output blob is not NFC-normalized")
        if state.unit_manifest is None or state.boundaries is None:
            raise VerifiedBatchError("material ancestry refs are incomplete")
        self._validate_receipt_chain(state)
        merge_receipt = self._receipt_for(
            state,
            MaterialGenerationStage.COMPLETE_MERGE,
            0,
            state.complete,
            (
                state.request.pin.normalized_blob,
                state.unit_manifest,
                state.boundaries,
                *state.segments,
            ),
            state.complete_limitations,
        )
        study_receipt = self._receipt_for(
            state,
            MaterialGenerationStage.STUDY,
            0,
            state.study,
            (state.complete,),
            state.study_limitations,
        )
        pins = state.request.pins
        batch_id = artifact_batch_id_for(
            state.request.pin.course_id,
            state.request.pin.session_id,
            state.run_id,
            state.request.request_id,
        )
        complete_content = LessonMaterialContent(
            LessonMaterialVariant.COMPLETE,
            state.request.pin.title,
            state.complete,
            len(complete_text),
            state.request.pin.normalized_blob.checksum_sha256,
            (*state.complete_limitations,),
        )
        study_content = LessonMaterialContent(
            LessonMaterialVariant.STUDY,
            state.request.pin.title,
            state.study,
            len(study_text),
            state.complete.checksum_sha256,
            (*state.study_limitations,),
        )
        complete_envelope = StudyArtifactEnvelope(
            kind=StudyArtifactKind.LESSON_MATERIAL,
            content=complete_content,
        )
        study_envelope = StudyArtifactEnvelope(
            kind=StudyArtifactKind.LESSON_MATERIAL,
            content=study_content,
        )
        complete_provenance = self._provenance(
            state,
            pins,
            LessonMaterialVariant.COMPLETE,
            sha256(complete_envelope.to_bytes()).hexdigest(),
            merge_receipt,
        )
        study_provenance = self._provenance(
            state,
            pins,
            LessonMaterialVariant.STUDY,
            sha256(study_envelope.to_bytes()).hexdigest(),
            study_receipt,
        )
        complete_provenance_bytes = artifact_provenance_to_bytes(complete_provenance)
        study_provenance_bytes = artifact_provenance_to_bytes(study_provenance)
        complete_id = artifact_id_for(batch_id, 0)
        study_id = artifact_id_for(batch_id, 1)
        complete_revision = artifact_revision_id_for(
            complete_id,
            complete_envelope.kind,
            complete_envelope.to_bytes(),
            complete_provenance_bytes,
        )
        study_revision = artifact_revision_id_for(
            study_id,
            study_envelope.kind,
            study_envelope.to_bytes(),
            study_provenance_bytes,
        )
        return VerifiedGeneratedArtifactBatch(
            state.run_id,
            state.request.pin.course_id,
            state.request.pin.session_id,
            (
                ArtifactProposal(0, complete_envelope, complete_provenance),
                ArtifactProposal(1, study_envelope, study_provenance),
            ),
            GeneratedBatchProofReceipt(
                "material-verified-batch",
                "1.0.0",
                sha256(
                    canonical_json_bytes(
                        {
                            "complete_revision": str(complete_revision),
                            "study_revision": str(study_revision),
                            "complete_blob": state.complete.checksum_sha256,
                            "study_blob": state.study.checksum_sha256,
                        }
                    )
                ).hexdigest(),
            ),
        )

    def _provenance(
        self,
        state: MaterialGenerationState,
        pins: GenerationPipelinePins,
        variant: LessonMaterialVariant,
        output_fingerprint: str,
        stage_receipt: StageReceipt,
    ) -> GeneratedArtifactProvenance:
        commitments = tuple(self._source_commitments(state.request.pin))
        self._validate_commitments(commitments, state.request.pin)
        dependencies = (
            ArtifactReadDependency(
                "source_revision",
                str(state.request.pin.source_id),
                str(state.request.pin.revision_id),
            ),
            ArtifactReadDependency("material_generation", state.job_id, pins.pipeline),
        )
        receipt_fingerprint = sha256(
            canonical_json_bytes(
                {
                    "job": state.job_id,
                    "variant": variant.value,
                    "complete": state.complete.checksum_sha256 if state.complete else None,
                    "study": state.study.checksum_sha256 if state.study else None,
                }
            )
        ).hexdigest()
        validators = tuple(
            ValidatorProvenance(validator_id, "2", True, "passed", fingerprint)
            for validator_id, fingerprint in (
                ("utf8-nfc", receipt_fingerprint),
                ("lineage", stage_receipt.validator_fingerprint),
                ("numeric-anchors", stage_receipt.validator_fingerprint),
                ("limitations", stage_receipt.validator_fingerprint),
            )
        )
        relevant_receipts = self._provenance_receipts(state, variant)
        usage = self._summed_usage(relevant_receipts)
        prompt_composition_fingerprint = sha256(
            canonical_json_bytes(
                {
                    "variant": variant.value,
                    "receipts": tuple(receipt.receipt_fingerprint for receipt in relevant_receipts),
                }
            )
        ).hexdigest()
        return GeneratedArtifactProvenance(
            source_commitments=commitments,
            prompt=PromptProvenance(
                "material-complete-pipeline"
                if variant is LessonMaterialVariant.COMPLETE
                else stage_receipt.prompt_id,
                "1" if variant is LessonMaterialVariant.COMPLETE else stage_receipt.prompt_version,
                prompt_composition_fingerprint,
            ),
            model=ModelProvenance(
                stage_receipt.adapter_id,
                stage_receipt.adapter_version,
                stage_receipt.model_id,
                relevant_receipts[0].provider_response_id if len(relevant_receipts) == 1 else None,
                state.run_id,
                usage,
            ),
            retrieval=RetrievalProvenance(
                "pinned-transcript",
                "1.0.0",
                state.request.pin.fingerprint,
                pins.pipeline,
                state.request.pin.fingerprint,
            ),
            validators=validators,
            pins=VersionPins(
                "material-generation",
                pins.pipeline,
                pins.pipeline,
                pins.model_adapter,
                "material-state@2",
                "no-tools",
            ),
            profile_selection=None,
            read_dependencies=dependencies,
            output_fingerprint=output_fingerprint,
            run_id=state.run_id,
        )

    def _validate_receipt_chain(self, state: MaterialGenerationState) -> None:
        assert state.unit_manifest is not None and state.boundaries is not None
        manifest = UnitManifest.from_bytes(self._blobs.get(state.unit_manifest))
        boundaries = SegmentBoundaries.from_bytes(self._blobs.get(state.boundaries))
        boundaries.validate_against(manifest)
        pins = state.request.pins
        expected_adapter, expected_version = pins.model_adapter.split("@", 1)
        expected_prompts = {
            MaterialGenerationStage.BOUNDARIES: pins.boundaries_prompt,
            MaterialGenerationStage.COMPLETE_SEGMENT: pins.complete_segment_prompt,
            MaterialGenerationStage.COMPLETE_MERGE: pins.complete_merge_prompt,
            MaterialGenerationStage.STUDY: pins.study_prompt,
        }
        seen: set[tuple[MaterialGenerationStage, int]] = set()
        for receipt in state.receipts:
            key = (receipt.stage, receipt.stage_ordinal)
            if key in seen or receipt.stage not in expected_prompts:
                raise VerifiedBatchError("material state contains a duplicate or unknown receipt")
            seen.add(key)
            prompt_id, prompt_version = expected_prompts[receipt.stage].rsplit("@", 1)
            if (
                receipt.prompt_id != prompt_id
                or receipt.prompt_version != prompt_version
                or receipt.adapter_id != expected_adapter
                or receipt.adapter_version != expected_version
                or receipt.model_id != pins.model_id
                or receipt.tool_calls
                or receipt.no_tools is not True
            ):
                raise VerifiedBatchError("stage receipt does not prove the pinned Luna invocation")
            if receipt.stage is MaterialGenerationStage.BOUNDARIES:
                if (
                    receipt.stage_ordinal != 0
                    or receipt.input_blobs
                    != (state.request.pin.normalized_blob, state.unit_manifest)
                    or receipt.output != state.boundaries
                ):
                    raise VerifiedBatchError("boundary receipt ancestry was tampered")
            elif receipt.stage is MaterialGenerationStage.COMPLETE_SEGMENT:
                if (
                    receipt.stage_ordinal >= len(state.segments)
                    or receipt.output != state.segments[receipt.stage_ordinal]
                ):
                    raise VerifiedBatchError("segment receipt ancestry was tampered")
                if receipt.input_blobs != (
                    state.request.pin.normalized_blob,
                    state.unit_manifest,
                    state.boundaries,
                ):
                    raise VerifiedBatchError("segment receipt inputs were tampered")
            elif receipt.stage is MaterialGenerationStage.COMPLETE_MERGE:
                expected_inputs = (
                    state.request.pin.normalized_blob,
                    state.unit_manifest,
                    state.boundaries,
                    *state.segments,
                )
                if receipt.input_blobs != expected_inputs or receipt.output != state.complete:
                    raise VerifiedBatchError("merge receipt ancestry was tampered")
            elif receipt.stage is MaterialGenerationStage.STUDY and (
                state.complete is None
                or receipt.input_blobs != (state.complete,)
                or receipt.output != state.study
            ):
                raise VerifiedBatchError("study receipt ancestry was tampered")
            expected_request = self._request_for_receipt(state, manifest, boundaries, receipt)
            if request_fingerprint(expected_request) != receipt.composition_fingerprint:
                raise VerifiedBatchError("stage receipt request composition was tampered")
            expected_validator = sha256(
                canonical_json_bytes(
                    {
                        "validator": pins.validator,
                        "stage": receipt.stage.value,
                        "output": receipt.output.checksum_sha256,
                        "limitations": receipt.limitations_fingerprint,
                    }
                )
            ).hexdigest()
            if receipt.validator_fingerprint != expected_validator:
                raise VerifiedBatchError("stage receipt validator proof was tampered")
            if receipt.stage is MaterialGenerationStage.COMPLETE_MERGE:
                expected_limitations = state.complete_limitations
            elif receipt.stage is MaterialGenerationStage.STUDY:
                expected_limitations = state.study_limitations
            else:
                expected_limitations = None
            if (
                expected_limitations is not None
                and receipt.limitations_fingerprint != limitations_fingerprint(expected_limitations)
            ):
                raise VerifiedBatchError("stage receipt limitations proof was tampered")
        expected_receipts = {
            (MaterialGenerationStage.BOUNDARIES, 0),
            *(
                (MaterialGenerationStage.COMPLETE_SEGMENT, ordinal)
                for ordinal in range(len(state.segments))
            ),
            (MaterialGenerationStage.COMPLETE_MERGE, 0),
            (MaterialGenerationStage.STUDY, 0),
        }
        if seen != expected_receipts:
            raise VerifiedBatchError("material state does not contain the complete receipt chain")

    def _request_for_receipt(
        self,
        state: MaterialGenerationState,
        manifest: UnitManifest,
        boundaries: SegmentBoundaries,
        receipt: StageReceipt,
    ) -> ModelRequest:
        pins = state.request.pins
        if receipt.stage is MaterialGenerationStage.BOUNDARIES:
            return boundary_request(manifest, pins)
        if receipt.stage is MaterialGenerationStage.COMPLETE_SEGMENT:
            if receipt.stage_ordinal >= len(boundaries.segments):
                raise VerifiedBatchError("segment receipt ordinal exceeds boundaries")
            return complete_segment_request(
                manifest, boundaries.segments[receipt.stage_ordinal], pins=pins
            )
        if receipt.stage is MaterialGenerationStage.COMPLETE_MERGE:
            segments = tuple(self._blobs.get(item).decode("utf-8") for item in state.segments)
            return complete_merge_request(segments, title=state.request.pin.title, pins=pins)
        if receipt.stage is MaterialGenerationStage.STUDY:
            if state.complete is None:
                raise VerifiedBatchError("study receipt lacks complete input")
            complete = self._blobs.get(state.complete).decode("utf-8")
            return study_from_complete_request(complete, title=state.request.pin.title, pins=pins)
        raise VerifiedBatchError("unsupported receipt stage")

    @staticmethod
    def _receipt_for(
        state: MaterialGenerationState,
        stage: MaterialGenerationStage,
        ordinal: int,
        output: BlobRef | None,
        inputs: tuple[BlobRef, ...],
        limitations: tuple[str, ...],
    ) -> StageReceipt:
        matches = tuple(item for item in state.receipts if item.stage is stage)
        if len(matches) != 1:
            raise VerifiedBatchError(f"material state must contain one {stage.value} receipt")
        receipt = matches[0]
        if (
            receipt.stage_ordinal != ordinal
            or receipt.output != output
            or receipt.input_blobs != inputs
            or receipt.attempt < 1
            or receipt.limitations_fingerprint != limitations_fingerprint(limitations)
        ):
            raise VerifiedBatchError(f"{stage.value} receipt ancestry is not exact")
        return receipt

    @staticmethod
    def _validate_commitments(
        commitments: tuple[SourceCommitment, ...], pin: PinnedTranscriptInput
    ) -> None:
        if not commitments:
            raise VerifiedBatchError("canonical source commitments are required")
        previous_end = -1
        seen: set[tuple[str, str, int, int]] = set()
        for commitment in commitments:
            if (
                commitment.source_id != pin.source_id
                or commitment.revision_id != pin.revision_id
                or commitment.start_offset < 0
                or commitment.end_offset <= commitment.start_offset
                or commitment.end_offset > pin.normalized_character_length
                or commitment.start_offset < previous_end
            ):
                raise VerifiedBatchError("source commitments are outside the pinned revision")
            key = (
                str(commitment.chunk_id),
                str(commitment.revision_id),
                commitment.start_offset,
                commitment.end_offset,
            )
            if key in seen:
                raise VerifiedBatchError("source commitments must be unique")
            seen.add(key)
            previous_end = commitment.end_offset

    @staticmethod
    def _provenance_receipts(
        state: MaterialGenerationState, variant: LessonMaterialVariant
    ) -> tuple[StageReceipt, ...]:
        if variant is LessonMaterialVariant.STUDY:
            receipts = tuple(
                item for item in state.receipts if item.stage is MaterialGenerationStage.STUDY
            )
        else:
            receipts = tuple(
                item for item in state.receipts if item.stage is not MaterialGenerationStage.STUDY
            )
        if not receipts:
            raise VerifiedBatchError("material provenance receipt set is empty")
        return receipts

    @staticmethod
    def _summed_usage(
        receipts: tuple[StageReceipt, ...],
    ) -> ModelUsageProvenance | None:
        usages = [item.usage for item in receipts]
        if not usages or any(item is None for item in usages):
            return None
        return ModelUsageProvenance(
            sum(item.input_tokens for item in usages if item is not None),
            sum(item.output_tokens for item in usages if item is not None),
        )


__all__ = ["MaterialVerifiedBatchAdapter", "VerifiedBatchError"]
