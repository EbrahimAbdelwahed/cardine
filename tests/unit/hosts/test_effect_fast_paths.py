from __future__ import annotations

import asyncio
from collections.abc import Mapping

from cardine.hosts import (
    AdvertisedCapability,
    AssistantMessageDecision,
    StartCapabilityDecision,
    TutorDecision,
    TutorHostContext,
)
from cardine.hosts.clarification_recovery import ClarificationRecoveryTutorDecisionPort
from cardine.hosts.flashcard_routing import FlashcardProfileRoutingTutorDecisionPort
from cardine.hosts.source_grounding import SourceGroundedTutorDecisionPort
from study_agent.ports.tutor_host import TutorInterruptionToken


class _Token:
    def is_interrupted(self) -> bool:
        return False


class _CountingPort:
    def __init__(self, decision: TutorDecision | None = None) -> None:
        self.calls = 0
        self.contexts: list[TutorHostContext] = []
        self.decision = decision or AssistantMessageDecision("ok")

    async def decide(
        self, context: TutorHostContext, interruption: TutorInterruptionToken
    ) -> TutorDecision:
        del interruption
        self.calls += 1
        self.contexts.append(context)
        return self.decision


def _capability(identifier: str) -> AdvertisedCapability:
    return AdvertisedCapability(
        identifier,
        f"{identifier}@1.0.0",
        "a" * 64,
        {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ("query",),
            "additionalProperties": False,
        },
        False,
    )


def _context(
    learner_text: str,
    *,
    presentations: tuple[Mapping[str, object], ...] = (),
    learner_sequence: int = 2,
) -> TutorHostContext:
    return TutorHostContext(
        "course-1",
        "session-1",
        learner_sequence,
        learner_sequence,
        {
            "course_id": "course-1",
            "session_id": "session-1",
            "materials": ({"source_id": "source-1"},),
            "timeline": (
                {
                    "kind": "learner",
                    "content": learner_text,
                    "course_sequence": learner_sequence,
                },
            ),
            "tutor_presentations": presentations,
        },
        {
            "course_id": "course-1",
            "through_sequence": learner_sequence,
            "estimates": (),
        },
        (_capability("explain_concept"), _capability("propose_flashcards")),
    )


def test_explicit_flashcard_effect_skips_routing_model_call() -> None:
    delegate = _CountingPort()

    decision = asyncio.run(
        FlashcardProfileRoutingTutorDecisionPort(delegate).decide(
            _context("genera 15 flashcards sulla lezione 1"), _Token()
        )
    )

    assert isinstance(decision, StartCapabilityDecision)
    assert decision.capability_id == "propose_flashcards"
    assert delegate.calls == 0


def test_flashcard_meta_question_still_uses_language_model() -> None:
    delegate = _CountingPort()

    decision = asyncio.run(
        FlashcardProfileRoutingTutorDecisionPort(delegate).decide(
            _context("Cosa sono le flashcards?"), _Token()
        )
    )

    assert isinstance(decision, AssistantMessageDecision)
    assert delegate.calls == 1


def test_explicit_grounded_explanation_skips_routing_model_call() -> None:
    delegate = _CountingPort()

    decision = asyncio.run(
        SourceGroundedTutorDecisionPort(delegate).decide(
            _context("Spiegami il legame peptidico"), _Token()
        )
    )

    assert isinstance(decision, StartCapabilityDecision)
    assert decision.capability_id == "explain_concept"
    assert delegate.calls == 0


def test_answered_clarification_enriches_the_single_model_call() -> None:
    delegate = _CountingPort()
    context = _context(
        "negli istoni",
        presentations=(
            {
                "kind": "learner_question",
                "content": "In generale o negli istoni?",
                "course_sequence": 1,
            },
        ),
    )

    asyncio.run(ClarificationRecoveryTutorDecisionPort(delegate).decide(context, _Token()))

    assert delegate.calls == 1
    resolution = delegate.contexts[0].tutor_snapshot["clarification_resolution"]
    assert isinstance(resolution, Mapping)
    assert resolution["current_answer"] == "negli istoni"


def test_history_scoped_flashcards_with_complete_window_use_one_model_call() -> None:
    expected = StartCapabilityDecision(
        "propose_flashcards",
        {
            "query": "glicolisi",
            "scope": "glicolisi",
            "language": "it",
            "candidate_ceiling": 12,
            "continuation_summary_json": None,
        },
    )
    delegate = _CountingPort(expected)

    decision = asyncio.run(
        FlashcardProfileRoutingTutorDecisionPort(delegate).decide(
            _context("Crea flashcard su quello che abbiamo discusso finora"),
            _Token(),
        )
    )

    assert decision == expected
    assert delegate.calls == 1


def test_history_scoped_model_promise_cannot_complete_the_effect() -> None:
    delegate = _CountingPort()

    decision = asyncio.run(
        FlashcardProfileRoutingTutorDecisionPort(delegate).decide(
            _context("Crea flashcard su quello che abbiamo discusso finora"),
            _Token(),
        )
    )

    assert not isinstance(decision, AssistantMessageDecision)
    assert delegate.calls == 1
