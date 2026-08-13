"""Minimal reference harness over the canonical grounded-question service."""

from __future__ import annotations

from collections.abc import AsyncIterator

from cardine.application.grounding_ask import (
    GroundingAskError,
    GroundingAskErrorCode,
    GroundingAskService,
)
from study_agent.domain import ExecutionContext
from study_agent.domain._validation import freeze_object
from study_agent.tools.builtin import _study_event
from study_agent.tools.contracts import StudyEvent, StudyEventKind


class StudyHarness:
    def __init__(self, grounding: GroundingAskService) -> None:
        self._grounding = grounding

    async def ask(self, question: str, context: ExecutionContext) -> AsyncIterator[StudyEvent]:
        try:
            result = await self._grounding.ask(question, context)
        except GroundingAskError as error:
            kind = (
                StudyEventKind.GROUNDING_SUSPENDED
                if error.code is GroundingAskErrorCode.SUSPENDED
                else StudyEventKind.GROUNDING_FAILED
            )
            data: dict[str, object] = {
                "course_id": str(context.course_id),
                "error_code": error.code.value,
            }
            if context.session_id is not None:
                data["session_id"] = str(context.session_id)
            yield StudyEvent(kind, freeze_object(data))  # type: ignore[arg-type]
            return
        except Exception:
            # The reference harness is an application boundary: unexpected
            # dependency failures must not expose provider, source, or secret
            # details to its event consumer.  Cancellation and process-control
            # exceptions remain untouched because they do not derive from
            # Exception.
            data = {
                "course_id": str(context.course_id),
                "error_code": GroundingAskErrorCode.EXECUTION_FAILED.value,
            }
            if context.session_id is not None:
                data["session_id"] = str(context.session_id)
            yield StudyEvent(StudyEventKind.GROUNDING_FAILED, freeze_object(data))  # type: ignore[arg-type]
            return
        for event in result.events:
            yield _study_event(event)
