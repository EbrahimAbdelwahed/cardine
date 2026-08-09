"""Binding and fail-closed validators for the hybrid flashcard profile."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import cast

from study_agent.artifacts.candidates import (
    FlashcardCandidate,
    FlashcardCandidateBatch,
    FlashcardPedagogicalRole,
)
from study_agent.capabilities.bindings import (
    CapabilityDependencyResolver,
    ProfiledCapabilityBinding,
)
from study_agent.capabilities.builtin import PROPOSE_FLASHCARDS_MANIFEST
from study_agent.capabilities.worker_adapter import ProfiledWorkerExecutionDescriptor
from study_agent.domain import ExecutionContext, RetrievalForm
from study_agent.domain._validation import JsonObject, JsonValue, freeze_object
from study_agent.flashcards.lesson_worker_contracts import (
    LessonWorkerRequest,
    ProfileTaskExpectation,
    VerifiedFlashcardPageResult,
)
from study_agent.flashcards.planning import PreparedPlannedFlashcardScope
from study_agent.grounding import GroundingContractError
from study_agent.pedagogy import HYBRID_MACRO_DETAIL_V1
from study_agent.playbooks import (
    ToolBehaviorPin,
    ValidationOutcome,
    ValidatorDisposition,
    VersionPins,
    playbook_definition_fingerprint,
)
from study_agent.playbooks.builtin.hybrid_flashcards_flow import HYBRID_FLASHCARDS_FLOW
from study_agent.ports import EvidenceStatus, SourceContentPort
from study_agent.prompts.hybrid_flashcards_v1 import HYBRID_FLASHCARDS_PROMPT
from study_agent.skills import ArtifactReference, SemanticVersion
from study_agent.skills.builtin.hybrid_flashcards import HYBRID_FLASHCARDS_SKILL
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
class _TopicPlan:
    topic_key: str
    disposition: str
    candidate_keys: tuple[str, ...]
    omission_reason: str | None


@dataclass(frozen=True, slots=True)
class _HybridDraft:
    topic_plan: tuple[_TopicPlan, ...]
    batch: FlashcardCandidateBatch
    detail_bases: tuple[tuple[str, str], ...]


class HybridFlashcardsReadinessValidator:
    id = "hybrid_flashcards_readiness"
    version = VERSION

    async def validate(self, inputs: JsonObject) -> ValidationOutcome:
        try:
            if set(inputs) != {"prepared_scope", "scope"}:
                raise ValueError("hybrid readiness requires prepared_scope and scope")
            prepared = _prepared_scope(inputs["prepared_scope"])
            scope = inputs["scope"]
            if scope is not None and (
                not isinstance(scope, str) or not scope or scope != scope.strip()
            ):
                raise ValueError("scope must be null or non-empty trimmed text")
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


class HybridFlashcardsIntegrityValidator:
    id = "hybrid_flashcards_integrity"
    version = VERSION

    def __init__(self, content: SourceContentPort) -> None:
        self._content = content

    async def validate(self, inputs: JsonObject) -> ValidationOutcome:
        try:
            if set(inputs) == {"output"}:
                _parse_draft(inputs["output"])
                return ValidationOutcome(
                    True,
                    ValidatorDisposition.CONTINUE,
                    {"schema_valid": True},
                )
            if set(inputs) != {"prepared_scope", "requested_ceiling", "draft"}:
                raise GroundingContractError(
                    "hybrid integrity requires prepared_scope, requested_ceiling, and draft"
                )
            prepared = _prepared_scope(inputs["prepared_scope"])
            _require_sufficient(prepared)
            ceiling = inputs["requested_ceiling"]
            if type(ceiling) is not int or not 1 <= ceiling <= 24:
                raise GroundingContractError("requested_ceiling must be an integer from 1 to 24")
            draft = _parse_draft(inputs["draft"])
            self._validate_grounded_plan(prepared, ceiling, draft)
        except (GroundingContractError, KeyError, TypeError, ValueError) as error:
            return _failure(error)
        return ValidationOutcome(
            True,
            ValidatorDisposition.CONTINUE,
            draft.batch.to_json(),
        )

    def _validate_grounded_plan(
        self,
        prepared: PreparedPlannedFlashcardScope,
        requested_ceiling: int,
        draft: _HybridDraft,
    ) -> None:
        batch = draft.batch
        if len(batch.candidates) > requested_ceiling or len(batch.candidates) > 24:
            raise GroundingContractError("candidate count exceeds its global ceiling")

        expected_topics = prepared.active_topic_keys
        actual_topics = tuple(plan.topic_key for plan in draft.topic_plan)
        if actual_topics != expected_topics:
            raise GroundingContractError(
                "topic_plan must cover the prepared scope exactly once in canonical order"
            )

        candidates = {item.candidate_key: item for item in batch.candidates}
        assignments: dict[str, set[str]] = {key: set() for key in candidates}
        omission_uses: set[int] = set()
        entries = {entry.topic_key: entry for entry in prepared.prepared_scope.index}
        for plan in draft.topic_plan:
            entry = entries[plan.topic_key]
            unknown = set(plan.candidate_keys) - set(candidates)
            if unknown:
                raise GroundingContractError("topic_plan references an unknown candidate")
            if plan.disposition == "generate":
                has_cards = bool(plan.candidate_keys)
                has_omission = plan.omission_reason is not None
                if has_cards == has_omission:
                    raise GroundingContractError(
                        "generated topics require either assigned candidates or one omission"
                    )
                for key in plan.candidate_keys:
                    assignments[key].add(plan.topic_key)
            else:
                if plan.candidate_keys:
                    raise GroundingContractError("omitted topics cannot generate cards")
            if plan.omission_reason is not None:
                matching = tuple(
                    index
                    for index, omission in enumerate(batch.omissions)
                    if omission.reason == plan.omission_reason
                    and set(omission.evidence_ids) == set(entry.evidence_handles)
                )
                if len(matching) != 1 or matching[0] in omission_uses:
                    raise GroundingContractError(
                        "topic omission must map bijectively to its exact grounded omission"
                    )
                omission_uses.add(matching[0])

        if any(not topics for topics in assignments.values()):
            raise GroundingContractError("every candidate must be assigned to a content topic")
        if omission_uses != set(range(len(batch.omissions))):
            raise GroundingContractError("public omissions must be exhausted by topic_plan")

        topic_evidence = {
            entry.topic_key: set(entry.evidence_handles) for entry in prepared.prepared_scope.index
        }
        for candidate in batch.candidates:
            permitted = {
                handle
                for topic in assignments[candidate.candidate_key]
                for handle in topic_evidence[topic]
            }
            if not set(candidate.evidence_ids) <= permitted:
                raise GroundingContractError(
                    "candidate evidence must be linked to its assigned topics"
                )

        _validate_hierarchy(batch)
        _validate_detail_bases(batch, draft.detail_bases)
        _validate_uniqueness(batch)
        self._resolve_evidence(prepared, batch)

    def _resolve_evidence(
        self, prepared: PreparedPlannedFlashcardScope, batch: FlashcardCandidateBatch
    ) -> None:
        trusted = prepared.prepared_scope.evidence.by_handle()
        referenced = {
            handle for candidate in batch.candidates for handle in candidate.evidence_ids
        } | {handle for omission in batch.omissions for handle in omission.evidence_ids}
        if not referenced <= set(trusted):
            raise GroundingContractError("draft references evidence outside the active envelope")
        for handle in sorted(referenced):
            item = trusted[handle]
            try:
                resolved = self._content.resolve(item.citation)
            except Exception as error:
                raise GroundingContractError(
                    "flashcard evidence does not resolve to canonical content"
                ) from error
            if resolved.citation != item.citation or resolved.text != item.text:
                raise GroundingContractError(
                    "flashcard evidence no longer matches canonical content"
                )


def hybrid_flashcards_binding(
    *,
    dependency_resolver: CapabilityDependencyResolver,
    model_adapter: ArtifactReference,
    state_contract: ArtifactReference,
) -> ProfiledCapabilityBinding:
    pins = VersionPins(
        ArtifactReference(HYBRID_FLASHCARDS_SKILL.id, HYBRID_FLASHCARDS_SKILL.version),
        ArtifactReference(HYBRID_FLASHCARDS_FLOW.id, HYBRID_FLASHCARDS_FLOW.version),
        HYBRID_FLASHCARDS_PROMPT,
        (ToolBehaviorPin("source.prepare_planned_flashcard_scope", VERSION),),
        model_adapter,
        state_contract,
    )
    return ProfiledCapabilityBinding(
        PROPOSE_FLASHCARDS_MANIFEST,
        PROPOSE_FLASHCARDS_MANIFEST.fingerprint,
        HYBRID_MACRO_DETAIL_V1,
        HYBRID_FLASHCARDS_SKILL,
        HYBRID_FLASHCARDS_FLOW,
        pins,
        "candidate_batch",
        dependency_resolver,
    )


class HybridFlashcardTaskBinding:
    """Build exact B2 tasks for one persisted hybrid lesson request."""

    def __init__(self, request: LessonWorkerRequest, binding: ProfiledCapabilityBinding) -> None:
        if request.profile_expectation.profile_selection_receipt.profile != binding.profile:
            raise ValueError("lesson request profile differs from hybrid binding")
        self._request = request
        self._binding = binding
        expected = ProfileTaskExpectation(
            request.profile_expectation.profile_selection_receipt,
            binding.manifest.id,
            binding.manifest.version,
            binding.manifest_fingerprint,
            binding.manifest.required_authority,
            binding.pins,
            _definition_fingerprint(binding),
            binding.manifest.output_schema,
            fingerprint_output_schema(binding.manifest.output_schema),
            _validation_expectations(),
        )
        if expected != request.profile_expectation:
            raise ValueError("lesson request expectation differs from hybrid implementation")
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
            raise ValueError("hybrid public inputs differ from the persisted request")
        if not set(self._expectation.required_authority) <= context.requested_capabilities:
            raise ValueError("hybrid worker context lacks required authority")
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


class HybridPlannedBundleWorker:
    """Decode only verified B1 hybrid output for B2 page coordination."""

    def __init__(
        self,
        request: LessonWorkerRequest,
        task_binding: HybridFlashcardTaskBinding,
        worker: GenerationWorkerService,
    ) -> None:
        if task_binding.expectation != request.profile_expectation:
            raise ValueError("hybrid worker request and task binding differ")
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
            raise GenerationWorkerConflictError("hybrid worker task changed")
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
            raise GenerationWorkerConflictError("hybrid worker output is not an object")
        batch = FlashcardCandidateBatch.from_json(detail.output)
        return VerifiedFlashcardPageResult(
            len(batch.candidates),
            len(batch.omissions),
            detail.receipt.output_fingerprint,
            detail,
        )


def hybrid_flashcards_validators(
    content: SourceContentPort,
) -> tuple[HybridFlashcardsReadinessValidator, HybridFlashcardsIntegrityValidator]:
    return (
        HybridFlashcardsReadinessValidator(),
        HybridFlashcardsIntegrityValidator(content),
    )


def _definition_fingerprint(binding: ProfiledCapabilityBinding) -> str:
    return playbook_definition_fingerprint(binding.playbook)


def _validation_expectations() -> tuple[ValidationExpectation, ...]:
    return (
        ValidationExpectation(
            "check_hybrid_readiness",
            ValidationReceiptSource.VALIDATE_STEP,
            "hybrid_flashcards_readiness",
            "1.0.0",
        ),
        ValidationExpectation(
            "generate_hybrid_flashcards",
            ValidationReceiptSource.STRUCTURED_OUTPUT_FALLBACK,
            "hybrid_flashcards_integrity",
            "1.0.0",
        ),
        ValidationExpectation(
            "validate_hybrid_flashcards",
            ValidationReceiptSource.VALIDATE_STEP,
            "hybrid_flashcards_integrity",
            "1.0.0",
        ),
    )


def _prepared_scope(value: JsonValue) -> PreparedPlannedFlashcardScope:
    if not isinstance(value, Mapping):
        raise GroundingContractError("prepared_scope must be an object")
    return PreparedPlannedFlashcardScope.from_json(value)


def _require_sufficient(prepared: PreparedPlannedFlashcardScope) -> None:
    if (
        prepared.prepared_scope.evidence.status is not EvidenceStatus.SUFFICIENT
        or not prepared.prepared_scope.evidence.items
    ):
        raise GroundingContractError("hybrid flashcards require sufficient active evidence")


def _parse_draft(value: JsonValue) -> _HybridDraft:
    root = _object(
        value,
        {"topic_plan", "candidates", "omissions", "detail_bases"},
        "hybrid draft",
    )
    batch = FlashcardCandidateBatch.from_json(
        freeze_object({"candidates": root["candidates"], "omissions": root["omissions"]})
    )
    plans: list[_TopicPlan] = []
    for raw in _array(root["topic_plan"], "topic_plan", 256, require_nonempty=True):
        item = _object(
            raw,
            {"topic_key", "disposition", "candidate_keys", "omission_reason"},
            "topic plan",
        )
        topic_key = _text(item["topic_key"], "topic_key")
        disposition = _text(item["disposition"], "disposition")
        if disposition not in {"generate", "omit_scaffolding", "omit"}:
            raise GroundingContractError("topic disposition is unsupported")
        candidate_keys = _text_array(item["candidate_keys"], "candidate_keys", 24)
        omission = item["omission_reason"]
        if omission is not None:
            omission = _text(omission, "omission_reason")
        plans.append(_TopicPlan(topic_key, disposition, candidate_keys, omission))
    details: list[tuple[str, str]] = []
    for raw in _array(root["detail_bases"], "detail_bases", 24):
        item = _object(raw, {"candidate_key", "basis"}, "detail basis")
        key = _text(item["candidate_key"], "detail basis candidate_key")
        basis = _text(item["basis"], "detail basis")
        if basis not in {"fragile", "not_recoverable"}:
            raise GroundingContractError("detail basis is unsupported")
        details.append((key, basis))
    if len({key for key, _ in details}) != len(details):
        raise GroundingContractError("detail basis candidate keys must be unique")
    return _HybridDraft(tuple(plans), batch, tuple(details))


def _validate_hierarchy(batch: FlashcardCandidateBatch) -> None:
    if not batch.candidates:
        return
    roles = tuple(item.pedagogical_role for item in batch.candidates)
    allowed = {FlashcardPedagogicalRole.SECTION, FlashcardPedagogicalRole.DETAIL}
    if set(roles) - allowed:
        raise GroundingContractError("hybrid bundle candidates use only section and detail")
    rank = {
        FlashcardPedagogicalRole.SECTION: 0,
        FlashcardPedagogicalRole.DETAIL: 1,
    }
    if tuple(rank[role] for role in roles) != tuple(sorted(rank[role] for role in roles)):
        raise GroundingContractError("all sections must precede earned details")
    seen: dict[str, FlashcardPedagogicalRole] = {}
    for candidate in batch.candidates:
        if candidate.retrieval_form is not RetrievalForm.DIRECT_RECALL:
            raise GroundingContractError("hybrid candidates require direct recall")
        if candidate.morphology_family is not None or candidate.cognitive_function is not None:
            raise GroundingContractError("hybrid candidates forbid morphology fields")
        if candidate.media_evidence_ids:
            raise GroundingContractError("hybrid candidates forbid media")
        parent = candidate.parent_candidate_key
        role = candidate.pedagogical_role
        if role is FlashcardPedagogicalRole.SECTION:
            if parent is not None:
                raise GroundingContractError("section candidates must be roots")
        elif parent is None or seen.get(parent) is not FlashcardPedagogicalRole.SECTION:
            raise GroundingContractError("details require an earlier same-page section parent")
        seen[candidate.candidate_key] = role


def _validate_detail_bases(
    batch: FlashcardCandidateBatch, detail_bases: tuple[tuple[str, str], ...]
) -> None:
    expected = {
        candidate.candidate_key
        for candidate in batch.candidates
        if candidate.pedagogical_role is FlashcardPedagogicalRole.DETAIL
    }
    actual = {key for key, _ in detail_bases}
    if actual != expected:
        raise GroundingContractError("detail_bases must cover every and only detail candidate")


def _validate_uniqueness(batch: FlashcardCandidateBatch) -> None:
    prompts = tuple(_normalize(candidate.prompt) for candidate in batch.candidates)
    answers = tuple(_normalized_answer(candidate) for candidate in batch.candidates)
    if any(not value for value in prompts + answers):
        raise GroundingContractError("normalized prompts and answers cannot be empty")
    if len(set(prompts)) != len(prompts):
        raise GroundingContractError("normalized candidate prompts must be unique")
    if len(set(answers)) != len(answers):
        raise GroundingContractError("normalized candidate answers must be unique")
    payloads = tuple(_content_payload(candidate) for candidate in batch.candidates)
    if len(set(payloads)) != len(payloads):
        raise GroundingContractError("candidate payloads must be unique")
    for index, (prompt, answer) in enumerate(zip(prompts, answers, strict=True)):
        for other_prompt, other_answer in zip(
            prompts[index + 1 :], answers[index + 1 :], strict=True
        ):
            if (prompt in other_prompt and answer in other_answer) or (
                other_prompt in prompt and other_answer in answer
            ):
                raise GroundingContractError(
                    "candidate prompt/answer pairs cannot be containment-equivalent"
                )


def _normalized_answer(candidate: FlashcardCandidate) -> str:
    return " ".join(
        part
        for block in candidate.answer_blocks
        for part in (_normalize(block.text), *(_normalize(point) for point in block.key_points))
        if part
    )


def _content_payload(candidate: FlashcardCandidate) -> str:
    payload = dict(candidate.to_json())
    payload.pop("candidate_key")
    payload.pop("parent_candidate_key")
    return canonical_json_bytes(cast(JsonObject, payload)).decode("utf-8")


def _normalize(value: str) -> str:
    folded = unicodedata.normalize("NFKC", value).casefold()
    return " ".join("".join(char if char.isalnum() else " " for char in folded).split())


def _object(value: JsonValue, fields: set[str], name: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise GroundingContractError(f"{name} must contain exactly {sorted(fields)}")
    return value


def _array(
    value: JsonValue, name: str, maximum: int, *, require_nonempty: bool = False
) -> tuple[JsonValue, ...]:
    if not isinstance(value, tuple):
        raise GroundingContractError(f"{name} must be an array")
    if len(value) > maximum or (require_nonempty and not value):
        raise GroundingContractError(f"{name} has an invalid item count")
    return value


def _text(value: JsonValue, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise GroundingContractError(f"{name} must be non-empty trimmed text")
    return value


def _text_array(value: JsonValue, name: str, maximum: int) -> tuple[str, ...]:
    items = _array(value, name, maximum)
    parsed = tuple(_text(item, name) for item in items)
    if len(set(parsed)) != len(parsed):
        raise GroundingContractError(f"{name} must be unique")
    return parsed


def _failure(error: Exception) -> ValidationOutcome:
    reason = str(error).strip() or "hybrid flashcard validation failed"
    return ValidationOutcome(
        False,
        ValidatorDisposition.TERMINATE,
        freeze_object({"status": "failed", "code": "hybrid_flashcard_validation_failed"}),
        reason,
    )


__all__ = [
    "HybridFlashcardsIntegrityValidator",
    "HybridFlashcardsReadinessValidator",
    "hybrid_flashcards_binding",
    "hybrid_flashcards_validators",
]
