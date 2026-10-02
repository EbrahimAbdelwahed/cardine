"""Versioned prompt definitions and deterministic composition."""

from .assess_understanding_v1 import (
    ASSESS_UNDERSTANDING_LAYERS,
    ASSESS_UNDERSTANDING_PROMPT,
)
from .composer import CanonicalPromptComposer, canonical_json
from .contracts import (
    ComposedPrompt,
    PromptComposer,
    PromptCompositionError,
    PromptLayerRecord,
)
from .explain_concept_v1 import EXPLAIN_CONCEPT_LAYERS, EXPLAIN_CONCEPT_PROMPT
from .grade_response_v1 import GRADE_RESPONSE_LAYERS, GRADE_RESPONSE_PROMPT
from .grounded_answer_v1 import GROUNDED_ANSWER_LAYERS, GROUNDED_ANSWER_PROMPT
from .retrieval_query_recovery_v1 import (
    RETRIEVAL_QUERY_RECOVERY_INSTRUCTION,
    RETRIEVAL_QUERY_RECOVERY_PROMPT,
)
from .tutor_decision_v1 import TUTOR_DECISION_INSTRUCTION, TUTOR_DECISION_PROMPT

__all__ = [
    "ASSESS_UNDERSTANDING_LAYERS",
    "ASSESS_UNDERSTANDING_PROMPT",
    "EXPLAIN_CONCEPT_LAYERS",
    "EXPLAIN_CONCEPT_PROMPT",
    "GRADE_RESPONSE_LAYERS",
    "GRADE_RESPONSE_PROMPT",
    "GROUNDED_ANSWER_LAYERS",
    "GROUNDED_ANSWER_PROMPT",
    "RETRIEVAL_QUERY_RECOVERY_INSTRUCTION",
    "RETRIEVAL_QUERY_RECOVERY_PROMPT",
    "TUTOR_DECISION_INSTRUCTION",
    "TUTOR_DECISION_PROMPT",
    "CanonicalPromptComposer",
    "ComposedPrompt",
    "PromptComposer",
    "PromptCompositionError",
    "PromptLayerRecord",
    "canonical_json",
]
