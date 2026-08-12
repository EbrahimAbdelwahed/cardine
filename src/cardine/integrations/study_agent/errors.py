"""Closed Cardine-owned failures for runtime composition and leasing."""

from __future__ import annotations

from study_agent.retrieval import SourceContentError, SourceContentErrorCode


class CardineError(RuntimeError):
    """Base class for failures visible across the lifecycle seam."""

    _default_message = "cardine runtime operation failed"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(self._default_message if message is None else message)


class CardineConfigError(CardineError):
    _default_message = "cardine runtime configuration is invalid"


class CardineUnavailableError(CardineError):
    _default_message = "cardine repository is unavailable"


class CardineInternalError(CardineError):
    _default_message = "cardine runtime failed internally"


class CardineSourceContentUnavailableError(CardineError):
    _default_message = "canonical source content is unavailable"


def translate_exception(error: Exception) -> CardineError:
    """Translate a foreign lifecycle failure without exposing its details."""

    if isinstance(error, CardineError):
        return error
    if isinstance(error, SourceContentError):
        if error.code is SourceContentErrorCode.NOT_FOUND:
            return CardineSourceContentUnavailableError()
        return CardineInternalError()
    if isinstance(error, (OSError, TimeoutError, ConnectionError)):
        return CardineUnavailableError()
    return CardineInternalError()


__all__ = [
    "CardineConfigError",
    "CardineError",
    "CardineInternalError",
    "CardineSourceContentUnavailableError",
    "CardineUnavailableError",
    "translate_exception",
]
