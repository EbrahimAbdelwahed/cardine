"""Host routing for the executable, profile-dispatched flashcard capability."""

from __future__ import annotations

import re
from collections.abc import Mapping

from cardine.application.flashcard_profile_selection import (
    FlashcardProfileRouteKind,
    select_flashcard_profile,
)
from study_agent.domain._validation import JsonObject
from study_agent.ports.tutor_host import TutorDecisionPort, TutorInterruptionToken

from .contracts import (
    AskLearnerDecision,
    StartCapabilityDecision,
    TutorDecision,
    TutorHostContext,
)

_PROPOSE_FLASHCARDS = "propose_flashcards"
_FLASHCARD_REQUEST = re.compile(
    r"\b(?:flashcard|flashcards|flashcard[s]?|schede|carte\s+di\s+studio|"
    r"crea\s+(?:delle\s+)?(?:flashcard|schede)|create\s+(?:some\s+)?flashcards?)\b",
    re.IGNORECASE,
)
_ITALIAN = re.compile(
    r"\b(?:crea|delle|schede|fonti|anatomia|morfologia|ricostruzione|rapporti)\b",
    re.IGNORECASE,
)


class FlashcardProfileRoutingTutorDecisionPort(TutorDecisionPort):
    """Replace model capability guesses with a closed host-owned route."""

    def __init__(self, delegate: TutorDecisionPort) -> None:
        if not hasattr(delegate, "decide"):
            raise TypeError("flashcard routing requires a tutor decision port")
        self._delegate = delegate

    async def decide(
        self, context: TutorHostContext, interruption: TutorInterruptionToken
    ) -> TutorDecision:
        decision = await self._delegate.decide(context, interruption)
        if context.pending_continuation is not None:
            return decision
        learner_text = _latest_learner_text(context)
        if learner_text is None or not _FLASHCARD_REQUEST.search(learner_text):
            return decision
        if not any(item.id == _PROPOSE_FLASHCARDS for item in context.advertised_capabilities):
            return decision
        route = select_flashcard_profile(learner_text)
        if route.kind is FlashcardProfileRouteKind.CLARIFICATION:
            return AskLearnerDecision(route.clarification or "Quale profilo preferisci?")
        return StartCapabilityDecision(
            _PROPOSE_FLASHCARDS,
            _flashcard_inputs(learner_text),
        )


def _latest_learner_text(context: TutorHostContext) -> str | None:
    timeline = context.tutor_snapshot.get("timeline")
    if not isinstance(timeline, tuple):
        return None
    for item in reversed(timeline):
        if not isinstance(item, Mapping) or item.get("kind") != "learner":
            continue
        content = item.get("content")
        return content if isinstance(content, str) and content.strip() else None
    return None


def _flashcard_inputs(learner_text: str) -> JsonObject:
    prompt = learner_text.strip()
    return {
        "query": prompt[:4_000],
        "scope": prompt[:1_000],
        "language": "it" if _ITALIAN.search(prompt) else "en",
        "candidate_ceiling": 24,
        "continuation_summary_json": None,
    }


__all__ = ["FlashcardProfileRoutingTutorDecisionPort"]
