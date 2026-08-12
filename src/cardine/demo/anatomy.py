"""Installed offline anatomy demo for the bounded adaptive tutor host.

Run with ``cardine-demo``. The
recorded Responses exchange and the scripted adapter both cross the same
``TutorHostRunner`` and gateway boundary; no SDK, key, network, or product UI
is required.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from importlib import resources
from typing import cast

from cardine.adapters.host import (
    OpenAIResponsesResource,
    OpenAIResponsesTutorConfig,
    OpenAIResponsesTutorDecisionPort,
)
from study_agent.adapters.memory import MemoryHostFileIdentity, MemoryHostFileSnapshotStore
from study_agent.capabilities import (
    EXPLAIN_CONCEPT_MANIFEST,
    CapabilityContinuation,
    CapabilityManifest,
    CompletedCapabilityOutcome,
    SuspendedCapabilityOutcome,
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
from study_agent.domain._validation import JsonObject
from cardine.hosts import (
    AdvertisedCapability,
    AnswerDialogueDecision,
    HostActionIdentity,
    HostFileDescriptor,
    HostFileRegistry,
    PendingContinuationDescriptor,
    ScriptedDecision,
    ScriptedTutorDecisionPort,
    StartCapabilityDecision,
    TutorDecision,
    TutorHostContext,
    TutorHostLimits,
    TutorHostRunner,
    TutorHostRunResult,
    TutorHostRunStatus,
    decision_to_bytes,
)
from study_agent.playbooks import (
    PlaybookRunStatus,
    ToolBehaviorPin,
    VerifiedRunRecord,
    VersionPins,
)
from study_agent.ports.source_input import SourceSnapshot
from study_agent.ports.tutor_host import TutorDecisionPort
from study_agent.skills import ArtifactReference, SemanticVersion

COURSE_ID = CourseId("demo-course")
SESSION_ID = SessionId("demo-session")
_SHA = "a" * 64
_DEFAULT_LEARNER_ENTRY = "I have ten minutes. Help me understand heart valves."
_FIXTURE_TITLE = "Valvole cardiache — materiale di esempio"
_FIXTURE_EVIDENCE = (
    "The aortic valve sits between the left ventricle and the aorta.",
    "The pulmonary valve sits between the right ventricle and the pulmonary trunk.",
)


def _fixture_content() -> bytes:
    return (
        resources.files("cardine.demo")
        .joinpath("fixtures/heart-valves.md")
        .read_bytes()
    )


class _Token:
    def is_interrupted(self) -> bool:
        return False


class _MemorySource:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def snapshot(self, relative_path: str) -> SourceSnapshot:
        return SourceSnapshot(
            relative_path,
            self.content,
            sha256(self.content).hexdigest(),
            len(self.content),
        )

    def snapshots(self, relative_paths: Sequence[str]) -> tuple[SourceSnapshot, ...]:
        return tuple(self.snapshot(path) for path in relative_paths)


class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 18, 12, tzinfo=UTC)


class _DemoAssembler:
    def __init__(self, descriptor: HostFileDescriptor) -> None:
        self.descriptor = descriptor
        self.learner_entry = "I have ten minutes and need help with valves."
        self.evidence_sequence = 1

    def assemble(
        self,
        course_id: CourseId,
        session_id: SessionId,
        *,
        pending_continuation: PendingContinuationDescriptor | None = None,
        host_files: tuple[HostFileDescriptor, ...] = (),
    ) -> TutorHostContext:
        del course_id, session_id
        advertised = (
            AdvertisedCapability(
                EXPLAIN_CONCEPT_MANIFEST.id.value,
                EXPLAIN_CONCEPT_MANIFEST.identity,
                EXPLAIN_CONCEPT_MANIFEST.fingerprint,
                EXPLAIN_CONCEPT_MANIFEST.input_schema,
                EXPLAIN_CONCEPT_MANIFEST.supports_suspension,
            ),
        )
        return TutorHostContext(
            str(COURSE_ID),
            str(SESSION_ID),
            self.evidence_sequence,
            self.evidence_sequence,
            {
                "learner_entry": self.learner_entry,
                "evidence_revision": self.evidence_sequence,
                "source_grounding": {
                    "title": _FIXTURE_TITLE,
                    "evidence": _FIXTURE_EVIDENCE,
                },
            },
            {"reviewed_topics": ("heart valves",)},
            advertised,
            pending_continuation,
            host_files or (self.descriptor,),
        )


class _DemoAuthority:
    def create_context(
        self,
        course_id: CourseId,
        session_id: SessionId,
        capability_id: TutorCapabilityId,
        action_identity: HostActionIdentity,
    ) -> ExecutionContext:
        del capability_id
        return ExecutionContext(
            PrincipalKind.SERVICE,
            "reference-host",
            course_id,
            CorrelationId("reference-correlation"),
            frozenset({"course:read"}),
            session_id,
            idempotency_key=action_identity.value,
        )


class _DemoIdentity:
    def issue(
        self,
        host_turn_id: str,
        context_fingerprint: str,
        decision_fingerprint: str,
        decision_generation: int,
    ) -> HostActionIdentity:
        return HostActionIdentity(
            f"{host_turn_id}:{context_fingerprint}:{decision_fingerprint}:{decision_generation}"
        )


class _DemoStore:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str, str], bytes] = {}

    def create(
        self,
        course_id: CourseId,
        session_id: SessionId,
        fingerprint: str,
        payload: bytes,
    ) -> bool:
        key = (str(course_id), str(session_id), fingerprint)
        previous = self.values.get(key)
        if previous is not None:
            return previous == payload
        self.values[key] = payload
        return True

    def load(self, course_id: CourseId, session_id: SessionId, fingerprint: str) -> bytes:
        return self.values[(str(course_id), str(session_id), fingerprint)]

    def delete(self, course_id: CourseId, session_id: SessionId, fingerprint: str) -> None:
        self.values.pop((str(course_id), str(session_id), fingerprint), None)


def _inputs(query: str) -> JsonObject:
    return {
        "query": query,
        "target": None,
        "language": "en",
        "learner_goal": "understand the mechanism",
        "continuation_summary_json": None,
    }


def _pins() -> VersionPins:
    version = SemanticVersion.parse("1.0.0")
    return VersionPins(
        ArtifactReference("skill", version),
        ArtifactReference("playbook", version),
        ArtifactReference("prompt", version),
        (ToolBehaviorPin("tool", version),),
        ArtifactReference("model", version),
        ArtifactReference("state", version),
    )


def _continuation(inputs: JsonObject) -> CapabilityContinuation:
    return CapabilityContinuation(
        RunId("demo-run-clarification"),
        TutorCapabilityId.EXPLAIN_CONCEPT,
        SemanticVersion.parse("1.0.0"),
        EXPLAIN_CONCEPT_MANIFEST.fingerprint,
        _SHA,
        "b" * 64,
        "c" * 64,
        "d" * 64,
        "clarify",
        1,
        inputs,
        _pins(),
        (),
    )


def _completed(inputs: JsonObject) -> CompletedCapabilityOutcome:
    continuation = _continuation(inputs)
    run = VerifiedRunRecord(
        continuation.run_id,
        continuation.definition_fingerprint,
        continuation.inputs,
        continuation.pins,
        continuation.read_dependencies,
        {"status": "answered"},
        (),
        PlaybookRunStatus.COMPLETED,
    )
    return CompletedCapabilityOutcome(run, {"status": "answered", "topic": inputs["query"]})


class _DemoGateway:
    def __init__(self) -> None:
        self.events: list[str] = []

    def discover(self) -> tuple[CapabilityManifest, ...]:
        return (EXPLAIN_CONCEPT_MANIFEST,)

    async def start(
        self,
        capability_id: TutorCapabilityId,
        inputs: JsonObject,
        context: ExecutionContext,
    ) -> CompletedCapabilityOutcome | SuspendedCapabilityOutcome:
        assert capability_id is TutorCapabilityId.EXPLAIN_CONCEPT
        assert context.course_id == COURSE_ID
        if inputs["query"] == "needs clarification":
            continuation = _continuation(inputs)
            self.events.append("start:suspended")
            return SuspendedCapabilityOutcome(
                continuation.run_id,
                "Which valve should we focus on?",
                continuation,
                {"type": "string", "minLength": 1},
            )
        self.events.append("start:completed")
        return _completed(inputs)

    async def resume(
        self,
        continuation: CapabilityContinuation,
        response: object,
        context: ExecutionContext,
    ) -> CompletedCapabilityOutcome:
        assert context.course_id == COURSE_ID
        assert isinstance(response, str)
        self.events.append("resume:completed")
        return _completed(continuation.inputs)


class _RecordedResponses(OpenAIResponsesResource):
    def __init__(self, decisions: Sequence[TutorDecision]) -> None:
        self._responses = tuple(decisions)
        self.requests: list[dict[str, object]] = []
        self._index = 0

    async def create(self, **kwargs: object) -> object:
        self.requests.append(kwargs)
        decision = self._responses[self._index]
        self._index += 1
        text = json.dumps(
            {
                "decision": json.loads(
                    decision_to_bytes(decision)
                )
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return {
            "status": "completed",
            "error": None,
            "incomplete_details": None,
            "output": [
                {"type": "reasoning"},
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                },
            ],
        }


class _RecordedClient:
    def __init__(self, responses: OpenAIResponsesResource) -> None:
        self.responses = responses


def _run_trace(
    decision_port: TutorDecisionPort,
    decisions: Sequence[TutorDecision],
    descriptor: HostFileDescriptor,
    learner_entry: str,
) -> tuple[list[TutorHostRunResult], _DemoGateway]:
    assembler = _DemoAssembler(descriptor)
    assembler.learner_entry = learner_entry
    gateway = _DemoGateway()
    runner = TutorHostRunner(
        decision_port,
        None,
        None,
        gateway,
        _DemoAuthority(),
        _DemoIdentity(),
        _DemoStore(),
        TutorHostLimits(3, 2, 1, 2_000),
        context_assembler=assembler,  # type: ignore[arg-type]
    )
    direct = StartCapabilityDecision(
        TutorCapabilityId.EXPLAIN_CONCEPT.value, _inputs("heart valves")
    )
    clarification = StartCapabilityDecision(
        TutorCapabilityId.EXPLAIN_CONCEPT.value,
        _inputs("needs clarification"),
    )
    context = assembler.assemble(COURSE_ID, SESSION_ID)
    result_direct = asyncio.run(
        runner.run(COURSE_ID, SESSION_ID, "demo-direct", _Token())
    )
    assert result_direct.status is TutorHostRunStatus.COMPLETED
    context_clarification = assembler.assemble(COURSE_ID, SESSION_ID)
    result_suspended = asyncio.run(
        runner.run(COURSE_ID, SESSION_ID, "demo-clarify", _Token())
    )
    assert result_suspended.status is TutorHostRunStatus.SUSPENDED
    assert result_suspended.pending_continuation is not None
    assembler.evidence_sequence = 2
    result_resumed = asyncio.run(
        runner.run(
            COURSE_ID,
            SESSION_ID,
            "demo-answer",
            _Token(),
            pending_fingerprint=result_suspended.pending_continuation.fingerprint,
        )
    )
    assert result_resumed.status is TutorHostRunStatus.COMPLETED
    assert context.fingerprint == context_clarification.fingerprint
    assert decisions == (direct, clarification, AnswerDialogueDecision(
        result_suspended.pending_continuation.fingerprint, "aortic valve"
    ))
    return [result_direct, result_suspended, result_resumed], gateway


def _capture_descriptor() -> HostFileDescriptor:
    registry = HostFileRegistry(
        _MemorySource(_fixture_content()),
        MemoryHostFileIdentity(),
        MemoryHostFileSnapshotStore(),
        _FixedClock(),
        timedelta(hours=1),
    )
    return registry.capture(
        "fixtures/heart-valves.md", COURSE_ID, SESSION_ID, "Lesson notes"
    )


def run_reference_demo(
    learner_entry: str = _DEFAULT_LEARNER_ENTRY,
) -> dict[str, object]:
    descriptor = _capture_descriptor()
    direct = StartCapabilityDecision(
        TutorCapabilityId.EXPLAIN_CONCEPT.value, _inputs("heart valves")
    )
    clarification = StartCapabilityDecision(
        TutorCapabilityId.EXPLAIN_CONCEPT.value,
        _inputs("needs clarification"),
    )
    pending = _continuation(clarification.inputs)
    answer = AnswerDialogueDecision(pending.fingerprint, "aortic valve")
    decisions = (direct, clarification, answer)
    scripted_context = _DemoAssembler(descriptor)
    scripted_context.learner_entry = learner_entry
    expected = (
        ScriptedDecision(scripted_context.assemble(COURSE_ID, SESSION_ID).fingerprint, direct),
        ScriptedDecision(
            scripted_context.assemble(COURSE_ID, SESSION_ID).fingerprint,
            clarification,
        ),
    )
    resume_context = _DemoAssembler(descriptor)
    resume_context.learner_entry = learner_entry
    resume_context.evidence_sequence = 2
    scripted = ScriptedTutorDecisionPort((*expected,
        ScriptedDecision(
            resume_context.assemble(
                COURSE_ID,
                SESSION_ID,
                pending_continuation=PendingContinuationDescriptor(
                    pending.fingerprint,
                    EXPLAIN_CONCEPT_MANIFEST.identity,
                    "clarify",
                    "Which valve should we focus on?",
                    {"type": "string", "minLength": 1},
                )
            ).fingerprint,
            answer,
        ),
    ))
    scripted_results, scripted_gateway = _run_trace(
        scripted, decisions, descriptor, learner_entry
    )

    recorded_client = _RecordedResponses(decisions)
    recorded = OpenAIResponsesTutorDecisionPort(
        OpenAIResponsesTutorConfig("gpt-5.6", "OPENAI_API_KEY"),
        client=_RecordedClient(recorded_client),
    )
    recorded_results, recorded_gateway = _run_trace(
        recorded, decisions, descriptor, learner_entry
    )
    scripted_statuses = tuple(result.status.value for result in scripted_results)
    recorded_statuses = tuple(result.status.value for result in recorded_results)
    source = _fixture_content()
    return {
        "learner_entry": learner_entry,
        "discovered_capabilities": tuple(item.id.value for item in scripted_gateway.discover()),
        "captured_file": descriptor.to_json(),
        "scripted_statuses": scripted_statuses,
        "recorded_statuses": recorded_statuses,
        "parity": scripted_statuses == recorded_statuses
        and scripted_gateway.events == recorded_gateway.events,
        "evidence_refresh_sequence": 2,
        "gateway_trace": tuple(scripted_gateway.events),
        "recorded_request_count": len(recorded_client.requests),
        "timeline": (
            {"step": 1, "status": "completed", "detail": "Initial grounded explanation"},
            {"step": 2, "status": "suspended", "detail": "Which valve should we focus on?"},
            {"step": 3, "status": "completed", "detail": "Resumed with refreshed evidence"},
        ),
        "context_state": {
            "initial_sequence": 1,
            "refreshed_sequence": 2,
            "selected_focus": "aortic valve",
        },
        "source_state": {
            "fixture": "heart-valves.md",
            "title": _FIXTURE_TITLE,
            "checksum_sha256": sha256(source).hexdigest(),
            "byte_size": len(source),
            "evidence": _FIXTURE_EVIDENCE,
        },
    }


def _render(result: dict[str, object]) -> str:
    source = result["source_state"]
    context = result["context_state"]
    assert isinstance(source, dict)
    assert isinstance(context, dict)
    timeline = result["timeline"]
    assert isinstance(timeline, tuple)
    evidence = source["evidence"]
    assert isinstance(evidence, tuple)
    capabilities = cast(tuple[str, ...], result["discovered_capabilities"])
    lines = [
        "STUDY AGENT HARNESS — OFFLINE ANATOMY DEMO",
        "",
        f"Learner > {result['learner_entry']}",
        "",
        "SOURCE-GROUNDED CONTEXT",
        f"  Fixture: {source['fixture']}",
        f"  Snapshot: sha256:{source['checksum_sha256']} ({source['byte_size']} bytes)",
        *(f"  Evidence: {item}" for item in evidence),
        "",
        "DETERMINISTIC TUTOR TRACE",
    ]
    lines.extend(
        f"  {item['step']}. {item['status']} — {item['detail']}"
        for item in timeline
        if isinstance(item, dict)
    )
    lines.extend(
        (
            "",
            "INSPECTABLE STATE",
            f"  Evidence sequence: {context['initial_sequence']} → {context['refreshed_sequence']}",
            f"  Learner clarification: {context['selected_focus']}",
            f"  Capabilities: {', '.join(capabilities)}",
            f"  Scripted/recorded parity: {str(result['parity']).lower()}",
            "  Replay: completed → suspended → completed",
            "",
            "Offline proof complete: no network, credentials, model SDK, or provider call.",
        )
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="cardine-demo",
        description="Run the deterministic offline anatomy tutor trace.",
    )
    parser.add_argument(
        "learner_prompt",
        nargs="?",
        default=_DEFAULT_LEARNER_ENTRY,
        help="free-form learner entry shown in the deterministic trace",
    )
    parser.add_argument("--json", action="store_true", help="emit inspectable JSON")
    args = parser.parse_args()
    result = run_reference_demo(args.learner_prompt)
    if args.json:
        print(json.dumps(result, sort_keys=True, default=list, indent=2))
    else:
        print(_render(result))


if __name__ == "__main__":
    main()
