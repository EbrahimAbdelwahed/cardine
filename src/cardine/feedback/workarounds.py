"""Static, host-owned workaround manifests and truthful receipts.

Only closed metadata lives here.  There is intentionally no executor, shell,
filesystem, network client, package loader, or model integration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Any, cast

from study_agent.domain._validation import JsonValue
from study_agent.state import canonical_json_bytes, canonical_json_object

_OPAQUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def _json_text(value: JsonValue, field: str) -> str:
    if not isinstance(value, str):
        raise WorkaroundValidationError(f"invalid_{field}")
    return value


def _json_int(value: JsonValue, field: str) -> int:
    if type(value) is not int:
        raise WorkaroundValidationError(f"invalid_{field}")
    return value


def _json_optional_text(value: JsonValue, field: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise WorkaroundValidationError(f"invalid_{field}")
    return value


def _json_sequence(value: JsonValue, field: str) -> tuple[JsonValue, ...]:
    if not isinstance(value, tuple):
        raise WorkaroundValidationError(f"invalid_{field}")
    return value


class WorkaroundValidationError(ValueError):
    """A manifest, task, grant, or receipt is not the closed contract."""


class WorkaroundAuthorityError(PermissionError):
    """The host did not grant or approve the selected installed strategy."""


def _opaque(value: object, field: str) -> str:
    if not isinstance(value, str) or _OPAQUE.fullmatch(value) is None:
        raise WorkaroundValidationError(f"invalid_{field}")
    return value


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise WorkaroundValidationError(f"invalid_{field}")
    return value


class WorkaroundInputKind(StrEnum):
    PDF = "pdf"
    TEXT = "text"
    MARKDOWN = "markdown"
    TABULAR = "tabular"


class WorkaroundOutputKind(StrEnum):
    TEXT = "text"
    MARKDOWN = "markdown"
    TABULAR = "tabular"


class WorkaroundEffect(StrEnum):
    PURE = "pure"
    READ_LOCAL = "read_local"
    WRITE_DERIVED = "write_derived"
    NETWORK = "network"
    CREDENTIAL = "credential"


class WorkaroundApprovalPolicy(StrEnum):
    NONE = "none"
    HOST_APPROVAL = "host_approval"


class WorkaroundReceiptStatus(StrEnum):
    NOT_AVAILABLE = "not_available"
    REQUIRES_APPROVAL = "requires_approval"
    ATTEMPTED_SUCCEEDED = "attempted_succeeded"
    ATTEMPTED_FAILED = "attempted_failed"


@dataclass(frozen=True, slots=True)
class WorkaroundProvenance:
    """Digest-only lineage for a derived artifact; no paths or material."""

    input_fingerprint: str
    output_fingerprint: str
    quality_limitation_fingerprints: tuple[str, ...]

    def __post_init__(self) -> None:
        _digest(self.input_fingerprint, "input_fingerprint")
        _digest(self.output_fingerprint, "output_fingerprint")
        if (
            tuple(sorted(self.quality_limitation_fingerprints))
            != self.quality_limitation_fingerprints
        ):
            raise WorkaroundValidationError("noncanonical_provenance")
        for value in self.quality_limitation_fingerprints:
            _digest(value, "quality_limitation_fingerprint")

    def to_json(self) -> dict[str, object]:
        return {
            "input_fingerprint": self.input_fingerprint,
            "output_fingerprint": self.output_fingerprint,
            "quality_limitation_fingerprints": list(self.quality_limitation_fingerprints),
            "schema_version": 1,
        }

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(cast(Any, self.to_json()))


@dataclass(frozen=True, slots=True)
class WorkaroundGrant:
    manifest_identity: str
    manifest_version: int
    effect_fingerprint: str

    def __post_init__(self) -> None:
        _opaque(self.manifest_identity, "manifest_identity")
        if type(self.manifest_version) is not int or self.manifest_version < 1:
            raise WorkaroundValidationError("invalid_manifest_version")
        _digest(self.effect_fingerprint, "effect_fingerprint")


@dataclass(frozen=True, slots=True)
class WorkaroundManifest:
    identity: str
    version: int
    input_kind: WorkaroundInputKind
    output_kind: WorkaroundOutputKind
    effects: tuple[WorkaroundEffect, ...]
    approval_policy: WorkaroundApprovalPolicy
    preconditions: tuple[str, ...] = ()
    quality_limitations: tuple[str, ...] = ()
    provenance_obligations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _opaque(self.identity, "identity")
        if type(self.version) is not int or self.version < 1:
            raise WorkaroundValidationError("invalid_version")
        if not isinstance(self.input_kind, WorkaroundInputKind):
            raise WorkaroundValidationError("invalid_input_kind")
        if not isinstance(self.output_kind, WorkaroundOutputKind):
            raise WorkaroundValidationError("invalid_output_kind")
        if not isinstance(self.approval_policy, WorkaroundApprovalPolicy):
            raise WorkaroundValidationError("invalid_approval_policy")
        if (
            not self.effects
            or tuple(sorted(set(self.effects), key=lambda v: v.value)) != self.effects
        ):
            raise WorkaroundValidationError("noncanonical_effects")
        if any(not isinstance(effect, WorkaroundEffect) for effect in self.effects):
            raise WorkaroundValidationError("invalid_effects")
        for value in (*self.preconditions, *self.quality_limitations, *self.provenance_obligations):
            _opaque(value, "manifest_text")
        if not self.provenance_obligations:
            raise WorkaroundValidationError("provenance_obligation_required")
        # Generic registry refuses effects that would require hidden authority.
        if WorkaroundEffect.NETWORK in self.effects or WorkaroundEffect.CREDENTIAL in self.effects:
            raise WorkaroundValidationError("undeclared_effect")

    def to_json(self) -> dict[str, Any]:
        return {
            "approval_policy": self.approval_policy.value,
            "effects": [effect.value for effect in self.effects],
            "identity": self.identity,
            "input_kind": self.input_kind.value,
            "output_kind": self.output_kind.value,
            "preconditions": list(self.preconditions),
            "provenance_obligations": list(self.provenance_obligations),
            "quality_limitations": list(self.quality_limitations),
            "schema_version": 1,
            "version": self.version,
        }

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(cast(Any, self.to_json()))

    @classmethod
    def from_bytes(cls, data: bytes) -> WorkaroundManifest:
        try:
            value = canonical_json_object(data)
            fields = {
                "schema_version",
                "identity",
                "version",
                "input_kind",
                "output_kind",
                "effects",
                "approval_policy",
                "preconditions",
                "quality_limitations",
                "provenance_obligations",
            }
            if (
                set(value) != fields
                or canonical_json_bytes(value) != data
                or value["schema_version"] != 1
            ):
                raise ValueError
            effects = _json_sequence(value["effects"], "effects")
            preconditions = _json_sequence(value["preconditions"], "preconditions")
            quality_limitations = _json_sequence(
                value["quality_limitations"], "quality_limitations"
            )
            provenance_obligations = _json_sequence(
                value["provenance_obligations"], "provenance_obligations"
            )
            return cls(
                _json_text(value["identity"], "identity"),
                _json_int(value["version"], "version"),
                WorkaroundInputKind(_json_text(value["input_kind"], "input_kind")),
                WorkaroundOutputKind(_json_text(value["output_kind"], "output_kind")),
                tuple(WorkaroundEffect(_json_text(item, "effect")) for item in effects),
                WorkaroundApprovalPolicy(
                    _json_text(value["approval_policy"], "approval_policy")
                ),
                tuple(_json_text(item, "precondition") for item in preconditions),
                tuple(_json_text(item, "quality_limitation") for item in quality_limitations),
                tuple(_json_text(item, "provenance_obligation") for item in provenance_obligations),
            )
        except (TypeError, ValueError, UnicodeDecodeError):
            raise WorkaroundValidationError("invalid_manifest") from None

    @property
    def fingerprint(self) -> str:
        return sha256(b"study-agent-workaround-manifest-v1\0" + self.to_bytes()).hexdigest()

    @property
    def effect_fingerprint(self) -> str:
        encoded = canonical_json_bytes(
            cast(Any, {"effects": [effect.value for effect in self.effects]})
        )
        return sha256(b"study-agent-workaround-effects-v1\0" + encoded).hexdigest()

    @property
    def quality_limitation_fingerprint(self) -> str:
        encoded = canonical_json_bytes(cast(Any, list(self.quality_limitations)))
        return sha256(b"study-agent-workaround-quality-v1\0" + encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class WorkaroundTask:
    input_kind: WorkaroundInputKind
    output_kind: WorkaroundOutputKind
    input_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.input_kind, WorkaroundInputKind):
            raise WorkaroundValidationError("invalid_input_kind")
        if not isinstance(self.output_kind, WorkaroundOutputKind):
            raise WorkaroundValidationError("invalid_output_kind")
        _digest(self.input_fingerprint, "input_fingerprint")

    @property
    def fingerprint(self) -> str:
        encoded = canonical_json_bytes(
            cast(
                Any,
                {
                    "input_fingerprint": self.input_fingerprint,
                    "input_kind": self.input_kind.value,
                    "output_kind": self.output_kind.value,
                },
            )
        )
        return sha256(b"study-agent-workaround-task-v1\0" + encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class WorkaroundApprovalReceipt:
    """Host-issued, closed approval bound to one exact task and manifest."""

    task_fingerprint: str
    manifest_identity: str
    manifest_version: int
    manifest_fingerprint: str
    effect_fingerprint: str
    approval_fingerprint: str

    def __post_init__(self) -> None:
        _digest(self.task_fingerprint, "task_fingerprint")
        _opaque(self.manifest_identity, "manifest_identity")
        if type(self.manifest_version) is not int or self.manifest_version < 1:
            raise WorkaroundValidationError("invalid_manifest_version")
        _digest(self.manifest_fingerprint, "manifest_fingerprint")
        _digest(self.effect_fingerprint, "effect_fingerprint")
        _digest(self.approval_fingerprint, "approval_fingerprint")

    def to_json(self) -> dict[str, object]:
        return {
            "approval_fingerprint": self.approval_fingerprint,
            "effect_fingerprint": self.effect_fingerprint,
            "manifest_fingerprint": self.manifest_fingerprint,
            "manifest_identity": self.manifest_identity,
            "manifest_version": self.manifest_version,
            "schema_version": 1,
            "task_fingerprint": self.task_fingerprint,
        }

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(cast(Any, self.to_json()))

    @classmethod
    def from_bytes(cls, data: bytes) -> WorkaroundApprovalReceipt:
        try:
            value = canonical_json_object(data)
            fields = {
                "approval_fingerprint",
                "effect_fingerprint",
                "manifest_fingerprint",
                "manifest_identity",
                "manifest_version",
                "schema_version",
                "task_fingerprint",
            }
            if (
                set(value) != fields
                or canonical_json_bytes(value) != data
                or value["schema_version"] != 1
            ):
                raise ValueError
            result = cls(
                _json_text(value["task_fingerprint"], "task_fingerprint"),
                _json_text(value["manifest_identity"], "manifest_identity"),
                _json_int(value["manifest_version"], "manifest_version"),
                _json_text(value["manifest_fingerprint"], "manifest_fingerprint"),
                _json_text(value["effect_fingerprint"], "effect_fingerprint"),
                _json_text(value["approval_fingerprint"], "approval_fingerprint"),
            )
            if result.to_bytes() != data:
                raise ValueError
            return result
        except (TypeError, ValueError, UnicodeDecodeError):
            raise WorkaroundValidationError("invalid_approval_receipt") from None


@dataclass(frozen=True, slots=True)
class WorkaroundExecutionReceipt:
    status: WorkaroundReceiptStatus
    manifest_identity: str
    manifest_version: int
    input_fingerprint: str
    output_fingerprint: str | None = None
    provenance_fingerprint: str | None = None
    limitation_fingerprint: str | None = None
    manifest_fingerprint: str | None = None
    effect_fingerprint: str | None = None
    approval_fingerprint: str | None = None
    executor_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, WorkaroundReceiptStatus):
            raise WorkaroundValidationError("invalid_status")
        _opaque(self.manifest_identity, "manifest_identity")
        if type(self.manifest_version) is not int or self.manifest_version < 1:
            raise WorkaroundValidationError("invalid_manifest_version")
        _digest(self.input_fingerprint, "input_fingerprint")
        if self.status is WorkaroundReceiptStatus.ATTEMPTED_SUCCEEDED and (
            self.output_fingerprint is None or self.provenance_fingerprint is None
        ):
            raise WorkaroundValidationError("success_requires_provenance")
        if (
            self.status is WorkaroundReceiptStatus.ATTEMPTED_FAILED
            and self.limitation_fingerprint is None
        ):
            raise WorkaroundValidationError("failure_requires_limitation")
        for value, field in (
            (self.output_fingerprint, "output_fingerprint"),
            (self.provenance_fingerprint, "provenance_fingerprint"),
            (self.limitation_fingerprint, "limitation_fingerprint"),
            (self.manifest_fingerprint, "manifest_fingerprint"),
            (self.effect_fingerprint, "effect_fingerprint"),
            (self.approval_fingerprint, "approval_fingerprint"),
            (self.executor_fingerprint, "executor_fingerprint"),
        ):
            if value is not None:
                _digest(value, field)

    def to_json(self) -> dict[str, object]:
        return {
            "approval_fingerprint": self.approval_fingerprint,
            "effect_fingerprint": self.effect_fingerprint,
            "executor_fingerprint": self.executor_fingerprint,
            "input_fingerprint": self.input_fingerprint,
            "limitation_fingerprint": self.limitation_fingerprint,
            "manifest_fingerprint": self.manifest_fingerprint,
            "manifest_identity": self.manifest_identity,
            "manifest_version": self.manifest_version,
            "output_fingerprint": self.output_fingerprint,
            "provenance_fingerprint": self.provenance_fingerprint,
            "schema_version": 1,
            "status": self.status.value,
        }

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(cast(Any, self.to_json()))

    @classmethod
    def from_bytes(cls, data: bytes) -> WorkaroundExecutionReceipt:
        try:
            value = canonical_json_object(data)
            fields = set(
                cls(WorkaroundReceiptStatus.NOT_AVAILABLE, "none@1", 1, "0" * 64).to_json()
            )
            if (
                set(value) != fields
                or canonical_json_bytes(value) != data
                or value["schema_version"] != 1
            ):
                raise ValueError
            result = cls(
                WorkaroundReceiptStatus(_json_text(value["status"], "status")),
                _json_text(value["manifest_identity"], "manifest_identity"),
                _json_int(value["manifest_version"], "manifest_version"),
                _json_text(value["input_fingerprint"], "input_fingerprint"),
                _json_optional_text(value["output_fingerprint"], "output_fingerprint"),
                _json_optional_text(value["provenance_fingerprint"], "provenance_fingerprint"),
                _json_optional_text(value["limitation_fingerprint"], "limitation_fingerprint"),
                _json_optional_text(value["manifest_fingerprint"], "manifest_fingerprint"),
                _json_optional_text(value["effect_fingerprint"], "effect_fingerprint"),
                _json_optional_text(value["approval_fingerprint"], "approval_fingerprint"),
                _json_optional_text(value["executor_fingerprint"], "executor_fingerprint"),
            )
            if result.to_bytes() != data:
                raise ValueError
            return result
        except (TypeError, ValueError, UnicodeDecodeError):
            raise WorkaroundValidationError("invalid_execution_receipt") from None


def _optional_digest(value: object, field: str) -> str | None:
    return None if value is None else _digest(value, field)


class WorkaroundRegistry:
    """Deterministic lookup over host-installed static manifests."""

    def __init__(self, manifests: tuple[WorkaroundManifest, ...] = ()) -> None:
        by_identity: dict[str, WorkaroundManifest] = {}
        for manifest in manifests:
            if not isinstance(manifest, WorkaroundManifest):
                raise WorkaroundValidationError("invalid_manifest")
            if manifest.identity in by_identity:
                raise WorkaroundValidationError("duplicate_manifest_identity")
            by_identity[manifest.identity] = manifest
        self._manifests = by_identity

    def select(
        self, task: WorkaroundTask, granted: frozenset[str] | tuple[WorkaroundGrant, ...]
    ) -> WorkaroundExecutionReceipt:
        if not isinstance(task, WorkaroundTask):
            raise WorkaroundValidationError("invalid_task")
        grants = _grant_map(granted)
        for identity in sorted(self._manifests):
            manifest = self._manifests[identity]
            if (
                manifest.input_kind is not task.input_kind
                or manifest.output_kind is not task.output_kind
            ):
                continue
            grant = grants.get(identity)
            if (
                grant is None
                or grant.manifest_version != manifest.version
                or grant.effect_fingerprint != manifest.effect_fingerprint
            ):
                continue
            if manifest.approval_policy is WorkaroundApprovalPolicy.HOST_APPROVAL:
                return WorkaroundExecutionReceipt(
                    WorkaroundReceiptStatus.REQUIRES_APPROVAL,
                    identity,
                    manifest.version,
                    task.input_fingerprint,
                    manifest_fingerprint=manifest.fingerprint,
                    effect_fingerprint=manifest.effect_fingerprint,
                )
            return WorkaroundExecutionReceipt(
                WorkaroundReceiptStatus.NOT_AVAILABLE,
                identity,
                manifest.version,
                task.input_fingerprint,
                manifest_fingerprint=manifest.fingerprint,
                effect_fingerprint=manifest.effect_fingerprint,
            )
        return WorkaroundExecutionReceipt(
            WorkaroundReceiptStatus.NOT_AVAILABLE, "none@1", 1, task.input_fingerprint
        )

    def get(self, identity: str) -> WorkaroundManifest:
        try:
            return self._manifests[identity]
        except KeyError:
            raise WorkaroundValidationError("manifest_not_found") from None

    def resolve_execution(
        self, task: WorkaroundTask, granted: frozenset[str] | tuple[WorkaroundGrant, ...]
    ) -> tuple[WorkaroundManifest, WorkaroundGrant]:
        """Resolve one installed, exact-grant strategy without executing it."""

        if not isinstance(task, WorkaroundTask):
            raise WorkaroundValidationError("invalid_task")
        grants = _grant_map(granted)
        for identity in sorted(self._manifests):
            manifest = self._manifests[identity]
            grant = grants.get(identity)
            if (
                manifest.input_kind is task.input_kind
                and manifest.output_kind is task.output_kind
                and grant is not None
                and grant.manifest_version == manifest.version
                and grant.effect_fingerprint == manifest.effect_fingerprint
            ):
                return manifest, grant
        raise WorkaroundAuthorityError("workaround_not_available")

    def validate_approval(
        self,
        task: WorkaroundTask,
        manifest: WorkaroundManifest,
        grant: WorkaroundGrant,
        approval: WorkaroundApprovalReceipt | None,
    ) -> None:
        if manifest.approval_policy is WorkaroundApprovalPolicy.NONE:
            if approval is not None:
                raise WorkaroundValidationError("unexpected_approval")
            return
        if not isinstance(approval, WorkaroundApprovalReceipt):
            raise WorkaroundAuthorityError("workaround_approval_required")
        if (
            approval.task_fingerprint != task.fingerprint
            or approval.manifest_identity != manifest.identity
            or approval.manifest_version != manifest.version
            or approval.manifest_fingerprint != manifest.fingerprint
            or approval.effect_fingerprint != manifest.effect_fingerprint
            or grant.effect_fingerprint != manifest.effect_fingerprint
        ):
            raise WorkaroundAuthorityError("workaround_approval_mismatch")

    @property
    def manifests(self) -> tuple[WorkaroundManifest, ...]:
        return tuple(self._manifests[key] for key in sorted(self._manifests))

    def validate_execution(
        self,
        task: WorkaroundTask,
        receipt: WorkaroundExecutionReceipt,
        *,
        granted: frozenset[str] | tuple[WorkaroundGrant, ...],
        approval: WorkaroundApprovalReceipt | None = None,
    ) -> WorkaroundExecutionReceipt:
        if receipt.status not in {
            WorkaroundReceiptStatus.ATTEMPTED_SUCCEEDED,
            WorkaroundReceiptStatus.ATTEMPTED_FAILED,
        }:
            raise WorkaroundValidationError("execution_not_attempted")
        manifest = self.get(receipt.manifest_identity)
        grants = _grant_map(granted)
        grant = grants.get(manifest.identity)
        if grant is None or grant.manifest_version != manifest.version:
            raise WorkaroundAuthorityError("workaround_not_granted")
        self.validate_approval(task, manifest, grant, approval)
        if (
            receipt.manifest_version != manifest.version
            or receipt.manifest_fingerprint != manifest.fingerprint
        ):
            raise WorkaroundValidationError("manifest_fingerprint_mismatch")
        if (
            receipt.effect_fingerprint != manifest.effect_fingerprint
            or grant.effect_fingerprint != manifest.effect_fingerprint
        ):
            raise WorkaroundValidationError("effect_fingerprint_mismatch")
        if receipt.input_fingerprint != task.input_fingerprint:
            raise WorkaroundValidationError("input_fingerprint_mismatch")
        if (
            manifest.input_kind is not task.input_kind
            or manifest.output_kind is not task.output_kind
        ):
            raise WorkaroundValidationError("task_kind_mismatch")
        if receipt.executor_fingerprint is None:
            raise WorkaroundValidationError("executor_fingerprint_required")
        if approval is None:
            if receipt.approval_fingerprint is not None:
                raise WorkaroundValidationError("unexpected_approval_fingerprint")
        elif receipt.approval_fingerprint != approval.approval_fingerprint:
            raise WorkaroundValidationError("approval_fingerprint_mismatch")
        if (
            receipt.status is WorkaroundReceiptStatus.ATTEMPTED_SUCCEEDED
            and manifest.quality_limitations
            and receipt.limitation_fingerprint != manifest.quality_limitation_fingerprint
        ):
            raise WorkaroundValidationError("quality_limitation_mismatch")
        return receipt


def _grant_map(granted: frozenset[str] | tuple[WorkaroundGrant, ...]) -> dict[str, WorkaroundGrant]:
    if isinstance(granted, frozenset):
        return {identity: WorkaroundGrant(identity, 1, "0" * 64) for identity in granted}
    if not isinstance(granted, tuple):
        raise WorkaroundAuthorityError("invalid_grants")
    result: dict[str, WorkaroundGrant] = {}
    for grant in granted:
        if not isinstance(grant, WorkaroundGrant) or grant.manifest_identity in result:
            raise WorkaroundAuthorityError("invalid_grants")
        result[grant.manifest_identity] = grant
    return result


WorkaroundReceipt = WorkaroundExecutionReceipt
WorkaroundStrategyManifest = WorkaroundManifest


__all__ = [
    "WorkaroundApprovalPolicy",
    "WorkaroundApprovalReceipt",
    "WorkaroundAuthorityError",
    "WorkaroundEffect",
    "WorkaroundExecutionReceipt",
    "WorkaroundGrant",
    "WorkaroundInputKind",
    "WorkaroundManifest",
    "WorkaroundOutputKind",
    "WorkaroundProvenance",
    "WorkaroundReceipt",
    "WorkaroundReceiptStatus",
    "WorkaroundRegistry",
    "WorkaroundStrategyManifest",
    "WorkaroundTask",
    "WorkaroundValidationError",
]
