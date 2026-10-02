from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import cast

import pytest

from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.ports.judgement import (
    ChoiceJudgement,
    ChoiceJudgementRequest,
    ChoiceOption,
    ChoiceProbability,
    validate_judgement,
)


def request() -> ChoiceJudgementRequest:
    return ChoiceJudgementRequest("Choose", {}, (ChoiceOption("a", "A"), ChoiceOption("b", "B")))


def result() -> ChoiceJudgement:
    return ChoiceJudgement(
        "a",
        (ChoiceProbability("a", 0.6), ChoiceProbability("b", 0.4)),
        None,
        "fixture",
        "1",
        "choice-model",
        None,
    )


def test_validated_full_distribution() -> None:
    assert validate_judgement(request(), result()) == result()


@pytest.mark.parametrize(
    "options", [(), (ChoiceOption("a", "A"),), (ChoiceOption("a", "A"), ChoiceOption("a", "B"))]
)
def test_request_rejects_nonclosed_choices(options: tuple[ChoiceOption, ...]) -> None:
    with pytest.raises(ValueError):
        ChoiceJudgementRequest("Choose", {}, options)


@pytest.mark.parametrize("probability", [float("nan"), float("inf"), -0.1, 1.1, True])
def test_probability_rejects_invalid_numbers(probability: float) -> None:
    with pytest.raises(ValueError):
        ChoiceProbability("a", probability)


def test_bad_normalization_duplicates_and_selected_key_rejected() -> None:
    for values in (
        (ChoiceProbability("a", 0.2), ChoiceProbability("b", 0.2)),
        (ChoiceProbability("a", 0.5), ChoiceProbability("a", 0.5)),
    ):
        with pytest.raises(ValueError):
            replace(result(), probabilities=values)
    with pytest.raises(ValueError):
        replace(result(), selected_key="unknown")


def test_consumer_rejects_unknown_missing_and_forged_results() -> None:
    other = ChoiceJudgement(
        "a",
        (ChoiceProbability("a", 0.6), ChoiceProbability("c", 0.4)),
        None,
        "fixture",
        "1",
        "model",
        None,
    )
    with pytest.raises(ValueError, match="exactly"):
        validate_judgement(request(), other)
    forged = result()
    object.__setattr__(forged, "probabilities", (ChoiceProbability("a", 0.6),))
    with pytest.raises(ValueError):
        validate_judgement(request(), forged)
    forged = result()
    object.__setattr__(forged.probabilities[0], "probability", -0.4)
    object.__setattr__(forged.probabilities[1], "probability", 1.4)
    with pytest.raises(ValueError):
        validate_judgement(request(), forged)


def test_json_payloads_are_deeply_frozen() -> None:
    state: dict[str, JsonValue] = {"nested": {"key": "before"}}
    metadata: dict[str, JsonValue] = {"version": "before"}
    bounded = replace(request(), state=state, metadata=metadata)
    cast(dict[str, JsonValue], state["nested"])["key"] = "after"
    metadata["version"] = "after"
    nested = cast(Mapping[str, JsonValue], cast(JsonObject, bounded.state)["nested"])
    assert nested["key"] == "before"
    assert bounded.metadata["version"] == "before"
    usage: dict[str, JsonValue] = {"calls": 1}
    judged = replace(result(), usage=usage)
    usage["calls"] = 2
    assert judged.usage["calls"] == 1
