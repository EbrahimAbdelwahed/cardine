from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from study_agent.capabilities import CapabilityContinuation, TutorCapabilityId
from study_agent.domain import (
    CorrelationId,
    CourseId,
    ExecutionContext,
    PrincipalKind,
    RunId,
    SessionId,
)
from study_agent.hosts import (
    AssistantMessageDecision,
    HostActionIdentity,
    HostRetryReceipt,
    ScriptedDecision,
    ScriptedDecisionError,
    ScriptedTutorDecisionPort,
    TutorContinuationRecord,
    TutorHostContext,
    TutorHostLimits,
    TutorHostRunner,
    TutorHostRunResult,
    TutorHostRunStatus,
    decision_fingerprint,
)
from study_agent.hosts.contracts import AdvertisedCapability, PendingContinuationDescriptor
from study_agent.playbooks import ToolBehaviorPin, VersionPins
from study_agent.skills import ArtifactReference, SemanticVersion

SHA_A = "a" * 64


def _context() -> TutorHostContext:
    return TutorHostContext(
        "course",
        "session",
        1,
        1,
        {},
        {},
        (
            AdvertisedCapability(
                "explain_concept",
                "explain_concept@1.0.0",
                SHA_A,
                {
                    "type": "object",
                    "properties": {},
                    "required": (),
                    "additionalProperties": False,
                },
                False,
            ),
        ),
    )


class _Token:
    def __init__(self) -> None:
        self.interrupted = False

    def is_interrupted(self) -> bool:
        return self.interrupted


class _Assembler:
    def __init__(self, context: TutorHostContext) -> None:
        self.context = context

    def assemble(self, *args: object, **kwargs: object) -> TutorHostContext:
        del args, kwargs
        return self.context


class _Gateway:
    def discover(self) -> tuple[object, ...]:
        return ()

    async def start(self, *args: object) -> object:
        raise AssertionError("message-only turn must not invoke gateway")

    async def resume(self, *args: object) -> object:
        raise AssertionError("message-only turn must not invoke gateway")


class _Authority:
    def create_context(self, *args: object) -> object:
        raise AssertionError("message-only turn must not create authority")


class _Identity:
    def issue(self, *args: object) -> object:
        raise AssertionError("message-only turn must not issue identity")


class _Store:
    def create(self, *args: object) -> bool:
        raise AssertionError("message-only turn must not write continuation")

    def load(self, *args: object) -> object:
        raise AssertionError("message-only turn must not load continuation")

    def delete(self, *args: object) -> None:
        raise AssertionError("message-only turn must not delete continuation")


def _runner(port: object) -> TutorHostRunner:
    return TutorHostRunner(
        port,  # type: ignore[arg-type]
        None,
        None,
        _Gateway(),  # type: ignore[arg-type]
        _Authority(),  # type: ignore[arg-type]
        _Identity(),  # type: ignore[arg-type]
        _Store(),  # type: ignore[arg-type]
        TutorHostLimits(2, 2, 1, 100),
        context_assembler=_Assembler(_context()),  # type: ignore[arg-type]
    )


def test_limits_and_result_matrix_are_strict() -> None:
    with pytest.raises(ValueError):
        TutorHostLimits(0, 1, 1, 1)
    assert TutorHostRunResult(TutorHostRunStatus.COMPLETED).output is None
    with pytest.raises(ValueError):
        TutorHostRunResult(TutorHostRunStatus.COMPLETED, learner_text="leak")
    with pytest.raises(ValueError):
        TutorHostRunResult(
            TutorHostRunStatus.FAILED,
            completed_output={"leak": True},
        )
    receipt = HostRetryReceipt("turn-1", SHA_A, SHA_A, SHA_A, 1, 1)
    result = TutorHostRunResult(
        TutorHostRunStatus.ASSISTANT_MESSAGE,
        receipt,
        learner_text="safe",
    )
    assert result.text == "safe"
    assert result.output is None


def test_result_matrix_rejects_cross_status_fields() -> None:
    receipt = HostRetryReceipt("turn-1", SHA_A, SHA_A, SHA_A, 1, 1)
    pending = PendingContinuationDescriptor(
        SHA_A, "explain_concept@1", "confirm", "Confirm?", {"type": "boolean"}
    )
    invalid: tuple[tuple[TutorHostRunStatus, dict[str, object]], ...] = (
        (TutorHostRunStatus.COMPLETED, {"retry_receipt": receipt}),
        (TutorHostRunStatus.SUSPENDED, {}),
        (TutorHostRunStatus.SUSPENDED, {"pending_continuation": pending, "learner_text": "leak"}),
        (
            TutorHostRunStatus.NEEDS_LEARNER_INPUT,
            {"pending_continuation": pending, "learner_text": "?"},
        ),
        (TutorHostRunStatus.ASSISTANT_MESSAGE, {"learner_text": "ok", "completed_output": {}}),
        (TutorHostRunStatus.INTERRUPTED, {"learner_text": "leak"}),
        (TutorHostRunStatus.TERMINATED, {"pending_continuation": pending}),
    )
    for status, fields in invalid:
        with pytest.raises(ValueError):
            TutorHostRunResult(status, **fields)  # type: ignore[arg-type]


def test_scripted_adapter_consumes_exact_context_and_fails_closed() -> None:
    context = _context()
    decision = AssistantMessageDecision("hello")
    adapter = ScriptedTutorDecisionPort((ScriptedDecision(context.fingerprint, decision),))
    token = _Token()
    assert asyncio.run(adapter.decide(context, token)) == decision
    with pytest.raises(ScriptedDecisionError, match="exhausted"):
        asyncio.run(adapter.decide(context, token))

    wrong = ScriptedTutorDecisionPort(((decision_fingerprint(decision), decision),))
    with pytest.raises(ScriptedDecisionError, match="mismatch"):
        asyncio.run(wrong.decide(context, token))


def test_runner_returns_bounded_assistant_message_without_gateway_effect() -> None:
    context = _context()
    decision = AssistantMessageDecision("hello")
    adapter = ScriptedTutorDecisionPort(((context.fingerprint, decision),))
    result = asyncio.run(
        _runner(adapter).run(CourseId("course"), SessionId("session"), "turn-1", _Token())
    )
    assert result.status is TutorHostRunStatus.ASSISTANT_MESSAGE
    assert result.learner_text == "hello"
    assert result.presentation_receipt is not None
    assert result.presentation_receipt.host_turn_id == "turn-1"
    assert result.presentation_receipt.kind.value == "assistant_message"
    assert result.presentation_receipt.content == "hello"
    assert result.presentation_receipt.observed_host_context_sequence == 1
    assert result.presentation_receipt.host_context_fingerprint == context.fingerprint


def test_continuation_record_codec_is_canonical_and_reconstructs_authority() -> None:
    version = SemanticVersion.parse("1.0.0")
    pins = VersionPins(
        ArtifactReference("skill", version),
        ArtifactReference("playbook", version),
        ArtifactReference("prompt", version),
        (ToolBehaviorPin("tool", version),),
        ArtifactReference("model", version),
        ArtifactReference("state", version),
    )
    continuation = CapabilityContinuation(
        RunId("run-1"),
        TutorCapabilityId.EXPLAIN_CONCEPT,
        version,
        SHA_A,
        "b" * 64,
        "c" * 64,
        "d" * 64,
        "e" * 64,
        "confirm",
        1,
        {"topic": "valves"},
        pins,
        (),
    )
    descriptor = PendingContinuationDescriptor(
        continuation.fingerprint,
        "explain_concept@1",
        "confirm",
        "Confirm?",
        {"type": "boolean"},
    )
    context = ExecutionContext(
        PrincipalKind.SERVICE,
        "host",
        CourseId("course"),
        CorrelationId("corr"),
        frozenset({"study:explain"}),
        SessionId("session"),
        idempotency_key="action",
    )
    record = TutorContinuationRecord(continuation, context, descriptor)
    assert TutorContinuationRecord.from_bytes(record.to_bytes()) == record
    with pytest.raises(ValueError, match="canonical"):
        TutorContinuationRecord.from_bytes(record.to_bytes() + b" ")
    import json

    raw = json.loads(record.to_bytes())
    raw["extra"] = True
    with pytest.raises(ValueError, match="invalid field set"):
        TutorContinuationRecord.from_bytes(
            json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
        )

    raw = json.loads(record.to_bytes())
    raw["execution_context"]["requested_capabilities"] = ["z", "a"]
    with pytest.raises(ValueError, match="canonically ordered"):
        TutorContinuationRecord.from_bytes(
            json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
        )
    raw["execution_context"]["requested_capabilities"] = ["a", "a"]
    with pytest.raises(ValueError, match="unique"):
        TutorContinuationRecord.from_bytes(
            json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
        )


def test_retry_receipt_wrong_turn_is_rejected_before_identity() -> None:
    class CountingIdentity:
        calls = 0

        def issue(self, *args: object) -> object:
            self.calls += 1
            return HostActionIdentity("generated")

    context = _context()
    decision = AssistantMessageDecision("hello")
    identity = CountingIdentity()
    runner = _runner(ScriptedTutorDecisionPort(()))
    runner._action_identity = identity  # type: ignore[assignment]
    receipt = HostRetryReceipt(
        "other-turn",
        SHA_A,
        context.fingerprint,
        decision_fingerprint(decision),
        1,
        1,
    )
    assert runner._issue_action("turn-1", context, decision, 1, receipt, _Token()) is None
    assert identity.calls == 0


@pytest.mark.parametrize(
    "field",
    (
        "context_fingerprint",
        "action_fingerprint",
        "action_identity_fingerprint",
        "decision_generation",
        "attempt",
    ),
)
def test_retry_receipt_mismatch_never_reaches_gateway(field: str) -> None:
    context = _context()
    decision = AssistantMessageDecision("hello")
    identity = HostActionIdentity("expected")
    receipt = HostRetryReceipt(
        "turn-1",
        identity.fingerprint,
        context.fingerprint,
        decision_fingerprint(decision),
        1,
        1,
    )
    if field == "context_fingerprint":
        mismatch = replace(receipt, context_fingerprint="b" * 64)
    elif field == "action_fingerprint":
        mismatch = replace(receipt, action_fingerprint="c" * 64)
    elif field == "action_identity_fingerprint":
        mismatch = replace(receipt, action_identity_fingerprint="d" * 64)
    elif field == "decision_generation":
        mismatch = replace(receipt, decision_generation=2)
    else:
        mismatch = replace(receipt, attempt=2)

    class CountingIdentity:
        calls = 0

        def issue(self, *args: object) -> HostActionIdentity:
            self.calls += 1
            return identity

    runner = _runner(ScriptedTutorDecisionPort(()))
    counter = CountingIdentity()
    runner._action_identity = counter
    result = runner._issue_action("turn-1", context, decision, 1, mismatch, _Token())
    if field == "attempt":
        assert result is not None
        assert result[1].attempt == 3
        assert counter.calls == 1
    else:
        assert result is None
        assert counter.calls == (1 if field == "action_identity_fingerprint" else 0)


def test_decision_retry_budget_counts_exact_provider_calls() -> None:
    class RetryThenMessage:
        def __init__(self) -> None:
            self.calls = 0

        async def decide(self, context: TutorHostContext, interruption: _Token) -> object:
            del context, interruption
            self.calls += 1
            if self.calls == 1:
                from study_agent.hosts import RetryableTutorDecisionError

                raise RetryableTutorDecisionError("transient")
            return AssistantMessageDecision("hello")

    adapter = RetryThenMessage()
    runner = _runner(adapter)
    result = asyncio.run(runner.run(CourseId("course"), SessionId("session"), "turn-1", _Token()))
    assert result.status is TutorHostRunStatus.ASSISTANT_MESSAGE
    assert adapter.calls == 2

    exhausted = RetryThenMessage()
    runner = TutorHostRunner(
        exhausted,  # type: ignore[arg-type]
        None,
        None,
        _Gateway(),  # type: ignore[arg-type]
        _Authority(),  # type: ignore[arg-type]
        _Identity(),  # type: ignore[arg-type]
        _Store(),  # type: ignore[arg-type]
        TutorHostLimits(2, 1, 1, 100),
        context_assembler=_Assembler(_context()),  # type: ignore[arg-type]
    )
    result = asyncio.run(runner.run(CourseId("course"), SessionId("session"), "turn-1", _Token()))
    assert result.status is TutorHostRunStatus.BUDGET_EXHAUSTED
    assert exhausted.calls == 1
