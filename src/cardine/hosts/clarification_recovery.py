"""One bounded semantic retry after an answered tutor clarification."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from study_agent.ports.tutor_host import TutorDecisionPort, TutorInterruptionToken

from .contracts import AskLearnerDecision, TutorDecision, TutorHostContext

_RECOVERY_INSTRUCTION = (
    "Treat the current answer as resolving the previous question. "
    "Choose the study action now; ask again only if the answer is genuinely unusable."
)


class ClarificationRecoveryTutorDecisionPort(TutorDecisionPort):
    """Retry one repeated clarification with the latest exchange made explicit."""

    def __init__(self, delegate: TutorDecisionPort) -> None:
        if not hasattr(delegate, "decide"):
            raise TypeError("clarification recovery requires a tutor decision port")
        self._delegate = delegate

    async def decide(
        self, context: TutorHostContext, interruption: TutorInterruptionToken
    ) -> TutorDecision:
        decision = await self._delegate.decide(context, interruption)
        if context.pending_continuation is not None or not isinstance(
            decision, AskLearnerDecision
        ):
            return decision
        exchange = _latest_clarification_exchange(context)
        if exchange is None:
            return decision
        previous_question, current_answer = exchange
        retry_context = replace(
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
        try:
            return await self._delegate.decide(retry_context, interruption)
        except Exception:
            if interruption.is_interrupted():
                raise
            return decision


def _latest_clarification_exchange(context: TutorHostContext) -> tuple[str, str] | None:
    timeline = context.tutor_snapshot.get("timeline")
    presentations = context.tutor_snapshot.get("tutor_presentations")
    if not isinstance(timeline, tuple) or not isinstance(presentations, tuple):
        return None
    latest_learner = next(
        (
            item
            for item in reversed(timeline)
            if isinstance(item, Mapping) and item.get("kind") == "learner"
        ),
        None,
    )
    latest_question = presentations[-1] if presentations else None
    if latest_learner is None or not isinstance(latest_question, Mapping):
        return None
    learner_sequence = latest_learner.get("course_sequence")
    question_sequence = latest_question.get("course_sequence")
    current_answer = latest_learner.get("content")
    previous_question = latest_question.get("content")
    if (
        type(learner_sequence) is not int
        or type(question_sequence) is not int
        or latest_question.get("kind") != "learner_question"
        or question_sequence >= learner_sequence
        or not isinstance(current_answer, str)
        or not current_answer.strip()
        or not isinstance(previous_question, str)
        or not previous_question.strip()
    ):
        return None
    return previous_question, current_answer


__all__ = ["ClarificationRecoveryTutorDecisionPort"]
