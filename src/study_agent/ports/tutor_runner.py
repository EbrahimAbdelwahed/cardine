"""Narrow effect and authority ports used by the provider-neutral tutor runner."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from study_agent.domain import CourseId, ExecutionContext, SessionId
from study_agent.domain._validation import JsonObject, JsonValue

if TYPE_CHECKING:
    from study_agent.capabilities.contracts import (
        CapabilityContinuation,
        CapabilityManifest,
        CapabilityOutcome,
        TutorCapabilityId,
    )
    from cardine.hosts.contracts import HostActionIdentity


class TutorCapabilityGatewayPort(Protocol):
    """The public capability lifecycle exposed to an external tutor host."""

    def discover(self) -> tuple[CapabilityManifest, ...]: ...

    async def start(
        self,
        capability_id: TutorCapabilityId,
        inputs: JsonObject,
        context: ExecutionContext,
    ) -> CapabilityOutcome: ...

    async def resume(
        self,
        continuation: CapabilityContinuation,
        response: JsonValue,
        context: ExecutionContext,
    ) -> CapabilityOutcome: ...


class TutorHostAuthorityPort(Protocol):
    """Create trusted execution authority for a newly selected capability."""

    def create_context(
        self,
        course_id: CourseId,
        session_id: SessionId,
        capability_id: TutorCapabilityId,
        action_identity: HostActionIdentity,
    ) -> ExecutionContext: ...


class TutorHostActionIdentityPort(Protocol):
    """Issue stable opaque identities for exact host actions."""

    def issue(
        self,
        host_turn_id: str,
        context_fingerprint: str,
        decision_fingerprint: str,
        decision_generation: int,
    ) -> HostActionIdentity: ...


class TutorContinuationStore(Protocol):
    """Operational, host-only continuation storage with exact keyed access."""

    def create(
        self,
        course_id: CourseId,
        session_id: SessionId,
        continuation_fingerprint: str,
        payload: bytes,
    ) -> bool: ...

    def load(
        self,
        course_id: CourseId,
        session_id: SessionId,
        continuation_fingerprint: str,
    ) -> bytes: ...

    def delete(
        self,
        course_id: CourseId,
        session_id: SessionId,
        continuation_fingerprint: str,
    ) -> None: ...


class TutorCompletionHandoffStore(Protocol):
    """Durable CAS slots for host capability-completion handoffs."""

    def create(self, key: str, payload: bytes) -> bool: ...

    def load(self, key: str) -> bytes: ...

    def compare_and_set(self, key: str, expected: bytes, replacement: bytes) -> bool: ...


__all__ = [
    "TutorCapabilityGatewayPort",
    "TutorCompletionHandoffStore",
    "TutorContinuationStore",
    "TutorHostActionIdentityPort",
    "TutorHostAuthorityPort",
]
