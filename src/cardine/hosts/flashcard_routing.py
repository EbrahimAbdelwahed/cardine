"""Host routing for the executable, profile-dispatched flashcard capability."""

from __future__ import annotations

import json
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
    InvokeToolDecision,
    StartCapabilityDecision,
    TutorDecision,
    TutorHostContext,
)

_PROPOSE_FLASHCARDS = "propose_flashcards"
_FLASHCARD_ACTION = (
    r"(?:crea(?:re|mi|te|ta)?|genera(?:re|mi|te|ta|i|no)?|generi|"
    r"prepara(?:re|mi|te|ta)?|produci|costruisci|proponi|fammi|dammi|"
    r"fai(?=\s+(?:\d+\s+)?(?:(?:le|la|i|gli|delle|della|dei|degli|una|un)\s+)?"
    r"(?:flash\s*cards?|cards?|falsh\s*cards?|schede(?:\s+(?:di|per)\s+studio)?|"
    r"carte\s+di\s+studio))|"
    r"create|creating|created|generate|generating|generated|make|making|"
    r"prepare|preparing|produce|producing|build|building|draft|drafting|"
    r"give\s+me)"
)
_FLASHCARD_KIND = (
    r"(?:flash\s*cards?|cards?|falsh\s*cards?|schede(?:\s+(?:di|per)\s+studio)?|"
    r"carte\s+di\s+studio)"
)
_FLASHCARD_REQUEST = re.compile(
    rf"(?<![\w-]){_FLASHCARD_ACTION}(?:\s+[\w'-]+){{0,8}}\s+{_FLASHCARD_KIND}(?![\w-])",
    re.IGNORECASE,
)
_FLASHCARD_META_PREFIX = re.compile(
    r"(?:\b(?:cosa\s+sono|cos(?:'|\u2019)e|what\s+are|tell\s+me\s+about)|"
    r"\b(?:cosa\s+succede\s+se|what\s+happens\s+if)|"
    r"\b(?:quando|perch[eé]|why|when|whether)\b|"
    r"\b(?:[eè]\s+utile|conviene|is\s+it\s+useful|should\s+i)\b|"
    r"\b(?:come|how)\b.{0,24}\b(?:posso|si\s+pu[oò]|can|do\s+i|to)\b|"
    r"\b(?:spiegami|spiegare|explain|describe)\b)",
    re.IGNORECASE,
)
_FLASHCARD_NEGATION = re.compile(r"\b(?:non|don't|do\s+not|never)\b", re.IGNORECASE)
_FLASHCARD_CLAUSE_BOUNDARY = re.compile(r"[,;.!?:\n]")
_ITALIAN = re.compile(
    r"\b(?:crea|genera|prepara|fai|lezione|questa|delle|schede|fonti|anatomia|"
    r"morfologia|ricostruzione|rapporti)\b",
    re.IGNORECASE,
)
_HISTORY_SCOPED = re.compile(
    r"\b(?:quello\s+che\s+abbiamo\s+(?:discusso|studiato|visto)|"
    r"ci[oò]\s+che\s+abbiamo\s+(?:discusso|studiato|visto)|"
    r"questa\s+(?:chat|conversazione)|finora|"
    r"what\s+we(?:'ve|\s+have)?\s+(?:discussed|studied|covered)|"
    r"this\s+(?:chat|conversation)|so\s+far)\b",
    re.IGNORECASE,
)
_MEMORY_TOPIC_STOPWORDS = frozenset(
    {
        "abbiamo",
        "about",
        "cards",
        "che",
        "crea",
        "create",
        "della",
        "delle",
        "degli",
        "discusso",
        "discussed",
        "generate",
        "genera",
        "flashcard",
        "flashcards",
        "quello",
        "questo",
        "sulla",
        "sulle",
        "su",
        "that",
        "what",
    }
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
        if context.pending_continuation is not None:
            return await self._delegate.decide(context, interruption)
        learner_text = _latest_learner_text(context)
        if learner_text is None or not _is_flashcard_generation_request(learner_text):
            return await self._delegate.decide(context, interruption)
        if not any(item.id == _PROPOSE_FLASHCARDS for item in context.advertised_capabilities):
            return await self._delegate.decide(context, interruption)

        observed_history = _observed_conversation_history(context)
        if not observed_history and (
            _HISTORY_SCOPED.search(learner_text)
            and _omitted_conversation_entries(context) > 0
            and _has_conversation_read_tool(context)
        ):
            return InvokeToolDecision(
                "conversation.read",
                {
                    "cursor": _oldest_included_conversation_sequence(context),
                    "direction": "backward",
                    "limit": 12,
                },
            )
        if observed_history:
            decision = await self._delegate.decide(context, interruption)
            if isinstance(decision, InvokeToolDecision):
                return decision
            if (
                isinstance(decision, StartCapabilityDecision)
                and decision.capability_id == _PROPOSE_FLASHCARDS
            ):
                return _bounded_memory_flashcard_decision(decision, context)
            return AskLearnerDecision(
                "Quali argomenti della conversazione devo trasformare in flashcard?"
            )

        route = select_flashcard_profile(learner_text)
        if route.kind is FlashcardProfileRouteKind.CLARIFICATION:
            return AskLearnerDecision(route.clarification or "Quale profilo preferisci?")
        return StartCapabilityDecision(
            _PROPOSE_FLASHCARDS,
            _flashcard_inputs(learner_text),
            None,
        )


def _is_flashcard_generation_request(learner_text: str) -> bool:
    """Recognize an explicit card-generation action, not a card-related question."""

    for match in _FLASHCARD_REQUEST.finditer(learner_text):
        prefix = learner_text[: match.start()]
        if _FLASHCARD_META_PREFIX.search(prefix):
            continue
        clause_start = max(
            (boundary.end() for boundary in _FLASHCARD_CLAUSE_BOUNDARY.finditer(prefix)),
            default=0,
        )
        if not _FLASHCARD_NEGATION.search(prefix[clause_start:]):
            return True
    return False


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


def _omitted_conversation_entries(context: TutorHostContext) -> int:
    window = context.tutor_snapshot.get("conversation_window")
    if not isinstance(window, Mapping):
        return 0
    omitted = window.get("omitted_entries")
    return omitted if type(omitted) is int and omitted > 0 else 0


def _observed_conversation_history(context: TutorHostContext) -> bool:
    observations = context.tutor_snapshot.get("agent_observations")
    if not isinstance(observations, tuple):
        return False
    return any(
        isinstance(item, Mapping)
        and item.get("tool_name") in {"conversation.search", "conversation.read"}
        and item.get("status") == "succeeded"
        for item in observations
    )


def _has_conversation_read_tool(context: TutorHostContext) -> bool:
    tools = context.tutor_snapshot.get("harness_tools")
    return isinstance(tools, tuple) and any(
        isinstance(item, Mapping) and item.get("name") == "conversation.read" for item in tools
    )


def _oldest_included_conversation_sequence(context: TutorHostContext) -> int | None:
    candidates: list[int] = []
    for field in ("timeline", "tutor_presentations"):
        entries = context.tutor_snapshot.get(field)
        if not isinstance(entries, tuple):
            continue
        candidates.extend(
            sequence
            for item in entries
            if isinstance(item, Mapping) and type(sequence := item.get("course_sequence")) is int
        )
    return min(candidates) if candidates else None


def _bounded_memory_flashcard_decision(
    decision: StartCapabilityDecision, context: TutorHostContext
) -> StartCapabilityDecision:
    """Persist only a topic sketch derived from the canonical current request."""

    inputs = dict(decision.inputs)
    learner_text = _latest_learner_text(context)
    if learner_text is None:
        raise ValueError("memory-informed flashcards require a learner request")
    topic_terms = tuple(
        dict.fromkeys(
            token.casefold()
            for token in re.findall(r"[\wÀ-ÿ-]+", learner_text, re.UNICODE)
            if 2 <= len(token) <= 40 and token.casefold() not in _MEMORY_TOPIC_STOPWORDS
        )
    )[:6]
    if not topic_terms:
        raise ValueError("memory-informed flashcard query has no bounded topic terms")
    observations = context.tutor_snapshot.get("agent_observations")
    consulted = 0
    if isinstance(observations, tuple):
        for observation in observations:
            if not isinstance(observation, Mapping):
                continue
            result = observation.get("result")
            if not isinstance(result, Mapping):
                continue
            entries = result.get("entries")
            if isinstance(entries, tuple):
                consulted += len(entries)
    topic_query = " ".join(topic_terms)
    inputs["query"] = topic_query
    inputs["scope"] = topic_query
    inputs["continuation_summary_json"] = json.dumps(
        {
            "topic_terms": topic_terms,
            "messages_consulted": min(consulted, 24),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return StartCapabilityDecision(decision.capability_id, inputs, decision.progress_message)


__all__ = ["FlashcardProfileRoutingTutorDecisionPort"]
