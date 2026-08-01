from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from study_agent.adapters.filesystem import initialize_local_repository
from study_agent.artifacts import (
    AnswerBlock,
    HumanAuthoredArtifactProvenance,
    HybridFlashcardContent,
    StudyArtifactEnvelope,
)
from study_agent.cli.repository import LocalRepository
from study_agent.demo.ui_application import (
    DemoUiApplication,
    RepositoryUiApplication,
    UiRequestError,
)
from study_agent.domain import (
    Actor,
    ArtifactReadDependency,
    ArtifactRevisionId,
    CorrelationId,
    CourseId,
    CourseProfile,
    DomainEvent,
    EventId,
    ExecutionContext,
    HybridFlashcardRole,
    InteractionId,
    InteractionKind,
    PrincipalKind,
    RetrievalForm,
    SessionId,
    SourceCommitment,
    SourceId,
    StudyArtifactKind,
    TutorSnapshotV1,
)
from study_agent.ingestion import decode_source_revision_ingested
from study_agent.ports.scheduling import SchedulingPolicyPort
from study_agent.recall import (
    RecallRating,
    SchedulingRequest,
    SchedulingResult,
    effective_policy_fingerprint,
    result_fingerprint,
)
from study_agent.repository_config import EMPTY_CONFIG
from study_agent.sessions import (
    SESSION_INTERACTION_RECORDED,
    SESSION_SCHEMA_VERSION,
    interaction_recorded_payload,
)
from study_agent.state import Projection

COURSE = CourseId("cardine-recall-course")
SESSION = SessionId("cardine-recall-session")
SECOND_SESSION = SessionId("cardine-recall-later-session")


class _Scheduler:
    def __init__(self) -> None:
        self.calls: list[SchedulingRequest] = []
        self.fail_next = False

    def decide(self, request: SchedulingRequest) -> SchedulingResult:
        self.calls.append(request)
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("scheduler unavailable")
        policy_fingerprint = effective_policy_fingerprint(
            request.policy, "fake", "1", "fake", "1"
        )
        due_at = (
            request.enrollment_at
            if not request.history
            else request.history[-1].occurred_at
        )
        partial = SchedulingResult(
            due_at,
            "fake",
            "1",
            policy_fingerprint,
            "fake",
            "1",
            request.history_fingerprint,
            "0" * 64,
        )
        return replace(
            partial,
            result_fingerprint=result_fingerprint(request, partial),
        )


class _InterleavingApplication(RepositoryUiApplication):
    inject_before_capture = False

    def _captured_state(
        self, repository: LocalRepository
    ) -> tuple[Projection, TutorSnapshotV1]:
        if self.inject_before_capture:
            self.inject_before_capture = False
            with LocalRepository.open(self.repository) as external:
                sequence = external.events.read(COURSE)[-1].course_sequence
                external.events.append(
                    COURSE,
                    sequence,
                    (
                        DomainEvent(
                            EventId("external-interleaving-event"),
                            COURSE,
                            sequence + 1,
                            SESSION_INTERACTION_RECORDED,
                            SESSION_SCHEMA_VERSION,
                            Actor(PrincipalKind.HUMAN, "external-learner"),
                            external.clock.now(),
                            CorrelationId("external-interleaving"),
                            interaction_recorded_payload(
                                InteractionId("external-interleaving"),
                                InteractionKind.HUMAN,
                                "external course write",
                            ),
                            SESSION,
                        ),
                    ),
                )
        return super()._captured_state(repository)


def _context(
    key: str,
    principal: PrincipalKind = PrincipalKind.HUMAN,
    *,
    session_id: SessionId = SESSION,
) -> ExecutionContext:
    return ExecutionContext(
        principal,
        "recall-test",
        COURSE,
        CorrelationId(f"recall-{key}"),
        frozenset({"study:recall"}),
        session_id,
        idempotency_key=key,
    )


def _course_context(key: str) -> ExecutionContext:
    return ExecutionContext(
        PrincipalKind.SERVICE,
        "recall-test",
        COURSE,
        CorrelationId(f"recall-{key}"),
        frozenset({"study:recall"}),
        idempotency_key=key,
    )


def _seed(root: Path) -> ArtifactRevisionId:
    initialize_local_repository(root, EMPTY_CONFIG)
    with LocalRepository.open(root) as repository:
        service_context = _course_context("course")
        repository.course_service.create(
            CourseProfile(
                COURSE,
                "Recall",
                "en",
                learning_goals=("Recall",),
            ),
            service_context,
        )
        repository.for_course(COURSE).ingestion.ingest(
            filename="notes.md",
            content=b"The aortic valve has three cusps.",
            source_id=SourceId("aortic"),
            title="Aortic notes",
            trust_level=90,
            source_role="primary",
            context=service_context,
        )
        repository.session_service.start(
            _context("session", PrincipalKind.SERVICE)
        )
        repository.session_service.start(
            _context(
                "later-session",
                PrincipalKind.SERVICE,
                session_id=SECOND_SESSION,
            )
        )
        sequence = repository.events.read(COURSE)[-1].course_sequence
        interaction_id = InteractionId("recall-origin")
        repository.events.append(
            COURSE,
            sequence,
            (
                DomainEvent(
                    EventId("recall-origin-event"),
                    COURSE,
                    sequence + 1,
                    SESSION_INTERACTION_RECORDED,
                    SESSION_SCHEMA_VERSION,
                    Actor(PrincipalKind.HUMAN, "learner"),
                    repository.clock.now(),
                    CorrelationId("recall-origin"),
                    interaction_recorded_payload(
                        interaction_id,
                        InteractionKind.HUMAN,
                        "make card",
                    ),
                    SESSION,
                ),
            ),
        )
        source = next(
            decode_source_revision_ingested(event.payload)
            for event in repository.events.read(COURSE)
            if event.event_type == "source.revision_ingested"
        )
        chunk = source.chunks[0]
        commitment = SourceCommitment(
            chunk.source_id,
            chunk.revision_id,
            chunk.chunk_id,
            chunk.start_offset,
            chunk.end_offset,
        )
        content = StudyArtifactEnvelope(
            StudyArtifactKind.FLASHCARD,
            HybridFlashcardContent(
                RetrievalForm.DIRECT_RECALL,
                "How many cusps does the aortic valve have?",
                (AnswerBlock("Answer", "Three cusps"),),
                HybridFlashcardRole.DETAIL,
                "Bounded study rationale",
                (0,),
            ),
        )
        proposal = repository.artifact_service.record_human_revision(
            content,
            HumanAuthoredArtifactProvenance(
                PrincipalKind.HUMAN,
                interaction_id,
                (commitment,),
                (
                    ArtifactReadDependency(
                        "source_revision",
                        str(chunk.source_id),
                        str(chunk.revision_id),
                    ),
                ),
            ),
            None,
            _context("proposal"),
            repository.events.read(COURSE)[-1].course_sequence,
        )
        return proposal.pending()[0].id


def _opener(scheduler: SchedulingPolicyPort) -> Callable[..., object]:
    def open_repository(root: Path, **_: object) -> object:
        return LocalRepository.open(root, recall_scheduler=scheduler)

    return open_repository


def _command(
    request_id: str,
    expected_sequence: int,
    payload: Mapping[str, object] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "request_id": request_id,
        "expected_sequence": expected_sequence,
        "payload": {} if payload is None else dict(payload),
    }


def _items(value: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    return cast(tuple[dict[str, object], ...], value["items"])


def _accept(
    app: RepositoryUiApplication,
    revision_id: ArtifactRevisionId,
    request_id: str,
) -> dict[str, object]:
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    return app.post(
        f"/api/v1/artifacts/{revision_id}/decisions",
        _command(request_id, sequence, {"decision": "accepted"}),
    )


def test_accept_enroll_failure_retry_restart_is_exact_and_preserves_acceptance(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    revision_id = _seed(root)
    scheduler = _Scheduler()
    app = RepositoryUiApplication(
        root,
        COURSE,
        SESSION,
        repository_opener=_opener(scheduler),
    )
    _accept(app, revision_id, "accept-route")
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    scheduler.fail_next = True
    enrollment = _command("enroll", sequence)

    with pytest.raises(UiRequestError) as error:
        app.post(f"/api/v1/recall/{revision_id}/enrollments", enrollment)
    assert error.value.status_code == 503
    artifact = next(
        row
        for row in _items(app.get("/api/v1/artifacts"))
        if row["revision_id"] == str(revision_id)
    )
    assert artifact["status"] == "accepted"
    assert artifact["enrollment_status"] == "not_enrolled"

    committed = app.post(
        f"/api/v1/recall/{revision_id}/enrollments", enrollment
    )
    assert committed["status"] == "committed"
    assert len(scheduler.calls) == 2

    restarted = RepositoryUiApplication(
        root,
        COURSE,
        SESSION,
        repository_opener=_opener(scheduler),
    )
    due = restarted.get("/api/v1/recall/due")
    assert due["status"] == "ready"
    assert len(_items(due)) == 1
    assert (
        restarted.post(
            f"/api/v1/recall/{revision_id}/enrollments",
            enrollment,
        )
        == committed
    )
    assert len(scheduler.calls) == 2


@pytest.mark.parametrize("rating", tuple(RecallRating))
def test_due_dto_and_four_ratings_are_atomic_and_exact_retry_safe(
    tmp_path: Path,
    rating: RecallRating,
) -> None:
    root = tmp_path / rating.value
    revision_id = _seed(root)
    scheduler = _Scheduler()
    app = RepositoryUiApplication(
        root,
        COURSE,
        SESSION,
        repository_opener=_opener(scheduler),
    )
    accepted = _accept(app, revision_id, f"accept-{rating.value}")
    app.post(
        f"/api/v1/recall/{revision_id}/enrollments",
        _command(
            f"enroll-{rating.value}",
            cast(int, accepted["high_water_sequence"]),
        ),
    )
    row = _items(app.get("/api/v1/recall/due"))[0]
    assert set(row) == {
        "artifact_id",
        "revision_id",
        "status",
        "due_at",
        "front",
        "back",
        "provenance",
    }
    assert row["front"] == "How many cusps does the aortic valve have?"
    assert row["back"] == "Answer: Three cusps"
    encoded = str(row)
    assert "Bounded study rationale" not in encoded
    assert "raw_schedule" not in encoded

    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    command = _command(
        f"review-{rating.value}",
        sequence,
        {"rating": rating.value},
    )
    receipt = app.post(f"/api/v1/recall/{revision_id}/reviews", command)
    assert receipt["status"] == "committed"
    assert receipt["next_schedule"]
    assert cast(dict[str, object], receipt["next_schedule"])[
        "high_water_sequence"
    ] == receipt["high_water_sequence"]
    assert cast(dict[str, object], receipt["result"])[
        "high_water_sequence"
    ] == receipt["high_water_sequence"]
    assert len(scheduler.calls) == 2
    assert (
        app.post(f"/api/v1/recall/{revision_id}/reviews", command)
        == receipt
    )
    assert len(scheduler.calls) == 2

    with LocalRepository.open(root) as repository:
        recall_events = tuple(
            event
            for event in repository.events.read(COURSE)
            if event.event_type.startswith("recall.")
        )
    assert recall_events[-2].event_type == "recall.review_recorded"
    assert recall_events[-2].actor.kind is PrincipalKind.HUMAN
    assert recall_events[-1].event_type == "recall.schedule_applied"
    assert recall_events[-1].actor.kind is PrincipalKind.SERVICE


def test_review_receipt_uses_one_captured_hwm_after_external_interleaving_write(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    revision_id = _seed(root)
    scheduler = _Scheduler()
    app = _InterleavingApplication(
        root,
        COURSE,
        SESSION,
        repository_opener=_opener(scheduler),
    )
    accepted = _accept(app, revision_id, "accept-before-interleaving")
    enrolled = app.post(
        f"/api/v1/recall/{revision_id}/enrollments",
        _command(
            "enroll-before-interleaving",
            cast(int, accepted["high_water_sequence"]),
        ),
    )
    app.inject_before_capture = True

    receipt = app.post(
        f"/api/v1/recall/{revision_id}/reviews",
        _command(
            "review-with-interleaving",
            cast(int, enrolled["high_water_sequence"]),
            {"rating": RecallRating.GOOD.value},
        ),
    )

    assert cast(dict[str, object], receipt["next_schedule"])[
        "high_water_sequence"
    ] == receipt["high_water_sequence"]
    assert cast(dict[str, object], receipt["result"])[
        "high_water_sequence"
    ] == receipt["high_water_sequence"]


def test_stale_invalid_target_and_public_demo_are_no_write_paths(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    revision_id = _seed(root)
    scheduler = _Scheduler()
    app = RepositoryUiApplication(
        root,
        COURSE,
        SESSION,
        repository_opener=_opener(scheduler),
    )
    accepted = _accept(app, revision_id, "accept")
    app.post(
        f"/api/v1/recall/{revision_id}/enrollments",
        _command("enroll", cast(int, accepted["high_water_sequence"])),
    )
    sequence = cast(int, app.get("/api/v1/bootstrap")["high_water_sequence"])
    calls = len(scheduler.calls)

    with pytest.raises(UiRequestError) as invalid:
        app.post(
            f"/api/v1/recall/{revision_id}/reviews",
            _command("invalid", sequence, {"rating": "bad"}),
        )
    assert invalid.value.status_code == 400
    with pytest.raises(UiRequestError) as stale:
        app.post(
            f"/api/v1/recall/{revision_id}/reviews",
            _command("stale", sequence - 1, {"rating": "good"}),
        )
    assert stale.value.status_code == 409
    with pytest.raises(UiRequestError) as missing:
        app.post(
            "/api/v1/recall/does-not-exist/reviews",
            _command("missing", sequence, {"rating": "good"}),
        )
    assert missing.value.status_code in {400, 409}
    assert len(scheduler.calls) == calls

    public = DemoUiApplication()
    assert public.get("/api/v1/recall/due")["status"] == "unavailable"
    with pytest.raises(UiRequestError) as blocked:
        public.post(
            f"/api/v1/recall/{revision_id}/reviews",
            _command("public", 0, {"rating": "good"}),
        )
    assert blocked.value.status_code == 405


def test_later_course_session_can_review_enrolled_card(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    revision_id = _seed(root)
    scheduler = _Scheduler()
    original = RepositoryUiApplication(
        root,
        COURSE,
        SESSION,
        repository_opener=_opener(scheduler),
    )
    accepted = _accept(original, revision_id, "accept")
    original.post(
        f"/api/v1/recall/{revision_id}/enrollments",
        _command("enroll", cast(int, accepted["high_water_sequence"])),
    )
    later = RepositoryUiApplication(
        root,
        COURSE,
        SECOND_SESSION,
        repository_opener=_opener(scheduler),
    )
    sequence = cast(int, later.get("/api/v1/bootstrap")["high_water_sequence"])
    receipt = later.post(
        f"/api/v1/recall/{revision_id}/reviews",
        _command("later-review", sequence, {"rating": "good"}),
    )
    assert receipt["status"] == "committed"


def test_recall_unconfigured_and_factory_failure_are_honest(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    _seed(root)
    plain = RepositoryUiApplication(root, COURSE, SESSION)
    assert plain.get("/api/v1/recall/due")["status"] == "not_configured"

    def failing_repository(root_path: Path, **_: object) -> object:
        def factory() -> SchedulingPolicyPort:
            raise RuntimeError("optional scheduler missing")

        return LocalRepository.open(
            root_path,
            recall_scheduler_factory=factory,
        )

    failed = RepositoryUiApplication(
        root,
        COURSE,
        SESSION,
        repository_opener=failing_repository,
    )
    unavailable = failed.get("/api/v1/recall/due")
    assert unavailable["status"] == "unavailable"
    assert "install" in cast(str, unavailable["message"]).lower()


def test_recall_rejects_unknown_selected_session_before_scheduler_work(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    revision_id = _seed(root)
    scheduler = _Scheduler()
    valid = RepositoryUiApplication(
        root,
        COURSE,
        SESSION,
        repository_opener=_opener(scheduler),
    )
    accepted = _accept(valid, revision_id, "accept-before-invalid-session")
    invalid = RepositoryUiApplication(
        root,
        COURSE,
        SessionId("missing-recall-session"),
        repository_opener=_opener(scheduler),
    )

    with pytest.raises(UiRequestError) as caught:
        invalid.post(
            f"/api/v1/recall/{revision_id}/enrollments",
            _command(
                "invalid-session-enrollment",
                cast(int, accepted["high_water_sequence"]),
            ),
        )

    assert caught.value.status_code == 404
    assert scheduler.calls == []


def test_exact_concurrent_enrollment_across_ui_instances_schedules_once(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    revision_id = _seed(root)
    scheduler = _Scheduler()
    first = RepositoryUiApplication(
        root,
        COURSE,
        SESSION,
        repository_opener=_opener(scheduler),
    )
    second = RepositoryUiApplication(
        root,
        COURSE,
        SESSION,
        repository_opener=_opener(scheduler),
    )
    accepted = _accept(first, revision_id, "accept-before-concurrent-enrollment")
    command = _command(
        "same-enrollment-command",
        cast(int, accepted["high_water_sequence"]),
    )
    path = f"/api/v1/recall/{revision_id}/enrollments"

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = tuple(
            executor.map(
                lambda app: app.post(path, command),
                (first, second),
            )
        )

    assert [receipt["status"] for receipt in receipts] == [
        "committed",
        "committed",
    ]
    assert len(scheduler.calls) == 1


def test_browser_reveal_ratings_and_count_refresh_are_wired() -> None:
    javascript = (
        Path(__file__).parents[4] / "src/study_agent/demo/browser.js"
    ).read_text(encoding="utf-8")

    assert "state.revealedReviews[revisionId]" in javascript
    assert 'data-command="review"' in javascript
    for rating in RecallRating:
        assert f'["{rating.value}",' in javascript
    assert "/api/v1/recall/${encodeURIComponent" in javascript
    assert "await refreshBootstrapCounts()" in javascript
