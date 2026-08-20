from __future__ import annotations

import pytest

from cardine.materials.generation_contracts import (
    GenerationPipelinePins,
    MaterialGenerationRequest,
    MaterialGenerationStage,
    MaterialGenerationState,
    PinnedTranscriptInput,
)
from study_agent.domain import (
    BlobId,
    BlobRef,
    CourseId,
    RevisionId,
    RunId,
    SessionId,
    SourceId,
    SourceKind,
)


def pin() -> PinnedTranscriptInput:
    blob = BlobRef(BlobId("sha256:" + "a" * 64), "a" * 64, 5)
    return PinnedTranscriptInput(
        CourseId("course-1"),
        SessionId("session-1"),
        SourceId("source-1"),
        RevisionId("revision-1"),
        SourceKind.MARKDOWN,
        "Lesson",
        blob,
        blob,
        5,
        "a" * 64,
        4,
    )


def test_job_id_is_request_stable_but_fingerprint_binds_all_pins() -> None:
    first = MaterialGenerationRequest(pin(), "request-1")
    changed = MaterialGenerationRequest(
        pin(), "request-1", GenerationPipelinePins(validator="validators@2")
    )
    assert first.job_id == changed.job_id
    assert first.fingerprint != changed.fingerprint


def test_checkpoint_codec_is_strict_and_contains_only_blob_refs_and_receipts() -> None:
    state = MaterialGenerationState(
        MaterialGenerationRequest(pin(), "request-1"),
        RunId("run-1"),
        stage=MaterialGenerationStage.BOUNDARIES,
    )
    encoded = state.to_bytes()
    assert MaterialGenerationState.from_bytes(encoded) == state
    assert b"Lecture" not in encoded
    with pytest.raises(ValueError, match="canonical"):
        MaterialGenerationState.from_bytes(encoded + b" ")
