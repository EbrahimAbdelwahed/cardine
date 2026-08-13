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
_FLASHCARD_ACTION = (
    r"(?:crea(?:re|mi|te|ta)?|genera(?:re|mi|te|ta|i|no)?|"
    r"prepara(?:re|mi|te|ta)?|produci|costruisci|proponi|fammi|dammi|"
    r"create|creating|created|generate|generating|generated|make|making|"
    r"prepare|preparing|produce|producing|build|building|draft|drafting|"
    r"give\s+me)"
)
_FLASHCARD_KIND = (
    r"(?:flash\s*cards?|cards?|schede(?:\s+(?:di|per)\s+studio)?|"
    r"carte\s+di\s+studio)"
)
_FLASHCARD_REQUEST = re.compile(
    rf"(?<![\w-]){_FLASHCARD_ACTION}(?:\s+[\w'-]+){{0,8}}\s+{_FLASHCARD_KIND}(?![\w-])",
    re.IGNORECASE,
)
_FLASHCARD_META_PREFIX = re.compile(
    r"(?:\b(?:cosa\s+sono|cos(?:'|\u2019)e|what\s+are|tell\s+me\s+about)|"
    r"\b(?:quando|perch[eé]|why|when|whether)\b|"
    r"\b(?:[eè]\s+utile|conviene|is\s+it\s+useful|should\s+i)\b|"
    r"\b(?:come|how)\b.{0,24}\b(?:posso|si\s+pu[oò]|can|do\s+i|to)\b|"
    r"\b(?:spiegami|spiegare|explain|describe)\b)",
    re.IGNORECASE,
)
_FLASHCARD_NEGATION = re.compile(r"\b(?:non|don't|do\s+not|never)\b", re.IGNORECASE)
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
        if learner_text is None or not _is_flashcard_generation_request(learner_text):
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


def _is_flashcard_generation_request(learner_text: str) -> bool:
    """Recognize an explicit card-generation action, not a card-related question."""

    match = _FLASHCARD_REQUEST.search(learner_text)
    if match is None:
        return False
    prefix = learner_text[: match.start()]
    if _FLASHCARD_META_PREFIX.search(prefix):
        return False
    action_start = max(0, match.start() - 32)
    return not _FLASHCARD_NEGATION.search(learner_text[action_start : match.start()])


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
