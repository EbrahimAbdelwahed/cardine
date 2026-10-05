"""Read-only calibration report over stored tutor routing receipts (ADR-0027).

Receipts are derived, content-free telemetry. The report copies only counts,
closed codes and probabilities; it never reads canonical events or source text.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Iterable, Mapping
from contextlib import closing
from pathlib import Path

from cardine.hosts.routing import RoutingThreshold
from study_agent.domain._validation import JsonObject, JsonValue

_RECEIPT_PREFIX = "tutor-routing-receipts-sha256:"
_QUANTILES = (("p10", 0.1), ("p25", 0.25), ("p50", 0.5), ("p75", 0.75), ("p90", 0.9))


def load_receipts(runs_database: Path) -> tuple[JsonObject, ...]:
    """Open the operational run store read-only and return routing receipts in order."""

    uri = f"{Path(runs_database).resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        rows = connection.execute(
            "SELECT payload FROM playbook_runs WHERE substr(run_id, 1, ?) = ? ORDER BY rowid",
            (len(_RECEIPT_PREFIX), _RECEIPT_PREFIX),
        ).fetchall()
    return tuple(json.loads(bytes(row[0])) for row in rows)


def calibration_report(
    receipts: Iterable[Mapping[str, object]], candidates: tuple[RoutingThreshold, ...]
) -> JsonObject:
    items = tuple(receipts)
    use_cases: dict[str, dict[str, int]] = {}
    error_codes: Counter[str] = Counter()
    routes: dict[str, list[tuple[float, float]]] = {"flat": [], "cascade": []}
    for receipt in items:
        for judgement in _judgements(receipt):
            use_case = _text(judgement.get("use_case")) or "unknown"
            counts = use_cases.setdefault(use_case, {"judged": 0, "accepted": 0})
            counts["judged"] += 1
            counts["accepted"] += judgement.get("accepted") is True
            code = _text(judgement.get("error_code"))
            if code is not None:
                error_codes[code] += 1
            if use_case == "route":
                shape_and_scores = _route_scores(judgement)
                if shape_and_scores is not None:
                    shape, scores = shape_and_scores
                    routes[shape].append(scores)
    return {
        "receipts": len(items),
        "candidate_used": sum(item.get("fallback_reason") is None for item in items),
        "fallback_reasons": _counts(item.get("fallback_reason") for item in items),
        "legacy_kinds": _counts(item.get("legacy_kind") for item in items),
        "legacy_failures": _counts(item.get("legacy_failure") for item in items),
        "fallback_bindings": _counts(item.get("fallback_binding") for item in items),
        "judgement_error_codes": dict(sorted(error_codes.items())),
        "use_cases": {key: dict(value) for key, value in sorted(use_cases.items())},
        "route": {
            "shapes": {shape: len(values) for shape, values in routes.items()},
            "flat_top_probability": _quantiles(top for top, _ in routes["flat"]),
            "flat_margin": _quantiles(margin for _, margin in routes["flat"]),
            "what_if": tuple(
                {
                    "probability": threshold.minimum_probability,
                    "margin": threshold.minimum_margin,
                    **{
                        shape: _acceptance(values, threshold)
                        for shape, values in routes.items()
                    },
                }
                for threshold in candidates
            ),
        },
    }


def _judgements(receipt: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    values = receipt.get("judgements")
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(item for item in values if isinstance(item, Mapping))


def _route_scores(judgement: Mapping[str, object]) -> tuple[str, tuple[float, float]] | None:
    raw = judgement.get("probabilities")
    if not isinstance(raw, (list, tuple)):
        return None
    probabilities: list[tuple[str, float]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            return None
        key, value = item.get("key"), item.get("probability")
        if not isinstance(key, str) or isinstance(value, bool) or not isinstance(
            value, (int, float)
        ):
            return None
        probabilities.append((key, float(value)))
    if len(probabilities) < 2:
        return None
    ranked = sorted((value for _, value in probabilities), reverse=True)
    # Flat choices name concrete actions; the retired cascade scored abstract kinds.
    shape = "flat" if any(":" in key for key, _ in probabilities) else "cascade"
    return shape, (ranked[0], ranked[0] - ranked[1])


def _acceptance(values: list[tuple[float, float]], threshold: RoutingThreshold) -> float | None:
    if not values:
        return None
    accepted = sum(
        top >= threshold.minimum_probability and margin >= threshold.minimum_margin
        for top, margin in values
    )
    return round(accepted / len(values), 4)


def _quantiles(values: Iterable[float]) -> JsonObject:
    ordered = sorted(values)
    if not ordered:
        return {}
    result: dict[str, JsonValue] = {}
    for name, fraction in _QUANTILES:
        position = fraction * (len(ordered) - 1)
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        value = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
        result[name] = round(value, 4)
    return result


def _counts(values: Iterable[object]) -> JsonObject:
    counter = Counter(text for value in values if (text := _text(value)) is not None)
    return dict(sorted(counter.items()))


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


__all__ = ["calibration_report", "load_receipts"]
