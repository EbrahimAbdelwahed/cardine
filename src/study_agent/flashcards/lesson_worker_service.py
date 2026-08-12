"""Resumable provider-neutral fan-out over exact planned flashcard bundles."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from hashlib import sha256
from typing import NoReturn

from study_agent.artifacts.candidates import (
    FlashcardCandidateBatch,
    FlashcardPedagogicalRole,
)
from study_agent.domain import ExecutionContext, RunId
from study_agent.domain._validation import freeze_object
from study_agent.flashcards.lesson_worker_contracts import (
    LessonWorkerCheckpoint,
    LessonWorkerPageCheckpoint,
    LessonWorkerPageReceipt,
    LessonWorkerPageStatus,
    LessonWorkerRequest,
    LessonWorkerStatus,
    VerifiedFlashcardPageResult,
    authority_fingerprint,
    child_task_id,
    lesson_run_id,
)
from study_agent.flashcards.lesson_worker_view import (
    LessonWorkerBatchReviewView,
    LessonWorkerCompactView,
    LessonWorkerCompletedReviewView,
    LessonWorkerOverviewAssociation,
    LessonWorkerPageReviewView,
)
from study_agent.flashcards.planning import (
    PlannedFlashcardBundle,
    PreparedPlannedFlashcardScope,
)
from study_agent.flashcards.scope import FlashcardScopeIndexEntry, PreparedFlashcardScope
from study_agent.ports.lesson_worker import (
    FlashcardProfileTaskBinding,
    LessonGeneratedBatchOwnerCommitment,
    LessonGeneratedBatchOwnerPublication,
    LessonGeneratedBatchOwnerWriter,
    LessonWorkerStore,
    PlannedBundleEvidenceResolver,
    PlannedBundleWorker,
)
from study_agent.state import canonical_json_bytes
from study_agent.workers.contracts import (
    GenerationWorkerReceipt,
    GenerationWorkerStatus,
    GenerationWorkerTask,
    GenerationWorkerTaskKind,
)
from study_agent.workers.view import WorkerCompactView


class LessonWorkerConflictError(RuntimeError):
    """An exact request, authority, plan, profile, source, or task binding changed."""


class _UnconfiguredGeneratedBatchOwnerWriter:
    def create(
        self,
        commitment: LessonGeneratedBatchOwnerCommitment,
        task: GenerationWorkerTask,
        receipt: GenerationWorkerReceipt,
        context: ExecutionContext,
    ) -> LessonGeneratedBatchOwnerPublication:
        del commitment, task, receipt, context
        raise LessonWorkerConflictError("generated batch owner writer is not configured")


_UNCONFIGURED_OWNER_WRITER = _UnconfiguredGeneratedBatchOwnerWriter()


class LessonWorkerService:
    def __init__(
        self,
        *,
        store: LessonWorkerStore,
        resolver: PlannedBundleEvidenceResolver,
        task_binding: FlashcardProfileTaskBinding,
        worker: PlannedBundleWorker,
        owner_writer: LessonGeneratedBatchOwnerWriter = _UNCONFIGURED_OWNER_WRITER,
    ) -> None:
        self._store = store
        self._resolver = resolver
        self._task_binding = task_binding
        self._worker = worker
        self._owner_writer = owner_writer

    async def start(
        self,
        request: LessonWorkerRequest,
        parent: ExecutionContext,
    ) -> LessonWorkerCompactView:
        authority = _trusted_authority(request, parent)
        if self._task_binding.expectation != request.profile_expectation:
            self._conflict("lesson worker profile expectation changed")
        run_id = lesson_run_id(request, authority)
        checkpoint = LessonWorkerCheckpoint(
            request_bytes=request.to_bytes(),
            request_fingerprint=request.fingerprint,
            authority_fingerprint=authority,
            run_id=run_id,
            pages=tuple(
                LessonWorkerPageCheckpoint(
                    position=bundle.relative_position,
                    bundle_id=bundle.bundle_id,
                    status=LessonWorkerPageStatus.PENDING,
                    child_task_id=child_task_id(run_id, request, bundle, None),
                )
                for bundle in request.plan.bundles
            ),
        )
        if not self._store.create(str(run_id), checkpoint.to_bytes()):
            checkpoint = LessonWorkerCheckpoint.from_bytes(self._store.load(str(run_id)))
            self._require_identity(checkpoint, request, authority)
        return await self.advance(run_id, request, parent)

    async def advance(
        self,
        run_id: RunId,
        request: LessonWorkerRequest,
        parent: ExecutionContext,
    ) -> LessonWorkerCompactView:
        if not isinstance(run_id, RunId):
            raise TypeError("run_id must be RunId")
        authority = _trusted_authority(request, parent)
        checkpoint, raw = self._load(run_id, request, authority)

        # First observe every already claimed child. This preserves the global
        # cap across calls and recovers completion before claiming new work.
        existing_claimed = {
            page.position
            for page in checkpoint.pages
            if page.status is LessonWorkerPageStatus.CHILD_CLAIMED
        }
        for position in sorted(existing_claimed):
            page = checkpoint.pages[position]
            if page.status is LessonWorkerPageStatus.CHILD_CLAIMED:
                checkpoint, raw = await self._observe_claimed(
                    checkpoint, raw, position, request, parent
                )

        in_flight = sum(
            page.status is LessonWorkerPageStatus.CHILD_CLAIMED for page in checkpoint.pages
        )
        available = request.concurrency - in_flight
        if available <= 0:
            return _compact(checkpoint)

        for position in range(len(checkpoint.pages)):
            if available <= 0:
                break
            if position in existing_claimed:
                continue
            if checkpoint.pages[position].status is LessonWorkerPageStatus.CHILD_TERMINAL:
                continue
            checkpoint, raw = self._prepare_page(checkpoint, raw, position, request, parent)
            if checkpoint.pages[position].status is LessonWorkerPageStatus.PREPARED:
                checkpoint, raw = self._claim_child(checkpoint, raw, position, request, parent)
            if checkpoint.pages[position].status is LessonWorkerPageStatus.CHILD_CLAIMED:
                available -= 1
                checkpoint, raw = await self._observe_claimed(
                    checkpoint, raw, position, request, parent
                )
                if checkpoint.pages[position].status is LessonWorkerPageStatus.CHILD_TERMINAL:
                    available += 1
        return _compact(checkpoint)

    def review_page(
        self,
        run_id: RunId,
        request: LessonWorkerRequest,
        page_position: int,
        parent: ExecutionContext,
    ) -> LessonWorkerPageReviewView:
        authority = _trusted_authority(request, parent)
        checkpoint, _ = self._load(run_id, request, authority)
        if type(page_position) is not int or not 0 <= page_position < len(checkpoint.pages):
            raise ValueError("page_position is outside the canonical lesson plan")
        page = checkpoint.pages[page_position]
        if (
            page.status is not LessonWorkerPageStatus.CHILD_TERMINAL
            or page.receipt is None
            or page.receipt.failure_code is not None
            or page.wrapper_bytes is None
        ):
            self._conflict("verified lesson page detail is unavailable")
        wrapper = PreparedPlannedFlashcardScope.from_bytes(page.wrapper_bytes)
        if page.child_task_bytes is None:
            self._conflict("completed lesson page lacks its exact child task")
        task = GenerationWorkerTask.from_bytes(page.child_task_bytes)
        _verify_task(task, page.child_task_id, request, wrapper)
        verified = self._worker.detail(page.child_task_id, wrapper.wrapper_fingerprint, parent)
        _verify_completed_detail(
            verified,
            task,
            page.receipt.child_receipt_fingerprint,
            page.receipt.child_run_id,
        )
        if (
            verified.candidate_count != page.receipt.candidate_count
            or verified.omission_count != page.receipt.omission_count
        ):
            self._conflict("worker detail receipt changed")
        bundle = request.plan.bundles[page_position]
        return LessonWorkerPageReviewView(
            run_id=run_id,
            plan_fingerprint=request.plan.plan_fingerprint,
            profile_fingerprint=request.profile_expectation.profile_fingerprint,
            page_position=page_position,
            bundle_id=bundle.bundle_id,
            bundle_kind=bundle.kind,
            active_topic_keys=bundle.active_topic_keys,
            wrapper_fingerprint=wrapper.wrapper_fingerprint,
            scope_fingerprint=wrapper.prepared_scope.scope_fingerprint,
            read_set_fingerprint=wrapper.prepared_scope.evidence.read_set_fingerprint,
            revision_commitments_fingerprint=request.revision_commitments_fingerprint,
            detail=verified.detail,
        )

    def review_completed(
        self,
        run_id: RunId,
        request: LessonWorkerRequest,
        parent: ExecutionContext,
    ) -> LessonWorkerCompletedReviewView:
        """Decode and cross-check every verified page for an authorized reviewer."""

        authority = _trusted_authority(request, parent)
        checkpoint, _ = self._load(run_id, request, authority)
        if any(
            page.status is not LessonWorkerPageStatus.CHILD_TERMINAL
            or page.receipt is None
            or page.receipt.failure_code is not None
            or page.wrapper_bytes is None
            for page in checkpoint.pages
        ):
            self._conflict("completed lesson review is unavailable")

        pages: list[LessonWorkerBatchReviewView] = []
        candidate_keys: set[str] = set()
        referenced_spans: list[tuple[int, str, str, int, int]] = []
        overview_candidates: list[tuple[int, str]] = []
        for position in range(len(checkpoint.pages)):
            page = checkpoint.pages[position]
            assert page.receipt is not None and page.wrapper_bytes is not None
            reviewed = self.review_page(run_id, request, position, parent)
            if not isinstance(reviewed.detail.output, Mapping):
                self._conflict("verified flashcard page output is not an object")
            try:
                batch = FlashcardCandidateBatch.from_json(reviewed.detail.output)
            except (TypeError, ValueError) as error:
                raise LessonWorkerConflictError(
                    "verified flashcard page output is not an exact candidate batch"
                ) from error
            if (
                len(batch.candidates) != page.receipt.candidate_count
                or len(batch.omissions) != page.receipt.omission_count
            ):
                self._conflict("verified flashcard page batch counts changed")

            keys = {candidate.candidate_key for candidate in batch.candidates}
            if candidate_keys.intersection(keys):
                self._conflict("candidate key is duplicated across lesson pages")
            candidate_keys.update(keys)

            wrapper = PreparedPlannedFlashcardScope.from_bytes(page.wrapper_bytes)
            evidence = wrapper.prepared_scope.evidence.by_handle()
            referenced = {
                handle for candidate in batch.candidates for handle in candidate.evidence_ids
            } | {handle for omission in batch.omissions for handle in omission.evidence_ids}
            if not referenced <= set(evidence):
                self._conflict("candidate batch references evidence outside its page scope")
            cited_by_candidates = {
                handle
                for candidate in batch.candidates
                for handle in candidate.evidence_ids
            }
            index = {entry.topic_key: entry for entry in wrapper.prepared_scope.index}
            if any(
                not cited_by_candidates.intersection(index[topic_key].evidence_handles)
                for topic_key in reviewed.active_topic_keys
            ):
                self._conflict("lesson candidate coverage is incomplete")
            for handle in sorted(referenced):
                citation = evidence[handle].citation
                span = (
                    position,
                    str(citation.source_id),
                    str(citation.revision_id),
                    citation.start_offset,
                    citation.end_offset,
                )
                if any(_cross_page_span_overlap(span, prior) for prior in referenced_spans):
                    self._conflict("candidate evidence overlaps across lesson pages")
                referenced_spans.append(span)

            overview_candidates.extend(
                (position, candidate.candidate_key)
                for candidate in batch.candidates
                if candidate.pedagogical_role is FlashcardPedagogicalRole.OVERVIEW
            )

            pages.append(
                LessonWorkerBatchReviewView(
                    page_position=reviewed.page_position,
                    bundle_id=reviewed.bundle_id,
                    bundle_kind=reviewed.bundle_kind,
                    active_topic_keys=reviewed.active_topic_keys,
                    wrapper_fingerprint=reviewed.wrapper_fingerprint,
                    scope_fingerprint=reviewed.scope_fingerprint,
                    read_set_fingerprint=reviewed.read_set_fingerprint,
                    batch=batch,
                )
            )
        if len(overview_candidates) > 1:
            self._conflict("lesson contains multiple overview candidates")
        overview_associations: tuple[LessonWorkerOverviewAssociation, ...] = ()
        if overview_candidates:
            overview_position, overview_key = overview_candidates[0]
            if overview_position != 0:
                self._conflict("lesson overview is not in the earliest canonical page")
            associated_pages = tuple(
                page.page_position
                for page in pages
                if page.page_position != overview_position
            )
            associated_bundles = tuple(
                page.bundle_id
                for page in pages
                if page.page_position != overview_position
            )
            overview_associations = (
                LessonWorkerOverviewAssociation(
                    page_position=overview_position,
                    candidate_key=overview_key,
                    associated_page_positions=associated_pages,
                    associated_bundle_ids=associated_bundles,
                ),
            )
        overview_bundle_id: str | None = None
        overview_fingerprint: str | None = None
        if overview_associations:
            association = overview_associations[0]
            overview_bundle_id = pages[association.page_position].bundle_id
            overview_fingerprint = sha256(
                b"lesson-overview-association@1\0"
                + canonical_json_bytes(
                    freeze_object(
                        {
                            "page_position": association.page_position,
                            "candidate_key": association.candidate_key,
                            "associated_page_positions": association.associated_page_positions,
                            "associated_bundle_ids": association.associated_bundle_ids,
                        }
                    )
                )
            ).hexdigest()
        bundle_order = tuple(bundle.bundle_id for bundle in request.plan.bundles)
        for owner_page in pages:
            checkpoint_page = checkpoint.pages[owner_page.page_position]
            if checkpoint_page.child_task_bytes is None or checkpoint_page.receipt is None:
                self._conflict("completed lesson page lacks owner commitments")
            task = GenerationWorkerTask.from_bytes(checkpoint_page.child_task_bytes)
            detail = self._worker.detail(
                task.task_id, owner_page.wrapper_fingerprint, parent
            )
            _verify_completed_detail(
                detail,
                task,
                checkpoint_page.receipt.child_receipt_fingerprint,
                checkpoint_page.receipt.child_run_id,
            )
            bundle = request.plan.bundles[owner_page.page_position]
            association_id = (
                overview_bundle_id
                if overview_bundle_id is not None and owner_page.page_position != 0
                else None
            )
            association_fingerprint = overview_fingerprint if association_id else None
            publication = self._owner_writer.create(
                LessonGeneratedBatchOwnerCommitment(
                    lesson_run_id=run_id,
                    lesson_request_fingerprint=request.fingerprint,
                    lesson_plan_fingerprint=request.plan.plan_fingerprint,
                    lesson_profile_fingerprint=request.profile_expectation.profile_fingerprint,
                    coordinator_fingerprint=checkpoint.fingerprint,
                    page_position=owner_page.page_position,
                    bundle_order=bundle_order,
                    bundle_id=owner_page.bundle_id,
                    bundle_fingerprint=_bundle_fingerprint(bundle),
                    wrapper_fingerprint=owner_page.wrapper_fingerprint,
                    scope_fingerprint=owner_page.scope_fingerprint,
                    read_set_fingerprint=owner_page.read_set_fingerprint,
                    revision_commitments_fingerprint=(
                        request.revision_commitments_fingerprint
                    ),
                    associated_overview_bundle_id=association_id,
                    overview_association_fingerprint=association_fingerprint,
                    profile_selection_receipt=(
                        request.profile_expectation.profile_selection_receipt
                    ),
                ),
                task,
                detail.detail.receipt,
                parent,
            )
            _verify_owner_publication(publication, task, detail.detail.receipt)
        return LessonWorkerCompletedReviewView(
            run_id=run_id,
            plan_fingerprint=request.plan.plan_fingerprint,
            profile_fingerprint=request.profile_expectation.profile_fingerprint,
            revision_commitments_fingerprint=request.revision_commitments_fingerprint,
            pages=tuple(pages),
            overview_associations=overview_associations,
        )

    def _prepare_page(
        self,
        checkpoint: LessonWorkerCheckpoint,
        raw: bytes,
        position: int,
        request: LessonWorkerRequest,
        parent: ExecutionContext,
    ) -> tuple[LessonWorkerCheckpoint, bytes]:
        page = checkpoint.pages[position]
        if page.status is LessonWorkerPageStatus.PENDING:
            resolving = replace(page, status=LessonWorkerPageStatus.RESOLVING)
            checkpoint, raw = self._replace_page(checkpoint, raw, position, resolving)
            page = checkpoint.pages[position]
        if page.status is not LessonWorkerPageStatus.RESOLVING:
            return checkpoint, raw

        bundle = request.plan.bundles[position]
        resolved = self._resolver.resolve(
            request.plan,
            bundle,
            request.revision_commitments,
            parent,
        )
        resolved.validate(request.plan, bundle, request.revision_commitments)
        handles_by_topic: dict[str, list[str]] = {key: [] for key in bundle.active_topic_keys}
        for slot, item in zip(bundle.slots, resolved.envelope.items, strict=True):
            handles_by_topic[slot.topic_key].append(item.handle)
        index = tuple(
            FlashcardScopeIndexEntry(
                topic_key=item.topic_key,
                heading=item.title,
                locator=item.span.locator,
                relative_position=item.relative_position,
                character_count=item.subtree_visible_character_count,
                evidence_handles=tuple(handles_by_topic.get(item.topic_key, ())),
            )
            for item in request.plan.index
        )
        legacy = PreparedFlashcardScope.prepare(index, resolved.envelope)
        wrapper = PreparedPlannedFlashcardScope.prepare(legacy, request.plan, bundle.bundle_id)
        wrapper.validate_against_plan(request.plan)
        wrapper_bytes = wrapper.to_bytes()
        if len(wrapper_bytes) > 512 * 1024:
            raise ValueError("prepared planned wrapper exceeds 512 KiB")
        prepared = LessonWorkerPageCheckpoint(
            position=position,
            bundle_id=bundle.bundle_id,
            status=LessonWorkerPageStatus.PREPARED,
            child_task_id=child_task_id(checkpoint.run_id, request, bundle, wrapper),
            resolution_fingerprint=resolved.fingerprint,
            wrapper_bytes=wrapper_bytes,
        )
        return self._replace_page(checkpoint, raw, position, prepared)

    def _claim_child(
        self,
        checkpoint: LessonWorkerCheckpoint,
        raw: bytes,
        position: int,
        request: LessonWorkerRequest,
        parent: ExecutionContext,
    ) -> tuple[LessonWorkerCheckpoint, bytes]:
        page = checkpoint.pages[position]
        if page.status is not LessonWorkerPageStatus.PREPARED or page.wrapper_bytes is None:
            return checkpoint, raw
        wrapper = PreparedPlannedFlashcardScope.from_bytes(page.wrapper_bytes)
        task = self._task_binding.build(
            page.child_task_id,
            request.to_public_inputs(),
            wrapper,
            parent,
        )
        _verify_task(task, page.child_task_id, request, wrapper)
        claimed = replace(
            page,
            status=LessonWorkerPageStatus.CHILD_CLAIMED,
            child_task_bytes=task.to_bytes(),
        )
        return self._replace_page(checkpoint, raw, position, claimed)

    async def _observe_claimed(
        self,
        checkpoint: LessonWorkerCheckpoint,
        raw: bytes,
        position: int,
        request: LessonWorkerRequest,
        parent: ExecutionContext,
    ) -> tuple[LessonWorkerCheckpoint, bytes]:
        page = checkpoint.pages[position]
        if (
            page.status is not LessonWorkerPageStatus.CHILD_CLAIMED
            or page.wrapper_bytes is None
            or page.child_task_bytes is None
        ):
            return checkpoint, raw
        wrapper = PreparedPlannedFlashcardScope.from_bytes(page.wrapper_bytes)
        task = GenerationWorkerTask.from_bytes(page.child_task_bytes)
        _verify_task(task, page.child_task_id, request, wrapper)
        view = await self._worker.start(task, wrapper, parent)
        _verify_worker_view(view, task)
        if view.status in {GenerationWorkerStatus.PENDING, GenerationWorkerStatus.RUNNING}:
            return checkpoint, raw
        if view.status is GenerationWorkerStatus.SUSPENDED:
            receipt = _incompatible_suspended_receipt(position, page, view)
        elif view.status is GenerationWorkerStatus.COMPLETED:
            if view.child_run_id is None or view.receipt_fingerprint is None:
                self._conflict("completed child lacks compact receipt commitments")
            verified = self._worker.detail(task.task_id, wrapper.wrapper_fingerprint, parent)
            _verify_completed_detail(
                verified,
                task,
                view.receipt_fingerprint,
                view.child_run_id,
            )
            if verified.candidate_count > request.candidate_ceiling:
                self._conflict("completed child exceeds request candidate ceiling")
            receipt = LessonWorkerPageReceipt(
                position=position,
                bundle_id=page.bundle_id,
                child_task_id=page.child_task_id,
                child_run_id=view.child_run_id,
                child_receipt_fingerprint=view.receipt_fingerprint,
                candidate_count=verified.candidate_count,
                omission_count=verified.omission_count,
                failure_code=None,
            )
        elif view.status.is_terminal:
            if view.child_run_id is None or view.receipt_fingerprint is None:
                self._conflict("terminal child lacks compact receipt commitments")
            receipt = LessonWorkerPageReceipt(
                position=position,
                bundle_id=page.bundle_id,
                child_task_id=page.child_task_id,
                child_run_id=view.child_run_id,
                child_receipt_fingerprint=view.receipt_fingerprint,
                candidate_count=0,
                omission_count=0,
                failure_code=view.failure_code or "child_failed",
            )
        else:
            self._conflict("child returned an incompatible operational status")
        terminal = replace(
            page,
            status=LessonWorkerPageStatus.CHILD_TERMINAL,
            receipt=receipt,
        )
        return self._replace_page(checkpoint, raw, position, terminal)

    def _replace_page(
        self,
        checkpoint: LessonWorkerCheckpoint,
        raw: bytes,
        position: int,
        replacement: LessonWorkerPageCheckpoint,
    ) -> tuple[LessonWorkerCheckpoint, bytes]:
        pages = list(checkpoint.pages)
        pages[position] = replacement
        updated = replace(checkpoint, pages=tuple(pages))
        updated_raw = updated.to_bytes()
        key = str(checkpoint.run_id)
        try:
            changed = self._store.compare_and_set(key, raw, updated_raw)
        except ValueError as error:
            if str(error) == "checkpoint lesson run identity is invalid":
                self._conflict("lesson worker authority changed")
            raise
        if changed:
            return updated, updated_raw
        raced_raw = self._store.load(key)
        raced = LessonWorkerCheckpoint.from_bytes(raced_raw)
        self._require_identity(raced, checkpoint.request, checkpoint.authority_fingerprint)
        if raced.pages[position].to_json() == replacement.to_json():
            return raced, raced_raw
        if _page_rank(raced.pages[position].status) > _page_rank(replacement.status):
            return raced, raced_raw
        self._conflict("lesson worker page changed concurrently")

    def _load(
        self,
        run_id: RunId,
        request: LessonWorkerRequest,
        authority: str,
    ) -> tuple[LessonWorkerCheckpoint, bytes]:
        raw = self._store.load(str(run_id))
        checkpoint = LessonWorkerCheckpoint.from_bytes(raw)
        self._require_identity(checkpoint, request, authority)
        if checkpoint.run_id != run_id:
            self._conflict("lesson worker run identity changed")
        return checkpoint, raw

    def _require_identity(
        self,
        checkpoint: LessonWorkerCheckpoint,
        request: LessonWorkerRequest,
        authority: str,
    ) -> None:
        if (
            checkpoint.request_bytes != request.to_bytes()
            or checkpoint.request_fingerprint != request.fingerprint
        ):
            self._conflict("lesson worker request bytes changed")
        if checkpoint.authority_fingerprint != authority:
            self._conflict("lesson worker authority changed")
        if self._task_binding.expectation != request.profile_expectation:
            self._conflict("lesson worker profile expectation changed")

    @staticmethod
    def _conflict(message: str) -> NoReturn:
        raise LessonWorkerConflictError(message)


def _verify_task(
    task: GenerationWorkerTask,
    task_id: str,
    request: LessonWorkerRequest,
    wrapper: PreparedPlannedFlashcardScope,
) -> None:
    expected = request.profile_expectation
    # Locate by committed identity, not by a heading/index position coincidence.
    bundle = next(item for item in request.plan.bundles if item.bundle_id == wrapper.bundle_id)
    expected_index = _index_references(request, bundle, wrapper)
    expected_evidence = tuple(item.handle for item in wrapper.prepared_scope.evidence.items)
    if (
        task.task_id != task_id
        or task.task_kind is not GenerationWorkerTaskKind.FLASHCARD_BUNDLE
        or task.capability_id is not expected.capability_id
        or task.capability_version != expected.capability_version
        or task.manifest_fingerprint != expected.manifest_fingerprint
        or task.required_authority != expected.required_authority
        or task.pins != expected.pins
        or task.definition_fingerprint != expected.definition_fingerprint
        or task.output_schema != expected.output_schema
        or task.output_schema_fingerprint != expected.output_schema_fingerprint
        or task.expected_validations != expected.validations
        or task.payload != request.to_public_inputs()
        or task.language != request.language
        or task.preferences != freeze_object({})
        or task.continuation_summary != request.continuation_summary
        or task.index_references != expected_index
        or task.evidence_references != expected_evidence
    ):
        raise LessonWorkerConflictError("profile binding returned a changed worker task")


def _index_references(
    request: LessonWorkerRequest,
    bundle: PlannedFlashcardBundle,
    wrapper: PreparedPlannedFlashcardScope,
) -> tuple[str, ...]:
    bundle_fingerprint = _bundle_fingerprint(bundle)
    return (
        f"plan-sha256:{request.plan.plan_fingerprint}",
        f"bundle-sha256:{bundle_fingerprint}",
        f"wrapper-sha256:{wrapper.wrapper_fingerprint}",
        f"read-set-sha256:{wrapper.prepared_scope.evidence.read_set_fingerprint}",
        f"revisions-sha256:{request.revision_commitments_fingerprint}",
        f"profile-sha256:{request.profile_expectation.fingerprint}",
    )


def _bundle_fingerprint(bundle: PlannedFlashcardBundle) -> str:
    return sha256(
        b"lesson-worker-bundle@1\0" + canonical_json_bytes(bundle.to_json())
    ).hexdigest()


def _verify_owner_publication(
    publication: LessonGeneratedBatchOwnerPublication,
    task: GenerationWorkerTask,
    receipt: GenerationWorkerReceipt,
) -> None:
    if not isinstance(publication, LessonGeneratedBatchOwnerPublication):
        raise LessonWorkerConflictError("generated owner publication is invalid")
    if not isinstance(receipt, GenerationWorkerReceipt):
        raise LessonWorkerConflictError("generated owner receipt is invalid")
    if (
        publication.child_run_id != receipt.child_run_id
        or publication.child_task_fingerprint != task.fingerprint
        or publication.child_receipt_fingerprint != receipt.fingerprint
    ):
        raise LessonWorkerConflictError("generated owner publication changed child identity")
    for value in (
        publication.child_proof_fingerprint,
        publication.owner_receipt_fingerprint,
    ):
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise LessonWorkerConflictError("generated owner publication is invalid")


def _verify_worker_view(view: WorkerCompactView, task: GenerationWorkerTask) -> None:
    if (
        view.task_id != task.task_id
        or view.task_kind is not task.task_kind
        or view.task_fingerprint != task.fingerprint
    ):
        raise LessonWorkerConflictError("child compact view changed task identity")


def _verify_completed_detail(
    verified: VerifiedFlashcardPageResult,
    task: GenerationWorkerTask,
    expected_receipt_fingerprint: str,
    expected_child_run_id: RunId,
) -> None:
    receipt = verified.detail.receipt
    if (
        receipt.status is not GenerationWorkerStatus.COMPLETED
        or receipt.task_id != task.task_id
        or receipt.task_kind is not task.task_kind
        or receipt.child_run_id != expected_child_run_id
        or receipt.task_fingerprint != task.fingerprint
        or receipt.pins_fingerprint != task.pins_fingerprint
        or receipt.input_fingerprint != task.payload_fingerprint
        or receipt.output_fingerprint != verified.output_fingerprint
        or receipt.prompt_fingerprint is None
        or receipt.failure_code is not None
        or receipt.fingerprint != expected_receipt_fingerprint
    ):
        raise LessonWorkerConflictError("completed child receipt changed")


def _incompatible_suspended_receipt(
    position: int,
    page: LessonWorkerPageCheckpoint,
    view: WorkerCompactView,
) -> LessonWorkerPageReceipt:
    if view.child_run_id is None:
        raise LessonWorkerConflictError("suspended child lacks a run identity")
    synthetic = sha256(
        b"lesson-worker-suspended@1\0"
        + canonical_json_bytes(
            freeze_object(
                {
                    "task_id": view.task_id,
                    "child_run_id": str(view.child_run_id),
                    "generation": view.generation,
                }
            )
        )
    ).hexdigest()
    return LessonWorkerPageReceipt(
        position=position,
        bundle_id=page.bundle_id,
        child_task_id=page.child_task_id,
        child_run_id=view.child_run_id,
        child_receipt_fingerprint=synthetic,
        candidate_count=0,
        omission_count=0,
        failure_code="child_suspended_incompatible",
    )


def _trusted_authority(request: LessonWorkerRequest, parent: ExecutionContext) -> str:
    if not isinstance(parent, ExecutionContext):
        raise TypeError("parent must be ExecutionContext")
    required = request.profile_expectation.required_authority
    if set(required) - set(parent.requested_capabilities):
        raise LessonWorkerConflictError("parent lacks lesson-worker-required authority")
    return authority_fingerprint(
        freeze_object(
            {
                "principal_kind": parent.principal_kind.value,
                "principal_id": parent.principal_id,
                "course_id": str(parent.course_id),
                "session_id": str(parent.session_id) if parent.session_id else None,
                "required_authority": required,
            }
        )
    )


def _page_rank(status: LessonWorkerPageStatus) -> int:
    return tuple(LessonWorkerPageStatus).index(status)


def _cross_page_span_overlap(
    left: tuple[int, str, str, int, int],
    right: tuple[int, str, str, int, int],
) -> bool:
    left_page, left_source, left_revision, left_start, left_end = left
    right_page, right_source, right_revision, right_start, right_end = right
    return (
        left_page != right_page
        and left_source == right_source
        and left_revision == right_revision
        and left_start < right_end
        and right_start < left_end
    )


def _compact(checkpoint: LessonWorkerCheckpoint) -> LessonWorkerCompactView:
    request = checkpoint.request
    terminal = tuple(
        page for page in checkpoint.pages if page.status is LessonWorkerPageStatus.CHILD_TERMINAL
    )
    failed = tuple(
        page.position
        for page in terminal
        if page.receipt is not None and page.receipt.failure_code is not None
    )
    completed = tuple(
        page.position
        for page in terminal
        if page.receipt is not None and page.receipt.failure_code is None
    )
    pending = tuple(
        page.position
        for page in checkpoint.pages
        if page.status is not LessonWorkerPageStatus.CHILD_TERMINAL
    )
    in_progress = any(
        page.status is LessonWorkerPageStatus.CHILD_CLAIMED for page in checkpoint.pages
    )
    all_terminal = len(terminal) == len(checkpoint.pages)
    status = (
        LessonWorkerStatus.FAILED
        if all_terminal and failed
        else LessonWorkerStatus.COMPLETED
        if all_terminal
        else LessonWorkerStatus.RUNNING
        if checkpoint.pages
        else LessonWorkerStatus.COMPLETED
    )
    receipts = tuple(page.receipt for page in terminal if page.receipt is not None)
    return LessonWorkerCompactView(
        run_id=checkpoint.run_id,
        plan_fingerprint=request.plan.plan_fingerprint,
        profile_fingerprint=request.profile_expectation.profile_fingerprint,
        status=status,
        completed_positions=completed,
        failed_positions=failed,
        pending_positions=pending,
        candidate_count=sum(item.candidate_count for item in receipts),
        omission_count=sum(item.omission_count for item in receipts),
        failure_codes=tuple(
            item.failure_code for item in receipts if item.failure_code is not None
        ),
        in_progress=in_progress,
        advance_required=bool(pending),
    )


__all__ = ["LessonWorkerConflictError", "LessonWorkerService"]
