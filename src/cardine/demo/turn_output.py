"""Bounded process-local draft output, separate from canonical presentations."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Lock

from study_agent.domain._validation import JsonObject

_OUTPUT: ContextVar[Callable[[str], None] | None] = ContextVar("cardine_turn_output", default=None)
MAX_DRAFT_CHARACTERS = 16_000


def output_observer() -> Callable[[str], None] | None:
    return _OUTPUT.get()


class TurnOutputStore:
    """Drafts are never diagnostics, citations, actions or study evidence.

    The HTTP surface applies the same authentication as other course reads.
    Completion erases the draft; the validated presentation owns the answer.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._drafts: OrderedDict[str, str] = OrderedDict()

    @contextmanager
    def capture(self, request_id: str) -> Iterator[None]:
        with self._lock:
            self._drafts[request_id] = ""
            self._drafts.move_to_end(request_id)
            if len(self._drafts) > 24:
                self._drafts.popitem(last=False)

        def publish(text: str) -> None:
            with self._lock:
                if request_id in self._drafts:
                    self._drafts[request_id] = text[:MAX_DRAFT_CHARACTERS]

        token = _OUTPUT.set(publish)
        try:
            yield
        finally:
            _OUTPUT.reset(token)
            with self._lock:
                self._drafts.pop(request_id, None)

    def snapshot(self, request_id: str) -> JsonObject:
        with self._lock:
            text = self._drafts.get(request_id)
            return {
                "schema_version": 1,
                "state": "generating" if text is not None else "unavailable",
                "text": text or "",
                "verified": False,
            }
