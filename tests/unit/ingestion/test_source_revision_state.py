from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from study_agent.domain import (
    Actor,
    BlobId,
    BlobRef,
    CorrelationId,
    CourseId,
    DomainEvent,
    EventId,
    PrincipalKind,
    RevisionId,
    SourceDocument,
    SourceId,
    SourceKind,
    StructureOrigin,
)
from study_agent.ingestion import (
    CHUNK_MAX_CHARACTERS,
    CHUNKER_POLICY_VERSION,
    NORMALIZATION_POLICY_VERSION,
    SOURCE_REVISION_INGESTED,
    SOURCE_REVISION_SCHEMA_VERSION,
    SOURCE_REVISION_SELECTED,
    SOURCE_REVISION_SELECTED_SCHEMA_VERSION,
    ChunkingConfig,
    chunk_text,
    decode_source_revision_event,
    decode_source_revision_selected_event,
    normalize_utf8,
    register_source_revision_events,
    revision_id_for,
    source_event_id_for,
    source_revision_payload,
    source_revision_selected_event_id_for,
    source_revision_selected_payload,
)
from study_agent.ingestion.identity import legacy_revision_id_for
from study_agent.state import EventRegistry, PayloadValidationError, Projection, apply_event

type BlobLoader = Callable[[BlobRef], bytes]


def _blob(content: bytes) -> BlobRef:
    digest = sha256(content).hexdigest()
    return BlobRef(BlobId(f"sha256:{digest}"), digest, len(content))


def make_event(
    *,
    sequence: int = 1,
    original: bytes = "Cafe\u0301 🫀 valve".encode(),
    legacy_identity: bool = False,
) -> tuple[DomainEvent, BlobLoader]:
    normalized_text = normalize_utf8(original).text
    normalized = normalized_text.encode()
    original_blob = _blob(original)
    normalized_blob = _blob(normalized)
    source_id = SourceId("source-1")
    revision_id = revision_id_for(
        original_sha256=original_blob.checksum_sha256,
        source_id=source_id,
        kind=SourceKind.TEXT,
        title="Cardiac anatomy",
        trust_level=90,
        source_role="primary",
        normalization_version=NORMALIZATION_POLICY_VERSION,
        chunker_version=CHUNKER_POLICY_VERSION,
        max_characters=CHUNK_MAX_CHARACTERS,
    )
    if legacy_identity:
        revision_id = legacy_revision_id_for(
            original_sha256=original_blob.checksum_sha256,
            source_id=source_id,
            kind=SourceKind.TEXT,
            normalization_version=NORMALIZATION_POLICY_VERSION,
            chunker_version=CHUNKER_POLICY_VERSION,
            max_characters=CHUNK_MAX_CHARACTERS,
        )
    occurred_at = datetime(2026, 7, 11, 8, sequence, tzinfo=UTC)
    source = SourceDocument(
        source_id,
        revision_id,
        SourceKind.TEXT,
        "Cardiac anatomy",
        "text/plain",
        original_blob.checksum_sha256,
        original_blob.byte_length,
        occurred_at,
        90,
        "primary",
        original_blob,
        normalized_blob,
        NORMALIZATION_POLICY_VERSION,
        len(normalized_text),
        StructureOrigin.MECHANICALLY_EXTRACTED,
        "utf8-text-v1",
    )
    chunks = chunk_text(
        normalized_text,
        source_id=source_id,
        revision_id=revision_id,
        kind=SourceKind.TEXT,
        config=ChunkingConfig(
            max_characters=CHUNK_MAX_CHARACTERS,
            version=CHUNKER_POLICY_VERSION,
        ),
    )
    event = DomainEvent(
        source_event_id_for(CourseId("course-1"), revision_id),
        CourseId("course-1"),
        sequence,
        SOURCE_REVISION_INGESTED,
        SOURCE_REVISION_SCHEMA_VERSION,
        Actor(PrincipalKind.SERVICE, "ingestion"),
        occurred_at,
        CorrelationId("correlation-1"),
        source_revision_payload(
            source,
            chunks,
        ),
    )
    contents = {str(original_blob.id): original, str(normalized_blob.id): normalized}
    return event, lambda ref: contents[str(ref.id)]


def _replace_source(event: DomainEvent, **updates: object) -> DomainEvent:
    source = dict(event.payload["source"])  # type: ignore[arg-type]
    source.update(updates)  # type: ignore[arg-type]
    return DomainEvent(
        event.event_id,
        event.course_id,
        event.course_sequence,
        event.event_type,
        event.schema_version,
        event.actor,
        event.occurred_at,
        event.correlation_id,
        {**event.payload, "source": source},
    )


def test_full_event_decoder_verifies_utf8_nfc_content_spans_and_identities() -> None:
    event, load_blob = make_event()
    decoded = decode_source_revision_event(event, load_blob)

    assert decoded.normalized_character_length == len("Café 🫀 valve")
    assert decoded.source.created_at == event.occurred_at
    assert len(decoded.chunks) == 1


def test_full_event_decoder_preserves_legacy_revision_identity() -> None:
    event, load_blob = make_event(legacy_identity=True)
    decoded = decode_source_revision_event(event, load_blob)
    registry = EventRegistry()
    register_source_revision_events(registry, load_blob)
    source = event.payload.get("source")
    assert isinstance(source, Mapping)
    raw_revision_id = source.get("revision_id")
    assert isinstance(raw_revision_id, str)

    assert str(decoded.source.revision_id) == raw_revision_id
    assert registry.decode(event) == decoded


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        (lambda event: _replace_source(event, revision_id="revision-arbitrary"), "belong"),
        (
            lambda event: DomainEvent(
                EventId("event-arbitrary"),
                event.course_id,
                event.course_sequence,
                event.event_type,
                event.schema_version,
                event.actor,
                event.occurred_at,
                event.correlation_id,
                event.payload,
            ),
            "event_id",
        ),
        (
            lambda event: DomainEvent(
                event.event_id,
                event.course_id,
                event.course_sequence,
                event.event_type,
                event.schema_version,
                event.actor,
                event.occurred_at,
                event.correlation_id,
                {
                    **event.payload,
                    "chunking": {"version": "arbitrary", "max_characters": 1200},
                },
            ),
            "version",
        ),
        (
            lambda event: _replace_source(
                event,
                created_at=(event.occurred_at + timedelta(seconds=1)).isoformat(),
            ),
            "created_at",
        ),
    ],
)
def test_envelope_decoder_rejects_arbitrary_ids_versions_and_timestamps(
    tamper: Callable[[DomainEvent], DomainEvent], message: str
) -> None:
    event, load_blob = make_event()
    registry = EventRegistry()
    register_source_revision_events(registry, load_blob)

    with pytest.raises(PayloadValidationError, match=message):
        registry.decode(tamper(event))


def test_identity_reuses_verified_digest_without_skipping_original_integrity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import study_agent.ingestion.events as events

    original = "Cafe\u0301 🫀 valve".encode()
    event, load_blob = make_event(original=original)
    digest = sha256
    original_hashes = 0

    def counted(data: bytes = b"") -> object:
        nonlocal original_hashes
        if data == original:
            original_hashes += 1
        return digest(data)

    monkeypatch.setattr(events, "sha256", counted)
    decoded = decode_source_revision_event(event, load_blob)
    assert original_hashes == 1, "identity recomputed the already verified original digest"
    # A later same-length edit still has to read and hash the actual bytes.
    with pytest.raises(ValueError, match="checksum does not match loaded"):
        decode_source_revision_event(
            event,
            lambda ref: b"x" * ref.byte_length
            if ref == decoded.source.blob else load_blob(ref),
        )


def test_decoder_rejects_corrupt_normalized_blob_and_chunk_checksum() -> None:
    event, load_blob = make_event()
    decoded = decode_source_revision_event(event, load_blob)

    with pytest.raises(ValueError, match="checksum does not match loaded"):
        decode_source_revision_event(
            event,
            lambda ref: b"x" * ref.byte_length
            if ref == decoded.source.normalized_blob
            else load_blob(ref),
        )

    chunks = list(event.payload["chunks"])  # type: ignore[arg-type]
    first = dict(chunks[0])  # type: ignore[arg-type]
    first["checksum_sha256"] = "0" * 64
    chunks[0] = first
    tampered = DomainEvent(
        event.event_id,
        event.course_id,
        event.course_sequence,
        event.event_type,
        event.schema_version,
        event.actor,
        event.occurred_at,
        event.correlation_id,
        {**event.payload, "chunks": tuple(chunks)},
    )
    with pytest.raises(ValueError, match="checksum"):
        decode_source_revision_event(tampered, load_blob)

    first["checksum_sha256"] = decoded.chunks[0].checksum_sha256
    first["chunk_id"] = "chunk-arbitrary"
    chunks[0] = first
    arbitrary_chunk_id = DomainEvent(
        event.event_id,
        event.course_id,
        event.course_sequence,
        event.event_type,
        event.schema_version,
        event.actor,
        event.occurred_at,
        event.correlation_id,
        {**event.payload, "chunks": tuple(chunks)},
    )
    with pytest.raises(ValueError, match="chunk_id"):
        decode_source_revision_event(arbitrary_chunk_id, load_blob)


def test_reducer_preserves_prior_revisions_and_persisted_configuration() -> None:
    first, first_loader = make_event()
    second, second_loader = make_event(sequence=2, original="Changed 🫀 valve".encode())
    registry = EventRegistry()
    loaders: tuple[BlobLoader, ...] = (first_loader, second_loader)

    def load(ref: BlobRef) -> bytes:
        for loader in loaders:
            try:
                return loader(ref)
            except KeyError:
                continue
        raise KeyError(str(ref.id))

    register_source_revision_events(registry, load)
    projection = Projection(CourseId("course-1"), state={"unrelated": "preserved"})
    projection = apply_event(projection, first, registry)
    projection = apply_event(projection, second, registry)

    sources = projection.state["sources"]
    assert isinstance(sources, Mapping)
    source_state = sources["source-1"]
    assert isinstance(source_state, Mapping)
    revision_ids = source_state["revision_ids"]
    assert isinstance(revision_ids, tuple) and len(revision_ids) == 2
    first_revision_id = revision_ids[0]
    assert isinstance(first_revision_id, str)
    revisions = source_state["revisions"]
    assert isinstance(revisions, Mapping)
    first_manifest = revisions[first_revision_id]
    assert isinstance(first_manifest, Mapping)
    assert first_manifest["normalized_character_length"] == len("Café 🫀 valve")
    assert first_manifest["chunking"] == {
        "version": CHUNKER_POLICY_VERSION,
        "max_characters": CHUNK_MAX_CHARACTERS,
    }
    assert projection.state["unrelated"] == "preserved"


def test_selection_decoder_and_legacy_projection_append_are_strict_and_compatible() -> None:
    first, load_blob = make_event()
    registry = EventRegistry()
    register_source_revision_events(registry, load_blob)
    projection = apply_event(Projection(first.course_id), first, registry)
    sources = projection.state["sources"]
    assert isinstance(sources, Mapping)
    source_state = sources["source-1"]
    assert isinstance(source_state, Mapping)
    legacy_source_state = dict(source_state)
    legacy_source_state.pop("current_revision_id")
    legacy_projection = Projection(
        first.course_id,
        sequence=projection.sequence,
        state={**projection.state, "sources": {"source-1": legacy_source_state}},
    )
    source = first.payload["source"]
    assert isinstance(source, Mapping)
    revision_id = RevisionId(str(source["revision_id"]))
    selection = DomainEvent(
        source_revision_selected_event_id_for(
            first.course_id, SourceId("source-1"), revision_id, 2
        ),
        first.course_id,
        2,
        SOURCE_REVISION_SELECTED,
        SOURCE_REVISION_SELECTED_SCHEMA_VERSION,
        first.actor,
        first.occurred_at + timedelta(seconds=1),
        first.correlation_id,
        source_revision_selected_payload(SourceId("source-1"), revision_id),
    )

    decoded = decode_source_revision_selected_event(selection)
    selected_projection = apply_event(legacy_projection, selection, registry)
    selected_sources = selected_projection.state["sources"]
    assert isinstance(selected_sources, Mapping)
    selected_source = selected_sources["source-1"]
    assert isinstance(selected_source, Mapping)
    assert decoded.revision_id == revision_id
    assert selected_source["current_revision_id"] == str(revision_id)
    assert selected_source["revision_ids"] == legacy_source_state["revision_ids"]

    forged = DomainEvent(
        EventId("event-arbitrary"),
        selection.course_id,
        selection.course_sequence,
        selection.event_type,
        selection.schema_version,
        selection.actor,
        selection.occurred_at,
        selection.correlation_id,
        selection.payload,
    )
    with pytest.raises(ValueError, match="event_id"):
        decode_source_revision_selected_event(forged)
