"""Privileged admission of reviewed lesson material as generated sources."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from study_agent.artifacts.content import LessonMaterialContent, StudyArtifactEnvelope
from study_agent.artifacts.events import (
    DECISION_RECORDED,
    PROPOSAL_BATCH_RECORDED,
    DecisionRecorded,
    ProposalBatchRecorded,
    RecordedArtifactProposal,
    decode_decision_recorded,
    decode_proposal_batch_recorded,
)
from study_agent.artifacts.identity import GeneratedArtifactProvenance
from study_agent.domain import (
    Actor,
    ArtifactDecision,
    ArtifactRevisionId,
    ContentOrigin,
    CourseId,
    DomainEvent,
    ExecutionContext,
    GeneratedDocumentProvenance,
    LessonMaterialVariant,
    PrincipalKind,
    StructureOrigin,
)
from study_agent.domain._validation import JsonObject
from study_agent.domain.source import SourceChunk, SourceDocument, SourceKind
from study_agent.ingestion import (
    CHUNKER_VERSION,
    GENERATED_SOURCE_REVISION_SCHEMA_VERSION,
    SOURCE_REVISION_INGESTED,
    SourceRevisionIngested,
    decode_source_revision_event,
    generated_source_revision_payload,
)
from study_agent.ingestion.chunking import DEFAULT_CHUNKING_CONFIG, ChunkingConfig
from study_agent.ingestion.identity import (
    GENERATED_MARKDOWN_INGESTION_METHOD,
    generated_revision_id_for,
    generated_source_event_id_for,
    generated_source_id_for,
)
from study_agent.ingestion.preparation import PreparedText, prepare_chunks, prepare_text
from study_agent.ports import BlobStore, ClockPort, EventStore
from study_agent.ports.storage import EventSequenceConflictError
from study_agent.state import Projection

from .contracts import (
    GeneratedSourceMaterializationResult,
    GeneratedSourceMaterializationStatus,
)


class GeneratedSourceMaterializationError(ValueError):
    """A reviewed artifact cannot be admitted as a generated source."""


ProjectionLoader = Callable[[CourseId], Projection]


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class _ArtifactCandidate:
    proposal_event: DomainEvent
    batch: ProposalBatchRecorded
    proposal: RecordedArtifactProposal
    envelope: StudyArtifactEnvelope
    provenance: GeneratedArtifactProvenance
    decision_event: DomainEvent | None
    decision: DecisionRecorded | None


class GeneratedSourceMaterializer:
    """Materialize only a canonically accepted lesson-material artifact.

    The materializer derives every source identity and generated-source field
    from the canonical event stream.  Callers may select an artifact revision,
    but cannot supply a source id, root lineage, or human-decision receipt.
    """

    def __init__(
        self,
        *,
        blobs: BlobStore,
        events: EventStore,
        chunking: ChunkingConfig = DEFAULT_CHUNKING_CONFIG,
        clock: ClockPort | None = None,
        load_projection: ProjectionLoader,
    ) -> None:
        self._blobs = blobs
        self._events = events
        self._chunking = chunking
        self._clock = clock or _SystemClock()
        self._load_projection = load_projection

    def materialize(
        self,
        *,
        artifact_revision_id: ArtifactRevisionId,
        context: ExecutionContext,
        expected_sequence: int | None = None,
    ) -> GeneratedSourceMaterializationResult:
        if context.principal_kind is not PrincipalKind.SERVICE:
            raise GeneratedSourceMaterializationError(
                "generated source materialization requires SERVICE authority"
            )
        if not isinstance(artifact_revision_id, ArtifactRevisionId):
            raise TypeError("artifact_revision_id must be ArtifactRevisionId")
        stream = tuple(self._events.read(context.course_id))
        current_sequence = stream[-1].course_sequence if stream else 0
        if expected_sequence is not None and current_sequence != expected_sequence:
            raise GeneratedSourceMaterializationError(
                f"course stream does not match expected sequence {expected_sequence}"
            )
        if self._chunking.version != CHUNKER_VERSION:
            raise GeneratedSourceMaterializationError(
                f"unsupported chunker version: {self._chunking.version}"
            )

        candidate = self._candidate(stream, artifact_revision_id)
        sibling = self._sibling(candidate, stream)
        root = self._root_revision(stream, candidate.provenance)
        identity = self._source_identity(candidate, sibling, root, context)
        existing = self._existing_identity(stream, *identity)
        if existing is not None:
            existing_source, existing_chunks, sequence = existing
            return GeneratedSourceMaterializationResult(
                GeneratedSourceMaterializationStatus.IDEMPOTENT,
                existing_source,
                existing_chunks,
                sequence,
            )
        source, chunks, payload = self._build_source(
            candidate, sibling, root, context, self._clock.now()
        )
        if candidate.decision_event is None:
            raise GeneratedSourceMaterializationError("material candidate lacks its HUMAN decision")
        event = DomainEvent(
            generated_source_event_id_for(context.course_id, source.source_id, source.revision_id),
            context.course_id,
            current_sequence + 1,
            SOURCE_REVISION_INGESTED,
            GENERATED_SOURCE_REVISION_SCHEMA_VERSION,
            Actor(context.principal_kind, context.principal_id),
            source.created_at,
            context.correlation_id,
            payload,
            causation_id=candidate.decision_event.event_id,
        )
        decoded = decode_source_revision_event(event, self._blobs.get)
        if decoded.source != source or decoded.chunks != chunks:
            raise GeneratedSourceMaterializationError(
                "generated source payload changed immutable materialization state"
            )

        self._preflight(stream, event, decoded, context)
        existing = self._existing(stream, source)
        if existing is not None:
            existing_source, existing_chunks, sequence = existing
            return GeneratedSourceMaterializationResult(
                GeneratedSourceMaterializationStatus.IDEMPOTENT,
                existing_source,
                existing_chunks,
                sequence,
            )
        try:
            committed = self._events.append(context.course_id, current_sequence, (event,))
        except EventSequenceConflictError as error:
            concurrent_stream = tuple(self._events.read(context.course_id))
            existing = self._existing_identity(concurrent_stream, *identity)
            if existing is not None:
                existing_source, existing_chunks, sequence = existing
                return GeneratedSourceMaterializationResult(
                    GeneratedSourceMaterializationStatus.IDEMPOTENT,
                    existing_source,
                    existing_chunks,
                    sequence,
                )
            self._preflight(concurrent_stream, event, decoded, context)
            raise GeneratedSourceMaterializationError(
                "course event sequence changed during generated materialization"
            ) from error
        return GeneratedSourceMaterializationResult(
            GeneratedSourceMaterializationStatus.EMITTED, source, chunks, committed
        )

    def _candidate(
        self, stream: Sequence[DomainEvent], artifact_revision_id: ArtifactRevisionId
    ) -> _ArtifactCandidate:
        proposal_matches: list[
            tuple[DomainEvent, ProposalBatchRecorded, RecordedArtifactProposal]
        ] = []
        for event in stream:
            if event.event_type != PROPOSAL_BATCH_RECORDED:
                continue
            try:
                batch = decode_proposal_batch_recorded(event)
            except (TypeError, ValueError) as error:
                raise GeneratedSourceMaterializationError(
                    "artifact proposal stream failed canonical decoding"
                ) from error
            for proposal in batch.proposals:
                if proposal.revision_id == artifact_revision_id:
                    proposal_matches.append((event, batch, proposal))
        if len(proposal_matches) != 1:
            raise GeneratedSourceMaterializationError(
                "artifact revision must identify exactly one canonical proposal"
            )
        proposal_event, batch, proposal = proposal_matches[0]
        if batch.origin.value != "generated" or batch.run_id is None:
            raise GeneratedSourceMaterializationError(
                "lesson material must come from a generated proposal batch"
            )
        try:
            envelope = StudyArtifactEnvelope.from_bytes(proposal.content_bytes)
        except (TypeError, ValueError) as error:
            raise GeneratedSourceMaterializationError(
                "lesson material content is invalid"
            ) from error
        if envelope.kind.value != "lesson_material" or not isinstance(
            envelope.content, LessonMaterialContent
        ):
            raise GeneratedSourceMaterializationError("artifact revision is not lesson material")
        try:
            from study_agent.artifacts.identity import artifact_provenance_from_bytes

            provenance = artifact_provenance_from_bytes(proposal.provenance_bytes)
        except (TypeError, ValueError) as error:
            raise GeneratedSourceMaterializationError("artifact provenance is invalid") from error
        if not isinstance(provenance, GeneratedArtifactProvenance):
            raise GeneratedSourceMaterializationError(
                "lesson material requires generated artifact provenance"
            )
        decisions: list[tuple[DomainEvent, DecisionRecorded]] = []
        for event in stream:
            if event.event_type != DECISION_RECORDED:
                continue
            try:
                decision = decode_decision_recorded(event)
            except (TypeError, ValueError) as error:
                raise GeneratedSourceMaterializationError(
                    "artifact decision stream failed canonical decoding"
                ) from error
            if decision.revision_id == artifact_revision_id:
                decisions.append((event, decision))
        if len(decisions) != 1:
            raise GeneratedSourceMaterializationError(
                "lesson material requires exactly one terminal decision"
            )
        decision_event, decision = decisions[0]
        if (
            decision.decision is not ArtifactDecision.ACCEPT
            or decision_event.actor.kind is not PrincipalKind.HUMAN
            or decision.policy_receipt is not None
            or decision_event.session_id != proposal_event.session_id
        ):
            raise GeneratedSourceMaterializationError(
                "lesson material requires an exact HUMAN accept without policy proof"
            )
        return _ArtifactCandidate(
            proposal_event,
            batch,
            proposal,
            envelope,
            provenance,
            decision_event,
            decision,
        )

    def _preflight(
        self,
        stream: Sequence[DomainEvent],
        event: DomainEvent,
        decoded: SourceRevisionIngested,
        context: ExecutionContext,
    ) -> None:
        projection = self._load_projection(context.course_id)
        if not isinstance(projection, Projection):
            raise GeneratedSourceMaterializationError(
                "projection loader returned an invalid projection"
            )
        expected = stream[-1].course_sequence if stream else 0
        if projection.sequence != expected:
            raise GeneratedSourceMaterializationError("projection is stale for material admission")
        try:
            from study_agent.ingestion.projection import validate_generated_source_admission

            validate_generated_source_admission(projection.state, event, decoded)
        except (TypeError, ValueError) as error:
            raise GeneratedSourceMaterializationError(
                "generated source admission failed canonical projection validation"
            ) from error

    def _sibling(
        self, candidate: _ArtifactCandidate, stream: Sequence[DomainEvent]
    ) -> _ArtifactCandidate:
        siblings: list[_ArtifactCandidate] = []
        for event in stream:
            if event.event_type != PROPOSAL_BATCH_RECORDED:
                continue
            batch = decode_proposal_batch_recorded(event)
            if batch.batch_id != candidate.batch.batch_id:
                continue
            for proposal in batch.proposals:
                if proposal.revision_id == candidate.proposal.revision_id:
                    continue
                try:
                    envelope = StudyArtifactEnvelope.from_bytes(proposal.content_bytes)
                    from study_agent.artifacts.identity import artifact_provenance_from_bytes

                    provenance = artifact_provenance_from_bytes(proposal.provenance_bytes)
                except (TypeError, ValueError) as error:
                    raise GeneratedSourceMaterializationError(
                        "paired material proposal is invalid"
                    ) from error
                if not isinstance(envelope.content, LessonMaterialContent) or not isinstance(
                    provenance, GeneratedArtifactProvenance
                ):
                    continue
                decisions: list[tuple[DomainEvent, DecisionRecorded]] = []
                for event_candidate in stream:
                    if event_candidate.event_type != DECISION_RECORDED:
                        continue
                    decoded_decision = decode_decision_recorded(event_candidate)
                    if decoded_decision.revision_id == proposal.revision_id:
                        decisions.append((event_candidate, decoded_decision))
                if len(decisions) > 1:
                    raise GeneratedSourceMaterializationError(
                        "paired material proposal has duplicate terminal decisions"
                    )
                decision_event: DomainEvent | None
                decision: DecisionRecorded | None
                decision_event, decision = decisions[0] if decisions else (None, None)
                siblings.append(
                    _ArtifactCandidate(
                        event,
                        batch,
                        proposal,
                        envelope,
                        provenance,
                        decision_event,
                        decision,
                    )
                )
        if len(siblings) != 1:
            raise GeneratedSourceMaterializationError(
                "complete and study materials must be paired in one proposal batch"
            )
        sibling = siblings[0]
        candidate_content = candidate.envelope.content
        sibling_content = sibling.envelope.content
        if not isinstance(candidate_content, LessonMaterialContent) or not isinstance(
            sibling_content, LessonMaterialContent
        ):
            raise GeneratedSourceMaterializationError("paired materials are not lesson material")
        variants = {candidate_content.variant, sibling_content.variant}
        if variants != {LessonMaterialVariant.COMPLETE, LessonMaterialVariant.STUDY}:
            raise GeneratedSourceMaterializationError(
                "material proposal batch must contain exactly complete and study variants"
            )
        if candidate.batch.run_id != sibling.batch.run_id:
            raise GeneratedSourceMaterializationError(
                "paired materials must share one material run"
            )
        return sibling

    def _root_revision(
        self, stream: Sequence[DomainEvent], provenance: GeneratedArtifactProvenance
    ) -> SourceRevisionIngested:
        roots = {(item.source_id, item.revision_id) for item in provenance.source_commitments}
        if len(roots) != 1:
            raise GeneratedSourceMaterializationError(
                "lesson material must commit to exactly one root source revision"
            )
        root_source_id, root_revision_id = next(iter(roots))
        for event in stream:
            if event.event_type != SOURCE_REVISION_INGESTED:
                continue
            if event.schema_version == GENERATED_SOURCE_REVISION_SCHEMA_VERSION:
                continue
            decoded = decode_source_revision_event(event, self._blobs.get)
            if (
                decoded.source.source_id == root_source_id
                and decoded.source.revision_id == root_revision_id
            ):
                if decoded.source.content_origin is ContentOrigin.GENERATED:
                    raise GeneratedSourceMaterializationError(
                        "generated material root must not be another generated source"
                    )
                if not any(
                    dependency.kind == "source_revision"
                    and dependency.id == str(root_source_id)
                    and dependency.version == str(root_revision_id)
                    for dependency in provenance.read_dependencies
                ):
                    raise GeneratedSourceMaterializationError(
                        "lesson material lacks its exact root source dependency"
                    )
                return decoded
        raise GeneratedSourceMaterializationError("root source revision is not canonical")

    def _source_identity(
        self,
        candidate: _ArtifactCandidate,
        sibling: _ArtifactCandidate,
        root: SourceRevisionIngested,
        context: ExecutionContext,
    ) -> tuple[object, object]:
        content = candidate.envelope.content
        sibling_content = sibling.envelope.content
        assert isinstance(content, LessonMaterialContent)
        assert isinstance(sibling_content, LessonMaterialContent)
        complete = content if content.variant is LessonMaterialVariant.COMPLETE else sibling_content
        study = content if content.variant is LessonMaterialVariant.STUDY else sibling_content
        if complete.direct_parent_blob_sha256 != root.source.normalized_blob.checksum_sha256:
            raise GeneratedSourceMaterializationError(
                "complete material must directly parent the root"
            )
        if study.direct_parent_blob_sha256 != complete.markdown_blob.checksum_sha256:
            raise GeneratedSourceMaterializationError(
                "study material must directly parent complete"
            )
        prepared = self._verified_material_blob(content)
        artifact_hash = sha256(candidate.proposal.provenance_bytes).hexdigest()
        if candidate.decision_event is None:
            raise GeneratedSourceMaterializationError("material candidate lacks its HUMAN decision")
        decision_event = candidate.decision_event
        source_id = generated_source_id_for(
            course_id=context.course_id,
            root_source_id=root.source.source_id,
            root_revision_id=root.source.revision_id,
            artifact_revision_id=candidate.proposal.revision_id,
            material_run_id=candidate.provenance.run_id,
            variant=content.variant,
        )
        revision_id = generated_revision_id_for(
            source_id=source_id,
            root_source_id=root.source.source_id,
            root_revision_id=root.source.revision_id,
            artifact_revision_id=candidate.proposal.revision_id,
            material_run_id=candidate.provenance.run_id,
            variant=content.variant,
            markdown_sha256=prepared.original_blob.checksum_sha256,
            title=content.title,
            normalization_version=prepared.normalized.version,
            chunker_version=self._chunking.version,
            max_characters=self._chunking.max_characters,
            trust_level=root.source.trust_level,
            source_role=root.source.source_role,
            root_normalized_blob_sha256=root.source.normalized_blob.checksum_sha256,
            artifact_provenance_sha256=artifact_hash,
            direct_parent_blob_sha256=content.direct_parent_blob_sha256,
            human_decision_event_id=decision_event.event_id,
            human_decision_at=decision_event.occurred_at,
        )
        return source_id, revision_id

    def _existing_identity(
        self, stream: Sequence[DomainEvent], source_id: object, revision_id: object
    ) -> tuple[SourceDocument, tuple[SourceChunk, ...], int] | None:
        for event in stream:
            if (
                event.event_type == SOURCE_REVISION_INGESTED
                and event.schema_version == GENERATED_SOURCE_REVISION_SCHEMA_VERSION
            ):
                decoded = decode_source_revision_event(event, self._blobs.get)
                if (
                    decoded.source.source_id == source_id
                    and decoded.source.revision_id == revision_id
                ):
                    return decoded.source, decoded.chunks, event.course_sequence
        return None

    def _build_source(
        self,
        candidate: _ArtifactCandidate,
        sibling: _ArtifactCandidate,
        root: SourceRevisionIngested,
        context: ExecutionContext,
        occurred_at: datetime,
    ) -> tuple[SourceDocument, tuple[SourceChunk, ...], JsonObject]:
        if candidate.decision_event is None or candidate.decision is None:
            raise GeneratedSourceMaterializationError("material candidate lacks its HUMAN decision")
        decision_event = candidate.decision_event
        content = candidate.envelope.content
        sibling_content = sibling.envelope.content
        assert isinstance(content, LessonMaterialContent)
        assert isinstance(sibling_content, LessonMaterialContent)
        if candidate.provenance.run_id != candidate.batch.run_id:
            raise GeneratedSourceMaterializationError(
                "artifact provenance run differs from batch run"
            )
        if sibling.provenance.run_id != candidate.provenance.run_id:
            raise GeneratedSourceMaterializationError("paired artifact runs differ")
        root_digest = root.source.normalized_blob.checksum_sha256
        complete = content if content.variant is LessonMaterialVariant.COMPLETE else sibling_content
        study = content if content.variant is LessonMaterialVariant.STUDY else sibling_content
        if complete.direct_parent_blob_sha256 != root_digest:
            raise GeneratedSourceMaterializationError(
                "complete material must directly parent the root normalized blob"
            )
        if study.direct_parent_blob_sha256 != complete.markdown_blob.checksum_sha256:
            raise GeneratedSourceMaterializationError(
                "study material must directly parent the complete material blob"
            )
        prepared = self._verified_material_blob(content)
        source_id = generated_source_id_for(
            course_id=context.course_id,
            root_source_id=root.source.source_id,
            root_revision_id=root.source.revision_id,
            artifact_revision_id=candidate.proposal.revision_id,
            material_run_id=candidate.provenance.run_id,
            variant=content.variant,
        )
        generated_provenance = GeneratedDocumentProvenance(
            root_source_id=root.source.source_id,
            root_revision_id=root.source.revision_id,
            root_normalized_blob_sha256=root_digest,
            artifact_revision_id=candidate.proposal.revision_id,
            artifact_provenance_sha256=sha256(candidate.proposal.provenance_bytes).hexdigest(),
            material_run_id=candidate.provenance.run_id,
            variant=content.variant,
            direct_parent_blob_sha256=content.direct_parent_blob_sha256,
            human_decision_event_id=decision_event.event_id,
            human_decision_at=decision_event.occurred_at,
        )
        revision_id = generated_revision_id_for(
            source_id=source_id,
            root_source_id=root.source.source_id,
            root_revision_id=root.source.revision_id,
            artifact_revision_id=candidate.proposal.revision_id,
            material_run_id=candidate.provenance.run_id,
            variant=content.variant,
            markdown_sha256=prepared.original_blob.checksum_sha256,
            title=content.title,
            normalization_version=prepared.normalized.version,
            chunker_version=self._chunking.version,
            max_characters=self._chunking.max_characters,
            trust_level=root.source.trust_level,
            source_role=root.source.source_role,
            root_normalized_blob_sha256=root_digest,
            artifact_provenance_sha256=sha256(candidate.proposal.provenance_bytes).hexdigest(),
            direct_parent_blob_sha256=content.direct_parent_blob_sha256,
            human_decision_event_id=decision_event.event_id,
            human_decision_at=decision_event.occurred_at,
        )
        source = SourceDocument(
            source_id=source_id,
            revision_id=revision_id,
            kind=SourceKind.MARKDOWN,
            title=content.title,
            media_type="text/markdown",
            checksum_sha256=prepared.original_blob.checksum_sha256,
            byte_length=prepared.original_blob.byte_length,
            created_at=occurred_at,
            trust_level=root.source.trust_level,
            source_role=root.source.source_role,
            blob=prepared.original_blob,
            normalized_blob=prepared.normalized_blob,
            normalization_version=prepared.normalized.version,
            normalized_character_length=len(prepared.normalized.text),
            structure_origin=StructureOrigin.HUMAN_APPROVED,
            ingestion_method=GENERATED_MARKDOWN_INGESTION_METHOD,
            content_origin=ContentOrigin.GENERATED,
            generated_provenance=generated_provenance,
        )
        chunks = prepare_chunks(
            prepared.normalized.text,
            source_id=source.source_id,
            revision_id=source.revision_id,
            kind=source.kind,
            config=self._chunking,
        )
        chunks = tuple(
            SourceChunk(
                chunk.chunk_id,
                chunk.source_id,
                chunk.revision_id,
                chunk.start_offset,
                chunk.end_offset,
                (),
                chunk.ordinal,
                chunk.checksum_sha256,
                chunk.chunker_version,
                {"block_kind": "opaque"},
            )
            for chunk in chunks
        )
        return (
            source,
            chunks,
            generated_source_revision_payload(
                source,
                chunks,
                chunker_version=self._chunking.version,
                max_characters=self._chunking.max_characters,
            ),
        )

    def _verified_material_blob(self, candidate: LessonMaterialContent) -> PreparedText:
        ref = candidate.markdown_blob
        try:
            data = self._blobs.get(ref)
        except (LookupError, OSError) as error:
            raise GeneratedSourceMaterializationError(
                "lesson material Markdown blob is missing"
            ) from error
        if not isinstance(data, bytes) or len(data) != ref.byte_length:
            raise GeneratedSourceMaterializationError("lesson material blob length is invalid")
        if sha256(data).hexdigest() != ref.checksum_sha256:
            raise GeneratedSourceMaterializationError("lesson material blob checksum is invalid")
        try:
            prepared = prepare_text(data)
        except (TypeError, ValueError) as error:
            raise GeneratedSourceMaterializationError(
                "lesson material Markdown blob is not strict UTF-8"
            ) from error
        if prepared.normalized.content != data:
            raise GeneratedSourceMaterializationError(
                "lesson material Markdown blob is not canonically normalized"
            )
        if len(prepared.normalized.text) != candidate.markdown_character_length:
            raise GeneratedSourceMaterializationError(
                "lesson material Markdown character length is invalid"
            )
        return prepared

    def _existing(
        self, stream: Sequence[DomainEvent], source: SourceDocument
    ) -> tuple[SourceDocument, tuple[SourceChunk, ...], int] | None:
        for event in stream:
            if event.event_type != SOURCE_REVISION_INGESTED:
                continue
            if event.schema_version != GENERATED_SOURCE_REVISION_SCHEMA_VERSION:
                continue
            decoded = decode_source_revision_event(event, self._blobs.get)
            if decoded.source.source_id != source.source_id:
                continue
            expected_chunks = tuple(
                SourceChunk(
                    chunk.chunk_id,
                    chunk.source_id,
                    chunk.revision_id,
                    chunk.start_offset,
                    chunk.end_offset,
                    (),
                    chunk.ordinal,
                    chunk.checksum_sha256,
                    chunk.chunker_version,
                    {"block_kind": "opaque"},
                )
                for chunk in prepare_chunks(
                    self._blobs.get(source.normalized_blob).decode("utf-8"),
                    source_id=source.source_id,
                    revision_id=source.revision_id,
                    kind=source.kind,
                    config=self._chunking,
                )
            )
            if decoded.source != source or decoded.chunks != expected_chunks:
                raise GeneratedSourceMaterializationError(
                    "generated source identity already exists with different immutable data"
                )
            return decoded.source, decoded.chunks, event.course_sequence
        return None


__all__ = [
    "GeneratedSourceMaterializationError",
    "GeneratedSourceMaterializationResult",
    "GeneratedSourceMaterializer",
    "ProjectionLoader",
]
