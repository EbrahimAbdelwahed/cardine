"""Bounded, provider-neutral semantic choice contracts."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Protocol

from study_agent.domain._validation import (
    JsonObject,
    JsonValue,
    freeze_json,
    freeze_object,
    require_text,
)

NORMALIZATION_TOLERANCE = 1e-6


@dataclass(frozen=True, slots=True)
class ChoiceOption:
    key: str
    description: str

    def __post_init__(self) -> None:
        require_text(self.key, "key")
        require_text(self.description, "description")


@dataclass(frozen=True, slots=True)
class ChoiceJudgementRequest:
    instruction: str
    state: JsonValue
    options: tuple[ChoiceOption, ...]
    metadata: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_text(self.instruction, "instruction")
        options = tuple(self.options)
        if len(options) < 2 or any(not isinstance(o, ChoiceOption) for o in options):
            raise ValueError("judgement requires at least two ChoiceOption values")
        for option in options:
            option.__post_init__()
        if len({o.key for o in options}) != len(options):
            raise ValueError("option keys must be unique")
        object.__setattr__(self, "options", options)
        object.__setattr__(self, "state", freeze_json(self.state))
        object.__setattr__(self, "metadata", freeze_object(self.metadata))


@dataclass(frozen=True, slots=True)
class ChoiceProbability:
    key: str
    probability: float

    def __post_init__(self) -> None:
        require_text(self.key, "key")
        _number(self.probability, "probability", upper=1.0)


@dataclass(frozen=True, slots=True)
class ChoiceJudgement:
    selected_key: str
    probabilities: tuple[ChoiceProbability, ...]
    confidence: float | None
    producer_id: str
    producer_version: str
    model_id: str
    latency_ms: float | None
    usage: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("selected_key", "producer_id", "producer_version", "model_id"):
            require_text(getattr(self, name), name)
        entries = tuple(self.probabilities)
        if len(entries) < 2 or any(not isinstance(p, ChoiceProbability) for p in entries):
            raise ValueError("judgement requires at least two probability entries")
        for entry in entries:
            entry.__post_init__()
        if len({p.key for p in entries}) != len(entries):
            raise ValueError("probability keys must be unique")
        if self.selected_key not in {p.key for p in entries}:
            raise ValueError("selected key must occur in probability distribution")
        if not math.isclose(
            math.fsum(p.probability for p in entries),
            1.0,
            rel_tol=0.0,
            abs_tol=NORMALIZATION_TOLERANCE,
        ):
            raise ValueError("probability distribution must sum to one")
        if self.confidence is not None:
            _number(self.confidence, "confidence", upper=1.0)
        if self.latency_ms is not None:
            _number(self.latency_ms, "latency_ms")
        object.__setattr__(self, "probabilities", entries)
        object.__setattr__(self, "usage", freeze_object(self.usage))


def validate_judgement(
    request: ChoiceJudgementRequest,
    result: ChoiceJudgement,
) -> ChoiceJudgement:
    """Consumer boundary: never trust adapter construction or choice membership."""
    if not isinstance(request, ChoiceJudgementRequest) or not isinstance(result, ChoiceJudgement):
        raise ValueError("expected choice request and judgement contracts")
    request.__post_init__()
    result.__post_init__()
    expected = {option.key for option in request.options}
    if {entry.key for entry in result.probabilities} != expected:
        raise ValueError("distribution must cover exactly the requested choices")
    if result.selected_key not in expected:
        raise ValueError("selected key must be a requested choice")
    return result


def _number(value: float, name: str, *, upper: float | None = None) -> None:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    if upper is not None and value > upper:
        raise ValueError(f"{name} exceeds {upper}")


class ChoiceJudgementPort(Protocol):
    async def judge(self, request: ChoiceJudgementRequest) -> ChoiceJudgement: ...
