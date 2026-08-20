from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import cast

import pytest

from cardine.materials import (
    MaterialGenerationConflict,
    MaterialGenerationService,
    MaterialGenerationStage,
    MaterialGenerationStale,
    MaterialVerifiedBatchAdapter,
    PinnedTranscriptInput,
    VerifiedBatchError,
)
from cardine.materials.generation_contracts import (
    MaterialGenerationState,
    StageReceipt,
    limitations_fingerprint,
)
from cardine.materials.planning import UnitManifest
from study_agent.artifacts import GeneratedArtifactProvenance, LessonMaterialContent
from study_agent.domain import (
    BlobId,
    BlobRef,
    ChunkId,
    CorrelationId,
    CourseId,
    ExecutionContext,
    PrincipalKind,
    RevisionId,
    RunId,
    SessionId,
    SourceCommitment,
    SourceId,
    SourceKind,
)
from study_agent.domain._validation import JsonValue
from study_agent.ports import EventStore
from study_agent.ports.artifact import ArtifactCommandPort
from study_agent.ports.model import (
    CancellationToken,
    ModelCapabilities,
    ModelError,
    ModelErrorCode,
    ModelFinishReason,
    ModelInvocation,
    ModelRequest,
    ModelResponse,
    ModelStreamEvent,
    ModelUsage,
)


class MemoryBlobStore:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    def put(self, content: bytes) -> BlobRef:
        digest = sha256(content).hexdigest()
        ref = BlobRef(BlobId("sha256:" + digest), digest, len(content))
        self.values[digest] = content
        return ref

    def get(self, ref: BlobRef) -> bytes:
        return self.values[ref.checksum_sha256]


class MemoryCheckpointStore:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    def create(self, key: str, payload: bytes) -> bool:
        if key in self.values:
            return False
        self.values[key] = payload
        return True

    def compare_and_set(self, key: str, expected: bytes, replacement: bytes) -> bool:
        if self.values.get(key) != expected:
            return False
        self.values[key] = replacement
        return True

    def load(self, key: str) -> bytes:
        return self.values[key]


class ScriptedMaterialModel:
    def __init__(self, *, two_segments: bool = False) -> None:
        self.calls = 0
        self.two_segments = two_segments
        self.capabilities = ModelCapabilities(structured_output=True)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        invocation = ModelInvocation(
            "openai-gpt-5.6-luna", "1.0.0", "gpt-5.6-luna", f"response-{self.calls}"
        )
        usage = ModelUsage(10, 5)
        if request.structured_output is not None and request.structured_output.name == (
            "material_boundaries_v1"
        ):
            manifest = UnitManifest.from_bytes(
                request.messages[1].content.split("unit_manifest=", 1)[1].encode()
            )
            split = max(1, len(manifest.units) // 2)
            segments: tuple[JsonValue, ...] = (
                (
                    {"start_unit": 0, "end_unit": split, "title": "Part one"},
                    {
                        "start_unit": split,
                        "end_unit": len(manifest.units),
                        "title": "Part two",
                    },
                )
                if self.two_segments and split < len(manifest.units)
                else ({"start_unit": 0, "end_unit": len(manifest.units), "title": "Lesson"},)
            )
            return ModelResponse(
                "",
                usage,
                ModelFinishReason.STOP,
                invocation,
                structured_output={
                    "schema_version": 1,
                    "manifest_fingerprint": manifest.fingerprint,
                    "segments": segments,
                },
            )
        return ModelResponse(
            "",
            usage,
            ModelFinishReason.STOP,
            invocation,
            structured_output={
                "markdown": "# Lesson\nLecture 12 is important; this may be uncertain.",
                "limitations": ("The transcript contains uncertainty; it is preserved.",),
            },
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        raise AssertionError(request)
        yield  # pragma: no cover

    async def cancel(self, token: CancellationToken) -> None:
        raise AssertionError(token)


class FlakyMaterialModel(ScriptedMaterialModel):
    def __init__(self) -> None:
        super().__init__()
        self.fail_once = True

    async def generate(self, request: ModelRequest) -> ModelResponse:
        if self.fail_once:
            self.fail_once = False
            raise ModelError(ModelErrorCode.TIMEOUT, "bounded timeout", retryable=True)
        return await super().generate(request)


class AlwaysTimeoutMaterialModel(ScriptedMaterialModel):
    async def generate(self, request: ModelRequest) -> ModelResponse:
        del request
        self.calls += 1
        raise ModelError(ModelErrorCode.TIMEOUT, "bounded timeout", retryable=True)


@dataclass(frozen=True)
class FakeSnapshot:
    sequence: int
    batches: tuple[object, ...]


class RecordingArtifactCommand:
    def __init__(self, adapter: MaterialVerifiedBatchAdapter) -> None:
        self.adapter = adapter
        self.calls = 0
        self.proposals = 0

    def record_generated(
        self, run_id: RunId, context: ExecutionContext, expected_sequence: int
    ) -> FakeSnapshot:
        self.calls += 1
        batch = self.adapter.recover(run_id, context)
        self.proposals += len(batch.proposals)
        return FakeSnapshot(
            expected_sequence + 1,
            (
                type(
                    "Batch",
                    (),
                    {"run_id": run_id, "id": "batch-1", "revision_ids": ("a", "b")},
                )(),
            ),
        )


class MemoryEvents:
    def read(self, course_id: CourseId, after_sequence: int = 0) -> tuple[object, ...]:
        del course_id, after_sequence
        return ()


def _pin(blobs: MemoryBlobStore) -> PinnedTranscriptInput:
    return _pin_text(blobs, "Lecture 12 is important; this may be uncertain.")


def _pin_text(blobs: MemoryBlobStore, text: str) -> PinnedTranscriptInput:
    source = blobs.put(text.encode())
    return PinnedTranscriptInput(
        CourseId("course-1"),
        SessionId("session-1"),
        SourceId("source-1"),
        RevisionId("revision-1"),
        SourceKind.MARKDOWN,
        "Lesson",
        source,
        source,
        len(text),
        source.checksum_sha256,
        3,
    )


def _commitments(pin: PinnedTranscriptInput) -> tuple[SourceCommitment, ...]:
    return (
        SourceCommitment(
            pin.source_id,
            pin.revision_id,
            ChunkId("chunk-1"),
            0,
            pin.normalized_character_length,
        ),
    )


def _context() -> ExecutionContext:
    return ExecutionContext(
        PrincipalKind.SERVICE,
        "materials-service",
        CourseId("course-1"),
        CorrelationId("corr-1"),
        session_id=SessionId("session-1"),
    )


def _service(
    blobs: MemoryBlobStore,
    store: MemoryCheckpointStore,
    model: ScriptedMaterialModel,
    *,
    preflight: Callable[[PinnedTranscriptInput, ExecutionContext, str], None] | None = None,
) -> tuple[MaterialGenerationService, RecordingArtifactCommand]:
    def load_state(run_id: RunId) -> MaterialGenerationState:
        return MaterialGenerationState.from_bytes(store.load(str(run_id)))

    adapter = MaterialVerifiedBatchAdapter(
        blobs=blobs,
        load_state=load_state,
        source_commitments=lambda pin: _commitments(pin),
    )
    command = RecordingArtifactCommand(adapter)
    canonical_preflight = preflight or (lambda pin, context, stage: None)
    service = MaterialGenerationService(
        model=model,
        blobs=blobs,
        store=store,
        preflight=canonical_preflight,
        verified_batch=adapter,
        artifact_command=cast(ArtifactCommandPort, command),
        events=cast(EventStore, MemoryEvents()),
    )
    return service, command


def test_workflow_restarts_without_duplicate_committed_provider_stages() -> None:
    blobs = MemoryBlobStore()
    store = MemoryCheckpointStore()
    model = ScriptedMaterialModel()
    service, command = _service(blobs, store, model)
    first = service.request_pair(
        CourseId("course-1"), SessionId("session-1"), _pin(blobs), "request-1"
    )
    halfway = asyncio.run(service.reconcile(first.job_id, bounded_budget=2, context=_context()))
    assert halfway.stage is MaterialGenerationStage.COMPLETE_SEGMENT
    assert model.calls == 2
    resumed = asyncio.run(service.reconcile(first.job_id, bounded_budget=8, context=_context()))
    assert resumed.stage is MaterialGenerationStage.PROPOSED
    assert model.calls == 4
    assert command.calls == 1
    assert service.get(first.job_id, _context()).request_fingerprint == first.request_fingerprint


def test_fresh_service_resumes_each_checkpoint_in_a_two_segment_run() -> None:
    blobs = MemoryBlobStore()
    store = MemoryCheckpointStore()
    model = ScriptedMaterialModel(two_segments=True)
    pin = _pin_text(
        blobs,
        "Lecture 12 is important; this may be uncertain.\n"
        "The second paragraph preserves the same lesson context.",
    )
    service, _ = _service(blobs, store, model)
    requested = service.request_pair(
        CourseId("course-1"), SessionId("session-1"), pin, "request-restart"
    )

    observed_calls: list[int] = []
    result = requested
    for _ in range(6):
        restarted, _ = _service(blobs, store, model)
        result = asyncio.run(
            restarted.reconcile(requested.job_id, bounded_budget=1, context=_context())
        )
        observed_calls.append(model.calls)

    assert result.stage is MaterialGenerationStage.PROPOSED
    assert observed_calls == [1, 2, 3, 4, 5, 5]
    state = MaterialGenerationState.from_bytes(store.load(requested.job_id))
    assert len(state.segments) == 2
    assert len(state.receipts) == 5


def test_same_request_returns_same_job_without_provider_work() -> None:
    blobs = MemoryBlobStore()
    store = MemoryCheckpointStore()
    model = ScriptedMaterialModel()
    service, _ = _service(blobs, store, model)
    pin = _pin(blobs)
    first = service.request_pair(CourseId("course-1"), SessionId("session-1"), pin, "request-1")
    second = service.request_pair(CourseId("course-1"), SessionId("session-1"), pin, "request-1")
    assert second == first
    assert model.calls == 0


def test_verified_batch_adapter_recovers_exactly_two_material_proposals() -> None:
    blobs = MemoryBlobStore()
    store = MemoryCheckpointStore()
    model = ScriptedMaterialModel()
    service, command = _service(blobs, store, model)
    first = service.request_pair(
        CourseId("course-1"), SessionId("session-1"), _pin(blobs), "request-1"
    )
    result = asyncio.run(service.reconcile(first.job_id, context=_context()))
    assert result.stage is MaterialGenerationStage.PROPOSED
    state = MaterialGenerationState.from_bytes(store.load(first.job_id))
    batch = command.adapter.recover(state.run_id, _context())
    assert len(batch.proposals) == 2
    complete_content = batch.proposals[0].content.content
    study_content = batch.proposals[1].content.content
    complete_provenance = batch.proposals[0].provenance
    study_provenance = batch.proposals[1].provenance
    assert isinstance(complete_content, LessonMaterialContent)
    assert isinstance(study_content, LessonMaterialContent)
    assert [complete_content.variant.value, study_content.variant.value] == ["complete", "study"]
    assert isinstance(complete_provenance, GeneratedArtifactProvenance)
    assert complete_provenance.model is not None
    assert complete_provenance.model.usage is not None
    assert complete_provenance.model.usage.input_tokens == 30
    assert isinstance(study_provenance, GeneratedArtifactProvenance)
    assert study_provenance.model is not None
    assert study_provenance.model.usage is not None
    assert study_provenance.model.usage.input_tokens == 10
    with pytest.raises(VerifiedBatchError):
        command.adapter.recover(
            state.run_id,
            ExecutionContext(
                PrincipalKind.SERVICE,
                "materials-service",
                CourseId("course-1"),
                CorrelationId("corr-no-session"),
                session_id=None,
            ),
        )


def test_retryable_provider_failure_resumes_the_same_stage() -> None:
    blobs = MemoryBlobStore()
    store = MemoryCheckpointStore()
    model = FlakyMaterialModel()
    service, _ = _service(blobs, store, model)
    first = service.request_pair(
        CourseId("course-1"), SessionId("session-1"), _pin(blobs), "request-1"
    )
    retryable = asyncio.run(service.reconcile(first.job_id, bounded_budget=1, context=_context()))
    assert retryable.stage is MaterialGenerationStage.RETRYABLE
    resumed = asyncio.run(service.reconcile(first.job_id, bounded_budget=1, context=_context()))
    assert resumed.stage is MaterialGenerationStage.COMPLETE_SEGMENT
    assert model.calls == 1


def test_job_attempt_ceiling_bounds_repeated_provider_failures() -> None:
    blobs = MemoryBlobStore()
    store = MemoryCheckpointStore()
    model = AlwaysTimeoutMaterialModel()
    service, _ = _service(blobs, store, model)
    first = service.request_pair(
        CourseId("course-1"), SessionId("session-1"), _pin(blobs), "request-attempt-bound"
    )
    result = first
    for _ in range(25):
        result = asyncio.run(service.reconcile(first.job_id, bounded_budget=1, context=_context()))
    assert result.stage is MaterialGenerationStage.FAILED_TERMINAL
    assert model.calls == 24


def test_stale_preflight_makes_zero_provider_calls_and_no_proposal() -> None:
    blobs = MemoryBlobStore()
    store = MemoryCheckpointStore()
    model = ScriptedMaterialModel()

    def stale(_pin: PinnedTranscriptInput, _context: ExecutionContext, _stage: str) -> None:
        raise MaterialGenerationStale("consent is not active")

    service, command = _service(blobs, store, model, preflight=stale)
    first = service.request_pair(
        CourseId("course-1"), SessionId("session-1"), _pin(blobs), "request-1"
    )
    result = asyncio.run(service.reconcile(first.job_id, bounded_budget=8, context=_context()))
    assert result.stage is MaterialGenerationStage.STALE
    assert model.calls == 0
    assert command.calls == 0


def test_get_requires_service_authority_and_exact_session() -> None:
    blobs = MemoryBlobStore()
    store = MemoryCheckpointStore()
    service, _ = _service(blobs, store, ScriptedMaterialModel())
    view = service.request_pair(
        CourseId("course-1"), SessionId("session-1"), _pin(blobs), "request-1"
    )
    with pytest.raises(MaterialGenerationConflict):
        service.get(
            view.job_id,
            ExecutionContext(
                PrincipalKind.HUMAN,
                "human",
                CourseId("course-1"),
                CorrelationId("corr-2"),
                session_id=SessionId("session-1"),
            ),
        )
    with pytest.raises(MaterialGenerationConflict):
        service.get(
            view.job_id,
            ExecutionContext(
                PrincipalKind.SERVICE,
                "materials-service",
                CourseId("course-1"),
                CorrelationId("corr-3"),
                session_id=None,
            ),
        )


def test_stage_receipt_roundtrip_binds_composition_and_ancestry() -> None:
    blob = BlobRef(BlobId("sha256:" + "a" * 64), "a" * 64, 1)
    now = datetime.now(UTC)
    inputs = (blob,)
    fingerprint = StageReceipt.fingerprint_for(
        MaterialGenerationStage.STUDY,
        0,
        1,
        inputs,
        blob,
        "study-from-complete",
        "1",
        "b" * 64,
        "openai-gpt-5.6-luna",
        "1.0.0",
        "gpt-5.6-luna",
        "response-1",
        ModelUsage(1, 2),
        ModelFinishReason.STOP,
        False,
        "c" * 64,
        limitations_fingerprint(()),
        now,
    )
    receipt = StageReceipt(
        MaterialGenerationStage.STUDY,
        0,
        1,
        inputs,
        blob,
        "study-from-complete",
        "1",
        "b" * 64,
        "openai-gpt-5.6-luna",
        "1.0.0",
        "gpt-5.6-luna",
        "response-1",
        ModelUsage(1, 2),
        ModelFinishReason.STOP,
        False,
        "c" * 64,
        limitations_fingerprint(()),
        fingerprint,
        now,
    )
    assert StageReceipt.from_json(receipt.to_json()) == receipt
    raw = dict(receipt.to_json())
    raw["output"] = {"id": str(blob.id), "checksum_sha256": "d" * 64, "byte_length": 1}
    with pytest.raises(ValueError):
        StageReceipt.from_json(raw)
