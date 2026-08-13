"""Host-side enforcement for explicit requests about uploaded sources."""

from __future__ import annotations

import re
from collections.abc import Mapping

from study_agent.ports.tutor_host import TutorDecisionPort, TutorInterruptionToken

from .contracts import (
    StartCapabilityDecision,
    TutorDecision,
    TutorHostContext,
)

_EXPLAIN_CAPABILITY_ID = "explain_concept"
_SOURCE_REFERENCE = re.compile(
    r"\b(?:source|sources|fonte|fonti|materiale|materiali|documento|documenti|"
    r"file|appunti|note|notes)\b",
    re.IGNORECASE,
)
_SOURCE_EXPLANATION_REQUEST = re.compile(
    r"\b(?:read|explain|summari[sz]e|describe|tell|leggi|leggere|spiega|"
    r"spiegami|spiegare|spiegazione|parliamo|"
    r"riassumi|riassunto|descrivi|cosa\s+dice)\b",
    re.IGNORECASE,
)
_LESSON_REFERENCE = re.compile(
    r"(?:\blezione\s+(?:numero\s+)?\d+\b|"
    r"\bl[\s_-]*0*\d+(?=$|[\s_./-]))",
    re.IGNORECASE,
)
_LESSON_STUDY_REQUEST = re.compile(
    r"\b(?:studiamo|studiare|riprendiamo|riprendere|"
    r"di\s+cosa\s+parla|cosa\s+tratta|what\s+is\s+it\s+about|"
    r"let(?:'s|\s+us)\s+study)\b",
    re.IGNORECASE,
)
_DIRECT_LESSON_START = re.compile(
    r"\b(?:facciamo|avvia|avviare|inizia|iniziare)\s+(?:la\s+)?"
    r"(?:lezione\s+(?:numero\s+)?\d+|"
    r"l[\s_-]*0*\d+(?=$|[\s_./-]))",
    re.IGNORECASE,
)
_ITALIAN_REQUEST = re.compile(
    r"\b(?:fonte|fonti|materiale|materiali|documento|documenti|leggi|leggere|"
    r"spiega|spiegami|spiegare|spiegazione|avvia|avviare|inizia|iniziare|"
    r"parliamo|riassumi|riassunto|descrivi|cosa)\b",
    re.IGNORECASE,
)
_RETRIEVAL_STOP_WORDS = frozenset(
    {
        "a", "about", "and", "avvia", "avviare", "che", "cosa", "dalla", "dalle", "del", "della",
        "delle", "di", "does", "e", "explain", "fonte", "from", "ha", "how", "i",
        "il", "in", "inizia", "iniziare", "it", "la", "le", "leggi", "materiale", "me",
        "many", "parliamo", "quante", "read", "say", "source", "spiega", "spiegami",
        "spiegazione", "studiamo", "studiare", "facciamo", "fare", "riprendiamo",
        "riprendere", "parla", "tratta", "the", "this", "to", "una", "what", "with",
    }
)


class SourceGroundedTutorDecisionPort(TutorDecisionPort):
    """Prevent a source-directed learner request from ending in a bare acknowledgement."""

    def __init__(self, delegate: TutorDecisionPort) -> None:
        if not hasattr(delegate, "decide"):
            raise TypeError("source grounding requires a tutor decision port")
        self._delegate = delegate

    async def decide(
        self, context: TutorHostContext, interruption: TutorInterruptionToken
    ) -> TutorDecision:
        decision = await self._delegate.decide(context, interruption)
        return _require_grounded_explanation(decision, context)


def _require_grounded_explanation(
    decision: TutorDecision, context: TutorHostContext
) -> TutorDecision:
    """Select the advertised evidence-bound capability for an explicit source request."""

    if context.pending_continuation is not None:
        return decision
    learner_text = _latest_learner_text(context)
    if learner_text is None or not _is_source_explanation_request(learner_text):
        return decision
    if not _has_materials(context) or not any(
        item.id == _EXPLAIN_CAPABILITY_ID for item in context.advertised_capabilities
    ):
        return decision
    if (
        isinstance(decision, StartCapabilityDecision)
        and decision.capability_id == _EXPLAIN_CAPABILITY_ID
    ):
        return decision
    return StartCapabilityDecision(
        _EXPLAIN_CAPABILITY_ID,
        {
            "query": _retrieval_query(learner_text),
            "target": learner_text,
            "language": "it" if _ITALIAN_REQUEST.search(learner_text) else "en",
            "learner_goal": None,
            "continuation_summary_json": None,
        },
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


def _has_materials(context: TutorHostContext) -> bool:
    materials = context.tutor_snapshot.get("materials")
    return isinstance(materials, tuple) and bool(materials)


def _is_source_explanation_request(text: str) -> bool:
    # Once canonical course materials are available, an explicit request to
    # read or explain is source-first even if the learner names only a topic
    # (for example, "Leggi biochimica").  Requiring a literal word such as
    # "fonte" leaves the most natural requests to model guesswork and permits
    # a bare acknowledgement instead of the evidence-bound capability.
    return bool(
        _SOURCE_EXPLANATION_REQUEST.search(text)
        or _DIRECT_LESSON_START.search(text)
        or (_LESSON_REFERENCE.search(text) and _LESSON_STUDY_REQUEST.search(text))
    )


def _retrieval_query(learner_text: str) -> str:
    """Keep content words for the FTS adapter's literal-AND query contract."""

    terms = tuple(
        term
        for term in re.findall(r"[^\W_]+", learner_text.casefold(), flags=re.UNICODE)
        if term not in _RETRIEVAL_STOP_WORDS
    )
    return " ".join(terms) if terms else learner_text


__all__ = ["SourceGroundedTutorDecisionPort"]
