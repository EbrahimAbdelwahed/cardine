from __future__ import annotations

import asyncio
import json
from typing import Any, cast

import pytest

from cardine.hosts import (
    AdvertisedCapability,
    AnswerDialogueDecision,
    HostActionIdentity,
    PendingContinuationDescriptor,
    StartCapabilityDecision,
    TutorCompletionHandoff,
    TutorCompletionHandoffState,
    TutorContinuationRecord,
    TutorHostContext,
    TutorHostContextAssembler,
    TutorHostLimits,
    TutorHostRunner,
    TutorHostRunStatus,
    completion_handoff_key,
)
from study_agent.capabilities import (
    CapabilityContinuation,
    CompletedCapabilityOutcome,
    StaleCapabilityOutcome,
    TutorCapabilityId,
)
from study_agent.domain import (
    CorrelationId,
    CourseId,
    ExecutionContext,
    PrincipalKind,
    RunId,
    SessionId,
)
from study_agent.playbooks import (
    PlaybookRunStatus,
    ToolBehaviorPin,
    VerifiedRunRecord,
    VersionPins,
)
from study_agent.ports import TutorCapabilityGatewayPort, TutorDecisionPort
from study_agent.skills import ArtifactReference, SemanticVersion

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


class _Token:
    def __init__(self, stage: str | None = None) -> None:
        self.stage = stage
        self.interrupted = False

    def is_interrupted(self) -> bool:
        return self.interrupted

    def mark(self, stage: str) -> None:
        if self.stage == stage:
            self.interrupted = True


class _HandoffStore:
    def __init__(
        self,
        token: _Token | None = None,
        *,
        interrupt_after_cas: bool = False,
        events: list[str] | None = None,
    ) -> None:
        self.values: dict[str, bytes] = {}
        self.token = token
        self.interrupt_after_cas = interrupt_after_cas
        self.cas_calls = 0
        self.fail_cas_once = False
        self.states: list[str] = []
        self.events = events

    def create(self, key: str, payload: bytes) -> bool:
        if self.events is not None:
            self.events.append("handoff.create")
        if key in self.values:
            return False
        self.values[key] = bytes(payload)
        return True

    def load(self, key: str) -> bytes:
        if key not in self.values:
            raise KeyError(key)
        return self.values[key]

    def compare_and_set(self, key: str, expected: bytes, replacement: bytes) -> bool:
        if self.events is not None:
            self.events.append("handoff.cas")
        self.cas_calls += 1
        if self.fail_cas_once:
            self.fail_cas_once = False
            return False
        if self.values.get(key) != expected:
            return False
        self.values[key] = bytes(replacement)
        self.states.append(TutorCompletionHandoff.from_bytes(replacement).state.value)
        if self.interrupt_after_cas and self.token is not None:
            self.token.interrupted = True
        return True


class _ContinuationStore:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str, str], bytes] = {}

    def create(
        self,
        course: CourseId,
        session: SessionId,
        fingerprint: str,
        payload: bytes,
    ) -> bool:
        key = (str(course), str(session), fingerprint)
        if key in self.values:
            return False
        self.values[key] = bytes(payload)
        return True

    def load(self, course: CourseId, session: SessionId, fingerprint: str) -> bytes:
        return self.values[(str(course), str(session), fingerprint)]

    def delete(self, course: CourseId, session: SessionId, fingerprint: str) -> None:
        self.values.pop((str(course), str(session), fingerprint), None)


class _Assembler:
    def __init__(self, sequence: int = 1) -> None:
        self.sequence = sequence
        self.calls = 0
        self.capability = AdvertisedCapability(
            "explain_concept",
            "explain_concept@1",
            SHA_A,
            {
                "type": "object",
                "properties": {"topic": {"type": "string"}},
                "required": ("topic",),
                "additionalProperties": False,
            },
            True,
        )

    def assemble(
        self,
        course: CourseId,
        session: SessionId,
        *,
        pending_continuation: Any = None,
    ) -> TutorHostContext:
        self.calls += 1
        return TutorHostContext(
            str(course),
            str(session),
            self.sequence,
            self.sequence,
            {"visible": "redacted"},
            {"evidence": "redacted"},
            (self.capability,),
            pending_continuation,
        )


class _DecisionPort:
    def __init__(self, decision: object, *, explode: bool = False) -> None:
        self.decision = decision
        self.explode = explode
        self.calls = 0

    async def decide(self, context: TutorHostContext, interruption: _Token) -> object:
        del context, interruption
        self.calls += 1
        if self.explode:
            raise AssertionError("decision must be skipped during handoff preflight")
        if isinstance(self.decision, tuple):
            return self.decision[self.calls - 1]
        return self.decision


class _Authority:
    def create_context(
        self,
        course: CourseId,
        session: SessionId,
        capability_id: TutorCapabilityId,
        action_identity: HostActionIdentity,
    ) -> ExecutionContext:
        del capability_id
        return ExecutionContext(
            PrincipalKind.SERVICE,
            "host",
            course,
            CorrelationId("correlation"),
            frozenset({"study:explain"}),
            session,
            idempotency_key=action_identity.value,
        )


class _Identity:
    def issue(
        self,
        host_turn_id: str,
        context_fingerprint: str,
        decision_fingerprint: str,
        decision_generation: int,
    ) -> HostActionIdentity:
        del context_fingerprint, decision_fingerprint
        return HostActionIdentity(f"{host_turn_id}:action:{decision_generation}")


def _continuation() -> CapabilityContinuation:
    version = SemanticVersion.parse("1.0.0")
    pins = VersionPins(
        ArtifactReference("skill", version),
        ArtifactReference("playbook", version),
        ArtifactReference("prompt", version),
        (ToolBehaviorPin("tool", version),),
        ArtifactReference("model", version),
        ArtifactReference("state", version),
    )
    return CapabilityContinuation(
        RunId("run-1"), TutorCapabilityId.EXPLAIN_CONCEPT, version,
        SHA_A, SHA_B, SHA_C, "d" * 64, "e" * 64,
        "confirm", 1, {"topic": "valves"}, pins, (),
    )


def _completed() -> CompletedCapabilityOutcome:
    continuation = _continuation()
    run = VerifiedRunRecord(
        continuation.run_id,
        continuation.definition_fingerprint,
        continuation.inputs,
        continuation.pins,
        continuation.read_dependencies,
        {"answer": "done"},
        (),
        PlaybookRunStatus.COMPLETED,
    )
    return CompletedCapabilityOutcome(run, {"answer": "done"})


class _Gateway:
    def __init__(
        self,
        outcome: object | None = None,
        *,
        token: _Token | None = None,
        explode: bool = False,
        events: list[str] | None = None,
    ) -> None:
        self.outcome = _completed() if outcome is None else outcome
        self.token = token
        self.explode = explode
        self.starts = 0
        self.resumes = 0
        self.idempotency_keys: list[str | None] = []
        self.events = events

    def discover(self) -> tuple[object, ...]:
        return ()

    async def start(
        self,
        capability_id: TutorCapabilityId,
        inputs: object,
        context: ExecutionContext,
    ) -> object:
        del capability_id, inputs
        self.starts += 1
        if self.events is not None:
            self.events.append("gateway.start")
        self.idempotency_keys.append(context.idempotency_key)
        if self.explode:
            raise AssertionError("gateway must be skipped during completed preflight")
        if self.token is not None:
            self.token.mark("after_gateway")
        return self.outcome

    async def resume(
        self,
        continuation: object,
        response: object,
        context: ExecutionContext,
    ) -> object:
        del continuation, response
        self.resumes += 1
        if self.events is not None:
            self.events.append("gateway.resume")
        self.idempotency_keys.append(context.idempotency_key)
        if self.explode:
            raise AssertionError("gateway must be skipped during completed preflight")
        return self.outcome


class _StaleOnceGateway(_Gateway):
    async def start(
        self,
        capability_id: TutorCapabilityId,
        inputs: object,
        context: ExecutionContext,
    ) -> object:
        if self.starts == 0:
            self.starts += 1
            self.idempotency_keys.append(context.idempotency_key)
            return StaleCapabilityOutcome(RunId("run-1"), "stale")
        return await super().start(capability_id, inputs, context)


def _runner(
    decision: object,
    gateway: _Gateway,
    store: _HandoffStore,
    assembler: _Assembler | None = None,
    continuation_store: _ContinuationStore | None = None,
    *,
    explode_decision: bool = False,
) -> TutorHostRunner:
    return TutorHostRunner(
        cast(TutorDecisionPort, _DecisionPort(decision, explode=explode_decision)),
        None,
        None,
        cast(TutorCapabilityGatewayPort, gateway),
        _Authority(),
        _Identity(),
        continuation_store or _ContinuationStore(),
        TutorHostLimits(2, 1, 1, 128),
        context_assembler=cast(TutorHostContextAssembler, assembler or _Assembler()),
        completion_handoff_store=store,
    )


def _run(
    runner: TutorHostRunner,
    token: _Token | None = None,
    *,
    turn: str = "turn-1",
    **kwargs: Any,
) -> Any:
    return asyncio.run(
        runner.run(
            CourseId("course"), SessionId("session"), turn, token or _Token(), **kwargs
        )
    )


def _record(store: _HandoffStore, turn: str = "turn-1") -> TutorCompletionHandoff:
    key = completion_handoff_key(CourseId("course"), SessionId("session"), turn)
    return TutorCompletionHandoff.from_bytes(store.values[key])


def _rewrite_record(store: _HandoffStore, mutate: Any, *, turn: str = "turn-1") -> None:
    key = completion_handoff_key(CourseId("course"), SessionId("session"), turn)
    raw = json.loads(store.values[key].decode())
    mutate(raw)
    store.values[key] = json.dumps(
        raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def test_completed_preflight_skips_decision_and_gateway_and_replays_closed_reference() -> None:
    assembler = _Assembler()
    decision = StartCapabilityDecision("explain_concept", {"topic": "valves"})
    events: list[str] = []
    store = _HandoffStore(events=events)
    gateway = _Gateway(events=events)
    first = _run(_runner(decision, gateway, store, assembler), turn="turn-1")
    assert first.status is TutorHostRunStatus.COMPLETED
    assert first.completion_reference is not None
    handoff = _record(store)
    assert handoff.state is TutorCompletionHandoffState.COMPLETED
    assert events[:3] == ["handoff.create", "gateway.start", "handoff.cas"]

    second_gateway = _Gateway(explode=True)
    second = _run(
        _runner(decision, second_gateway, store, assembler, explode_decision=True),
        turn="turn-1",
    )
    assert second.status is TutorHostRunStatus.COMPLETED
    assert second.completion_reference == first.completion_reference
    assert second.learner_text is None
    assert second.completed_output is None
    assert second_gateway.starts == 0


def test_issued_retry_replays_exact_gateway_without_decision_after_gateway_crash() -> None:
    decision = StartCapabilityDecision("explain_concept", {"topic": "valves"})
    events: list[str] = []
    store = _HandoffStore(events=events)
    first_gateway = _Gateway(token=_Token("after_gateway"), events=events)
    interrupted_token = first_gateway.token
    assert interrupted_token is not None
    first = _run(_runner(decision, first_gateway, store), interrupted_token)
    assert first.status is TutorHostRunStatus.INTERRUPTED
    assert first.retry_receipt is not None
    issued = _record(store)
    assert issued.state is TutorCompletionHandoffState.ISSUED
    assert events[:2] == ["handoff.create", "gateway.start"]

    retry_gateway = _Gateway()
    second = _run(_runner(decision, retry_gateway, store, explode_decision=True), turn="turn-1")
    assert second.status is TutorHostRunStatus.COMPLETED
    assert retry_gateway.starts == 1
    assert first_gateway.idempotency_keys == retry_gateway.idempotency_keys
    assert _record(store).state is TutorCompletionHandoffState.COMPLETED


def test_stale_gateway_outcome_commits_stale_record_before_new_generation() -> None:
    decision = StartCapabilityDecision("explain_concept", {"topic": "valves"})
    store = _HandoffStore()
    gateway = _StaleOnceGateway()
    result = _run(_runner((decision, decision), gateway, store))
    assert result.status is TutorHostRunStatus.COMPLETED
    assert gateway.starts == 2
    assert TutorCompletionHandoffState.STALE.value in store.states
    assert _record(store).state is TutorCompletionHandoffState.COMPLETED


def test_crash_after_completion_cas_before_cleanup_is_recoverable_without_duplicate_gateway(
) -> None:
    continuation = _continuation()
    descriptor = PendingContinuationDescriptor(
        continuation.fingerprint,
        "explain_concept@1",
        continuation.dialogue_step_id,
        "Confirm?",
        {"type": "boolean"},
    )
    continuation_store = _ContinuationStore()
    execution = _Authority().create_context(
        CourseId("course"),
        SessionId("session"),
        TutorCapabilityId.EXPLAIN_CONCEPT,
        HostActionIdentity("seed"),
    )
    continuation_store.create(
        CourseId("course"),
        SessionId("session"),
        descriptor.fingerprint,
        TutorContinuationRecord(continuation, execution, descriptor).to_bytes(),
    )

    token = _Token()
    store = _HandoffStore(token, interrupt_after_cas=True)
    decision = AnswerDialogueDecision(descriptor.fingerprint, True)
    gateway = _Gateway()
    first = _run(
        _runner(decision, gateway, store, _Assembler(), continuation_store),
        token,
        pending_fingerprint=descriptor.fingerprint,
    )
    assert first.status is TutorHostRunStatus.INTERRUPTED
    assert gateway.resumes == 1
    assert _record(store).state is TutorCompletionHandoffState.COMPLETED
    assert continuation_store.values

    retry_gateway = _Gateway(explode=True)
    second = _run(
        _runner(
            decision,
            retry_gateway,
            store,
            _Assembler(),
            continuation_store,
            explode_decision=True,
        ),
        pending_fingerprint=descriptor.fingerprint,
    )
    assert second.status is TutorHostRunStatus.COMPLETED
    assert retry_gateway.resumes == 0


def test_sequence_advance_on_issued_retry_fails_closed_with_zero_gateway_and_presentation() -> None:
    decision = StartCapabilityDecision("explain_concept", {"topic": "valves"})
    store = _HandoffStore()
    token = _Token("after_gateway")
    first_gateway = _Gateway(token=token)
    first = _run(_runner(decision, first_gateway, store), token)
    assert first.status is TutorHostRunStatus.INTERRUPTED

    changed = _Assembler(sequence=2)
    retry_gateway = _Gateway(explode=True)
    second = _run(
        _runner(decision, retry_gateway, store, changed, explode_decision=True),
        turn="turn-1",
    )
    assert second.status is TutorHostRunStatus.FAILED
    assert second.retry_receipt is not None
    assert second.learner_text is None
    assert second.presentation_receipt is None
    assert retry_gateway.starts == 0


def test_namespaced_handoff_slots_isolate_same_turn_across_scope() -> None:
    decision = StartCapabilityDecision("explain_concept", {"topic": "valves"})
    store = _HandoffStore()
    first = _run(_runner(decision, _Gateway(), store), turn="shared")
    assert first.status is TutorHostRunStatus.COMPLETED
    other_runner = TutorHostRunner(
        cast(TutorDecisionPort, _DecisionPort(decision)),
        None,
        None,
        cast(TutorCapabilityGatewayPort, _Gateway()),
        _Authority(),
        _Identity(),
        _ContinuationStore(),
        TutorHostLimits(1, 1, 1, 128),
        context_assembler=cast(TutorHostContextAssembler, _Assembler()),
        completion_handoff_store=store,
    )
    other = asyncio.run(
        other_runner.run(
            CourseId("other-course"), SessionId("session"), "shared", _Token()
        )
    )
    assert other.status is TutorHostRunStatus.COMPLETED
    assert len(store.values) == 2


@pytest.mark.parametrize("tamper", ("scope", "receipt", "manifest", "output"))
def test_tampered_handoff_records_fail_closed_without_gateway_or_public_output(tamper: str) -> None:
    decision = StartCapabilityDecision("explain_concept", {"topic": "valves"})
    store = _HandoffStore()
    first = _run(_runner(decision, _Gateway(), store))
    assert first.status is TutorHostRunStatus.COMPLETED

    def mutate(raw: dict[str, Any]) -> None:
        if tamper == "scope":
            raw["course_id"] = "other-course"
        elif tamper == "receipt":
            raw["retry_receipt"]["attempt"] = 999
        elif tamper == "manifest":
            raw["manifest_fingerprint"] = SHA_B
            raw["completion_reference"]["manifest_fingerprint"] = SHA_B
        else:
            raw["completion_reference"]["output_fingerprint"] = SHA_C

    _rewrite_record(store, mutate)
    gateway = _Gateway(explode=True)
    result = _run(_runner(decision, gateway, store, explode_decision=True))
    assert result.status is TutorHostRunStatus.FAILED
    assert result.learner_text is None
    assert result.completed_output is None
    assert gateway.starts == 0


def test_cas_collision_does_not_publish_completion_or_presentation() -> None:
    decision = StartCapabilityDecision("explain_concept", {"topic": "valves"})
    store = _HandoffStore()
    store.fail_cas_once = True
    result = _run(_runner(decision, _Gateway(), store))
    assert result.status is TutorHostRunStatus.FAILED
    assert result.completed_output is None
    assert result.presentation_receipt is None
