from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from study_agent.adapters.filesystem import FilesystemBlobStore
from study_agent.adapters.model import ScriptedExchange, ScriptedModel
from study_agent.adapters.sqlite import SQLiteEventStore, SQLiteFtsRetrieval
from study_agent.application import (
    GroundingAskConfiguration,
    GroundingAskError,
    GroundingAskErrorCode,
    GroundingAskService,
)
from cardine.courses import course_profile_manifest, register_course_events
from study_agent.domain import (
    CorrelationId,
    CourseId,
    ExecutionContext,
    PrincipalKind,
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
    CancelledRunResult,
    EngineErrorCode,
    EngineFailure,
    ModelStep,
    PlaybookEngine,
    PromptComposerRegistration,
    ReadDependency,
    RuntimeRegistries,
    ToolBehaviorPin,
    ToolExecutor,
    VersionPins,
)
from study_agent.playbooks.builtin import GROUNDED_ANSWER_FLOW
from study_agent.ports import (
    IndexReceipt,
    ModelCapabilities,
    ModelFinishReason,
    ModelInvocation,
    ModelResponse,
    RetrievalPort,
    RetrievalQuery,
)
from study_agent.prompts import GROUNDED_ANSWER_PROMPT, CanonicalPromptComposer
from study_agent.retrieval import CourseSourceContent
from study_agent.sessions import (
    GroundedSessionFinalizer,
    ProjectionSessionView,
    RetryableSessionConflictError,
    SessionService,
    register_session_events,
)
from study_agent.skills import ArtifactReference, SemanticVersion
from study_agent.skills.builtin import GROUNDED_ANSWER_SKILL
from study_agent.state import EventRegistry
from tests.course_fixtures import canonical_profile, create_canonical_course

V1 = SemanticVersion.parse("1.0.0")
COURSE = CourseId("course-grounding-ask")
SESSION = SessionId("session-grounding-ask")


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 7, 12, 8, tzinfo=UTC)


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


class CountingRetrieval:
    def __init__(self, inner: RetrievalPort) -> None:
        self.inner = inner
        self.search_calls = 0

    def index(self, documents: object) -> IndexReceipt:
        raise AssertionError(f"service attempted indexing: {documents!r}")

    def search(self, query: object):  # type: ignore[no-untyped-def]
        self.search_calls += 1
        return self.inner.search(query)  # type: ignore[arg-type]


class Factory:
    def __init__(self, store: Store, content: CourseSourceContent) -> None:
        self.store = store
        self.content = content
        self.created = 0
        self.last_tools: tuple[ToolExecutor, ...] = ()
        self.model = ScriptedModel(
            (),
            ModelCapabilities(structured_output=True),
            adapter_id="scripted-model",
            adapter_version="1.0.0",
            model_id="unused-insufficient-model",
        )

    def create(self, *, tools: tuple[ToolExecutor, ...]) -> PlaybookEngine:
        self.created += 1
        self.last_tools = tools
        return PlaybookEngine(
            engine_version=V1,
            model_adapter=ArtifactReference("scripted-model", V1),
            state_contract=ArtifactReference("event_state", V1),
            model=self.model,
            registries=RuntimeRegistries(
                tools,
                (
                    EvidenceSufficiencyValidator(),
                    GroundedAnswerIntegrityValidator(self.content),
                ),
                (
                    PromptComposerRegistration(
                        GROUNDED_ANSWER_PROMPT, CanonicalPromptComposer()
                    ),
                ),
            ),
            run_store=self.store,
            clock=Clock(),
        )


class FailOnceFinalizer:
    def __init__(self, inner: GroundedSessionFinalizer) -> None:
        self.inner = inner
        self.calls = 0

    def finalize_grounded_run(self, **kwargs: object):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls == 1:
            raise RetryableSessionConflictError("simulated process loss before commit")
        return self.inner.finalize_grounded_run(**kwargs)  # type: ignore[arg-type]


class AppendBeforeFinalize:
    def __init__(self, inner: GroundedSessionFinalizer, append: Callable[[], object]) -> None:
        self.inner = inner
        self.append = append

    def finalize_grounded_run(self, **kwargs: object):  # type: ignore[no-untyped-def]
        self.append()
        return self.inner.finalize_grounded_run(**kwargs)  # type: ignore[arg-type]


def pins() -> VersionPins:
    return VersionPins(
        ArtifactReference(GROUNDED_ANSWER_SKILL.id, GROUNDED_ANSWER_SKILL.version),
        ArtifactReference(GROUNDED_ANSWER_FLOW.id, GROUNDED_ANSWER_FLOW.version),
        GROUNDED_ANSWER_PROMPT,
        (
            ToolBehaviorPin("session.get_context", V1),
            ToolBehaviorPin("source.search", V1),
        ),
        ArtifactReference("scripted-model", V1),
        ArtifactReference("event_state", V1),
    )


def context(
    *,
    key: str = "ask-1",
    capabilities: frozenset[str] | None = None,
    principal_kind: PrincipalKind = PrincipalKind.SERVICE,
) -> ExecutionContext:
    return ExecutionContext(
        principal_kind,
        "grounding-test",
        COURSE,
        CorrelationId("correlation-grounding-ask"),
        capabilities if capabilities is not None else frozenset({"study:ask"}),
        SESSION,
        idempotency_key=key,
    )


def composition(
    tmp_path: Path,
    *,
    fail_once: bool = False,
    receipt_fingerprint: str | None = None,
) -> tuple[
    GroundingAskService,
    SQLiteEventStore,
    CountingRetrieval,
    Factory,
    GroundedSessionFinalizer | FailOnceFinalizer,
    FilesystemBlobStore,
]:
    registry = EventRegistry()
    register_course_events(registry)
    blobs = FilesystemBlobStore(tmp_path / "blobs")
    register_source_revision_events(registry, blobs.get)
    register_session_events(registry)
    events = SQLiteEventStore(tmp_path / "events.sqlite3", registry)
    courses = create_canonical_course(events, COURSE)
    ingest = TextIngestionService(blobs=blobs, events=events, clock=Clock(), courses=courses)
    ingest.ingest(
        filename="heart.txt",
        content=b"The aortic valve has three cusps.",
        source_id=SourceId("heart"),
        title="Heart",
        trust_level=90,
        source_role="primary",
        context=ExecutionContext(
            PrincipalKind.SERVICE,
            "ingestion",
            COURSE,
            CorrelationId("correlation-ingestion"),
        ),
    )
    content = CourseSourceContent(COURSE, events, blobs)
    fts = SQLiteFtsRetrieval(tmp_path / "fts.sqlite3", content)
    documents = content.documents(include_superseded=True)
    receipt = fts.index(documents)
    if receipt_fingerprint is not None:
        receipt = replace(receipt, catalog_fingerprint=receipt_fingerprint)
    retrieval = CountingRetrieval(fts)
    sessions = ProjectionSessionView(events.projection)
    session_service = SessionService(events, Clock(), sessions, courses)
    session_service.start(context(key="session-start"))
    finalizer = GroundedSessionFinalizer(
        events,
        Clock(),
        sessions,
        content,
        GROUNDED_ANSWER_SKILL.state_write_policy,
    )
    wrapped = FailOnceFinalizer(finalizer) if fail_once else finalizer
    store = Store()
    factory = Factory(store, content)
    service = GroundingAskService(
        courses=courses,
        session_service=session_service,
        sessions=sessions,
        retrieval=retrieval,
        catalog=content,
        content=content,
        finalizer=wrapped,  # type: ignore[arg-type]
        engine_factory=factory,
        run_store=store,
        configuration=GroundingAskConfiguration(pins(), receipt),
        events=events,
    )
    return service, events, retrieval, factory, wrapped, blobs


def test_insufficient_answer_is_canonical_and_retry_has_zero_effects(tmp_path: Path) -> None:
    service, events, retrieval, factory, _, blobs = composition(tmp_path)
    before = len(events.read(COURSE))

    first = asyncio.run(service.ask("What is absent from these notes?", context()))
    after_first = len(events.read(COURSE))
    second = asyncio.run(service.ask("What is absent from these notes?", context()))

    assert first == second
    assert first.answer.answer.status.value == "insufficient_evidence"
    assert [event.kind.value for event in first.events] == [
        "grounding.accepted",
        "grounding.completed",
    ]
    assert after_first == before + 3
    assert len(events.read(COURSE)) == after_first
    assert retrieval.search_calls == 1
    assert factory.created == 1
    factory.model.assert_exhausted()
    assert events.verify_projection(COURSE)
    blobs.close()


def test_supported_answer_executes_model_once_and_retry_is_identical(tmp_path: Path) -> None:
    service, events, retrieval, factory, _, blobs = composition(tmp_path)
    evidence = EvidenceEnvelope.from_retrieval(
        retrieval.inner.search(RetrievalQuery(COURSE, "aortic valve"))
    ).to_json()
    first_item = cast(tuple[JsonObject, ...], evidence["items"])[0]
    evidence_id = cast(str, first_item["evidence_id"])
    step = cast(ModelStep, GROUNDED_ANSWER_FLOW.steps[3])
    composed = CanonicalPromptComposer().compose(
        prompt=GROUNDED_ANSWER_PROMPT,
        layers=GROUNDED_ANSWER_SKILL.prompt_layers,
        inputs={
            "question": "aortic valve",
            "course_profile": course_profile_manifest(canonical_profile(COURSE)),
            "continuation_summary": None,
            "evidence": evidence,
        },
        output_schema=step.output_schema,
    )
    request = replace(
        step.request,
        messages=composed.messages,
        metadata={
            "prompt_fingerprint": composed.fingerprint,
            "prompt_id": composed.prompt.id,
            "prompt_version": str(composed.prompt.version),
        },
    )
    factory.model = ScriptedModel(
        (
            ScriptedExchange(
                request,
                ModelResponse(
                    "",
                    None,
                    ModelFinishReason.STOP,
                    ModelInvocation(
                        "scripted-model", "1.0.0", "fixture-model", "response-supported"
                    ),
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
        adapter_id="scripted-model",
        adapter_version="1.0.0",
        model_id="fixture-model",
    )
    before = len(events.read(COURSE))

    first = asyncio.run(service.ask("aortic valve", context()))
    second = asyncio.run(service.ask("aortic valve", context()))

    assert first == second
    assert first.answer.answer.status.value == "answered"
    assert first.answer.answer.segments[0].citations[0].quoted_snippet == (
        "The aortic valve has three cusps."
    )
    assert retrieval.search_calls == 1
    assert len(events.read(COURSE)) == before + 3
    factory.model.assert_exhausted()
    blobs.close()


def test_changed_question_conflicts_before_any_new_effect(tmp_path: Path) -> None:
    service, events, retrieval, _, _, blobs = composition(tmp_path)
    asyncio.run(service.ask("What is absent from these notes?", context()))
    before = len(events.read(COURSE))

    with pytest.raises(GroundingAskError) as caught:
        asyncio.run(service.ask("A different question", context()))

    assert caught.value.code is GroundingAskErrorCode.CONFLICT
    assert len(events.read(COURSE)) == before
    assert retrieval.search_calls == 1
    blobs.close()


def test_expected_sequence_rejects_external_append_before_retrieval_or_model(
    tmp_path: Path,
) -> None:
    service, events, retrieval, factory, _, blobs = composition(tmp_path)
    expected = len(events.read(COURSE))
    service._session_service.record_note(
        context(key="external-before"), "An external note advanced the stream."
    )

    with pytest.raises(GroundingAskError) as caught:
        asyncio.run(
            service.ask(
                "What is absent from these notes?",
                context(),
                expected_sequence=expected,
            )
        )

    assert caught.value.code is GroundingAskErrorCode.RETRYABLE_CONFLICT
    assert retrieval.search_calls == 0
    assert factory.created == 0
    assert len(events.read(COURSE)) == expected + 2
    blobs.close()


def test_expected_sequence_rejects_append_after_model_without_answer_commit(
    tmp_path: Path,
) -> None:
    service, events, retrieval, factory, finalizer, blobs = composition(tmp_path)
    expected = len(events.read(COURSE))
    service._finalizer = cast(
        GroundedSessionFinalizer,
        AppendBeforeFinalize(
        cast(GroundedSessionFinalizer, finalizer),
        lambda: service._session_service.record_note(
            context(key="external-during-model"), "An external note won the race."
        ),
        ),
    )

    with pytest.raises(GroundingAskError) as caught:
        asyncio.run(
            service.ask(
                "What is absent from these notes?",
                context(),
                expected_sequence=expected,
            )
        )

    assert caught.value.code is GroundingAskErrorCode.RETRYABLE_CONFLICT
    assert retrieval.search_calls == 1
    assert factory.created == 1
    assert len(events.read(COURSE)) == expected + 2
    assert service._sessions.answers(COURSE, SESSION) == ()
    blobs.close()


def test_run_identity_commits_to_ordered_exact_read_dependencies(tmp_path: Path) -> None:
    service, _, _, _, _, blobs = composition(tmp_path)
    dependencies = (
        ReadDependency("course_profile", str(COURSE), "course-v1"),
        ReadDependency("source_revision_set", str(COURSE), "source-v1"),
        ReadDependency("retrieval_index", str(COURSE), "index-v1"),
        ReadDependency("session_state", str(SESSION), "session-v1"),
    )

    baseline = service._run_id(COURSE, SESSION, "ask-1", "question-v1", dependencies)

    assert baseline == service._run_id(
        COURSE, SESSION, "ask-1", "question-v1", dependencies
    )
    for index, dependency in enumerate(dependencies):
        changed = list(dependencies)
        changed[index] = ReadDependency(
            dependency.kind, dependency.id, dependency.version + "-changed"
        )
        assert service._run_id(
            COURSE, SESSION, "ask-1", "question-v1", tuple(changed)
        ) != baseline
    assert service._run_id(
        COURSE, SESSION, "ask-1", "question-v1", tuple(reversed(dependencies))
    ) != baseline
    blobs.close()


def test_request_bound_executors_reject_authority_arguments(tmp_path: Path) -> None:
    service, _, _, factory, _, blobs = composition(tmp_path)
    asyncio.run(service.ask("What is absent from these notes?", context()))

    with pytest.raises(ValueError, match="accepts no playbook arguments"):
        asyncio.run(factory.last_tools[0].invoke({"course_id": "forged"}))
    with pytest.raises(ValueError, match="trusted request question"):
        asyncio.run(
            factory.last_tools[1].invoke(
                {"query": "What is absent from these notes?", "session_id": "forged"}
            )
        )
    blobs.close()


def test_completed_run_recovers_after_process_loss_without_repeating_search(
    tmp_path: Path,
) -> None:
    service, events, retrieval, _, wrapped, blobs = composition(tmp_path, fail_once=True)
    before = len(events.read(COURSE))
    with pytest.raises(GroundingAskError) as caught:
        asyncio.run(service.ask("What is absent from these notes?", context()))
    assert caught.value.code is GroundingAskErrorCode.RETRYABLE_CONFLICT
    assert len(events.read(COURSE)) == before

    recovered = asyncio.run(service.ask("What is absent from these notes?", context()))

    assert recovered.answer.answer.status.value == "insufficient_evidence"
    assert retrieval.search_calls == 1
    assert isinstance(wrapped, FailOnceFinalizer) and wrapped.calls == 2
    assert len(events.read(COURSE)) == before + 3
    blobs.close()


def test_authority_and_capability_rejections_happen_before_effects(tmp_path: Path) -> None:
    service, events, retrieval, _, _, blobs = composition(tmp_path)
    before = len(events.read(COURSE))
    unauthorized = context(capabilities=frozenset())

    with pytest.raises(GroundingAskError) as caught:
        asyncio.run(service.ask("Question", unauthorized))

    assert caught.value.code is GroundingAskErrorCode.UNAUTHORIZED
    assert len(events.read(COURSE)) == before
    assert retrieval.search_calls == 0
    blobs.close()


def test_model_principal_with_explicit_grants_can_ask(tmp_path: Path) -> None:
    service, events, retrieval, _, _, blobs = composition(tmp_path)

    result = asyncio.run(
        service.ask(
            "What is absent from these notes?",
            context(principal_kind=PrincipalKind.MODEL),
        )
    )

    assert result.answer.answer.status.value == "insufficient_evidence"
    assert retrieval.search_calls == 1
    assert {event.actor.kind for event in events.read(COURSE)[-3:]} == {
        PrincipalKind.MODEL
    }
    assert events.verify_projection(COURSE)
    blobs.close()


def test_string_principal_kind_is_rejected_before_effects(tmp_path: Path) -> None:
    service, events, retrieval, factory, _, blobs = composition(tmp_path)
    forged = context(principal_kind=cast(PrincipalKind, "model"))
    before = len(events.read(COURSE))

    with pytest.raises(GroundingAskError) as caught:
        asyncio.run(service.ask("What is absent from these notes?", forged))

    assert caught.value.code is GroundingAskErrorCode.UNAUTHORIZED
    assert retrieval.search_calls == 0
    assert factory.created == 0
    assert len(events.read(COURSE)) == before
    blobs.close()


def test_forged_same_count_index_receipt_fails_before_execution(tmp_path: Path) -> None:
    service, events, retrieval, factory, _, blobs = composition(
        tmp_path, receipt_fingerprint="0" * 64
    )
    before = len(events.read(COURSE))

    with pytest.raises(GroundingAskError) as caught:
        asyncio.run(service.ask("What is absent from these notes?", context()))

    assert caught.value.code is GroundingAskErrorCode.INCOMPATIBLE_RUNTIME
    assert retrieval.search_calls == 0
    assert factory.created == 0
    assert len(events.read(COURSE)) == before
    blobs.close()


@pytest.mark.parametrize(
    ("persisted_status", "error_code"),
    [
        ("running", GroundingAskErrorCode.RUNNING),
        ("suspended", GroundingAskErrorCode.SUSPENDED),
        ("failed", GroundingAskErrorCode.FAILED),
        ("unknown", GroundingAskErrorCode.INCOMPATIBLE_RUNTIME),
    ],
)
def test_unsafe_persisted_states_never_reexecute(
    tmp_path: Path,
    persisted_status: str,
    error_code: GroundingAskErrorCode,
) -> None:
    service, events, retrieval, factory, _, blobs = composition(tmp_path, fail_once=True)
    with pytest.raises(GroundingAskError):
        asyncio.run(service.ask("What is absent from these notes?", context()))
    run_id = next(iter(factory.store.values))
    payload = json.loads(factory.store.values[run_id])
    payload["checkpoint"]["status"] = persisted_status
    factory.store.values[run_id] = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode()
    before = len(events.read(COURSE))

    with pytest.raises(GroundingAskError) as caught:
        asyncio.run(service.ask("What is absent from these notes?", context()))

    assert caught.value.code is error_code
    assert retrieval.search_calls == 1
    assert len(events.read(COURSE)) == before
    blobs.close()


def test_corrupt_persisted_run_never_reexecutes(tmp_path: Path) -> None:
    service, events, retrieval, factory, _, blobs = composition(tmp_path, fail_once=True)
    with pytest.raises(GroundingAskError):
        asyncio.run(service.ask("What is absent from these notes?", context()))
    run_id = next(iter(factory.store.values))
    factory.store.values[run_id] = b"not-json"
    before = len(events.read(COURSE))

    with pytest.raises(GroundingAskError) as caught:
        asyncio.run(service.ask("What is absent from these notes?", context()))

    assert caught.value.code is GroundingAskErrorCode.INCOMPATIBLE_RUNTIME
    assert retrieval.search_calls == 1
    assert len(events.read(COURSE)) == before
    blobs.close()


def test_malformed_or_missing_model_output_never_writes_session_events(tmp_path: Path) -> None:
    service, events, retrieval, factory, _, blobs = composition(tmp_path)
    before = len(events.read(COURSE))

    with pytest.raises(GroundingAskError) as caught:
        asyncio.run(service.ask("aortic valve", context()))

    assert caught.value.code is GroundingAskErrorCode.FAILED
    assert retrieval.search_calls == 1
    assert len(events.read(COURSE)) == before
    assert len(factory.store.values) == 1
    blobs.close()


def test_cancelled_playbook_result_is_existing_safe_failure_not_runtime_mismatch(
    tmp_path: Path,
) -> None:
    service, events, retrieval, factory, _, blobs = composition(tmp_path)
    before = len(events.read(COURSE))

    class CancelledEngine:
        async def execute(self, **kwargs: object) -> CancelledRunResult:
            assert kwargs["run_id"]
            return CancelledRunResult(
                {},
                (),
                EngineFailure(
                    EngineErrorCode.CANCELLED,
                    "model execution was cancelled",
                    "draft_answer",
                ),
            )

    class CancelledFactory:
        def create(self, *, tools: tuple[ToolExecutor, ...]) -> CancelledEngine:
            assert tools
            return CancelledEngine()

    service._engine_factory = cast(Factory, CancelledFactory())
    with pytest.raises(GroundingAskError) as caught:
        asyncio.run(service.ask("What is absent from these notes?", context()))

    assert caught.value.code is GroundingAskErrorCode.FAILED
    assert len(events.read(COURSE)) == before
    assert retrieval.search_calls == 0
    assert factory.created == 0
    blobs.close()
