"""One bounded semantic retry after an answered tutor clarification."""

from __future__ import annotations

from dataclasses import replace

from study_agent.ports.tutor_host import TutorDecisionPort, TutorInterruptionToken

from .clarification_state import answered_clarification
from .contracts import TutorDecision, TutorHostContext

_RECOVERY_INSTRUCTION = (
    "Treat the current answer as resolving the previous question. "
    "Choose the study action now. Another question is unavailable; if the answer is "
    "genuinely unusable, say briefly in an assistant_message what is needed."
)


class ClarificationRecoveryTutorDecisionPort(TutorDecisionPort):
    """Make an answered clarification explicit before the only model call."""

    def __init__(self, delegate: TutorDecisionPort) -> None:
        if not hasattr(delegate, "decide"):
            raise TypeError("clarification recovery requires a tutor decision port")
        self._delegate = delegate

    async def decide(
        self, context: TutorHostContext, interruption: TutorInterruptionToken
    ) -> TutorDecision:
        exchange = answered_clarification(context)
        if exchange is None:
            return await self._delegate.decide(context, interruption)
        previous_question, current_answer = exchange
        resolved_context = replace(
            context,
            tutor_snapshot={
                **context.tutor_snapshot,
                "clarification_resolution": {
                    "previous_question": previous_question,
                    "current_answer": current_answer,
                    "instruction": _RECOVERY_INSTRUCTION,
                },
            },
        )
        return await self._delegate.decide(resolved_context, interruption)


__all__ = ["ClarificationRecoveryTutorDecisionPort"]
