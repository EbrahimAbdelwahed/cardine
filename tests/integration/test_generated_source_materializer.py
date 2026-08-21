from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

import pytest

from cardine.materials import (
    GeneratedSourceMaterializationError,
    GeneratedSourceMaterializer,
)
from cardine.materials.contracts import GeneratedSourceMaterializationStatus
from study_agent.artifacts import (
    GeneratedArtifactProvenance,
    LessonMaterialContent,
    StudyArtifactEnvelope,
    artifact_batch_id_for,
    artifact_id_for,
    artifact_provenance_to_bytes,
    artifact_revision_id_for,
)
from study_agent.artifacts.contracts import ArtifactProposalOrigin, GeneratedBatchProofReceipt
from study_agent.artifacts.events import (
    ARTIFACT_SCHEMA_VERSION,
    DECISION_RECORDED,
    PROPOSAL_BATCH_RECORDED,
    RecordedArtifactProposal,
    decision_payload,
    decode_decision_recorded,
    decode_proposal_batch_recorded,
    proposal_batch_payload,
)
from study_agent.artifacts.projection import (
    reduce_decision_recorded,
    reduce_proposal_batch_recorded,
)
from study_agent.domain import (
    Actor,
    ArtifactBatchId,
    ArtifactDecision,
    ArtifactReadDependency,
    ArtifactRevisionId,
    BlobId,
    BlobRef,
    ContentOrigin,
    CorrelationId,
    CourseId,
    DomainEvent,
    ExecutionContext,
    LessonMaterialVariant,
    PrincipalKind,
    PromptProvenance,
    RetrievalProvenance,
    RunId,
    SessionId,
    SourceCommitment,
    SourceId,
    StructureOrigin,
    StudyArtifactKind,
    ValidatorProvenance,
    VersionPins,
    artifact_event_id_for,
)
from study_agent.domain.source import SourceDocument, SourceKind
from study_agent.ingestion import (
    CHUNKER_VERSION,
    NORMALIZATION_POLICY_VERSION,
    SOURCE_REVISION_INGESTED,
    SOURCE_REVISION_SCHEMA_VERSION,
    ChunkingConfig,
    chunk_text,
    decode_source_revision_event,
    revision_id_for,
    source_event_id_for,
    source_revision_payload,
)
from study_agent.ingestion.preparation import prepare_text
from study_agent.ingestion.projection import (
    reduce_generated_source_revision,
    reduce_source_revision,
)
from study_agent.ports.storage import EventSequenceConflictError
from study_agent.state import Projection

NOW = datetime(2026, 8, 14, 16, 30, tzinfo=UTC)
COURSE = CourseId("course-materializer")
SESSION = SessionId("session-materializer")
CORRELATION = CorrelationId("correlation-materializer")
ROOT_SOURCE = SourceId("root-materializer")
RUN = RunId("material-run")
PROPOSAL_KEY = "material-proposal"
PROOF = GeneratedBatchProofReceipt("material-verifier", "1.0.0", "e" * 64)


@dataclass
class MemoryBlobs:
    values: dict[BlobRef, bytes]

    def get(self, ref: BlobRef) -> bytes:
        try:
            return self.values[ref]
        except KeyError as error:
            raise LookupError(str(ref)) from error


class MemoryEvents:
    def __init__(self, events: Sequence[DomainEvent]) -> None:
        self.events = list(events)
        self.append_calls = 0

    def read(self, course_id: CourseId, after_sequence: int = 0) -> Sequence[DomainEvent]:
        return tuple(
            event
            for event in self.events
            if event.course_id == course_id and event.course_sequence > after_sequence
        )

    def append(
        self, course_id: CourseId, expected_sequence: int, events: Sequence[DomainEvent]
    ) -> int:
        self.append_calls += 1
        current = self.events[-1].course_sequence if self.events else 0
        if current != expected_sequence:
            raise EventSequenceConflictError(course_id, expected_sequence, current)
        self.events.extend(events)
        return self.events[-1].course_sequence


def _projection(events: MemoryEvents, blobs: MemoryBlobs) -> Projection:
    state = {
        "course": {},
        "sessions": {str(SESSION): {"course_id": str(COURSE)}},
    }
    for event in events.events:
        if event.event_type == SOURCE_REVISION_INGESTED:
            decoded = decode_source_revision_event(event, blobs.get)
            reducer = (
                reduce_generated_source_revision
                if event.schema_version != SOURCE_REVISION_SCHEMA_VERSION
                else reduce_source_revision
            )
            state = dict(reducer(state, event, decoded))
        elif event.event_type == PROPOSAL_BATCH_RECORDED:
            state = dict(
                reduce_proposal_batch_recorded(
                    state, event, decode_proposal_batch_recorded(event)
                )
            )
        elif event.event_type == DECISION_RECORDED:
            state = dict(
                reduce_decision_recorded(state, event, decode_decision_recorded(event))
            )
    return Projection(COURSE, events.events[-1].course_sequence, state)


def _root_fixture() -> tuple[DomainEvent, MemoryBlobs, SourceDocument, SourceCommitment]:
    original = b"# Root notes\n\nThree cusps form the valve.\n"
    prepared = prepare_text(original)
    revision = revision_id_for(
        original_sha256=sha256(original).hexdigest(),
        source_id=ROOT_SOURCE,
        kind=SourceKind.MARKDOWN,
        title="Root notes",
        trust_level=90,
        source_role="primary",
        normalization_version=NORMALIZATION_POLICY_VERSION,
        chunker_version=CHUNKER_VERSION,
        max_characters=1200,
    )
    source = SourceDocument(
        ROOT_SOURCE,
        revision,
        SourceKind.MARKDOWN,
        "Root notes",
        "text/markdown",
        prepared.original_blob.checksum_sha256,
        prepared.original_blob.byte_length,
        NOW,
        90,
        "primary",
        prepared.original_blob,
        prepared.normalized_blob,
        NORMALIZATION_POLICY_VERSION,
        len(prepared.normalized.text),
        StructureOrigin.MECHANICALLY_EXTRACTED,
        "utf8-markdown-v1",
        ContentOrigin.ORIGINAL,
    )
    chunks = chunk_text(
        prepared.normalized.text,
        source_id=source.source_id,
        revision_id=source.revision_id,
        kind=source.kind,
        config=ChunkingConfig(1200, CHUNKER_VERSION),
    )
    event = DomainEvent(
        source_event_id_for(COURSE, source.revision_id),
        COURSE,
        1,
        SOURCE_REVISION_INGESTED,
        SOURCE_REVISION_SCHEMA_VERSION,
        Actor(PrincipalKind.SERVICE, "ingestion"),
        NOW,
        CORRELATION,
        source_revision_payload(source, chunks),
    )
    blobs = MemoryBlobs(
        {
            prepared.original_blob: original,
            prepared.normalized_blob: prepared.normalized.content,
        }
    )
    commitment = SourceCommitment(
        source.source_id,
        source.revision_id,
        chunks[0].chunk_id,
        chunks[0].start_offset,
        chunks[0].end_offset,
    )
    return event, blobs, source, commitment


def _provenance(
    commitment: SourceCommitment,
    variant: LessonMaterialVariant,
    output_fingerprint: str,
) -> GeneratedArtifactProvenance:
    dependency = ArtifactReadDependency(
        "source_revision", str(commitment.source_id), str(commitment.revision_id)
    )
    return GeneratedArtifactProvenance(
        (commitment,),
        PromptProvenance("material", "1.0.0"),
        None,
        RetrievalProvenance("root", "1.0.0", "a" * 64, "root-index", "b" * 64),
        (ValidatorProvenance("material-integrity", "1.0.0", True, "continue", "c" * 64),),
        VersionPins(
            "material-skill@1",
            "material-flow@1",
            "material-prompt@1",
            None,
            "event-state@1",
            "source.search@1",
        ),
        None,
        (dependency,),
        output_fingerprint,
        RUN,
    )


def _content(
    variant: LessonMaterialVariant,
    markdown: bytes,
    parent: str,
) -> StudyArtifactEnvelope:
    blob = BlobRef(
        id=BlobId(f"sha256:{sha256(markdown).hexdigest()}"),
        checksum_sha256=sha256(markdown).hexdigest(),
        byte_length=len(markdown),
    )
    return StudyArtifactEnvelope(
        StudyArtifactKind.LESSON_MATERIAL,
        LessonMaterialContent(
            variant,
            "Cardiology material",
            blob,
            len(markdown.decode()),
            parent,
            ("Generated output is limited to the supplied root source.",),
        ),
    )


def _proposal(
    ordinal: int,
    batch_id: ArtifactBatchId,
    envelope: StudyArtifactEnvelope,
    provenance: GeneratedArtifactProvenance,
) -> RecordedArtifactProposal:
    artifact_id = artifact_id_for(batch_id, ordinal)
    content_bytes = envelope.to_bytes()
    provenance_bytes = artifact_provenance_to_bytes(provenance)
    return RecordedArtifactProposal(
        ordinal,
        artifact_id,
        artifact_revision_id_for(artifact_id, envelope.kind, content_bytes, provenance_bytes, None),
        envelope.kind,
        content_bytes,
        provenance_bytes,
        None,
        None,
    )


def _fixture() -> tuple[
    GeneratedSourceMaterializer,
    MemoryEvents,
    MemoryBlobs,
    ArtifactRevisionId,
    ArtifactRevisionId,
]:
    root_event, blobs, root, commitment = _root_fixture()
    complete_markdown = b"# Complete\n\nThree cusps form the valve.\n"
    study_markdown = b"# Study\n\nRecall: three cusps.\n"
    batch_id = artifact_batch_id_for(COURSE, SESSION, RUN, PROPOSAL_KEY)
    complete_content = _content(
        LessonMaterialVariant.COMPLETE,
        complete_markdown,
        root.normalized_blob.checksum_sha256,
    )
    study_content = _content(
        LessonMaterialVariant.STUDY,
        study_markdown,
        sha256(complete_markdown).hexdigest(),
    )
    complete = _proposal(
        0,
        batch_id,
        complete_content,
        _provenance(
            commitment,
            LessonMaterialVariant.COMPLETE,
            sha256(complete_content.to_bytes()).hexdigest(),
        ),
    )
    study = _proposal(
        1,
        batch_id,
        study_content,
        _provenance(
            commitment,
            LessonMaterialVariant.STUDY,
            sha256(study_content.to_bytes()).hexdigest(),
        ),
    )
    proposal = DomainEvent(
        artifact_event_id_for(COURSE, SESSION, PROPOSAL_KEY, "proposal"),
        COURSE,
        2,
        PROPOSAL_BATCH_RECORDED,
        ARTIFACT_SCHEMA_VERSION,
        Actor(PrincipalKind.SERVICE, "material-generator"),
        NOW,
        CORRELATION,
        proposal_batch_payload(
            batch_id,
            ArtifactProposalOrigin.GENERATED,
            (complete, study),
            SESSION,
            PROPOSAL_KEY,
            run_id=RUN,
            proof=PROOF,
        ),
        SESSION,
    )
    decision_complete = DomainEvent(
        artifact_event_id_for(COURSE, SESSION, "decision-complete", "decision"),
        COURSE,
        3,
        DECISION_RECORDED,
        ARTIFACT_SCHEMA_VERSION,
        Actor(PrincipalKind.HUMAN, "reviewer"),
        NOW,
        CORRELATION,
        decision_payload(
            complete.revision_id,
            ArtifactDecision.ACCEPT,
            None,
            SESSION,
            "decision-complete",
            None,
        ),
        SESSION,
    )
    decision_study = DomainEvent(
        artifact_event_id_for(COURSE, SESSION, "decision-study", "decision"),
        COURSE,
        4,
        DECISION_RECORDED,
        ARTIFACT_SCHEMA_VERSION,
        Actor(PrincipalKind.HUMAN, "reviewer"),
        NOW,
        CORRELATION,
        decision_payload(
            study.revision_id,
            ArtifactDecision.ACCEPT,
            None,
            SESSION,
            "decision-study",
            None,
        ),
        SESSION,
    )
    complete_digest = sha256(complete_markdown).hexdigest()
    study_digest = sha256(study_markdown).hexdigest()
    blobs.values.update(
        {
            BlobRef(BlobId(f"sha256:{complete_digest}"), complete_digest, len(complete_markdown)):
            complete_markdown,
            BlobRef(BlobId(f"sha256:{study_digest}"), study_digest, len(study_markdown)):
            study_markdown,
        }
    )
    events = MemoryEvents([root_event, proposal, decision_complete, decision_study])
    return (
        GeneratedSourceMaterializer(
            blobs=blobs,
            events=events,
            load_projection=lambda _course: _projection(events, blobs),
        ),
        events,
        blobs,
        complete.revision_id,
        study.revision_id,
    )


def test_materializer_derives_generated_source_and_is_exactly_idempotent() -> None:
    materializer, events, _blobs, complete_revision, _study_revision = _fixture()
    context = ExecutionContext(PrincipalKind.SERVICE, "materializer", COURSE, CORRELATION)

    first = materializer.materialize(artifact_revision_id=complete_revision, context=context)
    restarted = GeneratedSourceMaterializer(
        blobs=_blobs,
        events=events,
        load_projection=lambda _course: _projection(events, _blobs),
    )
    second = restarted.materialize(artifact_revision_id=complete_revision, context=context)

    assert first.status is GeneratedSourceMaterializationStatus.EMITTED
    assert second.status is GeneratedSourceMaterializationStatus.IDEMPOTENT
    assert second.source == first.source
    assert second.chunks == first.chunks
    assert len(events.events) == 5
    assert second.committed_sequence == first.committed_sequence
    assert first.source.content_origin is ContentOrigin.GENERATED
    assert first.source.structure_origin is StructureOrigin.HUMAN_APPROVED
    assert first.source.generated_provenance is not None
    assert (
        events.events[-1].causation_id
        == first.source.generated_provenance.human_decision_event_id
    )


def test_materializer_rejects_invalid_lineage_without_appending() -> None:
    materializer, events, _blobs, _complete_revision, _study_revision = _fixture()
    original = list(events.events)
    # The root commitment is not allowed to be a generated source; an unknown
    # revision therefore fails before the append boundary.
    with pytest.raises(
        GeneratedSourceMaterializationError,
        match=r"canonical proposal|root source",
    ):
        materializer.materialize(
            artifact_revision_id=ArtifactRevisionId("unknown-revision"),
            context=ExecutionContext(PrincipalKind.SERVICE, "materializer", COURSE, CORRELATION),
        )
    assert events.events == original
    assert events.append_calls == 0


def test_materializer_requires_exact_human_acceptance_without_service_policy_receipt() -> None:
    materializer, events, _blobs, complete_revision, _study_revision = _fixture()
    events.events[2] = DomainEvent(
        events.events[2].event_id,
        COURSE,
        3,
        DECISION_RECORDED,
        ARTIFACT_SCHEMA_VERSION,
        Actor(PrincipalKind.SERVICE, "policy"),
        NOW,
        CORRELATION,
        decision_payload(
            complete_revision,
            ArtifactDecision.ACCEPT,
            None,
            SESSION,
            "decision-service",
            None,
        ),
        SESSION,
    )
    with pytest.raises((GeneratedSourceMaterializationError, ValueError)):
        materializer.materialize(
            artifact_revision_id=complete_revision,
            context=ExecutionContext(PrincipalKind.SERVICE, "materializer", COURSE, CORRELATION),
        )
    assert events.append_calls == 0


@pytest.mark.parametrize("principal", (PrincipalKind.HUMAN, PrincipalKind.MODEL))
def test_materializer_rejects_non_service_authority_before_append(
    principal: PrincipalKind,
) -> None:
    materializer, events, _blobs, complete_revision, _study_revision = _fixture()

    with pytest.raises(GeneratedSourceMaterializationError, match="SERVICE authority"):
        materializer.materialize(
            artifact_revision_id=complete_revision,
            context=ExecutionContext(principal, "unprivileged", COURSE, CORRELATION),
        )

    assert events.append_calls == 0


def test_study_material_waits_for_complete_source_then_both_materialize() -> None:
    materializer, events, _blobs, complete_revision, study_revision = _fixture()
    context = ExecutionContext(PrincipalKind.SERVICE, "materializer", COURSE, CORRELATION)

    with pytest.raises(
        GeneratedSourceMaterializationError,
        match="canonical projection validation",
    ):
        materializer.materialize(artifact_revision_id=study_revision, context=context)
    assert events.append_calls == 0

    complete = materializer.materialize(
        artifact_revision_id=complete_revision, context=context
    )
    study = materializer.materialize(artifact_revision_id=study_revision, context=context)

    assert complete.status is GeneratedSourceMaterializationStatus.EMITTED
    assert study.status is GeneratedSourceMaterializationStatus.EMITTED
    assert study.source.generated_provenance is not None
    assert study.source.generated_provenance.variant is LessonMaterialVariant.STUDY
    assert len(events.events) == 6
