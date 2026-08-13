from __future__ import annotations

import asyncio

import pytest

from cardine.hosts import (
    AdvertisedCapability,
    AssistantMessageDecision,
    StartCapabilityDecision,
    TutorDecision,
    TutorHostContext,
)
from cardine.hosts.flashcard_routing import FlashcardProfileRoutingTutorDecisionPort
from study_agent.ports.tutor_host import TutorInterruptionToken


class _Token:
    def is_interrupted(self) -> bool:
        return False


class _PromisingDecisionPort:
    async def decide(
        self, context: TutorHostContext, interruption: TutorInterruptionToken
    ) -> TutorDecision:
        del context, interruption
        return AssistantMessageDecision(
            "Ok, allora adesso ti spiego come generare queste flashcard."
        )


def _context(learner_text: str) -> TutorHostContext:
    capability = AdvertisedCapability(
        "propose_flashcards",
        "propose_flashcards@1.0.0",
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
            "timeline": ({"kind": "learner", "content": learner_text},),
        },
        {"course_id": "course-1", "through_sequence": 1, "estimates": ()},
        (capability,),
    )


@pytest.mark.parametrize(
    "learner_text",
    (
        "genera 3 cards sul legamento peptidico",
        "crea 3 cards sul legame peptidico",
        "generate 3 cards about the peptide bond",
        "create some flashcards about the peptide bond",
    ),
)
def test_natural_flashcard_requests_start_advertised_capability_immediately(
    learner_text: str,
) -> None:
    decision = asyncio.run(
        FlashcardProfileRoutingTutorDecisionPort(_PromisingDecisionPort()).decide(
            _context(learner_text), _Token()
        )
    )

    assert isinstance(decision, StartCapabilityDecision)
    assert decision.capability_id == "propose_flashcards"
    assert decision.inputs["query"] == learner_text


@pytest.mark.parametrize(
    "learner_text",
    (
        "Spiegami il legamento peptidico in modo semplice.",
        "Cosa sono le flashcards?",
        "Quando è meglio generare flashcards?",
        "È utile generare flashcards?",
    ),
)
def test_explanations_and_flashcard_meta_questions_do_not_start_generation(
    learner_text: str,
) -> None:
    decision = asyncio.run(
        FlashcardProfileRoutingTutorDecisionPort(_PromisingDecisionPort()).decide(
            _context(learner_text), _Token()
        )
    )

    assert not isinstance(decision, StartCapabilityDecision)
