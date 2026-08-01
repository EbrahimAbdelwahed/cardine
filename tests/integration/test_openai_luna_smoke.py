from __future__ import annotations

import asyncio
import os

import pytest

from study_agent.adapters.model import (
    GPT_5_6_LUNA_ADAPTER_ID,
    GPT_5_6_LUNA_MODEL_ID,
    OpenAIGpt56LunaConfig,
    OpenAIGpt56LunaModel,
)
from study_agent.ports import (
    MessageRole,
    ModelFinishReason,
    ModelMessage,
    ModelRequest,
    StructuredOutputConstraint,
)


def test_opt_in_live_luna_strict_structured_output() -> None:
    if os.environ.get("STUDY_AGENT_LUNA_LIVE") != "1":
        pytest.skip("opt-in GPT-5.6 Luna smoke is disabled")
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        pytest.skip("opt-in GPT-5.6 Luna smoke requires OPENAI_API_KEY")
    adapter = OpenAIGpt56LunaModel(OpenAIGpt56LunaConfig(key))

    result = asyncio.run(
        adapter.generate(
            ModelRequest(
                (
                    ModelMessage(
                        MessageRole.SYSTEM,
                        "Return only the requested synthetic fixture object.",
                    ),
                    ModelMessage(
                        MessageRole.USER,
                        "Set status to supported. Do not include other fields.",
                    ),
                ),
                StructuredOutputConstraint(
                    "luna_adapter_smoke",
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ("status",),
                        "properties": {
                            "status": {
                                "type": "string",
                                "enum": ("supported",),
                            }
                        },
                    },
                ),
                max_output_tokens=128,
                temperature=0,
            )
        )
    )

    assert result.finish_reason is ModelFinishReason.STOP
    assert result.structured_output == {"status": "supported"}
    assert result.invocation.adapter_id == GPT_5_6_LUNA_ADAPTER_ID
    assert result.invocation.model_id == GPT_5_6_LUNA_MODEL_ID
