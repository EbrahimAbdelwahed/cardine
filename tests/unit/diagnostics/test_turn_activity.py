"""Public contracts for process-local live turn activity observations."""

from __future__ import annotations

from cardine.diagnostics.turn_activity import (
    TurnActivityStore,
    add_settled,
    begin_activity,
    finish_activity,
)


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
        assert len(snapshot["records"]) == 2
        assert snapshot["records"][0]["status"] == "done"
        assert snapshot["records"][1]["status"] == "running"
        assert "label" in snapshot["records"][0]
        assert snapshot["records"][0]["target"] == "Lezione 4 · Emostasi"
        assert snapshot["records"][0]["count"] == 8

    settled = store.settle("request-1", status="done")
    assert settled["state"] == "settled"
    assert all(record["status"] == "done" for record in settled["records"])
    assert all(record["ended_at"] for record in settled["records"])
    assert settled["records"][1]["sequence"] > settled["records"][0]["sequence"]


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

    assert [item["target"] for item in first.snapshot("same-request")["records"]] == [
        "First store",
        "First store again",
    ]
    assert [item["target"] for item in second.snapshot("same-request")["records"]] == [
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
                label="model supplied label",  # type: ignore[call-arg]
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
    assert len(current["records"]) == 1
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

    record = store.snapshot("request-settled")["records"][0]
    assert record["status"] == "done"
    assert record["ended_at"]
    assert record["started_at"] == record["ended_at"]

    unknown = store.snapshot("not-captured")
    assert unknown["state"] == "unknown"
    assert unknown["records"] == []
    assert unknown["omitted"] == 0
