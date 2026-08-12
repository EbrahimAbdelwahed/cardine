"""Small conversation-first consumer for the public tutor contracts.

The product shell intentionally owns presentation state only.  It reads a
sequence-consistent :class:`TutorSnapshotV1`, discovers capabilities through
the public gateway port, and accepts host results from an embedding host.  It
does not select capabilities, append events, read SQLite, or call a model
provider.

``cardine-shell`` is a deterministic terminal wrapper around the existing
anatomy host trace.  The reusable :class:`ProductShell` class is suitable for a
terminal, notebook, or a future UI without making any of those surfaces part
of the harness core.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, cast

from study_agent.capabilities import CapabilityManifest
from study_agent.domain import (
    CourseId,
    SessionId,
    TutorContextField,
    TutorHintDivergence,
    TutorMaterialSummary,
    TutorSnapshotV1,
    TutorTimelineEntry,
)
from study_agent.domain._validation import JsonObject
from cardine.hosts import (
    PendingContinuationDescriptor,
    TutorHostRunResult,
    TutorHostRunStatus,
)
from study_agent.ports import TutorCapabilityGatewayPort, TutorSnapshotPort

MAX_LEARNER_ENTRY_CHARS = 4_000


class ProductShellStatus(StrEnum):
    """Presentation states exposed by the thin product surface."""

    READY = "ready"
    WORKING = "working"
    NEEDS_LEARNER_INPUT = "needs_learner_input"
    SUSPENDED = "suspended"
    CONFLICTED_CONTEXT = "conflicted_context"
    NEEDS_REVIEW = "needs_review"
    STALE = "stale"
    DEGRADED = "degraded"
    RECOVERED = "recovered"


@dataclass(frozen=True, slots=True)
class DueReview:
    """Presentation-only due item returned by an optional recall view."""

    review_id: str
    label: str
    due_at: datetime

    def __post_init__(self) -> None:
        if not self.review_id.strip():
            raise ValueError("review_id must be non-empty")
        if not self.label.strip():
            raise ValueError("label must be non-empty")
        if self.due_at.tzinfo is None or self.due_at.utcoffset() is None:
            raise ValueError("due_at must be timezone-aware")

    def to_json(self) -> JsonObject:
        return {
            "review_id": self.review_id,
            "label": self.label,
            "due_at": self.due_at.isoformat(),
        }


class DueReviewViewPort(Protocol):
    """Optional TUT-07 read seam; absence is a supported composition."""

    def due(self, course_id: CourseId) -> Sequence[DueReview]: ...


class TutorConversationHostPort(Protocol):
    """Embedding-host seam for one free-form learner turn."""

    async def respond(
        self,
        course_id: CourseId,
        session_id: SessionId,
        learner_entry: str,
        *,
        pending_fingerprint: str | None = None,
    ) -> TutorHostRunResult: ...


@dataclass(frozen=True, slots=True)
class ProductShellView:
    """Immutable view model derived from one public tutor snapshot."""

    status: ProductShellStatus
    learner_entry: str | None
    assistant_message: str | None
    snapshot: TutorSnapshotV1 | None
    evidence_through_sequence: int | None
    capabilities: tuple[str, ...] = ()
    due_reviews: tuple[DueReview, ...] = ()
    continuation_request: str | None = None
    warning: str | None = None

    def __post_init__(self) -> None:
        if self.learner_entry is not None and len(self.learner_entry) > MAX_LEARNER_ENTRY_CHARS:
            raise ValueError("learner_entry exceeds the shell text bound")
        if (
            self.assistant_message is not None
            and len(self.assistant_message) > MAX_LEARNER_ENTRY_CHARS
        ):
            raise ValueError("assistant_message exceeds the shell text bound")
        if self.continuation_request is not None and not self.continuation_request.strip():
            raise ValueError("continuation_request must be non-empty when provided")
        if self.snapshot is None:
            if self.evidence_through_sequence is not None:
                raise ValueError("evidence sequence requires a snapshot")
        elif self.evidence_through_sequence != self.snapshot.high_water_sequence:
            raise ValueError("evidence sequence must match the snapshot high-water sequence")
        object.__setattr__(self, "capabilities", tuple(sorted(set(self.capabilities))))
        object.__setattr__(self, "due_reviews", tuple(self.due_reviews))

    @property
    def materials(self) -> tuple[TutorMaterialSummary, ...]:
        return () if self.snapshot is None else self.snapshot.materials

    @property
    def context(self) -> tuple[TutorContextField, ...]:
        return () if self.snapshot is None else self.snapshot.learner_context

    @property
    def divergences(self) -> tuple[TutorHintDivergence, ...]:
        return () if self.snapshot is None else self.snapshot.divergences

    @property
    def timeline(self) -> tuple[TutorTimelineEntry, ...]:
        return () if self.snapshot is None else self.snapshot.timeline

    def to_json(self) -> JsonObject:
        return {
            "status": self.status.value,
            "learner_entry": self.learner_entry,
            "assistant_message": self.assistant_message,
            "snapshot": None if self.snapshot is None else self.snapshot.to_json(),
            "evidence_through_sequence": self.evidence_through_sequence,
            "capabilities": self.capabilities,
            "due_reviews": tuple(item.to_json() for item in self.due_reviews),
            "continuation_request": self.continuation_request,
            "warning": self.warning,
        }


class ProductShell:
    """Presentation adapter over public snapshot, gateway, and host contracts."""

    def __init__(
        self,
        snapshots: TutorSnapshotPort,
        capabilities: TutorCapabilityGatewayPort,
        due_reviews: DueReviewViewPort | None = None,
    ) -> None:
        self._snapshots = snapshots
        self._capabilities = capabilities
        self._due_reviews = due_reviews
        self._last_status: ProductShellStatus | None = None
        self._last_entry: str | None = None
        self._pending_fingerprint: str | None = None

    def view(self, course_id: CourseId, session_id: SessionId) -> ProductShellView:
        """Read a fresh view without mutating canonical state."""

        snapshot = self._snapshots.get(course_id, session_id)
        return self._view_for(
            course_id,
            snapshot,
            base_status=ProductShellStatus.READY,
            learner_entry=self._last_entry,
        )

    def begin(
        self,
        course_id: CourseId,
        session_id: SessionId,
        learner_entry: str,
    ) -> ProductShellView:
        """Accept free-form text immediately and expose a working view."""

        entry = _bounded_entry(learner_entry)
        self._last_entry = entry
        self._pending_fingerprint = None
        snapshot = self._snapshots.get(course_id, session_id)
        return self._view_for(
            course_id,
            snapshot,
            base_status=ProductShellStatus.WORKING,
            learner_entry=entry,
        )

    async def submit(
        self,
        course_id: CourseId,
        session_id: SessionId,
        learner_entry: str,
        host: TutorConversationHostPort,
    ) -> ProductShellView:
        """Run one host turn, then refresh the public snapshot."""

        self.begin(course_id, session_id, learner_entry)
        result = await host.respond(
            course_id,
            session_id,
            self._last_entry or "",
            pending_fingerprint=self._pending_fingerprint,
        )
        return self.apply_result(course_id, session_id, result)

    def apply_result(
        self,
        course_id: CourseId,
        session_id: SessionId,
        result: TutorHostRunResult,
    ) -> ProductShellView:
        """Map one trusted host result to a refreshed shell view."""

        if not isinstance(result, TutorHostRunResult):
            raise TypeError("result must be TutorHostRunResult")
        snapshot = self._snapshots.get(course_id, session_id)
        raw_status = result.status.value
        if raw_status == "stale":
            base = ProductShellStatus.STALE
        elif result.status is TutorHostRunStatus.SUSPENDED:
            base = ProductShellStatus.SUSPENDED
        elif result.status is TutorHostRunStatus.NEEDS_LEARNER_INPUT:
            base = ProductShellStatus.NEEDS_LEARNER_INPUT
        elif result.status is TutorHostRunStatus.IN_PROGRESS:
            base = ProductShellStatus.WORKING
        elif result.status is TutorHostRunStatus.COMPLETED:
            base = (
                ProductShellStatus.RECOVERED
                if self._last_status is ProductShellStatus.STALE
                else ProductShellStatus.READY
            )
        elif result.status in {
            TutorHostRunStatus.FAILED,
            TutorHostRunStatus.INTERRUPTED,
            TutorHostRunStatus.BUDGET_EXHAUSTED,
        }:
            base = ProductShellStatus.DEGRADED
        else:
            base = ProductShellStatus.DEGRADED

        continuation = result.pending_continuation
        self._pending_fingerprint = None if continuation is None else continuation.fingerprint
        view = self._view_for(
            course_id,
            snapshot,
            base_status=base,
            learner_entry=self._last_entry,
            assistant_message=_result_message(result),
            continuation=continuation,
        )
        self._last_status = view.status
        return view

    def mark_stale(self, course_id: CourseId, session_id: SessionId) -> ProductShellView:
        """Expose a host-requested stale refresh before re-running a turn."""

        snapshot = self._snapshots.get(course_id, session_id)
        view = self._view_for(
            course_id,
            snapshot,
            base_status=ProductShellStatus.STALE,
            learner_entry=self._last_entry,
        )
        self._last_status = view.status
        return view

    def _view_for(
        self,
        course_id: CourseId,
        snapshot: TutorSnapshotV1,
        *,
        base_status: ProductShellStatus,
        learner_entry: str | None,
        assistant_message: str | None = None,
        continuation: PendingContinuationDescriptor | None = None,
    ) -> ProductShellView:
        capabilities, capability_warning = _discover(self._capabilities)
        due, due_warning = _read_due(self._due_reviews, course_id)
        warning = capability_warning or due_warning
        status = _prioritize_status(base_status, snapshot, due, warning)
        return ProductShellView(
            status,
            learner_entry,
            assistant_message,
            snapshot,
            snapshot.high_water_sequence,
            capabilities,
            due,
            None if continuation is None else continuation.dialogue_request,
            warning,
        )


def render(view: ProductShellView) -> str:
    """Render a stable, accessible terminal view with no ANSI control codes."""

    lines = ["STUDY AGENT PRODUCT SHELL", "", f"Status: {view.status.value}"]
    if view.learner_entry is not None:
        lines.extend((f"Learner > {view.learner_entry}", ""))
    if view.assistant_message is not None:
        lines.extend((f"Tutor > {view.assistant_message}", ""))
    if view.continuation_request is not None:
        lines.extend((f"Needs learner input > {view.continuation_request}", ""))
    if view.snapshot is not None:
        lines.extend(
            (
                "MATERIAL",
                *(f"  - {item.title} ({item.chunk_count} chunks)" for item in view.materials),
                "",
                f"EVIDENCE THROUGH SEQUENCE {view.evidence_through_sequence}",
                *(f"  - {field.kind.value}: {field.state.value}" for field in view.context),
            )
        )
        if view.divergences:
            lines.extend(
                ("", "CONTEXT CONFLICTS", *(f"  - {item.kind.value}" for item in view.divergences))
            )
        if view.timeline:
            lines.extend(
                (
                    "",
                    "CONVERSATION",
                    *(f"  - {item.kind.value}: {item.content}" for item in view.timeline),
                )
            )
    if view.due_reviews:
        lines.extend(
            (
                "",
                "DUE REVIEW",
                *(f"  - {item.label} ({item.due_at.isoformat()})" for item in view.due_reviews),
            )
        )
    elif view.status is ProductShellStatus.NEEDS_REVIEW:
        lines.extend(("", "DUE REVIEW", "  - review is due"))
    if view.capabilities:
        lines.extend(("", f"CAPABILITIES: {', '.join(view.capabilities)}"))
    if view.warning is not None:
        lines.extend(("", f"Warning: {view.warning}"))
    return "\n".join(lines)


def run_offline_shell_demo(
    learner_entry: str = "I have ten minutes. Help me understand heart valves.",
) -> JsonObject:
    """Return the existing deterministic anatomy trace in shell-shaped form."""

    # Import lazily so importing the reusable shell never imports an adapter or
    # constructs a demo provider.  The anatomy demo itself uses only recorded
    # Responses data and a scripted gateway.
    from .anatomy import run_reference_demo

    result = run_reference_demo(learner_entry)
    return cast(
        JsonObject,
        {
            "learner_entry": result["learner_entry"],
            "status": "recovered",
            "status_trace": result["timeline"],
            "material": result["source_state"],
            "evidence_sequence": result["evidence_refresh_sequence"],
            "context_state": result["context_state"],
            "capabilities": result["discovered_capabilities"],
            "due_review": {
                "status": "unavailable",
                "items": (),
                "message": (
                    "Il ripasso programmato non è attivo. "
                    "Puoi comunque studiare dalla chat e dalle fonti."
                ),
            },
            "optional_due_review": "unavailable (TUT-07 is optional)",
            "parity": result["parity"],
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="cardine-shell",
        description="Run the deterministic offline conversation-first product shell.",
    )
    parser.add_argument(
        "learner_prompt",
        nargs="?",
        default="I have ten minutes. Help me understand heart valves.",
        help="free-form learner entry",
    )
    parser.add_argument("--json", action="store_true", help="emit inspectable JSON")
    args = parser.parse_args()
    result = run_offline_shell_demo(args.learner_prompt)
    if args.json:
        print(json.dumps(result, sort_keys=True, default=list, indent=2))
    else:
        print(
            "\n".join(
                (
                    "STUDY AGENT PRODUCT SHELL — OFFLINE DEMO",
                    "",
                    f"Learner > {result['learner_entry']}",
                    "",
                    "STATUS TRACE",
                    *(
                        f"  {item['step']}. {item['status']} — {item['detail']}"
                        for item in cast(Sequence[dict[str, object]], result["status_trace"])
                    ),
                    "",
                    "Material, evidence refresh, capability discovery, and optional review "
                    "are inspectable with --json.",
                    "Offline proof complete: no network, credentials, model SDK, or provider call.",
                )
            )
        )


def _bounded_entry(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("learner_entry must be a string")
    entry = value.strip()
    if not entry:
        raise ValueError("learner_entry must be non-empty")
    if len(entry) > MAX_LEARNER_ENTRY_CHARS:
        raise ValueError("learner_entry exceeds the shell text bound")
    return entry


def _result_message(result: TutorHostRunResult) -> str | None:
    if result.text is not None:
        return result.text
    output = result.completed_output
    if not isinstance(output, Mapping):
        return None
    for key in ("tutor_message", "message", "answer"):
        value = output.get(key)
        if isinstance(value, str) and value.strip() and len(value) <= MAX_LEARNER_ENTRY_CHARS:
            return value
    return None


def _discover(
    gateway: TutorCapabilityGatewayPort,
) -> tuple[tuple[str, ...], str | None]:
    try:
        manifests = tuple(gateway.discover())
    except Exception:
        return (), "capability discovery unavailable"
    if not all(isinstance(item, CapabilityManifest) for item in manifests):
        return (), "capability discovery returned an invalid manifest"
    return tuple(sorted(item.identity for item in manifests)), None


def _read_due(
    port: DueReviewViewPort | None,
    course_id: CourseId,
) -> tuple[tuple[DueReview, ...], str | None]:
    if port is None:
        return (), None
    try:
        items = tuple(port.due(course_id))
    except (AttributeError, NotImplementedError):
        return (), None
    except Exception:
        return (), "due review unavailable; continuing without recall"
    if not all(isinstance(item, DueReview) for item in items):
        return (), "due review unavailable; continuing without recall"
    return tuple(sorted(items, key=lambda item: (item.due_at, item.review_id))), None


def _prioritize_status(
    base: ProductShellStatus,
    snapshot: TutorSnapshotV1,
    due: Sequence[DueReview],
    warning: str | None,
) -> ProductShellStatus:
    if warning is not None:
        return ProductShellStatus.DEGRADED
    if base is ProductShellStatus.STALE:
        return ProductShellStatus.STALE
    if base in {
        ProductShellStatus.SUSPENDED,
        ProductShellStatus.NEEDS_LEARNER_INPUT,
        ProductShellStatus.WORKING,
        ProductShellStatus.DEGRADED,
    }:
        return base
    if snapshot.divergences or any(
        item.state.value == "conflicting" for item in snapshot.learner_context
    ):
        return ProductShellStatus.CONFLICTED_CONTEXT
    if due:
        return ProductShellStatus.NEEDS_REVIEW
    return base


__all__ = [
    "MAX_LEARNER_ENTRY_CHARS",
    "DueReview",
    "DueReviewViewPort",
    "ProductShell",
    "ProductShellStatus",
    "ProductShellView",
    "TutorConversationHostPort",
    "main",
    "render",
    "run_offline_shell_demo",
]


if __name__ == "__main__":
    main()
