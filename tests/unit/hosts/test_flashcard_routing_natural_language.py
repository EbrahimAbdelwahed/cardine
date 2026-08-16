from __future__ import annotations

import asyncio

import pytest

from cardine.hosts import (
    AdvertisedCapability,
    AssistantMessageDecision,
    InvokeToolDecision,
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


class _StartingDecisionPort:
    async def decide(
        self, context: TutorHostContext, interruption: TutorInterruptionToken
    ) -> TutorDecision:
        del context, interruption
        return StartCapabilityDecision(
            "propose_flashcards",
            {"query": "old", "scope": "old"},
            progress_message="Preparo le flashcard",
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
        "Cosa succede se generi le flashcards?",
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


@pytest.mark.parametrize(
    "learner_text",
    (
        "voglio che generi 15 flashcards sulla lezione 1 di biochimica unificato",
        "genera le falshcards",
        "non mi rispondere, genera le cards",
        (
            "cerca nella conversazione, usa i tools a tua disposizione, smettila di "
            "girare intorno al lavoro. Cerca gli argomenti e fai le cards, basta turni inutili"
        ),
    ),
)
def test_live_flashcard_requests_override_a_model_promise(
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
    assert decision.inputs["language"] == "it"
    assert decision.progress_message is None


def test_affirmative_flashcard_clause_after_negated_clause_starts_generation() -> None:
    learner_text = "Non generare le cards sulla lezione 1; genera invece le cards."

    decision = asyncio.run(
        FlashcardProfileRoutingTutorDecisionPort(_PromisingDecisionPort()).decide(
            _context(learner_text), _Token()
        )
    )

    assert isinstance(decision, StartCapabilityDecision)
    assert decision.capability_id == "propose_flashcards"
    assert decision.inputs["language"] == "it"


@pytest.mark.parametrize(
    "learner_text",
    (
        "non generare le cards sulla lezione 1",
        "non voglio che generi flashcard sulla lezione 1",
        "fai una domanda sulle cards della lezione 1",
    ),
)
def test_flashcard_negation_and_unrelated_fai_requests_do_not_generate(
    learner_text: str,
) -> None:
    decision = asyncio.run(
        FlashcardProfileRoutingTutorDecisionPort(_PromisingDecisionPort()).decide(
            _context(learner_text), _Token()
        )
    )

    assert not isinstance(decision, StartCapabilityDecision)


def test_broad_history_request_reads_before_the_oldest_included_turn() -> None:
    context = _context("Crea flashcard su quello che abbiamo discusso finora")
    context = TutorHostContext(
        context.course_id,
        context.session_id,
        context.tutor_snapshot_sequence,
        context.learner_evidence_through_sequence,
        {
            **context.tutor_snapshot,
            "timeline": (
                {"kind": "learner", "content": "recente", "course_sequence": 31},
                {
                    "kind": "learner",
                    "content": "Crea flashcard su quello che abbiamo discusso finora",
                    "course_sequence": 33,
                },
            ),
            "conversation_window": {
                "total_entries": 32,
                "included_entries": 24,
                "omitted_entries": 8,
                "through_sequence": 33,
            },
            "harness_tools": (
                {"name": "conversation.read", "input_schema": {}},
            ),
        },
        context.learner_evidence,
        context.advertised_capabilities,
    )

    decision = asyncio.run(
        FlashcardProfileRoutingTutorDecisionPort(_PromisingDecisionPort()).decide(
            context, _Token()
        )
    )

    assert decision == InvokeToolDecision(
        "conversation.read",
        {"cursor": 31, "direction": "backward", "limit": 12},
    )


def test_memory_scoped_flashcard_wrapper_preserves_progress_message() -> None:
    base = _context("Crea flashcard su quello che abbiamo discusso finora")
    context = TutorHostContext(
        base.course_id,
        base.session_id,
        base.tutor_snapshot_sequence,
        base.learner_evidence_through_sequence,
        {
            **base.tutor_snapshot,
            "agent_observations": (
                {
                    "tool_name": "conversation.search",
                    "status": "succeeded",
                    "result": {"entries": ({"content": "glicolisi"},)},
                },
            ),
        },
        base.learner_evidence,
        base.advertised_capabilities,
    )

    decision = asyncio.run(
        FlashcardProfileRoutingTutorDecisionPort(_StartingDecisionPort()).decide(
            context, _Token()
        )
    )

    assert isinstance(decision, StartCapabilityDecision)
    assert decision.progress_message == "Preparo le flashcard"
    assert decision.inputs["scope"] == "finora"
