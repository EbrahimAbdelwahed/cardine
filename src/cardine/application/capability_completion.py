"""Closed product recovery for verified tutor capability completions.

The host runner exposes only a :class:`TutorCapabilityCompletionReference`.
This module keeps owner recovery private and gives the conversation application
one bounded, typed receipt that can be turned into a canonical presentation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from cardine.hosts import TutorCapabilityCompletionReference
from study_agent.domain import ExecutionContext, RunId

MAX_COMPLETION_CONTENT_CHARS = 4_000
MAX_CANONICAL_IDS = 64


@dataclass(frozen=True, slots=True)
class CapabilityCompletionProductReceipt:
    """Bounded result recovered from one closed capability owner."""

    capability_identity: str
    run_id: RunId
    content: str
    canonical_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.capability_identity, str) or not self.capability_identity:
            raise ValueError("completion capability identity is invalid")
        if not isinstance(self.run_id, RunId):
            raise TypeError("completion receipt run_id must be RunId")
        if (
            not isinstance(self.content, str)
            or not self.content
            or self.content != self.content.strip()
            or len(self.content) > MAX_COMPLETION_CONTENT_CHARS
        ):
            raise ValueError("completion receipt content is invalid")
        ids = tuple(self.canonical_ids)
        if len(ids) > MAX_CANONICAL_IDS or any(
            not isinstance(item, str) or not item or item != item.strip() or len(item) > 256
            for item in ids
        ):
            raise ValueError("completion receipt canonical ids are invalid")
        if ids != tuple(sorted(set(ids))):
            raise ValueError("completion receipt canonical ids must be sorted and unique")
        object.__setattr__(self, "canonical_ids", ids)


class CapabilityCompletionHandler(Protocol):
    def recover(
        self,
        reference: TutorCapabilityCompletionReference,
        context: ExecutionContext | None = None,
    ) -> CapabilityCompletionProductReceipt | None: ...


class CapabilityCompletionHandlerRegistry:
    """Closed registry keyed by capability identity and manifest fingerprint."""

    def __init__(
        self,
        handlers: Mapping[
            tuple[str, str], CapabilityCompletionHandler
        ] | Sequence[tuple[str, str, CapabilityCompletionHandler]] = (),
    ) -> None:
        if isinstance(handlers, Mapping):
            entries = tuple(
                (key[0], key[1], value) for key, value in handlers.items()
            )
        else:
            entries = tuple(handlers)
        normalized: dict[tuple[str, str], CapabilityCompletionHandler] = {}
        for entry in entries:
            if len(entry) != 3:
                raise ValueError(
                    "completion handler entries require identity, fingerprint, handler"
                )
            identity, fingerprint, handler = entry
            if not isinstance(identity, str) or not identity:
                raise ValueError("completion handler identity is invalid")
            if not isinstance(fingerprint, str) or len(fingerprint) != 64:
                raise ValueError("completion handler manifest fingerprint is invalid")
            if not callable(getattr(handler, "recover", None)):
                raise TypeError("completion handler must expose recover")
            key = (identity, fingerprint)
            if key in normalized:
                raise ValueError("completion handler identities must be unique")
            normalized[key] = handler
        self._handlers = normalized

    def recover(
        self,
        reference: TutorCapabilityCompletionReference,
        context: ExecutionContext | None = None,
    ) -> CapabilityCompletionProductReceipt | None:
        if not isinstance(reference, TutorCapabilityCompletionReference):
            raise TypeError("completion reference is invalid")
        handler = self._handlers.get(
            (reference.capability_identity, reference.manifest_fingerprint)
        )
        if handler is None:
            return None
        if context is None:
            receipt = handler.recover(reference)
        else:
            try:
                receipt = handler.recover(reference, context)
            except TypeError:
                # Preserve the closed one-argument adapter contract for
                # existing private handlers; the repository owner opts into
                # context to verify the exact gateway retry fingerprint.
                receipt = handler.recover(reference)
        if receipt is None:
            return None
        if (
            not isinstance(receipt, CapabilityCompletionProductReceipt)
            or receipt.capability_identity != reference.capability_identity
            or receipt.run_id != reference.run_id
        ):
            raise ValueError("completion handler returned an unbound receipt")
        return receipt


__all__ = [
    "MAX_CANONICAL_IDS",
    "MAX_COMPLETION_CONTENT_CHARS",
    "CapabilityCompletionHandler",
    "CapabilityCompletionHandlerRegistry",
    "CapabilityCompletionProductReceipt",
]
