"""Provider-neutral scripted and generic HTTP model adapters."""

from .openai_compatible import (
    ADAPTER_ID,
    ADAPTER_VERSION,
    HttpResponse,
    HttpTransport,
    OpenAICompatibleConfig,
    OpenAICompatibleModel,
    StdlibHttpTransport,
)
from cardine.adapters.model.openai_luna import (
    GPT_5_6_LUNA_ADAPTER_ID,
    GPT_5_6_LUNA_ADAPTER_VERSION,
    GPT_5_6_LUNA_ENDPOINT,
    GPT_5_6_LUNA_MODEL_ID,
    GPT_5_6_LUNA_REASONING_EFFORT,
    OpenAIGpt56LunaConfig,
    OpenAIGpt56LunaModel,
)
from .scripted import ScriptedExchange, ScriptedModel
from cardine.adapters.model.tutor_decision import (
    MAX_DECISION_OUTPUT_TOKENS,
    ModelTutorDecisionError,
    ModelTutorDecisionPort,
)

__all__ = [
    "ADAPTER_ID",
    "ADAPTER_VERSION",
    "GPT_5_6_LUNA_ADAPTER_ID",
    "GPT_5_6_LUNA_ADAPTER_VERSION",
    "GPT_5_6_LUNA_ENDPOINT",
    "GPT_5_6_LUNA_MODEL_ID",
    "GPT_5_6_LUNA_REASONING_EFFORT",
    "MAX_DECISION_OUTPUT_TOKENS",
    "HttpResponse",
    "HttpTransport",
    "ModelTutorDecisionError",
    "ModelTutorDecisionPort",
    "OpenAICompatibleConfig",
    "OpenAICompatibleModel",
    "OpenAIGpt56LunaConfig",
    "OpenAIGpt56LunaModel",
    "ScriptedExchange",
    "ScriptedModel",
    "StdlibHttpTransport",
]
