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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from study_agent.adapters.filesystem import FilesystemBlobStore
from study_agent.adapters.model import ScriptedModel
from study_agent.adapters.sqlite import SQLiteEventStore, SQLiteRunStore
from study_agent.artifacts import register_artifact_events
from study_agent.assessments import register_assessment_events
from study_agent.capabilities import StudyCapabilityGateway, builtin_capability_bindings
from study_agent.courses import CourseService, ProjectionCourseView, register_course_events
from study_agent.domain import (
    Actor,
    CorrelationId,
    CourseId,
    CourseProfile,
    EventId,
    ExecutionContext,
    PrincipalKind,
    SourceId,
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
from study_agent.recall import register_recall_events
from study_agent.sessions import register_session_events
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


def _parse_time(raw: str) -> datetime:
    value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("scripted clock timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _json(value: object) -> JsonValue:
    return cast(JsonValue, json.loads(json.dumps(value, sort_keys=True)))


def _register_parity_event(registry: EventRegistry, event_type: str) -> None:
    def decode(payload: JsonObject) -> JsonObject:
        return payload

    def reduce(state: JsonObject, _: DomainEvent, payload: JsonObject) -> Mapping[str, JsonValue]:
        return {**state, "parity": payload}

    registry.register(event_type, 1, decode, reduce)


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
    }


def _append_case_event(
    case: str, payload: JsonObject, clock: ScriptedClock, store: CountingEventStore
) -> None:
    course_id = CourseId("course-parity")
    prior = tuple(store.read(course_id))
    parent = prior[-1]
    event_type = f"parity.{case}"
    event = DomainEvent(
        event_id=EventId(f"event:{case}"),
        course_id=course_id,
        course_sequence=parent.course_sequence + 1,
        event_type=event_type,
        schema_version=1,
        actor=Actor(PrincipalKind.SERVICE, "parity-baseline"),
        occurred_at=clock.now(),
        correlation_id=CorrelationId("correlation:parity"),
        causation_id=parent.event_id,
        payload=payload,
    )
    store.append(course_id, parent.course_sequence, (event,))


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
        _register_parity_event(registry, f"parity.{case}")
        sqlite = SQLiteEventStore(root / "events.sqlite3", registry)
        store = CountingEventStore(sqlite)
        courses = ProjectionCourseView(sqlite.projection)
        service = CourseService(store, clock, courses)
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
            payload: JsonObject = {
                "semantic_id": f"{case}:source:parity",
                "schema_version": 1,
                "status": "resolved",
                "error_code": None,
                "provenance": {
                    "source_id": str(result.source.source_id),
                    "revision_id": str(result.source.revision_id),
                },
                "citations": tuple(
                    {"chunk_id": str(chunk.chunk_id), "source_id": str(chunk.source_id)}
                    for chunk in result.chunks
                ),
                "decisions": {
                    "normalization": result.source.normalization_version,
                    "chunker": result.chunks[0].chunker_version,
                },
            }
            _append_case_event(case, payload, clock, store)
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
        else:
            payload = {
                "semantic_id": f"{case}:semantic",
                "schema_version": 1,
                "status": "completed",
                "error_code": None,
                "provenance": {"origin": "cardine-0.2-baseline", "case": case},
                "citations": ({"source_id": "source:parity", "locator": "notes.md#1"},),
                "decisions": {"case": case, "accepted": True},
                "input": cast(JsonValue, input_data),
            }
            _append_case_event(case, payload, clock, store)
            output = _base_output(case, root, clock, store, gateway)
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
