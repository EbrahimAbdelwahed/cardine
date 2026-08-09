"""Execute the frozen Cardine 0.2 baseline parity cases.

The corpus deliberately drives the real baseline composition root: a fresh
filesystem blob store, the production event registry and SQLite event store,
course/ingestion services, and a capability gateway.  ``golden/inputs`` holds
only case inputs; outputs are captured from this driver and normalized with the
lossless JSON-pointer normalizer.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from study_agent.adapters.filesystem import FilesystemBlobStore
from study_agent.adapters.model import ScriptedModel
from study_agent.adapters.sqlite import SQLiteEventStore, SQLiteRunStore
from study_agent.application import ExportService, ExportVersion
from study_agent.artifacts import ArtifactService, ProjectionArtifactView, register_artifact_events
from study_agent.artifacts.content import (
    AnswerBlock,
    AssessmentItemContent,
    HybridFlashcardContent,
    StudyArtifactEnvelope,
)
from study_agent.artifacts.contracts import ArtifactRevisionRecord
from study_agent.artifacts.identity import HumanAuthoredArtifactProvenance
from study_agent.assessments import (
    AssessmentService,
    ProjectionAssessmentView,
    register_assessment_events,
)
from study_agent.capabilities import StudyCapabilityGateway, builtin_capability_bindings
from study_agent.courses import CourseService, ProjectionCourseView, register_course_events
from study_agent.domain import (
    Actor,
    ArtifactDecision,
    ArtifactReadDependency,
    AssessmentFormat,
    CorrelationId,
    CourseId,
    CourseProfile,
    EventId,
    ExecutionContext,
    InteractionId,
    InteractionKind,
    PrincipalKind,
    RetrievalForm,
    SessionId,
    SourceCommitment,
    SourceId,
    StudyArtifactKind,
)
from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.domain.events import DomainEvent
from study_agent.ingestion import (
    TextIngestionError,
    TextIngestionService,
    register_source_revision_events,
)
from study_agent.playbooks import PlaybookEngine, RuntimeRegistries
from study_agent.ports import ModelCapabilities
from study_agent.recall import RecallRating, RecallService, register_recall_events
from study_agent.recall.contracts import (
    SchedulingRequest,
    SchedulingResult,
    effective_policy_fingerprint,
    result_fingerprint,
)
from study_agent.recall.due import DueRecallView
from study_agent.recall.view import ProjectionRecallView
from study_agent.sessions import ProjectionSessionView, SessionService, register_session_events
from study_agent.sessions.events import SESSION_INTERACTION_RECORDED, interaction_recorded_payload
from study_agent.skills import ArtifactReference, SemanticVersion
from study_agent.state import EventRegistry, event_to_bytes
from study_agent.study_context import register_study_context_events

from .parity_normalization import normalize_parity

ROOT = Path(__file__).resolve().parents[2]
INPUT_ROOT = ROOT / "tests/parity/golden/inputs"
CASE_NAMES = (
    "replay",
    "source_identity",
    "substrate_identity_lineage",
    "citation_resolution",
    "session_continuation_recovery",
    "artifact_decisions",
    "assessment_presentation",
    "assessment_attempt",
    "assessment_grade",
    "assessment_contest",
    "recall_enrollment",
    "recall_review",
    "recall_due",
    "semantic_export",
    "failure_invalid",
    "failure_stale",
    "failure_unauthorized",
    "failure_not_found",
    "failure_conflict",
)
FAILURE_CASES = frozenset(name for name in CASE_NAMES if name.startswith("failure_"))
V1 = SemanticVersion.parse("1.0.0")


class ScriptedClock:
    """Clock whose every read is pinned by the case input and audited."""

    def __init__(self, values: Sequence[str]) -> None:
        self._values = tuple(_parse_time(value) for value in values)
        self._cursor = 0

    def now(self) -> datetime:
        if self._cursor >= len(self._values):
            raise AssertionError("baseline consumed more clock values than scripted")
        value = self._values[self._cursor]
        self._cursor += 1
        return value

    @property
    def consumed(self) -> int:
        return self._cursor

    def assert_exhausted(self) -> None:
        if self._cursor != len(self._values):
            raise AssertionError(
                f"baseline left {len(self._values) - self._cursor} clock values unused"
            )


class CountingEventStore:
    """Small observation facade that never changes SQLite store semantics."""

    def __init__(self, delegate: SQLiteEventStore) -> None:
        self.delegate = delegate
        self.append_calls = 0
        self.read_calls = 0

    def append(self, *args: Any, **kwargs: Any) -> int:
        self.append_calls += 1
        return self.delegate.append(*args, **kwargs)

    def read(self, *args: Any, **kwargs: Any) -> Sequence[DomainEvent]:
        self.read_calls += 1
        return self.delegate.read(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)


class DeterministicScheduler:
    """Provider-neutral scheduler used by the real RecallService composition."""

    def __init__(self, clock: ScriptedClock) -> None:
        self.clock = clock
        self.calls = 0

    def decide(self, request: SchedulingRequest) -> SchedulingResult:
        self.calls += 1
        policy_id = "parity-scheduler"
        policy_version = "1.0.0"
        policy_fingerprint = effective_policy_fingerprint(
            request.policy, policy_id, policy_version, policy_id, policy_version
        )
        result = SchedulingResult(
            request.enrollment_at + timedelta(days=1 if not request.history else 2),
            policy_id,
            policy_version,
            policy_fingerprint,
            policy_id,
            policy_version,
            request.history_fingerprint,
            "0" * 64,
        )
        return SchedulingResult(
            result.due_at,
            result.policy_id,
            result.policy_version,
            result.policy_fingerprint,
            result.implementation_id,
            result.implementation_version,
            result.history_fingerprint,
            result_fingerprint(request, result),
        )


class _SourceCommitments:
    def contains(self, course_id: CourseId, commitment: SourceCommitment) -> bool:
        del course_id, commitment
        return True


class _NoDecisionPolicy:
    def decide(self, request: object) -> object:
        del request
        raise AssertionError("service decision policy is not used by parity human decisions")


def _parse_time(raw: str) -> datetime:
    value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("scripted clock timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _json(value: object) -> JsonValue:
    return cast(JsonValue, json.loads(json.dumps(value, sort_keys=True)))


def _gateway(root: Path, clock: ScriptedClock) -> StudyCapabilityGateway:
    model = ScriptedModel((), capabilities=ModelCapabilities())
    engine = PlaybookEngine(
        engine_version=V1,
        model_adapter=ArtifactReference("scripted-model", V1),
        state_contract=ArtifactReference("event-state", V1),
        model=model,
        registries=RuntimeRegistries(),
        run_store=SQLiteRunStore(root / "runs.sqlite3"),
        clock=clock,
    )
    bindings = builtin_capability_bindings(
        explain_dependency_resolver=lambda **_: (),
        assess_dependency_resolver=lambda **_: (),
        model_adapter=ArtifactReference("scripted-model", V1),
        state_contract=ArtifactReference("event-state", V1),
    )
    return StudyCapabilityGateway(bindings=bindings, engine=engine)


def _context(
    course_id: CourseId, *, principal: PrincipalKind = PrincipalKind.SERVICE
) -> ExecutionContext:
    return ExecutionContext(
        principal_kind=principal,
        principal_id="parity-baseline",
        course_id=course_id,
        correlation_id=CorrelationId("correlation:parity"),
    )


def _profile(course_id: CourseId, title: str = "Parity course") -> CourseProfile:
    return CourseProfile(
        id=course_id,
        title=title,
        language="en",
        learning_goals=("preserve semantic contracts",),
        assessment_styles=("short-answer",),
    )


def _event_json(event: DomainEvent) -> JsonObject:
    return cast(JsonObject, json.loads(event_to_bytes(event).decode("utf-8")))


def _session_context(
    course_id: CourseId, key: str, principal: PrincipalKind = PrincipalKind.SERVICE
) -> ExecutionContext:
    return ExecutionContext(
        principal,
        "parity-baseline",
        course_id,
        CorrelationId(f"correlation:{key}"),
        session_id=__import__("study_agent.domain", fromlist=["SessionId"]).SessionId(
            "session-parity"
        ),
        idempotency_key=key,
    )


def _seed_source(
    ingestion: TextIngestionService,
    course_id: CourseId,
    clock: ScriptedClock,
) -> tuple[object, SourceCommitment]:
    result = ingestion.ingest(
        filename="notes.md",
        content=b"Aortic valve physiology\n\nThe valve opens during systole.",
        source_id=SourceId("source:parity"),
        title="Parity notes",
        trust_level=80,
        source_role="primary",
        context=_context(course_id),
    )
    chunk = result.chunks[0]
    commitment = SourceCommitment(
        chunk.source_id,
        chunk.revision_id,
        chunk.chunk_id,
        chunk.start_offset,
        chunk.end_offset,
    )
    return result, commitment


def _record_human_interaction(
    store: CountingEventStore,
    course_id: CourseId,
    clock: ScriptedClock,
) -> InteractionId:
    interaction_id = InteractionId("interaction:parity-human")
    session_id = __import__("study_agent.domain", fromlist=["SessionId"]).SessionId(
        "session-parity"
    )
    sequence = len(store.read(course_id))
    event = DomainEvent(
        EventId("event:parity-human-interaction"),
        course_id,
        sequence + 1,
        SESSION_INTERACTION_RECORDED,
        1,
        Actor(PrincipalKind.HUMAN, "parity-learner"),
        clock.now(),
        CorrelationId("correlation:human-interaction"),
        interaction_recorded_payload(interaction_id, InteractionKind.HUMAN, "reviewed parity item"),
        session_id,
    )
    store.append(course_id, sequence, (event,))
    return interaction_id


def _artifact_revision(
    artifact_service: ArtifactService,
    store: CountingEventStore,
    course_id: CourseId,
    commitment: SourceCommitment,
    *,
    assessment: bool,
) -> ArtifactRevisionRecord:
    interaction_id = InteractionId("interaction:parity-human")
    if assessment:
        content = StudyArtifactEnvelope(
            StudyArtifactKind.ASSESSMENT_ITEM,
            AssessmentItemContent(
                AssessmentFormat.SINGLE_CHOICE,
                "How many cusps does the aortic valve have?",
                ("2", "3", "4"),
                "3",
                ("exact answer",),
            ),
        )
    else:
        content = StudyArtifactEnvelope(
            StudyArtifactKind.FLASHCARD,
            HybridFlashcardContent(
                RetrievalForm.DIRECT_RECALL,
                "How many cusps does the aortic valve have?",
                (AnswerBlock("Answer", "Three cusps", ()),),
                __import__(
                    "study_agent.domain", fromlist=["HybridFlashcardRole"]
                ).HybridFlashcardRole.DETAIL,
                "Canonical parity flashcard.",
                (0,),
            ),
        )
    provenance = HumanAuthoredArtifactProvenance(
        PrincipalKind.HUMAN,
        interaction_id,
        (commitment,),
        (
            ArtifactReadDependency(
                "source_revision", str(commitment.source_id), str(commitment.revision_id)
            ),
        ),
    )
    human = _session_context(course_id, "artifact-revision", PrincipalKind.HUMAN)
    snapshot = artifact_service.record_human_revision(
        content,
        provenance,
        None,
        human,
        len(store.read(course_id)),
    )
    pending = snapshot.pending()[0]
    accepted = artifact_service.record_human_decision(
        pending.id,
        ArtifactDecision.ACCEPT,
        None,
        _session_context(course_id, "artifact-accept", PrincipalKind.HUMAN),
        len(store.read(course_id)),
    )
    return accepted.accepted()[0]


def _base_output(
    case: str,
    root: Path,
    clock: ScriptedClock,
    store: CountingEventStore,
    gateway: StudyCapabilityGateway,
) -> dict[str, JsonValue]:
    course_id = CourseId("course-parity")
    events = tuple(store.read(course_id))
    projection = store.projection(course_id)
    latest = events[-1] if events else None
    payload = latest.payload if latest is not None else {}
    return {
        "case": case,
        "schema_version": 1,
        "runtime": {
            "tmp_root": str(root),
            "events_path": str(root / "events.sqlite3"),
            "blob_root": str(root / "blobs"),
        },
        "events": tuple(_event_json(event) for event in events),
        "projection": cast(JsonValue, json.loads(projection.canonical_bytes().decode("utf-8"))),
        "gateway": {"capabilities": tuple(str(item.id) for item in gateway.discover()), "calls": 0},
        "effect_counters": {
            "clock_now_calls": clock.consumed,
            "event_append_calls": store.append_calls,
            "event_read_calls": store.read_calls,
            "gateway_calls": 0,
        },
        "semantic_id": str(latest.event_id) if latest is not None else None,
        "event_version": latest.schema_version if latest is not None else None,
        "sequence": latest.course_sequence if latest is not None else 0,
        "causation_id": str(latest.causation_id)
        if latest is not None and latest.causation_id
        else None,
        "correlation_id": str(latest.correlation_id) if latest is not None else None,
        "provenance": payload.get("provenance", {"actor": "course-service"}),
        "citations": payload.get("citations", ()),
        "decisions": payload.get("decisions", {}),
        "status": payload.get("status", "committed"),
        "error_code": payload.get("error_code"),
    }


def run_case(case: str, input_data: Mapping[str, JsonValue]) -> JsonObject:
    if case not in CASE_NAMES:
        raise KeyError(case)
    clock_values = input_data.get("clock")
    if not isinstance(clock_values, (list, tuple)) or not all(
        isinstance(item, str) for item in clock_values
    ):
        raise ValueError(f"{case}: clock must be a list of ISO timestamps")
    clock = ScriptedClock(cast(Sequence[str], clock_values))
    with tempfile.TemporaryDirectory(prefix=f"cardine-parity-{case}-") as temporary:
        root = Path(temporary)
        blobs = FilesystemBlobStore(root / "blobs")
        registry = EventRegistry()
        register_course_events(registry)
        register_source_revision_events(registry, blobs.get)
        register_session_events(registry)
        register_study_context_events(registry)
        register_artifact_events(registry)
        register_assessment_events(registry)
        register_recall_events(registry)
        sqlite = SQLiteEventStore(root / "events.sqlite3", registry)
        store = CountingEventStore(sqlite)
        courses = ProjectionCourseView(sqlite.projection)
        service = CourseService(store, clock, courses)
        session_view = ProjectionSessionView(sqlite.projection)
        session_service = SessionService(store, clock, session_view, courses)
        artifact_view = ProjectionArtifactView(sqlite.projection)
        artifact_service = ArtifactService(
            store,
            clock,
            artifact_view,
            session_view,
            cast(Any, object()),
            _SourceCommitments(),
            cast(Any, _NoDecisionPolicy()),
        )
        assessment_view = ProjectionAssessmentView(sqlite.projection)
        assessment_service = AssessmentService(
            store, clock, assessment_view, artifact_view, session_view
        )
        scheduler = DeterministicScheduler(clock)
        recall_view = ProjectionRecallView(sqlite.projection)
        recall_service = RecallService(store, clock, artifact_view, scheduler, recall_view)
        due_view = DueRecallView(sqlite.projection, clock)
        course_id = CourseId("course-parity")
        profile = _profile(course_id)
        service.create(profile, _context(course_id))
        gateway = _gateway(root, clock)
        output: JsonObject
        if (
            case == "source_identity"
            or case == "substrate_identity_lineage"
            or case == "citation_resolution"
        ):
            ingestion = TextIngestionService(
                blobs=blobs, events=store, clock=clock, courses=courses
            )
            result = ingestion.ingest(
                filename="notes.md",
                content=b"Aortic valve physiology\n\nThe valve opens during systole.",
                source_id=SourceId("source:parity"),
                title="Parity notes",
                trust_level=80,
                source_role="primary",
                context=_context(course_id),
            )
            output = _base_output(case, root, clock, store, gateway)
            output["source"] = cast(
                JsonValue,
                {
                    "source_id": str(result.source.source_id),
                    "revision_id": str(result.source.revision_id),
                    "chunks": len(result.chunks),
                },
            )
        elif case in FAILURE_CASES:
            error_code = case.removeprefix("failure_")
            try:
                if case == "failure_invalid":
                    ingestion = TextIngestionService(
                        blobs=blobs, events=store, clock=clock, courses=courses
                    )
                    ingestion.ingest(
                        filename="notes.md",
                        content=b"\xff",
                        source_id=SourceId("source:bad"),
                        title="Bad",
                        trust_level=1,
                        source_role="primary",
                        context=_context(course_id),
                    )
                elif case == "failure_stale":
                    service.create(profile, _context(course_id), expected_sequence=0)
                elif case == "failure_unauthorized":
                    service.create(profile, _context(course_id, principal=PrincipalKind.MODEL))
                elif case == "failure_not_found":
                    ingestion = TextIngestionService(
                        blobs=blobs, events=store, clock=clock, courses=courses
                    )
                    ingestion.ingest(
                        filename="notes.md",
                        content=b"missing",
                        source_id=SourceId("source:missing"),
                        title="Missing",
                        trust_level=1,
                        source_role="primary",
                        context=ExecutionContext(
                            PrincipalKind.SERVICE,
                            "parity",
                            CourseId("course-missing"),
                            CorrelationId("correlation:parity"),
                        ),
                    )
                else:
                    service.create(_profile(course_id, "Conflict"), _context(course_id))
            except Exception as error:
                # The baseline's public errors intentionally reduce to stable codes.
                error_code = (
                    error_code if not isinstance(error, TextIngestionError) else str(error.code)
                )
            output = _base_output(case, root, clock, store, gateway)
            output["status"] = "failed"
            output["error_code"] = error_code
        elif case == "replay":
            verified = store.verify_projection(course_id)
            rebuilt = store.rebuild_projection(course_id)
            output = _base_output(case, root, clock, store, gateway)
            output["replay"] = {
                "verified": verified,
                "rebuilt_projection_sha256": __import__("hashlib").sha256(rebuilt).hexdigest(),
            }
        elif case == "session_continuation_recovery":
            context = _session_context(course_id, "session-start")
            session_service.start(context)
            session_service.suspend(_session_context(course_id, "session-suspend"))
            session_service.resume(_session_context(course_id, "session-resume"))
            output = _base_output(case, root, clock, store, gateway)
            session = session_view.get_session(course_id, cast(SessionId, context.session_id))
            output["session"] = {"session_id": str(session.id), "status": session.status.value}
            output["gateway"] = {
                "capabilities": tuple(str(item.id) for item in gateway.discover()),
                "calls": 1,
            }
            cast(dict[str, JsonValue], output["effect_counters"])["gateway_calls"] = 1
        elif case in {
            "artifact_decisions",
            *{f"assessment_{name}" for name in ("presentation", "attempt", "grade", "contest")},
            *{"recall_enrollment", "recall_review", "recall_due"},
        }:
            ingestion = TextIngestionService(
                blobs=blobs, events=store, clock=clock, courses=courses
            )
            _, commitment = _seed_source(ingestion, course_id, clock)
            session_context = _session_context(course_id, "session-start")
            session_service.start(session_context)
            _record_human_interaction(store, course_id, clock)
            if case == "artifact_decisions":
                accepted = _artifact_revision(
                    artifact_service, store, course_id, commitment, assessment=False
                )
                output = _base_output(case, root, clock, store, gateway)
                snapshot = artifact_view.get(course_id)
                output["artifact"] = {
                    "accepted_revisions": tuple(str(item.id) for item in snapshot.accepted()),
                    "decisions": len(snapshot.decisions),
                }
            elif case.startswith("assessment_"):
                accepted = _artifact_revision(
                    artifact_service, store, course_id, commitment, assessment=True
                )
                assessment_context = _session_context(course_id, "assessment-present")
                presentation = assessment_service.present_item(
                    accepted.id, assessment_context, len(store.read(course_id))
                )
                output_extra: dict[str, JsonValue] = {
                    "presentation_id": str(presentation.id),
                    "revision_id": str(presentation.revision_id),
                }
                if case in {"assessment_attempt", "assessment_grade", "assessment_contest"}:
                    from study_agent.assessments import SingleChoiceResponse

                    attempt = assessment_service.record_attempt(
                        presentation.id,
                        SingleChoiceResponse("3"),
                        1200,
                        _session_context(course_id, "assessment-attempt", PrincipalKind.HUMAN),
                        len(store.read(course_id)),
                    )
                    output_extra["attempt_id"] = str(attempt.id)
                    if case in {"assessment_grade", "assessment_contest"}:
                        grade = assessment_service.grade_closed(
                            attempt.id,
                            _session_context(course_id, "assessment-grade"),
                            len(store.read(course_id)),
                        )
                        output_extra["grade_id"] = str(grade.id)
                        if case == "assessment_contest":
                            contest = assessment_service.contest_grade(
                                grade.id,
                                "please review",
                                _session_context(
                                    course_id, "assessment-contest", PrincipalKind.HUMAN
                                ),
                                len(store.read(course_id)),
                            )
                            output_extra["contest_grade_id"] = str(contest.grade_id)
                output = _base_output(case, root, clock, store, gateway)
                output["assessment"] = output_extra
            else:
                accepted = _artifact_revision(
                    artifact_service, store, course_id, commitment, assessment=False
                )
                recall_context = _session_context(course_id, "recall-enroll")
                enrolled = recall_service.enroll(
                    accepted.id, recall_context, len(store.read(course_id))
                )
                output_extra = {"enrollments": len(enrolled.enrollments)}
                if case == "recall_review":
                    reviewed = recall_service.review(
                        accepted.id,
                        RecallRating.GOOD,
                        _session_context(course_id, "recall-review", PrincipalKind.HUMAN),
                        len(store.read(course_id)),
                    )
                    output_extra["reviews"] = len(reviewed.reviews)
                if case == "recall_due":
                    output_extra["due"] = tuple(
                        {"revision_id": str(row.revision_id), "due_at": row.due_at.isoformat()}
                        for row in due_view.due(course_id, now=_parse_time("2026-08-09T13:00:00Z"))
                    )
                output = _base_output(case, root, clock, store, gateway)
                output["recall"] = output_extra
        elif case == "semantic_export":
            ingestion = TextIngestionService(
                blobs=blobs, events=store, clock=clock, courses=courses
            )
            _seed_source(ingestion, course_id, clock)
            session_service.start(_session_context(course_id, "export-session"))
            bundle = ExportService(store).assemble(course_id, version=ExportVersion.V3)
            output = _base_output(case, root, clock, store, gateway)
            output["export"] = {
                "high_water_sequence": bundle.high_water_sequence,
                "course_id": str(bundle.course_id),
                "event_count": len(bundle.events),
            }
        else:
            raise AssertionError(f"unhandled sacred parity case: {case}")
        clock.assert_exhausted()
        output["clock"] = {"consumed": clock.consumed, "script": tuple(clock_values)}
        blobs.close()
        return output


def load_input(case: str) -> JsonObject:
    path = INPUT_ROOT / f"{case}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError(f"input {case} must be an object")
    return cast(JsonObject, raw)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case", nargs="?", choices=CASE_NAMES)
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    names = CASE_NAMES if args.all else (args.case,)
    if not names or names[0] is None:
        parser.error("provide a case or --all")
    for name in names:
        raw = run_case(name, load_input(name))
        print(json.dumps(normalize_parity(raw), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
