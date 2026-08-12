"""Deterministic, credential-free TUT-04F headless eval coverage."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from typing import Final

from study_agent.application import ExportBundleV2, ExportService, ExportVersion
from study_agent.artifacts import (
    AnswerBlock,
    ArtifactProposal,
    ArtifactService,
    GeneratedArtifactProvenance,
    GeneratedBatchProofReceipt,
    HybridFlashcardContent,
    ProjectionArtifactView,
    StudyArtifactEnvelope,
    VerifiedGeneratedArtifactBatch,
)
from study_agent.artifacts.candidates import (
    FlashcardAnswerBlock,
    FlashcardCandidate,
    FlashcardCandidateBatch,
    FlashcardOmission,
    FlashcardPedagogicalRole,
)
from cardine.cli.repository import LocalRepository, initialize_local_repository
from study_agent.domain import (
    ArtifactDecision,
    ArtifactReadDependency,
    CorrelationId,
    CourseId,
    CourseProfile,
    ExecutionContext,
    HybridFlashcardRole,
    PrincipalKind,
    PromptProvenance,
    RetrievalForm,
    RetrievalProvenance,
    RunId,
    SessionId,
    SourceChunk,
    SourceCommitment,
    SourceId,
    StudyArtifactKind,
    ValidatorProvenance,
    VersionPins,
)
from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.flashcards.lesson_worker_contracts import LessonWorkerStatus
from study_agent.flashcards.lesson_worker_view import (
    LessonWorkerBatchReviewView,
    LessonWorkerCompactView,
    LessonWorkerCompletedReviewView,
    LessonWorkerOverviewAssociation,
)
from study_agent.flashcards.planning import PlannedBundleKind
from study_agent.pedagogy import (
    HYBRID_MACRO_DETAIL_V1,
    ProfileSelectionBasis,
    ProfileSelectionMode,
    ProfileSelectionReceipt,
    ProfileSelectorKind,
)
from study_agent.repository_config import EMPTY_CONFIG
from study_agent.state import canonical_json_bytes
from study_agent.tools.builtin import public_study_tool_manifests

_COURSE = CourseId("course-headless-eval")
_SESSION = SessionId("session-headless-eval")
_FORBIDDEN: Final[tuple[str, ...]] = (
    "mastery",
    "scratch",
    "credential",
    "principal",
    "response_id",
    "raw_source",
    "exam_body",
)


@dataclass(frozen=True, slots=True)
class _HeadlessFixture:
    """Aggregate worker observations plus the canonical export they summarize."""

    export: ExportBundleV2
    compact: LessonWorkerCompactView
    review: LessonWorkerCompletedReviewView


def _context(*, session: bool = False) -> ExecutionContext:
    return ExecutionContext(
        PrincipalKind.SERVICE,
        "headless-eval-host",
        _COURSE,
        CorrelationId("headless-eval-correlation"),
        session_id=_SESSION if session else None,
    )


def _content(role: HybridFlashcardRole, ordinal: int) -> StudyArtifactEnvelope:
    return StudyArtifactEnvelope(
        kind=StudyArtifactKind.FLASHCARD,
        content=HybridFlashcardContent(
            RetrievalForm.DIRECT_RECALL,
            f"Which valve fact is item {ordinal + 1}?",
            (AnswerBlock("Answer", "Three cusps"),),
            role,
            "A bounded source-grounded detail.",
            (0, 1),
        ),
    )


def _generated_provenance(
    content: StudyArtifactEnvelope,
    commitments: tuple[SourceCommitment, ...],
    run_id: RunId,
) -> GeneratedArtifactProvenance:
    dependencies = tuple(
        ArtifactReadDependency("source_revision", str(item.source_id), str(item.revision_id))
        for item in commitments
    )
    return GeneratedArtifactProvenance(
        commitments,
        PromptProvenance("headless-eval-prompt", "1.0.0"),
        None,
        RetrievalProvenance("lexical", "1.0.0", "c" * 64, "index-1", "d" * 64),
        (ValidatorProvenance("artifact-integrity", "1.0.0", True, "continue", "e" * 64),),
        VersionPins(
            "hybrid-skill@1",
            "hybrid-flow@1",
            "hybrid-prompt@1",
            None,
            "event-state@1",
            "source.prepare-flashcard-scope@1",
        ),
        ProfileSelectionReceipt(
            HYBRID_MACRO_DETAIL_V1,
            ProfileSelectionMode.DEFAULT,
            ProfileSelectorKind.HOST,
            PrincipalKind.SERVICE,
            ProfileSelectionBasis(),
        ),
        dependencies,
        sha256(content.to_bytes()).hexdigest(),
        run_id,
    )


def _commitment(chunk: SourceChunk) -> SourceCommitment:
    return SourceCommitment(
        chunk.source_id,
        chunk.revision_id,
        chunk.chunk_id,
        chunk.start_offset,
        chunk.end_offset,
    )


def _bundle_json(bundle: ExportBundleV2) -> JsonObject:
    return {
        "course_id": str(bundle.course_id),
        "high_water_sequence": bundle.high_water_sequence,
        "course": bundle.course,
        "sources": bundle.sources,
        "sessions": bundle.sessions,
        "answers": bundle.answers,
        "events": bundle.events,
        "artifacts": bundle.artifacts,
    }


class _GeneratedPort:
    def __init__(self, batches: Mapping[RunId, VerifiedGeneratedArtifactBatch]) -> None:
        self._batches = dict(batches)

    def recover(self, run_id: RunId, context: ExecutionContext) -> VerifiedGeneratedArtifactBatch:
        batch = self._batches[run_id]
        assert context.course_id == batch.course_id
        return batch


class _SourceCommitmentLookup:
    def __init__(self, commitments: tuple[SourceCommitment, ...]) -> None:
        self._commitments = frozenset(commitments)

    def contains(self, course_id: CourseId, commitment: SourceCommitment) -> bool:
        return course_id == _COURSE and commitment in self._commitments


class _NoPolicy:
    def decide(self, request):  # type: ignore[no-untyped-def]
        raise AssertionError("policy is not used for human decisions")


def _candidate_batch(role: FlashcardPedagogicalRole, ordinal: int) -> FlashcardCandidateBatch:
    candidate = FlashcardCandidate(
        f"candidate-{ordinal}",
        None,
        RetrievalForm.DIRECT_RECALL,
        f"Which valve fact is item {ordinal + 1}?",
        (FlashcardAnswerBlock("Answer", "Three cusps", ()),),
        role,
        None,
        None,
        "A bounded source-grounded detail.",
        (f"evhandle-{ordinal}",),
        (),
    )
    omissions = (
        FlashcardOmission(
            "The source paragraph did not support another card.",
            (f"evhandle-{ordinal}",),
        ),
    ) if ordinal == 1 else ()
    return FlashcardCandidateBatch((candidate,), omissions)


def _worker_gate() -> tuple[LessonWorkerCompactView, LessonWorkerCompletedReviewView]:
    """Typed worker gate fixture; export is intentionally a separate projection."""

    roles = (
        FlashcardPedagogicalRole.OVERVIEW,
        FlashcardPedagogicalRole.SECTION,
        FlashcardPedagogicalRole.DETAIL,
    )
    batches = tuple(_candidate_batch(role, ordinal) for ordinal, role in enumerate(roles))
    pages = tuple(
        LessonWorkerBatchReviewView(
            page_position=ordinal,
            bundle_id=f"bundle-{ordinal}",
            bundle_kind=PlannedBundleKind.TOPIC_GROUP,
            active_topic_keys=(f"topic-{ordinal}",),
            wrapper_fingerprint=f"{ordinal + 1}" * 64,
            scope_fingerprint=f"{ordinal + 2}" * 64,
            read_set_fingerprint=f"{ordinal + 3}" * 64,
            batch=batch,
        )
        for ordinal, batch in enumerate(batches)
    )
    run_id = RunId("headless-eval-lesson-run")
    review = LessonWorkerCompletedReviewView(
        run_id=run_id,
        plan_fingerprint="p" * 64,
        profile_fingerprint="q" * 64,
        revision_commitments_fingerprint="r" * 64,
        pages=pages,
        overview_associations=(
            LessonWorkerOverviewAssociation(
                page_position=0,
                candidate_key="candidate-0",
                associated_page_positions=(1, 2),
                associated_bundle_ids=("bundle-1", "bundle-2"),
            ),
        ),
    )
    compact = LessonWorkerCompactView(
        run_id=run_id,
        plan_fingerprint=review.plan_fingerprint,
        profile_fingerprint=review.profile_fingerprint,
        status=LessonWorkerStatus.COMPLETED,
        completed_positions=(0, 1, 2),
        failed_positions=(),
        pending_positions=(),
        candidate_count=sum(len(batch.candidates) for batch in batches),
        omission_count=sum(len(batch.omissions) for batch in batches),
        failure_codes=(),
        in_progress=False,
        advance_required=False,
    )
    return compact, review


def _fixture(tmp_path: Path) -> _HeadlessFixture:
    root = tmp_path / "headless-eval-repository"
    initialize_local_repository(root, EMPTY_CONFIG)
    with LocalRepository.open(root) as repository:
        repository.course_service.create(
            CourseProfile(_COURSE, "Headless artifact eval", "en", learning_goals=("Review",)),
            _context(),
        )
        lesson = repository.for_course(_COURSE).ingestion.ingest(
            filename="lesson.md",
            content=b"The aortic valve has three cusps.",
            source_id=SourceId("source-lesson"),
            title="Aortic valve lesson",
            trust_level=95,
            source_role="primary",
            context=_context(),
        )
        companion = repository.for_course(_COURSE).ingestion.ingest(
            filename="companion.md",
            content=b"The pulmonary valve also has three cusps.",
            source_id=SourceId("source-companion"),
            title="Companion valve notes",
            trust_level=90,
            source_role="secondary",
            context=_context(),
        )
        repository.session_service.start(_context(session=True))
        commitments = (_commitment(lesson.chunks[0]), _commitment(companion.chunks[0]))
        batches: dict[RunId, VerifiedGeneratedArtifactBatch] = {}
        roles = (
            HybridFlashcardRole.OVERVIEW,
            HybridFlashcardRole.SECTION,
            HybridFlashcardRole.DETAIL,
        )
        decisions = (None, ArtifactDecision.ACCEPT, ArtifactDecision.REJECT)
        for ordinal, (role, _decision) in enumerate(zip(roles, decisions, strict=True)):
            run_id = RunId(f"headless-eval-run-{ordinal}")
            content = _content(role, ordinal)
            provenance = _generated_provenance(content, commitments, run_id)
            batches[run_id] = VerifiedGeneratedArtifactBatch(
                run_id,
                _COURSE,
                _SESSION,
                (ArtifactProposal(0, content, provenance),),
                GeneratedBatchProofReceipt("headless-eval", "1.0.0", "f" * 64),
            )
        artifacts = ArtifactService(
            repository.events,
            repository.clock,
            ProjectionArtifactView(repository.events.projection),
            repository.sessions,
            _GeneratedPort(batches),
            _SourceCommitmentLookup(commitments),
            _NoPolicy(),
        )
        revisions = []
        for run_id, decision in zip(batches, decisions, strict=True):
            context = ExecutionContext(
                PrincipalKind.SERVICE,
                "headless-eval-host",
                _COURSE,
                CorrelationId(f"headless-eval-proposal-{run_id}"),
                session_id=_SESSION,
                idempotency_key=f"headless-eval-proposal-{run_id}",
            )
            snapshot = artifacts.record_generated(
                run_id, context, len(repository.events.read(_COURSE))
            )
            revision = snapshot.pending()[0]
            revisions.append(revision)
            if decision is not None:
                human = ExecutionContext(
                    PrincipalKind.HUMAN,
                    "headless-eval-reviewer",
                    _COURSE,
                    CorrelationId(f"headless-eval-decision-{run_id}"),
                    session_id=_SESSION,
                    idempotency_key=f"headless-eval-decision-{run_id}",
                )
                artifacts.record_human_decision(
                    revision.id,
                    decision,
                    None,
                    human,
                    len(repository.events.read(_COURSE)),
                )
        bundle = ExportService(repository.events).assemble(_COURSE, version=ExportVersion.V2)
        assert isinstance(bundle, ExportBundleV2)
        replay = ExportService(repository.events).assemble(_COURSE, version=ExportVersion.V2)
        assert isinstance(replay, ExportBundleV2)
        assert canonical_json_bytes(_bundle_json(bundle)) == canonical_json_bytes(
            _bundle_json(replay)
        )
        compact, review = _worker_gate()
        return _HeadlessFixture(
            bundle,
            compact,
            review,
        )


def _report(fixture: _HeadlessFixture) -> JsonObject:
    statuses = [str(row["status"]) for row in fixture.export.artifacts]
    compact = fixture.compact
    review = fixture.review
    roles = tuple(
        candidate.pedagogical_role.value
        for page in review.pages
        for candidate in page.batch.candidates
    )
    linked_rows = 0
    for row in fixture.export.artifacts:
        provenance = row["provenance"]
        if isinstance(provenance, Mapping):
            links = provenance.get("source_commitments", ())
            if isinstance(links, tuple) and links:
                linked_rows += 1
    return {
        "coverage": {
            "planned_pages": len(compact.completed_positions)
            + len(compact.failed_positions)
            + len(compact.pending_positions),
            "completed_pages": len(compact.completed_positions),
            "candidate_count": compact.candidate_count,
            "exported_artifacts": len(fixture.export.artifacts),
        },
        "omissions": {"count": compact.omission_count},
        "grounding_failures": {
            "count": sum(
                code.startswith("grounding") for code in compact.failure_codes
            )
        },
        "decisions": {
            "proposed": statuses.count("proposed"),
            "accepted": statuses.count("accepted"),
            "rejected": statuses.count("rejected"),
        },
        "hierarchy_roles": {role: roles.count(role) for role in sorted(set(roles))},
        "provenance": {"export_rows_with_source_links": linked_rows},
    }


def _assert_safe(value: JsonValue) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).lower()
            assert not any(token in lowered for token in _FORBIDDEN)
            _assert_safe(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            _assert_safe(item)
    elif isinstance(value, str):
        lowered = value.lower()
        assert not any(token in lowered for token in _FORBIDDEN)


def test_headless_artifact_eval_report_is_canonical_and_complete(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    expected = {
        "coverage": {
            "planned_pages": 3,
            "completed_pages": 3,
            "candidate_count": 3,
            "exported_artifacts": 3,
        },
        "omissions": {"count": 1},
        "grounding_failures": {"count": 0},
        "decisions": {"proposed": 1, "accepted": 1, "rejected": 1},
        "hierarchy_roles": {"detail": 1, "overview": 1, "section": 1},
        "provenance": {"export_rows_with_source_links": 3},
    }
    report = _report(fixture)
    assert report == expected
    first = canonical_json_bytes(report)
    assert first == canonical_json_bytes(_report(_fixture(tmp_path / "second")))
    _assert_safe(report)
    assert b"mastery" not in first


def test_worker_metrics_do_not_treat_export_rejects_as_omissions(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    rows = tuple({**row, "status": "accepted"} for row in fixture.export.artifacts)
    divergent = replace(fixture, export=replace(fixture.export, artifacts=rows))

    baseline = _report(fixture)
    changed = _report(divergent)
    assert changed["omissions"] == baseline["omissions"]
    assert changed["coverage"] == baseline["coverage"]
    assert changed["hierarchy_roles"] == baseline["hierarchy_roles"]
    assert changed["decisions"] != baseline["decisions"]


def test_headless_eval_keeps_exact_public_study_tool_surface() -> None:
    assert tuple(item.name for item in public_study_tool_manifests()) == (
        "citation.resolve",
        "course.get",
        "grounding.ask",
        "session.get_context",
        "session.record_note",
        "source.list",
        "source.search",
    )
