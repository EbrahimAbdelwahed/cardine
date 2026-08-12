from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from study_agent.adapters.filesystem import FilesystemBlobStore
from study_agent.adapters.model import ScriptedModel
from study_agent.adapters.sqlite import SQLiteEventStore
from cardine.courses import register_course_events
from study_agent.domain import (
    Citation,
    CorrelationId,
    CourseId,
    ExecutionContext,
    PrincipalKind,
    ResolvedCitation,
    RevisionId,
    RunId,
    SessionId,
    SourceId,
)
from study_agent.domain._validation import JsonObject
from study_agent.grounding import (
    EvidenceEnvelope,
    EvidenceSufficiencyValidator,
    GroundedAnswerIntegrityValidator,
)
from study_agent.ingestion import TextIngestionService, register_source_revision_events
from study_agent.playbooks import (
    PlaybookEngine,
    PromptComposerRegistration,
    RuntimeRegistries,
    ToolBehaviorPin,
    VersionPins,
)
from study_agent.playbooks.builtin import GROUNDED_ANSWER_FLOW
from study_agent.ports import (
    EvidenceStatus,
    ModelCapabilities,
    RetrievalEvidenceSet,
    retrieval_read_set_fingerprint,
)
from study_agent.prompts import GROUNDED_ANSWER_PROMPT, CanonicalPromptComposer
from study_agent.sessions import (
    GroundedSessionFinalizer,
    ProjectionSessionView,
    SessionService,
    register_session_events,
)
from study_agent.skills import ArtifactReference, SemanticVersion
from study_agent.skills.builtin import GROUNDED_ANSWER_SKILL
from study_agent.state import EventRegistry
from tests.course_fixtures import create_canonical_course

V1 = SemanticVersion.parse("1.0.0")
COURSE = CourseId("course-replay")
SESSION = SessionId("session-replay")
RUN = RunId("run-replay")
MODEL = ArtifactReference("scripted-model", V1)
STATE = ArtifactReference("event_state", V1)


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 7, 12, 9, tzinfo=UTC)


class PersistentRunStore:
    """Tiny test store whose bytes survive constructing a fresh engine/store."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(exist_ok=True)

    def _path(self, run_id: RunId) -> Path:
        return self.root / str(run_id)

    def create(self, run_id: RunId, payload: bytes) -> bool:
        path = self._path(run_id)
        if path.exists():
            return False
        path.write_bytes(payload)
        return True

    def compare_and_set(self, run_id: RunId, expected: bytes, replacement: bytes) -> bool:
        path = self._path(run_id)
        if not path.exists() or path.read_bytes() != expected:
            return False
        path.write_bytes(replacement)
        return True

    def load(self, run_id: RunId) -> bytes:
        return self._path(run_id).read_bytes()


class Tool:
    behavior_version = V1

    def __init__(self, name: str, result: JsonObject) -> None:
        self.name = name
        self.result = result
        self.calls: list[JsonObject] = []

    async def invoke(self, arguments: JsonObject) -> JsonObject:
        self.calls.append(arguments)
        return self.result


class NoContent:
    def get_text(self, revision_id: RevisionId) -> str:
        raise AssertionError(f"insufficient answer read revision {revision_id}")

    def resolve(self, citation: Citation) -> ResolvedCitation:
        raise AssertionError(f"insufficient answer resolved citation {citation}")


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


def _engine(
    store: PersistentRunStore, context_tool: Tool, search_tool: Tool, model: ScriptedModel
) -> PlaybookEngine:
    return PlaybookEngine(
        engine_version=V1,
        model_adapter=MODEL,
        state_contract=STATE,
        model=model,
        registries=RuntimeRegistries(
            (context_tool, search_tool),
            (
                EvidenceSufficiencyValidator(),
                GroundedAnswerIntegrityValidator(NoContent()),
            ),
            (PromptComposerRegistration(GROUNDED_ANSWER_PROMPT, CanonicalPromptComposer()),),
        ),
        run_store=store,
        clock=Clock(),
    )


def test_mixed_source_and_session_stream_rebuilds_after_process_loss_without_effect_replay(
    tmp_path: Path,
) -> None:
    blobs = FilesystemBlobStore(tmp_path / "blobs")
    registry = EventRegistry()
    register_course_events(registry)
    register_source_revision_events(registry, blobs.get)
    register_session_events(registry)
    events = SQLiteEventStore(tmp_path / "events.sqlite3", registry)
    courses = create_canonical_course(events, COURSE)
    view = ProjectionSessionView(events.projection)

    ingestion_context = ExecutionContext(
        PrincipalKind.SERVICE,
        "integration-test",
        COURSE,
        CorrelationId("ingest-source"),
    )
    TextIngestionService(
        blobs=blobs, events=events, clock=Clock(), courses=courses
    ).ingest(
        filename="cardiology.md",
        content=b"# Cardiology\n\nThe aortic valve has three cusps.",
        source_id=SourceId("source-cardiology"),
        title="Cardiology",
        trust_level=90,
        source_role="primary",
        context=ingestion_context,
    )
    session_context = _context("start-session")
    SessionService(events, Clock(), view, courses).start(session_context)

    empty = RetrievalEvidenceSet(
        EvidenceStatus.INSUFFICIENT,
        (),
        "a" * 64,
        "sqlite_fts5",
        "1.0.0",
        "index-v1",
        retrieval_read_set_fingerprint(()),
    )
    context_tool = Tool(
        "session.get_context",
        {
            "course_profile": {"language": "en"},
            "continuation_summary": None,
            "source_policy": {"minimum_trust_level": 80},
        },
    )
    search_tool = Tool("source.search", EvidenceEnvelope.from_retrieval(empty).to_json())
    run_store_path = tmp_path / "run-store"
    store = PersistentRunStore(run_store_path)
    first_model = ScriptedModel(
        (),
        ModelCapabilities(structured_output=True),
        adapter_id=MODEL.id,
        adapter_version=str(MODEL.version),
        model_id="never-called",
    )
    inputs: JsonObject = {
        "course_id": str(COURSE),
        "session_id": str(SESSION),
        "question": "How many pulmonary cusps are documented?",
    }
    asyncio.run(
        _engine(store, context_tool, search_tool, first_model).execute(
            run_id=RUN,
            skill=GROUNDED_ANSWER_SKILL,
            definition=GROUNDED_ANSWER_FLOW,
            inputs=inputs,
            pins=_pins(),
        )
    )
    assert len(context_tool.calls) == 1
    assert len(search_tool.calls) == 1
    first_model.assert_exhausted()

    # A fresh engine represents recovery after the process that executed the run is gone.
    recovery_context = Tool("session.get_context", context_tool.result)
    recovery_search = Tool("source.search", search_tool.result)
    recovery_model = ScriptedModel(
        (),
        ModelCapabilities(structured_output=True),
        adapter_id=MODEL.id,
        adapter_version=str(MODEL.version),
        model_id="never-called",
    )
    recovered_engine = _engine(
        PersistentRunStore(run_store_path),
        recovery_context,
        recovery_search,
        recovery_model,
    )
    answer = GroundedSessionFinalizer(
        events,
        Clock(),
        view,
        NoContent(),
        GROUNDED_ANSWER_SKILL.state_write_policy,
    ).finalize_grounded_run(
        context=session_context,
        engine=recovered_engine,
        run_id=RUN,
        definition=GROUNDED_ANSWER_FLOW,
        inputs=inputs,
        pins=_pins(),
        idempotency_key="finalize-after-loss",
    )

    assert answer.answer.status.value == "insufficient_evidence"
    assert recovery_context.calls == []
    assert recovery_search.calls == []
    recovery_model.assert_exhausted()
    assert tuple(event.event_type for event in events.read(COURSE)) == (
        "course.created",
        "source.revision_ingested",
        "session.started",
        "session.interaction_recorded",
        "session.answer_recorded",
        "session.continuation_summary_updated",
    )
    before = events.projection_bytes(COURSE)
    assert events.verify_projection(COURSE)
    rebuilt = events.rebuild_projection(COURSE)
    assert rebuilt == before
    assert events.projection_bytes(COURSE) == before
    assert view.get_answer(COURSE, SESSION, answer.id) == answer
    assert view.get_session(COURSE, SESSION).continuation_summary is not None
    blobs.close()
