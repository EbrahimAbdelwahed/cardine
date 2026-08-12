from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import cast

from study_agent.adapters.model import ScriptedExchange, ScriptedModel
from study_agent.adapters.sqlite import SQLiteEventStore
from cardine.courses import register_course_events
from study_agent.domain import (
    ChunkId,
    Citation,
    CorrelationId,
    CourseId,
    ExecutionContext,
    PrincipalKind,
    ResolvedCitation,
    RevisionId,
    RunId,
    SessionId,
    SourceChunk,
    SourceId,
)
from study_agent.domain._validation import JsonObject
from study_agent.grounding import (
    EvidenceEnvelope,
    EvidenceSufficiencyValidator,
    GroundedAnswerIntegrityValidator,
)
from study_agent.playbooks import (
    ModelStep,
    PlaybookEngine,
    PlaybookRunStatus,
    PromptComposerRegistration,
    RuntimeRegistries,
    StepTrace,
    StepTraceStatus,
    ToolBehaviorPin,
    ValidationOutcome,
    ValidatorDisposition,
    VerifiedRunRecord,
    VersionPins,
)
from study_agent.playbooks.builtin import GROUNDED_ANSWER_FLOW
from study_agent.ports import (
    EvidenceStatus,
    ModelCapabilities,
    ModelFinishReason,
    ModelInvocation,
    ModelRequest,
    ModelResponse,
    RetrievalEvidence,
    RetrievalEvidenceSet,
    retrieval_read_set_fingerprint,
)
from study_agent.prompts import GROUNDED_ANSWER_PROMPT, CanonicalPromptComposer, canonical_json
from study_agent.sessions import (
    GroundedSessionFinalizer,
    ProjectionSessionView,
    SessionService,
    register_session_events,
    summary_payload,
)
from study_agent.skills import ArtifactReference, SemanticVersion
from study_agent.skills.builtin import GROUNDED_ANSWER_SKILL
from study_agent.state import EventRegistry
from tests.course_fixtures import create_canonical_course

V1 = SemanticVersion.parse("1.0.0")
COURSE = CourseId("course-continuity")
SESSION = SessionId("session-continuity")
MODEL = ArtifactReference("scripted-model", V1)
STATE = ArtifactReference("event_state", V1)
NOW = datetime(2026, 7, 12, 10, tzinfo=UTC)


class Clock:
    def now(self) -> datetime:
        return NOW


class Store:
    def __init__(self) -> None:
        self.values: dict[RunId, bytes] = {}

    def create(self, run_id: RunId, payload: bytes) -> bool:
        if run_id in self.values:
            return False
        self.values[run_id] = payload
        return True

    def compare_and_set(self, run_id: RunId, expected: bytes, replacement: bytes) -> bool:
        if self.values.get(run_id) != expected:
            return False
        self.values[run_id] = replacement
        return True

    def load(self, run_id: RunId) -> bytes:
        return self.values[run_id]


class Tool:
    behavior_version = V1

    def __init__(self, name: str, result: JsonObject) -> None:
        self.name = name
        self.result = result
        self.calls: list[JsonObject] = []

    async def invoke(self, arguments: JsonObject) -> JsonObject:
        self.calls.append(arguments)
        return self.result


class Content:
    def __init__(self, citation: Citation, text: str) -> None:
        self.citation = citation
        self.text = text

    def get_text(self, revision_id: RevisionId) -> str:
        assert revision_id == self.citation.revision_id
        return self.text

    def resolve(self, citation: Citation) -> ResolvedCitation:
        assert citation == self.citation
        return ResolvedCitation(citation, self.text)


class NoContent:
    def get_text(self, revision_id: RevisionId) -> str:
        raise AssertionError(f"insufficient answer read {revision_id}")

    def resolve(self, citation: Citation) -> ResolvedCitation:
        raise AssertionError(f"insufficient answer resolved {citation}")


class RecoveryEngine:
    def __init__(self, run: VerifiedRunRecord) -> None:
        self.run = run

    def recover(self, **kwargs: object) -> VerifiedRunRecord:
        assert kwargs["run_id"] == self.run.run_id
        return self.run


def _context(correlation: str) -> ExecutionContext:
    return ExecutionContext(
        PrincipalKind.SERVICE,
        "integration-test",
        COURSE,
        CorrelationId(correlation),
        session_id=SESSION,
    )


def _pins() -> VersionPins:
    return VersionPins(
        ArtifactReference(GROUNDED_ANSWER_SKILL.id, GROUNDED_ANSWER_SKILL.version),
        ArtifactReference(GROUNDED_ANSWER_FLOW.id, GROUNDED_ANSWER_FLOW.version),
        GROUNDED_ANSWER_PROMPT,
        (
            ToolBehaviorPin("session.get_context", V1),
            ToolBehaviorPin("source.search", V1),
        ),
        MODEL,
        STATE,
    )


def _insufficient_run() -> VerifiedRunRecord:
    evidence = EvidenceEnvelope.from_retrieval(
        RetrievalEvidenceSet(
            EvidenceStatus.INSUFFICIENT,
            (),
            "a" * 64,
            "sqlite_fts5",
            "1.0.0",
            "index-v1",
            retrieval_read_set_fingerprint(()),
        )
    ).to_json()
    result: JsonObject = {
        "status": "insufficient_evidence",
        "segments": (),
        "unsupported_information_note": "No source evidence was found.",
    }
    termination = ValidationOutcome(
        True, ValidatorDisposition.TERMINATE, result, "retrieval was insufficient"
    )
    trace = StepTrace(
        "check_evidence",
        "validate",
        StepTraceStatus.COMPLETED,
        NOW,
        {
            "output_fingerprint": "f" * 64,
            "validator": {
                "validator_id": "evidence_sufficiency",
                "validator_version": "1.0.0",
                "passed": True,
                "disposition": "terminate",
                "result_fingerprint": "b" * 64,
                "reason": "retrieval was insufficient",
            },
        },
    )
    return VerifiedRunRecord(
        RunId("run-first"),
        "d" * 64,
        {
            "course_id": str(COURSE),
            "session_id": str(SESSION),
            "question": "What did the missing source say?",
        },
        _pins(),
        (),
        {"evidence": evidence, "evidence_gate": result},
        (trace,),
        PlaybookRunStatus.TERMINATED,
        termination,
    )


def _supported_evidence() -> tuple[JsonObject, Content, str]:
    text = "The aortic valve has three cusps."
    source = SourceId("source-heart")
    revision = RevisionId("revision-heart")
    chunk = SourceChunk(
        ChunkId("chunk-heart"),
        source,
        revision,
        0,
        len(text),
        (),
        0,
        sha256(text.encode()).hexdigest(),
        "chunker-v1",
    )
    citation = Citation(source, revision, chunk.chunk_id, 0, len(text), "Heart", text)
    item = RetrievalEvidence(chunk, citation, text, 0.9)
    envelope = EvidenceEnvelope.from_retrieval(
        RetrievalEvidenceSet(
            EvidenceStatus.SUFFICIENT,
            (item,),
            "c" * 64,
            "sqlite_fts5",
            "1.0.0",
            "index-v1",
            retrieval_read_set_fingerprint((item,)),
        )
    ).to_json()
    evidence_id = cast(tuple[JsonObject, ...], envelope["items"])[0]["evidence_id"]
    return envelope, Content(citation, text), cast(str, evidence_id)


def test_resume_supplies_only_bounded_summary_to_the_next_real_grounded_prompt(
    tmp_path: Path,
) -> None:
    registry = EventRegistry()
    register_course_events(registry)
    register_session_events(registry)
    events = SQLiteEventStore(tmp_path / "events.sqlite3", registry)
    view = ProjectionSessionView(events.projection)
    courses = create_canonical_course(events, COURSE)
    lifecycle = SessionService(events, Clock(), view, courses)
    context = _context("start")
    lifecycle.start(context)

    first = _insufficient_run()
    GroundedSessionFinalizer(
        events, Clock(), view, NoContent(), GROUNDED_ANSWER_SKILL.state_write_policy
    ).finalize_grounded_run(
        context=context,
        engine=cast(PlaybookEngine, RecoveryEngine(first)),
        run_id=first.run_id,
        definition=GROUNDED_ANSWER_FLOW,
        inputs=first.inputs,
        pins=first.pins,
        idempotency_key="first-answer",
    )
    before_suspend = lifecycle.get_context(context)
    assert before_suspend is not None
    lifecycle.suspend(_context("suspend"))
    lifecycle.resume(_context("resume"))
    resumed = lifecycle.get_context(context)
    assert resumed == before_suspend

    summary_json = cast(JsonObject, summary_payload(resumed)["summary"])
    context_result: JsonObject = {
        "course_profile": {"language": "en"},
        "continuation_summary": summary_json,
        "source_policy": {"minimum_trust_level": 80},
    }
    envelope, content, evidence_id = _supported_evidence()
    step = cast(ModelStep, GROUNDED_ANSWER_FLOW.steps[3])
    composed = CanonicalPromptComposer().compose(
        prompt=GROUNDED_ANSWER_PROMPT,
        layers=GROUNDED_ANSWER_SKILL.prompt_layers,
        inputs={
            "question": "How many aortic cusps?",
            "course_profile": context_result["course_profile"],
            "continuation_summary": summary_json,
            "evidence": envelope,
        },
        output_schema=step.output_schema,
    )
    request: ModelRequest = replace(
        step.request,
        messages=composed.messages,
        metadata={
            "prompt_fingerprint": composed.fingerprint,
            "prompt_id": composed.prompt.id,
            "prompt_version": str(composed.prompt.version),
        },
    )
    model = ScriptedModel(
        (
            ScriptedExchange(
                request,
                ModelResponse(
                    "",
                    None,
                    ModelFinishReason.STOP,
                    ModelInvocation("scripted-model", "1.0.0", "fixture-model", "r2"),
                    structured_output={
                        "status": "answered",
                        "segments": (
                            {
                                "kind": "supported_claim",
                                "text": "The aortic valve has three cusps.",
                                "evidence_ids": (evidence_id,),
                            },
                        ),
                        "unsupported_information_note": None,
                    },
                ),
            ),
        ),
        ModelCapabilities(structured_output=True),
        adapter_id=MODEL.id,
        adapter_version=str(MODEL.version),
        model_id="fixture-model",
    )
    context_tool = Tool("session.get_context", context_result)
    search_tool = Tool("source.search", envelope)
    runtime = PlaybookEngine(
        engine_version=V1,
        model_adapter=MODEL,
        state_contract=STATE,
        model=model,
        registries=RuntimeRegistries(
            (context_tool, search_tool),
            (
                EvidenceSufficiencyValidator(),
                GroundedAnswerIntegrityValidator(content),
            ),
            (PromptComposerRegistration(GROUNDED_ANSWER_PROMPT, CanonicalPromptComposer()),),
        ),
        run_store=Store(),
        clock=Clock(),
    )
    result = asyncio.run(
        runtime.execute(
            run_id=RunId("run-next"),
            skill=GROUNDED_ANSWER_SKILL,
            definition=GROUNDED_ANSWER_FLOW,
            inputs={
                "course_id": str(COURSE),
                "session_id": str(SESSION),
                "question": "How many aortic cusps?",
            },
            pins=_pins(),
        )
    )

    assert result.status is PlaybookRunStatus.COMPLETED
    assert context_tool.calls == [{}]
    assert search_tool.calls == [{"query": "How many aortic cusps?"}]
    assert set(context_tool.result) == {
        "course_profile",
        "continuation_summary",
        "source_policy",
    }
    assert "interactions" not in canonical_json(context_tool.result)
    continuation_message = composed.messages[3].content
    assert canonical_json({"continuation_summary": summary_json}) in continuation_message
    assert resumed.character_count <= 2_000
    assert len(resumed.recent_exchanges) <= 4
    model.assert_exhausted()
    assert events.verify_projection(COURSE)
