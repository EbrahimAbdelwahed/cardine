from __future__ import annotations

import inspect
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime

import pytest

from study_agent.artifacts.contracts import (
    ArtifactDecisionRequest,
    ArtifactProposal,
    ArtifactRevisionRecord,
    ArtifactSnapshot,
    ServiceDecisionPolicyReceipt,
    ServiceDecisionPolicyRequest,
    VerifiedGeneratedArtifactBatch,
)
from study_agent.artifacts.events import decode_proposal_batch_recorded
from study_agent.artifacts.projection import register_artifact_events
from study_agent.artifacts.service import (
    ArtifactCommandError,
    ArtifactConflictError,
    ArtifactService,
    RetryableArtifactConflictError,
    service_decision_result_fingerprint,
)
from study_agent.artifacts.view import ProjectionArtifactView
from study_agent.domain import (
    Actor,
    AnswerId,
    ArtifactBatchId,
    ArtifactDecision,
    ArtifactId,
    ArtifactRevisionId,
    ArtifactRevisionStatus,
    CorrelationId,
    CourseId,
    DomainEvent,
    EventId,
    ExecutionContext,
    InteractionKind,
    InteractionRecord,
    PrincipalKind,
    RunId,
    SessionId,
    StudyArtifactKind,
)
from study_agent.domain.session import (
    AnswerRecord,
    ContinuationSummaryV1,
    StudySessionRecord,
)
from study_agent.ports import EventSequenceConflictError
from study_agent.state import EventRegistry, Projection, apply_event
from tests.unit.artifacts.test_lifecycle_events import (
    COMMITMENT,
    COURSE,
    INTERACTION,
    NOW,
    PROOF,
    RUN,
    SESSION,
    content,
    generated_provenance,
    human_provenance,
)
from tests.unit.artifacts.test_lifecycle_projection import base_state


class Clock:
    def now(self) -> datetime:
        return NOW


class MemoryEvents:
    def __init__(self) -> None:
        self.registry = EventRegistry()
        register_artifact_events(self.registry)
        self.projection = Projection(COURSE, 1, base_state())
        self.values: list[DomainEvent] = [
            DomainEvent(
                EventId("fixture-initialized"),
                COURSE,
                1,
                "fixture.initialized",
                1,
                Actor(PrincipalKind.SERVICE, "fixture"),
                NOW,
                CorrelationId("fixture"),
            )
        ]
        self.race_mode: str | None = None
        self.append_calls = 0

    def append(
        self, course_id: CourseId, expected_sequence: int, events: Sequence[DomainEvent]
    ) -> int:
        assert course_id == COURSE
        self.append_calls += 1
        batch = tuple(events)
        if self.race_mode == "fail":
            raise EventSequenceConflictError(course_id, expected_sequence, expected_sequence + 1)
        if self.race_mode == "commit_then_fail":
            for event in batch:
                self.projection = apply_event(self.projection, event, self.registry)
                self.values.append(event)
            raise EventSequenceConflictError(course_id, expected_sequence, expected_sequence + 1)
        for event in batch:
            self.projection = apply_event(self.projection, event, self.registry)
            self.values.append(event)
        return len(self.values)

    def read(
        self, course_id: CourseId, after_sequence: int = 0
    ) -> Sequence[DomainEvent]:
        assert course_id == COURSE
        return tuple(self.values[after_sequence:])


class Sessions:
    def __init__(self, interactions: tuple[InteractionRecord, ...]) -> None:
        self.values = interactions

    def interactions(
        self, course_id: CourseId, session_id: SessionId
    ) -> tuple[InteractionRecord, ...]:
        return self.values

    def list_sessions(self, course_id: CourseId) -> tuple[StudySessionRecord, ...]:
        raise AssertionError("not used")

    def get_session(
        self, course_id: CourseId, session_id: SessionId
    ) -> StudySessionRecord:
        raise AssertionError("not used")

    def answers(
        self, course_id: CourseId, session_id: SessionId
    ) -> tuple[AnswerRecord, ...]:
        raise AssertionError("not used")

    def get_answer(
        self, course_id: CourseId, session_id: SessionId, answer_id: AnswerId
    ) -> AnswerRecord:
        raise AssertionError("not used")

    def get_context(
        self, course_id: CourseId, session_id: SessionId
    ) -> ContinuationSummaryV1 | None:
        raise AssertionError("not used")


class Generated:
    def __init__(self, batch: VerifiedGeneratedArtifactBatch) -> None:
        self.batch = batch
        self.calls: list[tuple[RunId, ExecutionContext]] = []
        self.error: Exception | None = None

    def recover(
        self, run_id: RunId, context: ExecutionContext
    ) -> VerifiedGeneratedArtifactBatch:
        self.calls.append((run_id, context))
        if self.error is not None:
            raise self.error
        return self.batch


class Sources:
    def __init__(self, valid: bool = True) -> None:
        self.valid = valid
        self.calls = 0

    def contains(self, course_id: CourseId, commitment: object) -> bool:
        self.calls += 1
        return self.valid and course_id == COURSE and commitment == COMMITMENT


class Policy:
    def __init__(self, decision: ArtifactDecision = ArtifactDecision.ACCEPT) -> None:
        self.decision = decision
        self.requests: list[ServiceDecisionPolicyRequest] = []
        self.mutate: str | None = None

    def decide(self, request: ServiceDecisionPolicyRequest) -> ServiceDecisionPolicyReceipt:
        self.requests.append(request)
        supersedes = (
            request.current_accepted_revision_id
            if self.decision is ArtifactDecision.ACCEPT
            else None
        )
        receipt = ServiceDecisionPolicyReceipt(
            request.request_id,
            self.decision,
            supersedes,
            "trusted-policy",
            "1.0.0",
            "7" * 64,
            service_decision_result_fingerprint(request, self.decision, supersedes),
        )
        if self.mutate == "request":
            return replace(receipt, request_id="other-request")
        if self.mutate == "result":
            return replace(receipt, result_fingerprint="0" * 64)
        return receipt


def context(kind: PrincipalKind, key: str) -> ExecutionContext:
    return ExecutionContext(
        kind,
        "artifact-host",
        COURSE,
        CorrelationId("correlation-artifact-service"),
        frozenset(),
        SESSION,
        idempotency_key=key,
    )


def generated_batch(run_id: RunId = RUN) -> VerifiedGeneratedArtifactBatch:
    envelope = content()
    return VerifiedGeneratedArtifactBatch(
        run_id,
        COURSE,
        SESSION,
        (ArtifactProposal(0, envelope, generated_provenance(envelope, run_id=run_id)),),
        PROOF,
    )


def harness(
    *,
    batch: VerifiedGeneratedArtifactBatch | None = None,
    interactions: tuple[InteractionRecord, ...] | None = None,
    source_valid: bool = True,
) -> tuple[ArtifactService, MemoryEvents, Generated, Sources, Policy]:
    events = MemoryEvents()
    view = ProjectionArtifactView(lambda course_id: events.projection)
    generated = Generated(batch or generated_batch())
    sources = Sources(source_valid)
    policy = Policy()
    sessions = Sessions(
        interactions
        if interactions is not None
        else (InteractionRecord(INTERACTION, InteractionKind.HUMAN, NOW, "Authored card"),)
    )
    service = ArtifactService(
        events, Clock(), view, sessions, generated, sources, policy
    )
    return service, events, generated, sources, policy


def test_generated_public_signature_accepts_only_run_identity_and_exact_retry_skips_proof() -> None:
    assert tuple(inspect.signature(ArtifactService.record_generated).parameters) == (
        "self",
        "run_id",
        "context",
        "expected_sequence",
    )
    service, events, generated, _, _ = harness()
    service_context = context(PrincipalKind.SERVICE, "generated")
    first = service.record_generated(RUN, service_context, 1)
    retry = service.record_generated(RUN, service_context, 1)
    assert retry == first
    assert len(first.revisions) == 1
    assert generated.calls == [(RUN, service_context)]
    assert len(events.values) == 2

    with pytest.raises(ArtifactConflictError):
        service.record_generated(
            RunId("different-run"), context(PrincipalKind.SERVICE, "generated"), 1
        )
    assert generated.calls == [(RUN, service_context)]


def test_failed_tampered_and_stale_generated_proof_cannot_append() -> None:
    service, events, generated, _, _ = harness()
    generated.error = RuntimeError("proof unavailable")
    with pytest.raises(RuntimeError, match="proof unavailable"):
        service.record_generated(RUN, context(PrincipalKind.SERVICE, "proof"), 1)
    assert len(events.values) == 1

    other = RunId("other-run")
    service, events, generated, _, _ = harness(batch=generated_batch(other))
    with pytest.raises(ArtifactCommandError, match="another run"):
        service.record_generated(RUN, context(PrincipalKind.SERVICE, "tampered"), 1)
    assert len(events.values) == 1

    service, events, generated, _, _ = harness()
    with pytest.raises(RetryableArtifactConflictError):
        service.record_generated(RUN, context(PrincipalKind.SERVICE, "stale"), 2)
    assert generated.calls == [] and len(events.values) == 1


def test_human_authoring_requires_authority_interaction_and_exact_source_commitment() -> None:
    envelope = content(text="Human-authored card")
    provenance = human_provenance()
    service, _, generated, sources, _ = harness()
    snapshot = service.record_human_revision(
        envelope,
        provenance,
        None,
        context(PrincipalKind.HUMAN, "human"),
        1,
    )
    assert len(snapshot.revisions) == 1
    assert sources.calls == 1 and generated.calls == []

    for kind in (PrincipalKind.SERVICE, PrincipalKind.MODEL):
        denied, denied_events, _, denied_sources, _ = harness()
        with pytest.raises(ArtifactCommandError, match="HUMAN"):
            denied.record_human_revision(
                envelope, provenance, None, context(kind, f"denied-{kind.value}"), 1
            )
        assert len(denied_events.values) == 1 and denied_sources.calls == 0

    for interactions, valid in (((), True), (None, False)):
        denied, denied_events, _, _, _ = harness(
            interactions=interactions, source_valid=valid
        )
        with pytest.raises(ArtifactCommandError):
            denied.record_human_revision(
                envelope,
                provenance,
                None,
                context(PrincipalKind.HUMAN, f"invalid-{valid}"),
                1,
            )
        assert len(denied_events.values) == 1


def test_batch_bounds_duplicate_targets_and_parent_resolution_fail_closed() -> None:
    envelope = content()
    with pytest.raises(ValueError, match=r"1\.\.24"):
        VerifiedGeneratedArtifactBatch(RUN, COURSE, SESSION, (), PROOF)
    with pytest.raises(ValueError, match="contiguous"):
        VerifiedGeneratedArtifactBatch(
            RUN,
            COURSE,
            SESSION,
            (ArtifactProposal(1, envelope, generated_provenance(envelope)),),
            PROOF,
        )
    maximum = tuple(
        ArtifactProposal(ordinal, envelope, generated_provenance(envelope))
        for ordinal in range(24)
    )
    assert len(
        VerifiedGeneratedArtifactBatch(RUN, COURSE, SESSION, maximum, PROOF).proposals
    ) == 24

    invalid_parent = content(parent_ordinal=0)
    batch = VerifiedGeneratedArtifactBatch(
        RUN,
        COURSE,
        SESSION,
        (ArtifactProposal(0, invalid_parent, generated_provenance(invalid_parent)),),
        PROOF,
    )
    service, events, _, _, _ = harness(batch=batch)
    with pytest.raises(ArtifactCommandError, match="parent ordinal"):
        service.record_generated(RUN, context(PrincipalKind.SERVICE, "parent"), 1)
    assert len(events.values) == 1


def test_generated_batch_mixes_new_and_current_head_lineages_without_identity_loss() -> None:
    service, _, generated, _, _ = harness()
    first = service.record_generated(
        RUN, context(PrincipalKind.SERVICE, "first-batch"), 1
    ).revisions[0]
    later_run = RunId("run-mixed-batch")
    revised_content = content(text="Revised existing artifact")
    new_content = content(text="New artifact in mixed batch")
    generated.batch = VerifiedGeneratedArtifactBatch(
        later_run,
        COURSE,
        SESSION,
        (
            ArtifactProposal(
                0,
                revised_content,
                generated_provenance(
                    revised_content, run_id=later_run, prior=first.id
                ),
                first.artifact_id,
            ),
            ArtifactProposal(
                1,
                new_content,
                generated_provenance(new_content, run_id=later_run),
            ),
        ),
        PROOF,
    )
    snapshot = service.record_generated(
        later_run, context(PrincipalKind.SERVICE, "mixed-batch"), 2
    )
    assert len(snapshot.history(first.artifact_id)) == 2
    assert snapshot.history(first.artifact_id)[-1].artifact_id == first.artifact_id
    assert len({item.artifact_id for item in snapshot.revisions}) == 2

    with pytest.raises(ValueError, match="twice"):
        replace(
            generated.batch,
            proposals=tuple(
                replace(item, target_artifact_id=first.artifact_id)
                for item in generated.batch.proposals
            ),
        )


def test_human_and_service_decision_authority_terminal_state_and_policy_binding() -> None:
    service, events, _, _, policy = harness()
    proposed = service.record_generated(
        RUN, context(PrincipalKind.SERVICE, "proposal"), 1
    ).revisions[0]
    accepted = service.record_human_decision(
        proposed.id,
        ArtifactDecision.ACCEPT,
        None,
        context(PrincipalKind.HUMAN, "accept"),
        2,
    )
    assert accepted.revision(proposed.id).status.value == "accepted"
    with pytest.raises(ArtifactConflictError, match="terminal"):
        service.record_human_decision(
            proposed.id,
            ArtifactDecision.REJECT,
            None,
            context(PrincipalKind.HUMAN, "again"),
            3,
        )
    with pytest.raises(ArtifactCommandError, match="HUMAN"):
        service.record_human_decision(
            proposed.id,
            ArtifactDecision.REJECT,
            None,
            context(PrincipalKind.MODEL, "model"),
            3,
        )
    assert policy.requests == [] and len(events.values) == 3

    for mutation in ("request", "result"):
        service, events, _, _, policy = harness()
        proposed = service.record_generated(
            RUN, context(PrincipalKind.SERVICE, f"proposal-{mutation}"), 1
        ).revisions[0]
        policy.mutate = mutation
        with pytest.raises(ArtifactCommandError, match="policy"):
            service.apply_service_decision(
                proposed.id,
                context(PrincipalKind.SERVICE, f"policy-{mutation}"),
                2,
            )
        assert len(events.values) == 2


def _bulk_generated_batch(run_id: RunId) -> VerifiedGeneratedArtifactBatch:
    first = content(text="First bulk card")
    second = content(text="Second bulk card")
    return VerifiedGeneratedArtifactBatch(
        run_id,
        COURSE,
        SESSION,
        (
            ArtifactProposal(0, first, generated_provenance(first, run_id=run_id)),
            ArtifactProposal(1, second, generated_provenance(second, run_id=run_id)),
        ),
        PROOF,
    )


def test_human_decision_batch_appends_mixed_outcomes_once_and_retries_from_events() -> None:
    run_id = RunId("bulk-run")
    service, events, _, _, _ = harness(batch=_bulk_generated_batch(run_id))
    proposed = service.record_generated(
        run_id, context(PrincipalKind.SERVICE, "bulk-proposal"), 1
    ).revisions
    decisions = (
        ArtifactDecisionRequest(proposed[0].id, ArtifactDecision.ACCEPT, None),
        ArtifactDecisionRequest(proposed[1].id, ArtifactDecision.REJECT, None),
    )
    human = context(PrincipalKind.HUMAN, "bulk-approval")
    receipt = service.record_human_decision_batch(decisions, human, 2)
    assert receipt.start_sequence == 3 and receipt.end_sequence == 4
    assert tuple(item.decision for item in receipt.results) == (
        ArtifactDecision.ACCEPT,
        ArtifactDecision.REJECT,
    )
    assert len(events.values) == 4
    assert events.append_calls == 2

    retry = service.record_human_decisions(decisions, human, 2)
    assert retry == receipt
    assert len(events.values) == 4
    assert events.append_calls == 2
    restarted = ArtifactService(
        events,
        Clock(),
        ProjectionArtifactView(lambda course_id: events.projection),
        Sessions((InteractionRecord(INTERACTION, InteractionKind.HUMAN, NOW, "Authored card"),)),
        Generated(_bulk_generated_batch(run_id)),
        Sources(),
        Policy(),
    )
    assert restarted.record_human_decision_batch(decisions, human, 2) == receipt
    assert events.append_calls == 2
    changed_manifest = (
        decisions[0],
        ArtifactDecisionRequest(proposed[1].id, ArtifactDecision.ACCEPT, None),
    )
    with pytest.raises(ArtifactConflictError, match="another manifest"):
        service.record_human_decision_batch(changed_manifest, human, 2)
    assert len(events.values) == 4
    assert events.append_calls == 2


def test_human_decision_batch_prevalidates_last_item_and_appends_zero_events() -> None:
    run_id = RunId("bulk-invalid")
    service, events, _, _, _ = harness(batch=_bulk_generated_batch(run_id))
    proposed = service.record_generated(
        run_id, context(PrincipalKind.SERVICE, "bulk-invalid-proposal"), 1
    ).revisions
    decisions = (
        ArtifactDecisionRequest(proposed[0].id, ArtifactDecision.ACCEPT, None),
        ArtifactDecisionRequest(
            proposed[1].id, ArtifactDecision.ACCEPT, ArtifactRevisionId("wrong-predecessor")
        ),
    )
    with pytest.raises(ArtifactConflictError, match="exact current accepted predecessor"):
        service.record_human_decision_batch(
            decisions, context(PrincipalKind.HUMAN, "bulk-invalid"), 2
        )
    assert len(events.values) == 2
    assert events.append_calls == 1


def test_human_decision_batch_rejects_two_proposed_revisions_of_one_artifact() -> None:
    first_run = RunId("bulk-duplicate-first")
    service, events, generated, _, _ = harness(batch=generated_batch(first_run))
    first = service.record_generated(
        first_run, context(PrincipalKind.SERVICE, "bulk-duplicate-first"), 1
    ).revisions[0]
    second_run = RunId("bulk-duplicate-second")
    revised = content(text="Revised duplicate target")
    generated.batch = VerifiedGeneratedArtifactBatch(
        second_run,
        COURSE,
        SESSION,
        (
            ArtifactProposal(
                0,
                revised,
                generated_provenance(revised, run_id=second_run, prior=first.id),
                first.artifact_id,
            ),
        ),
        PROOF,
    )
    second_snapshot = service.record_generated(
        second_run, context(PrincipalKind.SERVICE, "bulk-duplicate-second"), 2
    )
    second = next(item for item in second_snapshot.revisions if item.id != first.id)
    decisions = (
        ArtifactDecisionRequest(first.id, ArtifactDecision.ACCEPT, None),
        ArtifactDecisionRequest(second.id, ArtifactDecision.ACCEPT, None),
    )
    with pytest.raises(ArtifactCommandError, match="one artifact twice"):
        service.record_human_decision_batch(
            decisions, context(PrincipalKind.HUMAN, "bulk-duplicate"), 3
        )
    assert len(events.values) == 3
    assert events.append_calls == 2


def test_committed_retry_and_stale_sequence_do_not_run_service_policy() -> None:
    service, events, _, _, policy = harness()
    proposed = service.record_generated(
        RUN, context(PrincipalKind.SERVICE, "proposal"), 1
    ).revisions[0]
    decided = service.apply_service_decision(
        proposed.id, context(PrincipalKind.SERVICE, "policy"), 2
    )
    retry = service.apply_service_decision(
        proposed.id, context(PrincipalKind.SERVICE, "policy"), 2
    )
    assert retry == decided
    assert len(policy.requests) == 1

    service, events, _, _, policy = harness()
    proposed = service.record_generated(
        RUN, context(PrincipalKind.SERVICE, "proposal-stale"), 1
    ).revisions[0]
    with pytest.raises(RetryableArtifactConflictError):
        service.apply_service_decision(
            proposed.id, context(PrincipalKind.SERVICE, "stale-policy"), 1
        )
    assert policy.requests == [] and len(events.values) == 2


def test_append_race_policy_request_is_stable_and_concurrent_commit_is_observed() -> None:
    service, events, _, _, policy = harness()
    proposed = service.record_generated(
        RUN, context(PrincipalKind.SERVICE, "proposal"), 1
    ).revisions[0]
    events.race_mode = "fail"
    with pytest.raises(RetryableArtifactConflictError):
        service.apply_service_decision(
            proposed.id, context(PrincipalKind.SERVICE, "race"), 2
        )
    events.race_mode = None
    service.apply_service_decision(
        proposed.id, context(PrincipalKind.SERVICE, "race"), 2
    )
    assert len(policy.requests) == 2
    assert policy.requests[0] == policy.requests[1]

    service, events, _, _, policy = harness()
    proposed = service.record_generated(
        RUN, context(PrincipalKind.SERVICE, "proposal-commit"), 1
    ).revisions[0]
    events.race_mode = "commit_then_fail"
    snapshot = service.apply_service_decision(
        proposed.id, context(PrincipalKind.SERVICE, "race-commit"), 2
    )
    assert snapshot.revision(proposed.id).status.value == "accepted"
    assert len(policy.requests) == 1


def test_identical_timestamp_history_uses_canonical_lineage_head_not_lexical_order() -> None:
    artifact_id = ArtifactId("artifact-lineage")
    first_id = ArtifactRevisionId("revision-z-first")
    second_id = ArtifactRevisionId("revision-a-second")
    head_id = ArtifactRevisionId("revision-m-head")
    envelope = content()

    def revision(
        revision_id: ArtifactRevisionId,
        prior: ArtifactRevisionId | None,
    ) -> ArtifactRevisionRecord:
        return ArtifactRevisionRecord(
            revision_id,
            artifact_id,
            ArtifactBatchId(f"batch-{revision_id}"),
            0,
            StudyArtifactKind.FLASHCARD,
            ArtifactRevisionStatus.PROPOSED,
            envelope,
            generated_provenance(envelope, prior=prior),
            prior,
            None,
            NOW,
        )

    # This is the deterministic presentation order for equal timestamps, not
    # lineage order: a-second, m-head, z-first.
    snapshot = ArtifactSnapshot(
        COURSE,
        3,
        revisions=(
            revision(second_id, first_id),
            revision(head_id, second_id),
            revision(first_id, None),
        ),
    )
    assert snapshot.history(artifact_id)[-1].id == first_id
    assert snapshot.current_head(artifact_id).id == head_id

    later_run = RunId("run-after-nonmonotonic-history")
    next_content = content(text="Revision after canonical head")
    verified = VerifiedGeneratedArtifactBatch(
        later_run,
        COURSE,
        SESSION,
        (
            ArtifactProposal(
                0,
                next_content,
                generated_provenance(next_content, run_id=later_run, prior=head_id),
                artifact_id,
            ),
        ),
        PROOF,
    )

    class StaticView:
        def get(self, course_id: CourseId) -> ArtifactSnapshot:
            return snapshot

        def command_fingerprint(self, course_id: CourseId, event_id: EventId) -> str | None:
            return None

    class CaptureEvents:
        def __init__(self) -> None:
            self.appended: tuple[DomainEvent, ...] = ()
            self.placeholder = MemoryEvents().values[0]

        def read(
            self, course_id: CourseId, after_sequence: int = 0
        ) -> Sequence[DomainEvent]:
            return (self.placeholder, self.placeholder, self.placeholder)[after_sequence:]

        def append(
            self,
            course_id: CourseId,
            expected_sequence: int,
            events: Sequence[DomainEvent],
        ) -> int:
            self.appended = tuple(events)
            return expected_sequence + len(events)

    captured = CaptureEvents()
    service = ArtifactService(
        captured,
        Clock(),
        StaticView(),
        Sessions((InteractionRecord(INTERACTION, InteractionKind.HUMAN, NOW, "x"),)),
        Generated(verified),
        Sources(),
        Policy(),
    )
    service.record_generated(
        later_run,
        context(PrincipalKind.SERVICE, "after-head"),
        3,
    )
    assert len(captured.appended) == 1
    proposal = decode_proposal_batch_recorded(captured.appended[0]).proposals[0]
    assert proposal.prior_revision_id == head_id
