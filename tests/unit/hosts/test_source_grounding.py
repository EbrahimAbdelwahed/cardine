from __future__ import annotations

import asyncio

import pytest

from cardine.hosts import (
    AdvertisedCapability,
    AssistantMessageDecision,
    SourceGroundedTutorDecisionPort,
    StartCapabilityDecision,
    TutorDecision,
    TutorHostContext,
)
from study_agent.ports.tutor_host import TutorInterruptionToken


class _Token:
    def is_interrupted(self) -> bool:
        return False


class _AcknowledgingDecisionPort:
    async def decide(
        self, context: TutorHostContext, interruption: TutorInterruptionToken
    ) -> TutorDecision:
        del context, interruption
        return AssistantMessageDecision(
            "Posso iniziare una spiegazione basata sulla fonte disponibile."
        )


def _context(learner_text: str) -> TutorHostContext:
    capability = AdvertisedCapability(
        "explain_concept",
        "explain_concept@1.0.0",
        "a" * 64,
        {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ("query",),
            "additionalProperties": False,
        },
        False,
    )
    return TutorHostContext(
        "course-1",
        "session-1",
        1,
        1,
        {
            "course_id": "course-1",
            "session_id": "session-1",
            "materials": ({"source_id": "source-1"},),
            "timeline": ({"kind": "learner", "content": learner_text},),
        },
        {"course_id": "course-1", "through_sequence": 1, "estimates": ()},
        (capability,),
    )


@pytest.mark.parametrize(
    "learner_text, expected_query",
    [
        ("Avvia una spiegazione della lezione 1", "lezione 1"),
        ("Parliamo di legame peptidico", "legame peptidico"),
    ],
)
def test_source_study_request_starts_grounded_explanation_instead_of_promising_it(
    learner_text: str, expected_query: str
) -> None:
    decision = asyncio.run(
        SourceGroundedTutorDecisionPort(_AcknowledgingDecisionPort()).decide(
            _context(learner_text), _Token()
        )
    )

    assert isinstance(decision, StartCapabilityDecision)
    assert decision.capability_id == "explain_concept"
    assert decision.inputs["query"] == expected_query
