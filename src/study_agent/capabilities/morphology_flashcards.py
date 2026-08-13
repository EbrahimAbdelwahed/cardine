"""Binding and fail-closed validators for morphology-first flashcards."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256

from study_agent.artifacts.candidates import (
    FlashcardCandidateBatch,
    FlashcardPedagogicalRole,
)
from study_agent.capabilities.bindings import (
    CapabilityDependencyResolver,
    ProfiledCapabilityBinding,
)
from study_agent.capabilities.builtin import PROPOSE_FLASHCARDS_MANIFEST
from study_agent.capabilities.worker_adapter import ProfiledWorkerExecutionDescriptor
from study_agent.domain import ExecutionContext
from study_agent.domain._validation import JsonObject, JsonValue, freeze_object
from study_agent.flashcards.lesson_worker_contracts import (
    LessonWorkerRequest,
    ProfileTaskExpectation,
    VerifiedFlashcardPageResult,
)
from study_agent.flashcards.planning import PreparedPlannedFlashcardScope
from study_agent.grounding import GroundingContractError
from study_agent.pedagogy import MORPHOLOGY_FIRST_ANATOMY_V1, ProfileSelectionMode
from study_agent.playbooks import (
    ToolBehaviorPin,
    ValidationOutcome,
    ValidatorDisposition,
    VersionPins,
    playbook_definition_fingerprint,
)
from study_agent.playbooks.builtin.morphology_flashcards_flow import MORPHOLOGY_FLASHCARDS_FLOW
from study_agent.ports import EvidenceStatus, SourceContentPort
from study_agent.ports.flashcard import VerifiedMediaEvidencePort
from study_agent.prompts.morphology_flashcards_v1 import MORPHOLOGY_FLASHCARDS_PROMPT
from study_agent.skills import ArtifactReference, SemanticVersion
from study_agent.skills.builtin.morphology_flashcards import MORPHOLOGY_FLASHCARDS_SKILL
from study_agent.state import canonical_json_bytes
from study_agent.workers import (
    GenerationWorkerService,
    GenerationWorkerTask,
    GenerationWorkerTaskKind,
    ValidationExpectation,
    ValidationReceiptSource,
    fingerprint_output_schema,
)
from study_agent.workers.service import GenerationWorkerConflictError
from study_agent.workers.view import WorkerCompactView

VERSION = SemanticVersion.parse("1.0.0")


@dataclass(frozen=True, slots=True)
class _ObjectPlan:
    topic_keys: tuple[str, ...]
    macro_key: str
    atomic_keys: tuple[str, ...]
    dimensions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _MorphologyDraft:
    plans: tuple[_ObjectPlan, ...]
    batch: FlashcardCandidateBatch
    topic_omissions: tuple[tuple[str, int], ...]


class MorphologyFlashcardsReadinessValidator:
    id = "morphology_flashcards_readiness"
    version = VERSION

    async def validate(self, inputs: JsonObject) -> ValidationOutcome:
        try:
            if set(inputs) != {"prepared_scope", "scope"}:
                raise ValueError("morphology readiness inputs are not exact")
            prepared = _prepared(inputs["prepared_scope"])
            scope = inputs["scope"]
            if scope is not None and (
                not isinstance(scope, str) or not scope or scope != scope.strip()
            ):
                raise ValueError("scope must be null or trimmed text")
            _require_sufficient(prepared)
        except (GroundingContractError, KeyError, TypeError, ValueError) as error:
            return _failure(error)
        return ValidationOutcome(
            True,
            ValidatorDisposition.CONTINUE,
            {
                "needs_clarification": scope is None,
                "scope_fingerprint": prepared.prepared_scope.scope_fingerprint,
            },
        )


class MorphologyFlashcardsIntegrityValidator:
    id = "morphology_flashcards_integrity"
    version = VERSION

    def __init__(self, content: SourceContentPort, media: VerifiedMediaEvidencePort) -> None:
        self._content = content
        self._media = media

    async def validate(self, inputs: JsonObject) -> ValidationOutcome:
        try:
            if set(inputs) == {"output"}:
                _parse(inputs["output"])
                return ValidationOutcome(
                    True, ValidatorDisposition.CONTINUE, {"schema_valid": True}
                )
            if set(inputs) != {"prepared_scope", "requested_ceiling", "draft"}:
                raise GroundingContractError("morphology integrity inputs are not exact")
            prepared = _prepared(inputs["prepared_scope"])
            _require_sufficient(prepared)
            ceiling = inputs["requested_ceiling"]
            if type(ceiling) is not int or not 1 <= ceiling <= 24:
                raise GroundingContractError("requested ceiling must be 1..24")
            draft = _parse(inputs["draft"])
            self._validate(prepared, ceiling, draft)
        except (GroundingContractError, KeyError, TypeError, ValueError) as error:
            return _failure(error)
        return ValidationOutcome(True, ValidatorDisposition.CONTINUE, draft.batch.to_json())

    def _validate(
        self,
        prepared: PreparedPlannedFlashcardScope,
        ceiling: int,
        draft: _MorphologyDraft,
    ) -> None:
        if len(draft.batch.candidates) > ceiling:
            raise GroundingContractError("morphology candidate ceiling exceeded")
        active = prepared.active_topic_keys
        covered = tuple(key for plan in draft.plans for key in plan.topic_keys)
        omitted = tuple(key for key, _ in draft.topic_omissions)
        if len(set(covered + omitted)) != len(covered + omitted) or set(covered + omitted) != set(
            active
        ):
            raise GroundingContractError("active topics must be covered exactly once")
        positions = {key: index for index, key in enumerate(active)}
        if tuple(min(positions[key] for key in plan.topic_keys) for plan in draft.plans) != tuple(
            sorted(min(positions[key] for key in plan.topic_keys) for plan in draft.plans)
        ):
            raise GroundingContractError("object plans are not in active-topic order")

        candidates = {item.candidate_key: item for item in draft.batch.candidates}
        owned: set[str] = set()
        topic_owners: dict[str, tuple[str, ...]] = {}
        for plan in draft.plans:
            keys = (plan.macro_key, *plan.atomic_keys)
            if len(plan.atomic_keys) > 3 or not set(keys) <= set(candidates):
                raise GroundingContractError("object plan candidate keys are invalid")
            macro = candidates[plan.macro_key]
            if (
                macro.pedagogical_role is not FlashcardPedagogicalRole.MACRO_RECONSTRUCTION
                or macro.parent_candidate_key is not None
            ):
                raise GroundingContractError("object plan must begin with one root macro")
            for key in plan.atomic_keys:
                atomic = candidates[key]
                if (
                    atomic.pedagogical_role is not FlashcardPedagogicalRole.ATOMIC_DISCRIMINATION
                    or atomic.parent_candidate_key != plan.macro_key
                ):
                    raise GroundingContractError("atomic cards must parent to their macro")
            if owned.intersection(keys):
                raise GroundingContractError("candidate belongs to multiple object plans")
            owned.update(keys)
            for key in keys:
                topic_owners[key] = plan.topic_keys
        if owned != set(candidates):
            raise GroundingContractError("every candidate must belong to one object plan")
        prompts = tuple(_normalize(item.prompt) for item in draft.batch.candidates)
        if any(not item for item in prompts) or len(set(prompts)) != len(prompts):
            raise GroundingContractError("morphology prompts must be non-empty and unique")
        payloads = tuple(_candidate_content(item.to_json()) for item in draft.batch.candidates)
        if len(set(payloads)) != len(payloads):
            raise GroundingContractError("morphology candidate content must be unique")

        used_omissions: set[int] = set()
        for topic, index in draft.topic_omissions:
            if not 0 <= index < len(draft.batch.omissions) or index in used_omissions:
                raise GroundingContractError("topic omission index is invalid")
            used_omissions.add(index)
            handles = _topic_handles(prepared, (topic,))
            if set(draft.batch.omissions[index].evidence_ids) != handles:
                raise GroundingContractError("topic omission evidence changed")
        if used_omissions != set(range(len(draft.batch.omissions))):
            raise GroundingContractError("public omissions are not exhausted")

        trusted = prepared.prepared_scope.evidence.by_handle()
        for candidate in draft.batch.candidates:
            permitted = _topic_handles(prepared, topic_owners[candidate.candidate_key])
            if not set(candidate.evidence_ids) or not set(candidate.evidence_ids) <= permitted:
                raise GroundingContractError("candidate evidence is outside its object plan")
            for media_handle in candidate.media_evidence_ids:
                media = self._media.resolve(media_handle)
                if (
                    media.handle != media_handle
                    or media.evidence_handle not in candidate.evidence_ids
                ):
                    raise GroundingContractError("media is not linked to candidate evidence")
                if trusted[media.evidence_handle].citation != media.citation:
                    raise GroundingContractError("media citation differs from active evidence")
        referenced = {
            handle for candidate in draft.batch.candidates for handle in candidate.evidence_ids
        } | {handle for omission in draft.batch.omissions for handle in omission.evidence_ids}
        if not referenced <= set(trusted):
            raise GroundingContractError("draft references inactive evidence")
        for handle in referenced:
            item = trusted[handle]
            resolved = self._content.resolve(item.citation)
            if resolved.citation != item.citation or resolved.text != item.text:
                raise GroundingContractError("canonical evidence changed")


def morphology_flashcards_binding(
    *,
    dependency_resolver: CapabilityDependencyResolver,
    model_adapter: ArtifactReference,
    state_contract: ArtifactReference,
) -> ProfiledCapabilityBinding:
    pins = VersionPins(
        ArtifactReference(MORPHOLOGY_FLASHCARDS_SKILL.id, MORPHOLOGY_FLASHCARDS_SKILL.version),
        ArtifactReference(MORPHOLOGY_FLASHCARDS_FLOW.id, MORPHOLOGY_FLASHCARDS_FLOW.version),
        MORPHOLOGY_FLASHCARDS_PROMPT,
        (ToolBehaviorPin("source.prepare_planned_flashcard_scope", VERSION),),
        model_adapter,
        state_contract,
    )
    return ProfiledCapabilityBinding(
        PROPOSE_FLASHCARDS_MANIFEST,
        PROPOSE_FLASHCARDS_MANIFEST.fingerprint,
        MORPHOLOGY_FIRST_ANATOMY_V1,
        MORPHOLOGY_FLASHCARDS_SKILL,
        MORPHOLOGY_FLASHCARDS_FLOW,
        pins,
        "candidate_batch",
        dependency_resolver,
    )


class MorphologyFlashcardTaskBinding:
    """Build exact B2 tasks for one persisted morphology request."""

    def __init__(self, request: LessonWorkerRequest, binding: ProfiledCapabilityBinding) -> None:
        receipt = request.profile_expectation.profile_selection_receipt
        if receipt.profile != MORPHOLOGY_FIRST_ANATOMY_V1 or receipt.profile != binding.profile:
            raise ValueError("lesson request is not morphology-first")
        if receipt.mode not in {
            ProfileSelectionMode.TRUSTED_METADATA,
            ProfileSelectionMode.EXPLICIT_REQUEST,
        }:
            raise ValueError("morphology requires trusted metadata or explicit request")
        expected = ProfileTaskExpectation(
            receipt,
            binding.manifest.id,
            binding.manifest.version,
            binding.manifest_fingerprint,
            binding.manifest.required_authority,
            binding.pins,
            playbook_definition_fingerprint(binding.playbook),
            binding.manifest.output_schema,
            fingerprint_output_schema(binding.manifest.output_schema),
            _validation_expectations(),
        )
        if expected != request.profile_expectation:
            raise ValueError("lesson expectation differs from morphology implementation")
        self._request = request
        self._binding = binding
        self._expectation = expected

    @property
    def expectation(self) -> ProfileTaskExpectation:
        return self._expectation

    @property
    def execution_descriptor(self) -> ProfiledWorkerExecutionDescriptor:
        return ProfiledWorkerExecutionDescriptor(
            self._binding,
            self._expectation.profile_selection_receipt,
            self._expectation.fingerprint,
        )

    def build(
        self,
        task_id: str,
        public_inputs: JsonObject,
        prepared_scope: PreparedPlannedFlashcardScope,
        context: ExecutionContext,
    ) -> GenerationWorkerTask:
        prepared_scope.validate_against_plan(self._request.plan)
        if public_inputs != self._request.to_public_inputs():
            raise ValueError("morphology public inputs changed")
        if not set(self._expectation.required_authority) <= context.requested_capabilities:
            raise ValueError("morphology context lacks required authority")
        bundle = next(
            item
            for item in self._request.plan.bundles
            if item.bundle_id == prepared_scope.bundle_id
        )
        bundle_fingerprint = sha256(
            b"lesson-worker-bundle@1\0" + canonical_json_bytes(bundle.to_json())
        ).hexdigest()
        return GenerationWorkerTask(
            task_id,
            GenerationWorkerTaskKind.FLASHCARD_BUNDLE,
            self._expectation.capability_id,
            self._expectation.capability_version,
            self._expectation.manifest_fingerprint,
            self._expectation.required_authority,
            self._expectation.pins,
            self._expectation.definition_fingerprint,
            self._request.language,
            {},
            self._request.continuation_summary,
            (
                f"plan-sha256:{self._request.plan.plan_fingerprint}",
                f"bundle-sha256:{bundle_fingerprint}",
                f"wrapper-sha256:{prepared_scope.wrapper_fingerprint}",
                f"read-set-sha256:{prepared_scope.prepared_scope.evidence.read_set_fingerprint}",
                f"revisions-sha256:{self._request.revision_commitments_fingerprint}",
                f"profile-sha256:{self._expectation.fingerprint}",
            ),
            tuple(item.handle for item in prepared_scope.prepared_scope.evidence.items),
            public_inputs,
            self._expectation.output_schema,
            self._expectation.output_schema_fingerprint,
            self._expectation.validations,
        )


class MorphologyPlannedBundleWorker:
    def __init__(
        self,
        request: LessonWorkerRequest,
        task_binding: MorphologyFlashcardTaskBinding,
        worker: GenerationWorkerService,
    ) -> None:
        if request.profile_expectation != task_binding.expectation:
            raise ValueError("morphology worker and request differ")
        self._request = request
        self._task_binding = task_binding
        self._worker = worker

    async def start(
        self,
        task: GenerationWorkerTask,
        prepared_scope: PreparedPlannedFlashcardScope,
        context: ExecutionContext,
    ) -> WorkerCompactView:
        expected = self._task_binding.build(
            task.task_id, self._request.to_public_inputs(), prepared_scope, context
        )
        if expected != task:
            raise GenerationWorkerConflictError("morphology worker task changed")
        return await self._worker.start(task, context)

    def detail(
        self,
        task_id: str,
        prepared_scope_fingerprint: str,
        context: ExecutionContext,
    ) -> VerifiedFlashcardPageResult:
        if len(prepared_scope_fingerprint) != 64 or any(
            char not in "0123456789abcdef" for char in prepared_scope_fingerprint
        ):
            raise ValueError("prepared scope fingerprint is invalid")
        detail = self._worker.detail(task_id, context)
        if not isinstance(detail.output, Mapping):
            raise GenerationWorkerConflictError("morphology output is not an object")
        batch = FlashcardCandidateBatch.from_json(detail.output)
        return VerifiedFlashcardPageResult(
            len(batch.candidates),
            len(batch.omissions),
            detail.receipt.output_fingerprint,
            detail,
        )


def morphology_flashcards_validators(
    content: SourceContentPort, media: VerifiedMediaEvidencePort
) -> tuple[MorphologyFlashcardsReadinessValidator, MorphologyFlashcardsIntegrityValidator]:
    return (
        MorphologyFlashcardsReadinessValidator(),
        MorphologyFlashcardsIntegrityValidator(content, media),
    )


def _validation_expectations() -> tuple[ValidationExpectation, ...]:
    return (
        ValidationExpectation(
            "check_morphology_readiness",
            ValidationReceiptSource.VALIDATE_STEP,
            "morphology_flashcards_readiness",
            "1.0.0",
        ),
        ValidationExpectation(
            "generate_morphology_flashcards",
            ValidationReceiptSource.STRUCTURED_OUTPUT_FALLBACK,
            "morphology_flashcards_integrity",
            "1.0.0",
        ),
        ValidationExpectation(
            "validate_morphology_flashcards",
            ValidationReceiptSource.VALIDATE_STEP,
            "morphology_flashcards_integrity",
            "1.0.0",
        ),
    )


def _prepared(value: JsonValue) -> PreparedPlannedFlashcardScope:
    if not isinstance(value, Mapping):
        raise GroundingContractError("prepared scope must be an object")
    return PreparedPlannedFlashcardScope.from_json(value)


def _require_sufficient(value: PreparedPlannedFlashcardScope) -> None:
    if (
        value.prepared_scope.evidence.status is not EvidenceStatus.SUFFICIENT
        or not value.prepared_scope.evidence.items
    ):
        raise GroundingContractError("morphology requires sufficient active evidence")


def _parse(value: JsonValue) -> _MorphologyDraft:
    root = _object(
        value,
        {"object_plans", "candidates", "omissions", "topic_omissions"},
        "morphology draft",
    )
    batch = FlashcardCandidateBatch.from_json(
        freeze_object({"candidates": root["candidates"], "omissions": root["omissions"]})
    )
    plans: list[_ObjectPlan] = []
    for raw in _array(root["object_plans"], "object plans", 16):
        item = _object(
            raw,
            {
                "topic_keys",
                "macro_candidate_key",
                "atomic_candidate_keys",
                "reconstruction_dimensions",
            },
            "object plan",
        )
        topics = _texts(item["topic_keys"], "topic keys", 16, nonempty=True)
        atomics = _texts(item["atomic_candidate_keys"], "atomic keys", 3)
        dimensions = _texts(
            item["reconstruction_dimensions"], "reconstruction dimensions", 5, nonempty=True
        )
        if not set(dimensions) <= {
            "components",
            "topology",
            "relations",
            "course",
            "profiles",
            "landmarks",
        }:
            raise GroundingContractError("unknown reconstruction dimension")
        plans.append(
            _ObjectPlan(
                topics, _text(item["macro_candidate_key"], "macro key"), atomics, dimensions
            )
        )
    omissions: list[tuple[str, int]] = []
    for raw in _array(root["topic_omissions"], "topic omissions", 24):
        item = _object(raw, {"topic_key", "omission_index"}, "topic omission")
        index = item["omission_index"]
        if type(index) is not int:
            raise GroundingContractError("omission index must be an integer")
        omissions.append((_text(item["topic_key"], "topic key"), index))
    return _MorphologyDraft(tuple(plans), batch, tuple(omissions))


def _topic_handles(prepared: PreparedPlannedFlashcardScope, topics: tuple[str, ...]) -> set[str]:
    selected = set(topics)
    return {
        handle
        for entry in prepared.prepared_scope.index
        if entry.topic_key in selected
        for handle in entry.evidence_handles
    }


def _object(value: JsonValue, fields: set[str], name: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise GroundingContractError(f"{name} fields are not exact")
    return value


def _array(value: JsonValue, name: str, maximum: int) -> tuple[JsonValue, ...]:
    if not isinstance(value, tuple) or len(value) > maximum:
        raise GroundingContractError(f"{name} must be a bounded array")
    return value


def _text(value: JsonValue, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise GroundingContractError(f"{name} must be trimmed text")
    return value


def _texts(value: JsonValue, name: str, maximum: int, *, nonempty: bool = False) -> tuple[str, ...]:
    result = tuple(_text(item, name) for item in _array(value, name, maximum))
    if (nonempty and not result) or len(set(result)) != len(result):
        raise GroundingContractError(f"{name} must be ordered and unique")
    return result


def _failure(error: Exception) -> ValidationOutcome:
    return ValidationOutcome(
        False,
        ValidatorDisposition.TERMINATE,
        freeze_object({"status": "failed", "code": "morphology_flashcard_validation_failed"}),
        str(error).strip() or "morphology flashcard validation failed",
    )


def _normalize(value: str) -> str:
    folded = unicodedata.normalize("NFKC", value).casefold()
    return " ".join("".join(char if char.isalnum() else " " for char in folded).split())


def _candidate_content(value: JsonObject) -> str:
    content = dict(value)
    content.pop("candidate_key")
    content.pop("parent_candidate_key")
    return canonical_json_bytes(content).decode("utf-8")


__all__ = [
    "MorphologyFlashcardsIntegrityValidator",
    "MorphologyFlashcardsReadinessValidator",
    "morphology_flashcards_binding",
    "morphology_flashcards_validators",
]
