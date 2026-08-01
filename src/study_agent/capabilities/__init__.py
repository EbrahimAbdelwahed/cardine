"""Trusted, model-independent adaptive-tutor capabilities."""

from .bindings import (
    PROFILE_SELECTION_RECEIPT_INPUT,
    CapabilityBinding,
    CapabilityDependencyResolver,
    ProfiledCapabilityBinding,
)
from .builtin import (
    ANALYZE_EXAM_SAMPLE_MANIFEST,
    ASSESS_UNDERSTANDING_MANIFEST,
    EXPLAIN_CONCEPT_MANIFEST,
    GRADE_RESPONSE_MANIFEST,
    PROPOSE_FLASHCARDS_MANIFEST,
    assess_understanding_binding,
    builtin_capability_bindings,
    explain_concept_binding,
)
from .builtin_validators import builtin_tutor_validators
from .contracts import (
    CancelledCapabilityOutcome,
    CapabilityContinuation,
    CapabilityGatewayError,
    CapabilityGatewayErrorCode,
    CapabilityManifest,
    CapabilityOutcome,
    CapabilityOutcomeStatus,
    CompletedCapabilityOutcome,
    FailedCapabilityOutcome,
    StaleCapabilityOutcome,
    SuspendedCapabilityOutcome,
    TerminatedCapabilityOutcome,
    TutorCapabilityId,
)
from .dispatch import FlashcardCapabilityDispatcher
from .fingerprints import capability_output_fingerprint, capability_retry_fingerprint
from .gateway import StudyCapabilityGateway
from .registry import StudyCapabilityRegistry

__all__ = [
    "ANALYZE_EXAM_SAMPLE_MANIFEST",
    "ASSESS_UNDERSTANDING_MANIFEST",
    "EXPLAIN_CONCEPT_MANIFEST",
    "GRADE_RESPONSE_MANIFEST",
    "PROFILE_SELECTION_RECEIPT_INPUT",
    "PROPOSE_FLASHCARDS_MANIFEST",
    "CancelledCapabilityOutcome",
    "CapabilityBinding",
    "CapabilityContinuation",
    "CapabilityDependencyResolver",
    "CapabilityGatewayError",
    "CapabilityGatewayErrorCode",
    "CapabilityManifest",
    "CapabilityOutcome",
    "CapabilityOutcomeStatus",
    "CompletedCapabilityOutcome",
    "FailedCapabilityOutcome",
    "FlashcardCapabilityDispatcher",
    "GatewayIsolatedCapabilityRunAdapter",
    "ProfiledCapabilityBinding",
    "ProfiledWorkerExecutionDescriptor",
    "StaleCapabilityOutcome",
    "StudyCapabilityGateway",
    "StudyCapabilityRegistry",
    "SuspendedCapabilityOutcome",
    "TerminatedCapabilityOutcome",
    "TutorCapabilityId",
    "assess_understanding_binding",
    "builtin_capability_bindings",
    "builtin_tutor_validators",
    "capability_output_fingerprint",
    "capability_retry_fingerprint",
    "explain_concept_binding",
]


def __getattr__(name: str) -> object:
    """Load the worker adapter without making capability contracts depend on workers."""

    if name in {
        "GatewayIsolatedCapabilityRunAdapter",
        "ProfiledWorkerExecutionDescriptor",
    }:
        from .worker_adapter import (
            GatewayIsolatedCapabilityRunAdapter,
            ProfiledWorkerExecutionDescriptor,
        )

        return {
            "GatewayIsolatedCapabilityRunAdapter": GatewayIsolatedCapabilityRunAdapter,
            "ProfiledWorkerExecutionDescriptor": ProfiledWorkerExecutionDescriptor,
        }[name]
    raise AttributeError(name)
