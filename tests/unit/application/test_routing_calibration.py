"""ADR-0027 receipt-based calibration report: read-only, content-free, deterministic."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from cardine.application.routing_calibration import calibration_report, load_receipts
from cardine.hosts.routing import RoutingThreshold
from study_agent.domain._validation import JsonObject


def judgement(
    use_case: str, probabilities: dict[str, float], *, accepted: bool, error: str | None = None
) -> JsonObject:
    ranked = sorted(probabilities.values(), reverse=True)
    return {
        "use_case": use_case,
        "options": tuple(probabilities),
        "probabilities": tuple({"key": k, "probability": v} for k, v in probabilities.items()),
        "accepted": accepted,
        "margin": None if not ranked else ranked[0] - (ranked[1] if len(ranked) > 1 else 0),
        "failure_reason": None if accepted else ("judgement_provider_failure" if error
                                                 else "insufficient_separation"),
        "error_code": error,
    }


def receipt(*judgements: JsonObject, fallback: str | None, **extra: object) -> JsonObject:
    return {"judgements": judgements, "fallback_reason": fallback, "legacy_kind": None,
            "legacy_failure": None, "fallback_binding": None, **extra}  # type: ignore[dict-item]


RECEIPTS = (
    receipt(judgement("route", {"capability:a": 0.9, "assistant_message": 0.1}, accepted=True),
            fallback=None),
    receipt(judgement("route", {"capability:a": 0.6, "assistant_message": 0.4}, accepted=False),
            fallback="insufficient_separation", legacy_kind="ask_learner",
            fallback_binding="flashcard_scope_bound"),
    receipt(judgement("route", {"start_capability": 0.55, "invoke_tool": 0.45},
                      accepted=False), fallback="insufficient_separation",
            legacy_failure="protocol_error"),
    receipt(judgement("route", {}, accepted=False, error="jev_timeout"),
            fallback="judgement_provider_failure"),
)


def test_report_counts_fallbacks_failures_and_shapes() -> None:
    report = calibration_report(RECEIPTS, (RoutingThreshold(0.8, 0.2),))
    assert report["receipts"] == 4 and report["candidate_used"] == 1
    assert report["fallback_reasons"] == {
        "insufficient_separation": 2, "judgement_provider_failure": 1}
    assert report["legacy_kinds"] == {"ask_learner": 1}
    assert report["legacy_failures"] == {"protocol_error": 1}
    assert report["fallback_bindings"] == {"flashcard_scope_bound": 1}
    assert report["judgement_error_codes"] == {"jev_timeout": 1}
    assert report["use_cases"] == {"route": {"judged": 4, "accepted": 1}}
    route = report["route"]
    assert isinstance(route, dict)
    assert route["shapes"] == {"flat": 2, "cascade": 1}


def test_what_if_acceptance_is_computed_per_shape_from_stored_distributions() -> None:
    report = calibration_report(
        RECEIPTS, (RoutingThreshold(0.8, 0.2), RoutingThreshold(0.5, 0.15))
    )
    route = report["route"]
    assert isinstance(route, dict)
    assert route["what_if"] == (
        {"probability": 0.8, "margin": 0.2, "flat": 0.5, "cascade": 0.0},
        {"probability": 0.5, "margin": 0.15, "flat": 1.0, "cascade": 0.0},
    )
    flat = route["flat_top_probability"]
    assert isinstance(flat, dict) and flat["p50"] == pytest.approx(0.75)


def test_report_never_contains_unknown_text_fields() -> None:
    leaking = receipt(judgement("route", {"assistant_message": 1.0}, accepted=True),
                      fallback=None, utterance="PRIVATE")
    assert "PRIVATE" not in repr(calibration_report((leaking,), ()))


def test_loader_reads_only_routing_receipts_read_only(tmp_path: Path) -> None:
    database = tmp_path / "runs.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE playbook_runs (run_id TEXT PRIMARY KEY, payload BLOB)")
        connection.execute("INSERT INTO playbook_runs VALUES (?, ?)",
                           ("tutor-routing-receipts-sha256:" + "a" * 64,
                            b'{"fallback_reason":null,"judgements":[]}'))
        connection.execute("INSERT INTO playbook_runs VALUES (?, ?)",
                           ("capability-run-sha256:" + "b" * 64, b'{"secret":"PRIVATE"}'))
    assert load_receipts(database) == ({"fallback_reason": None, "judgements": []},)
    with pytest.raises(sqlite3.OperationalError):
        load_receipts(tmp_path / "missing.sqlite3")
