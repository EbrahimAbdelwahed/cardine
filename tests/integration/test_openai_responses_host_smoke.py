from __future__ import annotations

import asyncio
import importlib.util
import os

import pytest

from cardine.adapters.host import (
    OpenAIResponsesTutorConfig,
    OpenAIResponsesTutorDecisionPort,
)
from cardine.hosts import AdvertisedCapability, TutorHostContext, validate_decision

_SHA = "a" * 64


def _context() -> TutorHostContext:
    return TutorHostContext(
        "build-week-smoke",
        "build-week-smoke-session",
        1,
        1,
        {"status": "active"},
        {"estimates": ()},
        (
            AdvertisedCapability(
                "grounding.ask",
                "grounding.ask@1.0.0",
                _SHA,
                {
                    "type": "object",
                    "properties": {"topic": {"type": "string", "minLength": 1}},
                    "required": ("topic",),
                    "additionalProperties": False,
                },
                True,
            ),
        ),
    )


class _NoInterruption:
    def is_interrupted(self) -> bool:
        return False


@pytest.mark.skipif(
    not os.environ.get("STUDY_AGENT_OPENAI_SMOKE")
    or not os.environ.get("STUDY_AGENT_OPENAI_SMOKE_MODEL")
    or not os.environ.get("OPENAI_API_KEY")
    or importlib.util.find_spec("openai") is None,
    reason="opt-in OpenAI Responses smoke requires the SDK, model, and API key",
)
def test_openai_responses_smoke_is_opt_in() -> None:
    model = os.environ["STUDY_AGENT_OPENAI_SMOKE_MODEL"]
    port = OpenAIResponsesTutorDecisionPort(
        OpenAIResponsesTutorConfig(
            model,
            "OPENAI_API_KEY",
            timeout_seconds=20.0,
            max_output_tokens=128,
        )
    )
    decision = asyncio.run(port.decide(_context(), _NoInterruption()))
    validate_decision(decision, _context())
