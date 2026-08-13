from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import pytest

from cardine.feedback import (
    CapabilityGapAggregate,
    CapabilityGapObservation,
    CapabilityGapService,
    CapabilityGapWriteContext,
    GapCategory,
    GapExportState,
    GapOutboxBundle,
    GapOutboxCorruptionError,
    GapOutboxExportService,
    GapOutboxPublicationError,
    GapOutboxRecord,
    GapResolutionKind,
    ImpactKind,
    RequestedOperationKind,
    SafeTargetKind,
)
from study_agent.adapters.sqlite.capability_gap_store import SQLiteCapabilityGapStore
from study_agent.state import canonical_json_bytes

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
HARNESS_FINGERPRINT = sha256(
    b"study-agent-gap-outbox-harness-v1\0harness@1"
).hexdigest()


class _Publisher:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.payloads: list[bytes] = []

    def publish(self, payload: bytes) -> None:
        if self.fail:
            raise OSError("publisher unavailable")
        self.payloads.append(payload)


class _DurableThenFails(_Publisher):
    def publish(self, payload: bytes) -> None:
        self.payloads.append(payload)
        raise OSError("ack lost after durable write")


class _MutatingPublisher(_Publisher):
    def __init__(self, mutate: Any) -> None:
        super().__init__()
        self._mutate = mutate

    def publish(self, payload: bytes) -> None:
        self.payloads.append(payload)
        self._mutate()


def _record(tmp_path: Path) -> tuple[SQLiteCapabilityGapStore, GapOutboxRecord]:
    store = SQLiteCapabilityGapStore(tmp_path / "gaps.sqlite3")
    service = CapabilityGapService(store)
    observation = CapabilityGapObservation(
        GapCategory.INPUT_FORMAT,
        RequestedOperationKind.EXTRACT_TEXT,
        SafeTargetKind.PDF,
        ImpactKind.BLOCKED,
    )
    service.record(
        observation,
        CapabilityGapWriteContext("harness@1", "corr@1", "a" * 64, NOW),
    )
    report = service.record(
        observation,
        CapabilityGapWriteContext("harness@1", "corr@1", "a" * 64, NOW),
    )
    aggregate = CapabilityGapAggregate.from_bytes(store.load(report.gap_key.value))
    return store, GapOutboxRecord.from_aggregate(aggregate)


def test_record_and_bundle_are_canonical_and_roundtrip(tmp_path: Path) -> None:
    _store, record = _record(tmp_path)
    bundle = GapOutboxBundle(HARNESS_FINGERPRINT, (record,))
    assert GapOutboxRecord.from_bytes(record.to_bytes()) == record
    assert GapOutboxBundle.from_bytes(bundle.to_bytes()) == bundle
    assert bundle.to_bytes() == (
        b'{"harness_fingerprint":"1070cab09f30adfaefc66426e6286bd39bdf9ebc36fd9b7eefdae5d4ec3853e2","records":[{"dimensions":'
        b'{"category":"input_format","contract_identity_fingerprint":"9f2f3b8e4fe351277062dc161b71515f14bf9e612eee69c35ab5280d80d4451f",'
        b'"contract_major":1,"limitation_code":"missing_capability",'
        b'"requested_operation_kind":"extract_text","safe_target_kind":"pdf",'
        b'"schema_version":1},"export_state":"local","first_seen":'
        b'"2026-07-24T12:00:00.000000Z","gap_key":"08d93ea459d23bc6ba78d484747392bc3045a937f7147d943fad2e1b4a767485",'
        b'"impact_kind":"blocked","key_binding":"9c39dabd722a1cac19f9e99a9dcdf96662aed5db0df6f33450fd9918aa8c08ba","last_seen":"2026-07-24T12:00:00.000000Z",'
        b'"occurrence_count":1,"resolution":"unresolved",'
        b'"resolution_authority_fingerprint":null,"resolved_at":null,"schema_version":2,'
        b'"verification_kind":"unverified_request"}],"schema_version":2}'
    )
    assert bundle.bundle_fingerprint == GapOutboxBundle.from_bytes(
        bundle.to_bytes()
    ).bundle_fingerprint


def test_unknown_fields_tamper_and_key_mismatch_fail_closed(tmp_path: Path) -> None:
    _store, record = _record(tmp_path)
    raw = record.to_json()
    raw["learner_text"] = "secret"
    with pytest.raises(GapOutboxCorruptionError):
        GapOutboxRecord.from_bytes(canonical_json_bytes(cast(Any, raw)))
    tampered = record.to_json()
    tampered["gap_key"] = "b" * 64
    with pytest.raises(RuntimeError):
        GapOutboxRecord.from_bytes(canonical_json_bytes(cast(Any, tampered)))
    with pytest.raises(GapOutboxCorruptionError):
        GapOutboxRecord.from_bytes(record.to_bytes().replace(b",", b", ", 1))


def test_export_is_explicit_byte_stable_and_retains_source(tmp_path: Path) -> None:
    store, record = _record(tmp_path)
    publisher = _Publisher()
    export = GapOutboxExportService(store, publisher, harness_version="harness@1")
    first = export.export_pending()
    assert first.payload == publisher.payloads[0]
    assert first.bundle.records[0].gap_key == record.gap_key
    assert store.load(record.gap_key.value)
    assert store.list_aggregates(states={GapExportState.EXPORTED})
    assert export.snapshot().records == ()
    exported_record = CapabilityGapAggregate.from_bytes(store.load(record.gap_key.value))
    assert export.snapshot(include_exported=True).to_bytes() == GapOutboxBundle(
        HARNESS_FINGERPRINT, (GapOutboxRecord.from_aggregate(exported_record),)
    ).to_bytes()


def test_publish_failure_never_claims_exported(tmp_path: Path) -> None:
    store, _record_value = _record(tmp_path)
    export = GapOutboxExportService(
        store, _Publisher(fail=True), harness_version="harness@1"
    )
    with pytest.raises(GapOutboxPublicationError):
        export.export_pending()
    assert store.list_aggregates(states={GapExportState.FAILED})
    assert not store.list_aggregates(states={GapExportState.EXPORTED})


def test_retry_after_lost_ack_reuses_exact_snapshot(tmp_path: Path) -> None:
    store, _record_value = _record(tmp_path)
    first_publisher = _DurableThenFails()
    first_export = GapOutboxExportService(
        store, first_publisher, harness_version="harness@1"
    )
    with pytest.raises(GapOutboxPublicationError):
        first_export.export_pending()
    second_publisher = _Publisher()
    second_export = GapOutboxExportService(
        SQLiteCapabilityGapStore(tmp_path / "gaps.sqlite3"),
        second_publisher,
        harness_version="harness@1",
    )
    second_export.export_pending()
    assert second_publisher.payloads == first_publisher.payloads


def test_outbox_bytes_redact_identity_and_harness_version(tmp_path: Path) -> None:
    store, record = _record(tmp_path)
    payload = GapOutboxBundle(HARNESS_FINGERPRINT, (record,)).to_bytes()
    assert b"unverified-request@1" not in payload
    assert b"harness@1" not in payload
    assert b"relevant_contract_identity" not in payload
    assert b"harness_version" not in payload
    assert b"contract_identity_fingerprint" in payload
    assert store.load(record.gap_key.value)


def test_new_report_after_export_requeues_but_exact_retry_does_not(tmp_path: Path) -> None:
    store, record = _record(tmp_path)
    export = GapOutboxExportService(store, _Publisher(), harness_version="harness@1")
    export.export_pending()
    service = CapabilityGapService(store)
    observation = CapabilityGapObservation(
        GapCategory.INPUT_FORMAT,
        RequestedOperationKind.EXTRACT_TEXT,
        SafeTargetKind.PDF,
        ImpactKind.BLOCKED,
    )
    exact = service.record(
        observation,
        CapabilityGapWriteContext("harness@1", "corr@1", "a" * 64, NOW),
    )
    assert exact.occurrence_count == 1
    assert (
        CapabilityGapAggregate.from_bytes(store.load(record.gap_key.value)).export_state
        is GapExportState.EXPORTED
    )
    service.record(
        observation,
        CapabilityGapWriteContext("harness@1", "corr@1", "b" * 64, NOW),
    )
    updated = CapabilityGapAggregate.from_bytes(store.load(record.gap_key.value))
    assert updated.occurrence_count == 2
    assert updated.export_state is GapExportState.PENDING


def test_resolution_after_export_requeues(tmp_path: Path) -> None:
    store, record = _record(tmp_path)
    export = GapOutboxExportService(store, _Publisher(), harness_version="harness@1")
    export.export_pending()
    service = CapabilityGapService(store)
    service.resolve(record.gap_key, GapResolutionKind.ACCEPTED, "c" * 64, NOW)
    updated = CapabilityGapAggregate.from_bytes(store.load(record.gap_key.value))
    assert updated.export_state is GapExportState.PENDING


def test_claim_and_finalize_compare_and_swap_leaves_changed_row_pending(tmp_path: Path) -> None:
    store, record = _record(tmp_path)
    expected = store.claim_export_batch()
    assert len(expected) == 1
    service = CapabilityGapService(store)
    observation = CapabilityGapObservation(
        GapCategory.INPUT_FORMAT,
        RequestedOperationKind.EXTRACT_TEXT,
        SafeTargetKind.PDF,
        ImpactKind.BLOCKED,
    )
    service.record(
        observation,
        CapabilityGapWriteContext("harness@1", "corr@1", "b" * 64, NOW),
    )
    key = record.gap_key.value
    assert store.finalize_export_batch({key: expected[0]}, GapExportState.EXPORTED) == ()
    current = CapabilityGapAggregate.from_bytes(store.load(key))
    assert current.export_state is GapExportState.PENDING
    assert current.occurrence_count == 2


def test_new_key_after_claim_is_not_published_or_finalized(tmp_path: Path) -> None:
    store, record = _record(tmp_path)
    expected = store.claim_export_batch()
    service = CapabilityGapService(store)
    service.record(
        CapabilityGapObservation(
            GapCategory.OUTPUT_FORMAT,
            RequestedOperationKind.GENERATE_STUDY_ARTIFACT,
            SafeTargetKind.STUDY_ARTIFACT,
            ImpactKind.DEGRADED,
        ),
        CapabilityGapWriteContext("harness@1", "corr@1", "b" * 64, NOW),
    )
    finalized = store.finalize_export_batch(
        {record.gap_key.value: expected[0]}, GapExportState.EXPORTED
    )
    assert finalized == (record.gap_key.value,)
    aggregates = store.list_aggregates(states={GapExportState.LOCAL})
    assert len(aggregates) == 1


def test_export_barrier_update_is_cas_bound_and_retries_fresh_bytes(tmp_path: Path) -> None:
    store, record = _record(tmp_path)
    service = CapabilityGapService(store)
    observation = CapabilityGapObservation(
        GapCategory.INPUT_FORMAT,
        RequestedOperationKind.EXTRACT_TEXT,
        SafeTargetKind.PDF,
        ImpactKind.BLOCKED,
    )

    def update_existing() -> None:
        service.record(
            observation,
            CapabilityGapWriteContext("harness@1", "corr@1", "b" * 64, NOW),
        )

    publisher = _MutatingPublisher(update_existing)
    export = GapOutboxExportService(store, publisher, harness_version="harness@1")
    with pytest.raises(GapOutboxPublicationError, match="snapshot_changed"):
        export.export_pending()
    aggregate = CapabilityGapAggregate.from_bytes(store.load(record.gap_key.value))
    assert aggregate.export_state is GapExportState.PENDING
    assert aggregate.occurrence_count == 2


def test_export_barrier_new_key_is_excluded_from_exact_batch(tmp_path: Path) -> None:
    store, record = _record(tmp_path)
    service = CapabilityGapService(store)

    def add_new_key() -> None:
        service.record(
            CapabilityGapObservation(
                GapCategory.OUTPUT_FORMAT,
                RequestedOperationKind.GENERATE_STUDY_ARTIFACT,
                SafeTargetKind.STUDY_ARTIFACT,
                ImpactKind.DEGRADED,
            ),
            CapabilityGapWriteContext("harness@1", "corr@1", "b" * 64, NOW),
        )

    publisher = _MutatingPublisher(add_new_key)
    export = GapOutboxExportService(store, publisher, harness_version="harness@1")
    publication = export.export_pending()
    assert tuple(item.gap_key.value for item in publication.bundle.records) == (
        record.gap_key.value,
    )
    assert len(store.list_aggregates(states={GapExportState.LOCAL})) == 1
