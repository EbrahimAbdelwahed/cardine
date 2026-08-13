from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from cardine.courses import (
    CourseService,
    ProjectionCourseView,
    register_course_events,
)
from cardine.integrations.study_agent.course_policy import (
    PROVIDER_CONSENT_GRANTED,
    PROVIDER_CONSENT_REVOKED,
    SOURCE_RESTORED,
    SOURCE_RETIRED,
    consent_event_id,
    source_lifetime_event_id,
)
from study_agent.adapters.filesystem import (
    ExportDestinationExistsError,
    FilesystemBlobStore,
    FilesystemExportWriter,
)
from study_agent.adapters.filesystem import export as export_adapter
from study_agent.adapters.sqlite import SQLiteEventStore
from study_agent.application import ExportService, ExportStateError, ExportVersion
from study_agent.domain import (
    Actor,
    AnswerId,
    AnswerProvenance,
    AnswerRecord,
    AnswerSegment,
    AnswerStatus,
    Citation,
    ClaimOrigin,
    CorrelationId,
    CourseId,
    CourseProfile,
    DomainEvent,
    EventId,
    ExecutionContext,
    GroundedAnswer,
    InteractionId,
    InteractionKind,
    ModelProvenance,
    PrincipalKind,
    PromptProvenance,
    RetrievalProvenance,
    RunId,
    SegmentKind,
    SessionId,
    SourceCommitment,
    SourceId,
    ValidatorProvenance,
    VersionPins,
)
from study_agent.ingestion import (
    TextIngestionService,
    decode_source_revision_ingested,
    register_source_revision_events,
)
from study_agent.sessions import (
    ProjectionSessionView,
    SessionService,
    answer_recorded_payload,
    interaction_recorded_payload,
    lifecycle_payload,
    register_session_events,
)
from study_agent.state import EventRegistry

COURSE = CourseId("course-export")
SESSION = SessionId("session-export")
NOW = datetime(2026, 7, 12, 12, 34, 56, 789012, tzinfo=UTC)


class Clock:
    def now(self) -> datetime:
        return NOW


def _context(*, session: bool = False) -> ExecutionContext:
    return ExecutionContext(
        PrincipalKind.SERVICE,
        "principal-secret-never-export",
        COURSE,
        CorrelationId("correlation-export"),
        session_id=SESSION if session else None,
    )


def _stack(tmp_path: Path):  # type: ignore[no-untyped-def]
    blobs = FilesystemBlobStore(tmp_path / "blobs")
    registry = EventRegistry()
    register_course_events(registry)
    register_source_revision_events(registry, blobs.get)
    register_session_events(registry)
    events = SQLiteEventStore(tmp_path / "events.sqlite3", registry)
    courses = ProjectionCourseView(events.projection)
    sessions = ProjectionSessionView(events.projection)
    profile = CourseProfile(COURSE, "Cardiology", "en", learning_goals=("Learn",))
    CourseService(events, Clock(), courses).create(profile, _context())
    TextIngestionService(
        blobs=blobs, events=events, clock=Clock(), courses=courses
    ).ingest(
        filename="/Users/private/medical-secret.md",
        content=b"Aortic valve content must not be exported verbatim.",
        source_id=SourceId("source-aortic"),
        title="Aortic valve notes",
        trust_level=95,
        source_role="primary",
        context=_context(),
    )
    SessionService(events, Clock(), sessions, courses).start(_context(session=True))
    return blobs, events, courses, sessions


def _files(root: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(root.iterdir())}


def _policy_event(
    event_type: str,
    sequence: int,
    request_id: str,
    *,
    source_id: SourceId | None = None,
    correlation_id: str = "policy-correlation",
) -> DomainEvent:
    status = {
        PROVIDER_CONSENT_GRANTED: "granted",
        PROVIDER_CONSENT_REVOKED: "revoked",
        SOURCE_RETIRED: "retired",
        SOURCE_RESTORED: "restored",
    }[event_type]
    principal_id = "policy-human"
    payload = {
        "course_id": str(COURSE),
        "principal_id": principal_id,
        "request_id": request_id,
        "occurred_at": NOW.isoformat(),
        "status": status,
    }
    if source_id is None:
        event_id = consent_event_id(COURSE, principal_id, request_id, status)
    else:
        payload["source_id"] = str(source_id)
        event_id = source_lifetime_event_id(
            COURSE, source_id, principal_id, request_id, status
        )
    return DomainEvent(
        event_id,
        COURSE,
        sequence,
        event_type,
        1,
        Actor(PrincipalKind.HUMAN, principal_id),
        NOW,
        CorrelationId(correlation_id),
        payload,
    )


def test_unchanged_state_is_byte_identical_checksummed_and_redacted(tmp_path: Path) -> None:
    blobs, events, _, _ = _stack(tmp_path)
    bundle = ExportService(events).assemble(COURSE)
    first = tmp_path / "first"
    second = tmp_path / "second"

    receipt = FilesystemExportWriter().write(bundle, first)
    FilesystemExportWriter().write(bundle, second)

    assert _files(first) == _files(second)
    assert set(_files(first)) == {
        "manifest.json",
        "course.json",
        "sources.json",
        "sessions.jsonl",
        "answers.jsonl",
        "events.jsonl",
    }
    assert all(not content or content.endswith(b"\n") for content in _files(first).values())
    manifest = json.loads((first / "manifest.json").read_bytes())
    assert manifest["high_water_sequence"] == 3
    assert receipt.high_water_sequence == 3
    assert receipt.manifest_sha256 == sha256((first / "manifest.json").read_bytes()).hexdigest()
    for entry in manifest["files"]:
        content = (first / entry["name"]).read_bytes()
        assert entry == {
            "name": entry["name"],
            "sha256": sha256(content).hexdigest(),
            "byte_size": len(content),
        }

    combined = b"".join(_files(first).values())
    forbidden = (
        b"principal-secret-never-export",
        b"2026-07-12T12:34:56",
        b"/Users/private",
        b"medical-secret.md",
        b"Aortic valve content must not be exported verbatim.",
        b"normalized_blob",
        b'"blob"',
        b"occurred_at",
        b"principal_id",
        b"payload",
    )
    assert all(value not in combined for value in forbidden)
    blobs.close()


def test_answer_export_omits_model_trace_adapter_and_verbatim_snippet(
    tmp_path: Path,
) -> None:
    blobs, stored_events, _, _ = _stack(tmp_path)
    base_stream = tuple(stored_events.read(COURSE))
    source = decode_source_revision_ingested(base_stream[1].payload)
    chunk = source.chunks[0]
    run_id = RunId("run-sensitive")
    question_id = InteractionId("interaction-question")
    answer_interaction_id = InteractionId("interaction-answer")
    answer_id = AnswerId("answer-sensitive")
    citation = Citation(
        chunk.source_id,
        chunk.revision_id,
        chunk.chunk_id,
        chunk.start_offset,
        min(chunk.start_offset + 8, chunk.end_offset),
        "Aortic valve > 1",
        "VERBATIM_SOURCE_SECRET",
    )
    commitment = SourceCommitment(
        citation.source_id,
        citation.revision_id,
        citation.chunk_id,
        citation.start_offset,
        citation.end_offset,
    )
    provenance = AnswerProvenance(
        (commitment,),
        PromptProvenance("grounded_answer.v1", "1.0.0", "a" * 64, ("b" * 64,)),
        ModelProvenance(
            "provider-secret-adapter",
            "1.0.0",
            "provider-secret-model",
            "provider-secret-response",
            run_id,
        ),
        RetrievalProvenance("sqlite_fts5", "1.0.0", "c" * 64, "index-v1", "d" * 64),
        (ValidatorProvenance("grounded_answer_integrity", "1.0.0", True, "accept", "e" * 64),),
        VersionPins(
            "grounded_answer@1.0.0",
            "grounded_answer_flow@1.0.0",
            "grounded_answer.v1@1.0.0",
            "provider-secret-adapter@1.0.0",
            "event_state@1.0.0",
            "source.search@1.0.0",
        ),
        run_id,
    )
    answer = AnswerRecord(
        answer_id,
        answer_interaction_id,
        question_id,
        run_id,
        "idempotency-secret",
        "f" * 64,
        GroundedAnswer(
            AnswerStatus.ANSWERED,
            (
                AnswerSegment(
                    SegmentKind.SUPPORTED_CLAIM,
                    "Generated public answer.",
                    (citation,),
                    ClaimOrigin.INFERRED,
                ),
            ),
            None,
            provenance,
        ),
    )
    events = (
        *base_stream,
        DomainEvent(
            EventId("event-question"),
            COURSE,
            4,
            "session.interaction_recorded",
            1,
            Actor(PrincipalKind.HUMAN, "secret-human"),
            NOW,
            CorrelationId("question-correlation"),
            interaction_recorded_payload(question_id, InteractionKind.HUMAN, "Question?"),
            SESSION,
        ),
        DomainEvent(
            EventId("event-answer"),
            COURSE,
            5,
            "session.answer_recorded",
            1,
            Actor(PrincipalKind.SERVICE, "secret-service"),
            NOW,
            CorrelationId("answer-correlation"),
            answer_recorded_payload(answer),
            SESSION,
        ),
    )

    class Events:
        def read(
            self, course_id: CourseId, after_sequence: int = 0
        ) -> Sequence[DomainEvent]:
            assert course_id == COURSE and after_sequence == 0
            return events

        def append(self, course_id, expected_sequence, events):  # type: ignore[no-untyped-def]
            raise AssertionError("export must be read-only")

    bundle = ExportService(Events()).assemble(COURSE)
    encoded = json.dumps(bundle.answers, default=dict, sort_keys=True)
    assert "Generated public answer." in encoded
    for forbidden in (
        "VERBATIM_SOURCE_SECRET",
        "provider-secret",
        "idempotency-secret",
        "response_id",
        "model_adapter",
        "quoted_snippet",
        "input_tokens",
        "output_tokens",
    ):
        assert forbidden not in encoded

    class MissingSourceEvents(Events):
        def read(
            self, course_id: CourseId, after_sequence: int = 0
        ) -> Sequence[DomainEvent]:
            return (
                events[0],
                _at_sequence(events[2], 2),
                _at_sequence(events[3], 3),
                _at_sequence(events[4], 4),
            )

    with pytest.raises(ExportStateError, match="source chunk absent"):
        ExportService(MissingSourceEvents()).assemble(COURSE)
    blobs.close()


def _at_sequence(event: DomainEvent, sequence: int) -> DomainEvent:
    return DomainEvent(
        event.event_id,
        event.course_id,
        sequence,
        event.event_type,
        event.schema_version,
        event.actor,
        event.occurred_at,
        event.correlation_id,
        event.payload,
        event.session_id,
        event.causation_id,
    )


def test_malformed_session_event_order_fails_replay(tmp_path: Path) -> None:
    blobs, events, _, _ = _stack(tmp_path)
    stream = tuple(events.read(COURSE))
    resumed_without_suspend = DomainEvent(
        EventId("event-invalid-resume"),
        COURSE,
        4,
        "session.resumed",
        1,
        Actor(PrincipalKind.SERVICE, "service"),
        NOW,
        CorrelationId("invalid-resume"),
        lifecycle_payload(),
        SESSION,
    )

    class InvalidOrderEvents:
        def append(self, course_id, expected_sequence, values):  # type: ignore[no-untyped-def]
            raise AssertionError("export must be read-only")

        def read(self, course_id: CourseId, after_sequence: int = 0):  # type: ignore[no-untyped-def]
            return (*stream, resumed_without_suspend)

    with pytest.raises(ExportStateError, match="cannot be replayed"):
        ExportService(InvalidOrderEvents()).assemble(COURSE)
    blobs.close()


def test_unknown_or_corrupt_event_fails_closed(tmp_path: Path) -> None:
    _blobs, events, _, _ = _stack(tmp_path)
    stream = tuple(events.read(COURSE))

    class CorruptEvents:
        def append(self, course_id, expected_sequence, values):  # type: ignore[no-untyped-def]
            raise AssertionError("export must be read-only")

        def read(self, course_id: CourseId, after_sequence: int = 0):  # type: ignore[no-untyped-def]
            bad = DomainEvent(
                EventId("event-corrupt"),
                COURSE,
                2,
                "source.revision_ingested",
                1,
                Actor(PrincipalKind.SERVICE, "principal"),
                NOW,
                CorrelationId("corrupt"),
                {**stream[1].payload, "endpoint": "https://secret.example"},
            )
            return (stream[0], bad, *stream[2:])

    with pytest.raises(ExportStateError, match="invalid canonical event"):
        ExportService(CorruptEvents()).assemble(COURSE)

    class UnknownEvents(CorruptEvents):
        def read(self, course_id: CourseId, after_sequence: int = 0):  # type: ignore[no-untyped-def]
            unknown = DomainEvent(
                EventId("event-unknown"),
                COURSE,
                2,
                "provider.trace_recorded",
                1,
                Actor(PrincipalKind.SERVICE, "principal"),
                NOW,
                CorrelationId("unknown"),
                {"credential": "secret"},
            )
            return (stream[0], unknown, *stream[2:])

    with pytest.raises(ExportStateError, match="not allowlisted"):
        ExportService(UnknownEvents()).assemble(COURSE)
    _blobs.close()


def test_corrupt_cardine_policy_event_fails_as_closed_export_error(tmp_path: Path) -> None:
    blobs, events, _, _ = _stack(tmp_path)
    stream = tuple(events.read(COURSE))

    class CorruptPolicyEvents:
        def append(self, course_id, expected_sequence, values):  # type: ignore[no-untyped-def]
            raise AssertionError("export must be read-only")

        def read(self, course_id: CourseId, after_sequence: int = 0):  # type: ignore[no-untyped-def]
            corrupt = DomainEvent(
                EventId("event-corrupt-policy"),
                COURSE,
                4,
                PROVIDER_CONSENT_GRANTED,
                1,
                Actor(PrincipalKind.HUMAN, "principal"),
                NOW,
                CorrelationId("corrupt-policy"),
                {"unexpected": "secret-must-not-escape"},
            )
            return (*stream, corrupt)

    with pytest.raises(
        ExportStateError,
        match=r"^invalid canonical event: cardine\.provider_consent_granted@1$",
    ):
        ExportService(CorruptPolicyEvents()).assemble(COURSE)

    blobs.close()


@pytest.mark.parametrize("version", tuple(ExportVersion))
@pytest.mark.parametrize(
    "policy_events",
    (
        (_policy_event(PROVIDER_CONSENT_REVOKED, 4, "revoke-without-grant"),),
        (
            _policy_event(PROVIDER_CONSENT_GRANTED, 4, "reused-request"),
            _policy_event(PROVIDER_CONSENT_REVOKED, 5, "reused-request"),
        ),
        (
            _policy_event(
                SOURCE_RETIRED,
                4,
                "retire-foreign-source",
                source_id=SourceId("source-from-another-course"),
            ),
        ),
        (
            _policy_event(
                SOURCE_RESTORED,
                4,
                "restore-without-retire",
                source_id=SourceId("source-aortic"),
            ),
        ),
        (
            _policy_event(PROVIDER_CONSENT_GRANTED, 4, "cross-family-request"),
            _policy_event(
                SOURCE_RETIRED,
                5,
                "cross-family-request",
                source_id=SourceId("source-aortic"),
            ),
        ),
    ),
)
def test_export_rejects_forged_policy_histories_in_every_version(
    tmp_path: Path,
    version: ExportVersion,
    policy_events: tuple[DomainEvent, ...],
) -> None:
    blobs, events, _, _ = _stack(tmp_path)
    stream = tuple(events.read(COURSE))

    class ForgedPolicyEvents:
        def append(self, course_id, expected_sequence, values):  # type: ignore[no-untyped-def]
            raise AssertionError("export must be read-only")

        def read(self, course_id: CourseId, after_sequence: int = 0):  # type: ignore[no-untyped-def]
            return (*stream, *policy_events)

    with pytest.raises(ExportStateError):
        ExportService(ForgedPolicyEvents()).assemble(COURSE, version=version)

    blobs.close()


def test_policy_export_redacts_historical_request_derived_correlation(tmp_path: Path) -> None:
    blobs, events, _, _ = _stack(tmp_path)
    stream = tuple(events.read(COURSE))
    secret = "SECRET-IDEMPOTENCY-TOKEN"
    granted = _policy_event(
        PROVIDER_CONSENT_GRANTED,
        4,
        "grant-policy",
        correlation_id=f"cardine-browser-policy-{secret}",
    )

    class HistoricalPolicyEvents:
        def append(self, course_id, expected_sequence, values):  # type: ignore[no-untyped-def]
            raise AssertionError("export must be read-only")

        def read(self, course_id: CourseId, after_sequence: int = 0):  # type: ignore[no-untyped-def]
            return (*stream, granted)

    bundle = ExportService(HistoricalPolicyEvents()).assemble(COURSE)
    destination = tmp_path / "policy-export"
    FilesystemExportWriter().write(bundle, destination)

    assert bundle.events[-1]["correlation_id"] == "redacted-cardine-policy"
    assert secret not in repr(bundle)
    assert secret.encode() not in b"".join(_files(destination).values())
    blobs.close()
    blobs.close()


def test_writer_refuses_overwrite_and_cleans_partial_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blobs, events, _, _ = _stack(tmp_path)
    bundle = ExportService(events).assemble(COURSE)
    destination = tmp_path / "export"
    destination.mkdir()
    sentinel = destination / "keep.txt"
    sentinel.write_text("keep")

    with pytest.raises(ExportDestinationExistsError):
        FilesystemExportWriter().write(bundle, destination)
    assert sentinel.read_text() == "keep"

    failure_target = tmp_path / "failure"
    real_write = export_adapter._write_file
    calls = 0

    def fail_second(path: Path, content: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated write failure")
        real_write(path, content)

    monkeypatch.setattr(export_adapter, "_write_file", fail_second)
    with pytest.raises(OSError, match="simulated write failure"):
        FilesystemExportWriter().write(bundle, failure_target)
    assert not failure_target.exists()
    assert not tuple(tmp_path.glob(".failure.staging-*"))
    blobs.close()


def test_publish_race_cannot_replace_concurrently_created_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blobs, events, _, _ = _stack(tmp_path)
    bundle = ExportService(events).assemble(COURSE)
    destination = tmp_path / "raced"
    real_publish = export_adapter._rename_no_replace

    def race(source: Path, target: Path) -> None:
        target.mkdir()
        (target / "winner.txt").write_text("winner")
        real_publish(source, target)

    monkeypatch.setattr(export_adapter, "_rename_no_replace", race)
    with pytest.raises(ExportDestinationExistsError):
        FilesystemExportWriter().write(bundle, destination)

    assert (destination / "winner.txt").read_text() == "winner"
    assert set(path.name for path in destination.iterdir()) == {"winner.txt"}
    assert not tuple(tmp_path.glob(".raced.staging-*"))
    blobs.close()
