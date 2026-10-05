"""Host lifecycle state for an answered tutor clarification (ADR-0027).

This reads only canonical ordering and kinds; it never interprets learner language.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .contracts import TutorHostContext


def answered_clarification(context: TutorHostContext) -> tuple[str, str] | None:
    """Return ``(question, answer)`` when the newest learner message answers the
    newest tutor ``learner_question`` and no continuation is pending."""

    if context.pending_continuation is not None:
        return None
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
    answer = latest_learner.get("content")
    question = latest_question.get("content")
    if (
        type(learner_sequence) is not int
        or type(question_sequence) is not int
        or latest_question.get("kind") != "learner_question"
        or question_sequence >= learner_sequence
        or not isinstance(answer, str)
        or not answer.strip()
        or not isinstance(question, str)
        or not question.strip()
    ):
        return None
    return question, answer


__all__ = ["answered_clarification"]
