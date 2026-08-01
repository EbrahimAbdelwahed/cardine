from __future__ import annotations

import pytest

from study_agent.application import (
    ConversationTurnCommand,
    ConversationTurnError,
    ConversationTurnErrorCode,
)
from study_agent.domain import CorrelationId, CourseId, ExecutionContext, PrincipalKind, SessionId

COURSE = CourseId("unit-course")
SESSION = SessionId("unit-session")


def _context(request_id: str | None = "request-1") -> ExecutionContext:
    return ExecutionContext(
        PrincipalKind.HUMAN,
        "learner",
        COURSE,
        CorrelationId("unit-correlation"),
        session_id=SESSION,
        idempotency_key=request_id,
    )


def test_command_requires_session_and_server_owned_request_identity() -> None:
    with pytest.raises(ValueError, match="server-owned"):
        ConversationTurnCommand("hello", _context(None), 0)

    no_session = ExecutionContext(
        PrincipalKind.HUMAN,
        "learner",
        COURSE,
        CorrelationId("unit-correlation"),
        idempotency_key="request-1",
    )
    with pytest.raises(ValueError, match="session context"):
        ConversationTurnCommand("hello", no_session, 0)


@pytest.mark.parametrize(
    "content,sequence",
    [("", 0), (" hello", 0), ("hello ", 0), ("hello", -1), ("hello", True)],
)
def test_command_rejects_blank_untrimmed_or_invalid_sequence(content: str, sequence: int) -> None:
    with pytest.raises((ValueError, TypeError)):
        ConversationTurnCommand(content, _context(), sequence)


def test_error_codes_are_stable_and_messages_are_nonblank() -> None:
    for code in ConversationTurnErrorCode:
        error = ConversationTurnError(code, code.value)
        assert error.code is code
        assert str(error) == code.value
