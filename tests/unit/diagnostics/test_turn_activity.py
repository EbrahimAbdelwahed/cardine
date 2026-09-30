"""Public contracts for process-local live turn activity observations."""

from __future__ import annotations

from collections.abc import Mapping

from cardine.diagnostics.turn_activity import (
    TurnActivityStore,
    add_settled,
    begin_activity,
    finish_activity,
)
from study_agent.domain._validation import JsonObject


def _records(snapshot: JsonObject) -> tuple[JsonObject, ...]:
    records = snapshot["records"]
    assert isinstance(records, tuple)
    assert all(isinstance(record, Mapping) for record in records)
    return tuple(record for record in records if isinstance(record, Mapping))


def test_activity_is_private_deduplicated_and_settles_running_records() -> None:
    store = TurnActivityStore(max_turns=8, max_records_per_turn=8)

    with store.capture("request-1"):
        first = begin_activity(
            kind="retrieval",
            ref="retrieval.search",
            target="Lezione 4 · Emostasi",
            count=8,
        )
        # The same observed fact arriving through two host seams updates one
        # row rather than rendering duplicate chips.
        duplicate = begin_activity(
            kind="retrieval",
            ref="retrieval.search",
            target="Lezione 4 · Emostasi",
            count=8,
        )
        assert duplicate == first
        finish_activity(first, status="done")

        begin_activity(
            kind="capability",
            ref="capability.explain_concept",
            target="",
        )

        snapshot = store.snapshot("request-1")
        assert snapshot["state"] == "running"
        assert len(_records(snapshot)) == 2
        assert _records(snapshot)[0]["status"] == "done"
        assert _records(snapshot)[1]["status"] == "running"
        assert "label" in _records(snapshot)[0]
        assert _records(snapshot)[0]["target"] == "Lezione 4 · Emostasi"
        assert _records(snapshot)[0]["count"] == 8

    settled = store.settle("request-1", status="done")
    assert settled["state"] == "settled"
    assert all(record["status"] == "done" for record in _records(settled))
    assert all(record["ended_at"] for record in _records(settled))
    first_sequence = _records(settled)[0]["sequence"]
    second_sequence = _records(settled)[1]["sequence"]
    assert isinstance(first_sequence, int)
    assert isinstance(second_sequence, int)
    assert second_sequence > first_sequence


def test_contextvar_binds_helpers_to_the_capturing_store_instance() -> None:
    first = TurnActivityStore()
    second = TurnActivityStore()

    with first.capture("same-request"):
        begin_activity(
            kind="retrieval",
            ref="retrieval.search",
            target="First store",
        )
        with second.capture("same-request"):
            begin_activity(
                kind="retrieval",
                ref="retrieval.search",
                target="Second store",
            )
        begin_activity(
            kind="retrieval",
            ref="retrieval.search",
            target="First store again",
        )

    assert [item["target"] for item in _records(first.snapshot("same-request"))] == [
        "First store",
        "First store again",
    ]
    assert [item["target"] for item in _records(second.snapshot("same-request"))] == [
        "Second store",
    ]


def test_record_policy_rejects_model_text_and_uncompiled_labels() -> None:
    store = TurnActivityStore()
    with store.capture("request-policy"):
        try:
            begin_activity(
                kind="retrieval",
                ref="retrieval.search",
                target="<script>prompt and quoted evidence</script>",
                label="model supplied label",
            )
        except (TypeError, ValueError):
            pass
        else:  # pragma: no cover - red until the policy is enforced
            raise AssertionError("uncompiled labels must not enter activity records")

        try:
            begin_activity(
                kind="retrieval",
                ref="retrieval.search",
                target="source_id=abc revision_id=def chunk_id=ghi",
            )
        except ValueError:
            pass
        else:  # pragma: no cover - red until the privacy validator is enforced
            raise AssertionError("canonical identifiers must not enter targets")

        # Canonical titles are trusted host data and may contain ordinary
        # words that also happen to name implementation concepts.
        token = begin_activity(
            kind="retrieval",
            ref="retrieval.lesson",
            target="Query di ricerca e prompt clinici",
        )
        assert token is not None

        for forbidden in ("query=learner-private-text", "prompt=model-private-text"):
            try:
                begin_activity(
                    kind="retrieval",
                    ref="retrieval.lesson",
                    target=forbidden,
                )
            except ValueError:
                pass
            else:  # pragma: no cover - policy regression guard
                raise AssertionError(f"forbidden target accepted: {forbidden}")


def test_ring_retention_and_record_limit_expose_omitted_count() -> None:
    store = TurnActivityStore(max_turns=2, max_records_per_turn=1)
    for request_id in ("old", "middle", "new"):
        with store.capture(request_id):
            begin_activity(
                kind="retrieval",
                ref="retrieval.search",
                target=request_id,
            )
            begin_activity(
                kind="verification",
                ref="verification.answer",
                target=request_id,
            )

    assert store.snapshot("old")["state"] == "unknown"
    current = store.snapshot("new")
    assert current["state"] == "running"
    assert len(_records(current)) == 1
    assert current["omitted"] == 1


def test_settled_records_are_inserted_closed_and_unknown_ids_are_empty() -> None:
    store = TurnActivityStore()
    with store.capture("request-settled"):
        add_settled(
            {
                "kind": "verification",
                "ref": "verification.answer",
                "target": "",
                "status": "done",
                "error_code": None,
            }
        )

    record = _records(store.snapshot("request-settled"))[0]
    assert record["status"] == "done"
    assert record["ended_at"]
    assert record["started_at"] == record["ended_at"]

    unknown = store.snapshot("not-captured")
    assert unknown["state"] == "unknown"
    assert unknown["records"] == ()
    assert unknown["omitted"] == 0


def test_retry_reopens_failed_activity_without_reusing_old_tokens() -> None:
    store = TurnActivityStore(max_records_per_turn=1)
    with store.capture("retry"):
        old = begin_activity(kind="retrieval", ref="retrieval.search", target="Lesson")
        begin_activity(kind="verification", ref="verification.answer")
    failed = store.settle("retry", status="failed")
    assert failed["state"] == "failed"
    assert failed["omitted"] == 1
    assert _records(failed)[0]["status"] == "failed"

    with store.capture("retry"):
        reopened = store.snapshot("retry")
        assert reopened["state"] == "running"
        assert _records(reopened) == ()
        assert reopened["omitted"] == 0
        current = begin_activity(kind="retrieval", ref="retrieval.search", target="Lesson")
        assert old is not None and current is not None and current[1] > old[1]
        finish_activity(old, status="failed", error_code="provider_unavailable")
        assert _records(store.snapshot("retry"))[0]["status"] == "running"
        finish_activity(current, status="done")
    settled = store.settle("retry")
    assert settled["state"] == "settled"
    assert _records(settled)[0]["status"] == "done"
    assert _records(settled)[0]["error_code"] is None
