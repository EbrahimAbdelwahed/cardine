"""Restart-safe coordinator for paired complete/study material generation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Protocol
from unicodedata import normalize

from cardine.integrations.study_agent.course_policy import (  # type: ignore[import-untyped]
    ProviderConsentRequiredError,
)
from study_agent.artifacts.service import RetryableArtifactConflictError
from study_agent.domain import (
    BlobRef,
    CourseId,
    ExecutionContext,
    PrincipalKind,
    RunId,
    SessionId,
)
from study_agent.ports import (
    BlobStore,
    ClockPort,
    EventStore,
    ModelError,
    ModelErrorCode,
    ModelPort,
    VerifiedGeneratedBatchPort,
)
from study_agent.ports.artifact import ArtifactCommandPort
from study_agent.ports.model import ModelRequest, ModelResponse
from study_agent.state import canonical_json_bytes

from .generation_contracts import (
    MaterialGenerationErrorCode,
    MaterialGenerationRequest,
    MaterialGenerationStage,
    MaterialGenerationState,
    MaterialGenerationView,
    PinnedTranscriptInput,
    StageReceipt,
    limitations_fingerprint,
)
from .planning import (
    SegmentBoundaries,
    UnitManifest,
    build_unit_manifest,
    parse_segment_boundaries,
    units_for_boundary,
)
from .prompts import (
    boundary_request,
    complete_merge_request,
    complete_segment_request,
    request_fingerprint,
    study_from_complete_request,
)
from .validation import (
    MaterialValidationError,
    ValidatedText,
    parse_material_output,
    validate_complete_markdown,
    validate_markdown,
    validate_provider_response,
    validate_study_markdown,
)

MAX_MODEL_REQUEST_CHARACTERS = 1_500_000
MAX_JOB_PROVIDER_ATTEMPTS = 24
MAX_JOB_OUTPUT_TOKEN_CEILING = MAX_JOB_PROVIDER_ATTEMPTS * 65_536


class MaterialGenerationConflict(RuntimeError):
    """A job identity, CAS checkpoint, or canonical pin changed."""


class MaterialGenerationStale(RuntimeError):
    """The pinned source cannot be safely published anymore."""


class CheckpointStore(Protocol):
    def create(self, key: str, payload: bytes) -> bool: ...
    def compare_and_set(self, key: str, expected: bytes, replacement: bytes) -> bool: ...
    def load(self, key: str) -> bytes: ...


Preflight = Callable[[PinnedTranscriptInput, ExecutionContext, str], None]


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class MaterialGenerationService:
    """Own one durable paired-generation state machine.

    Provider calls are made only after a checkpoint claim and an injected
    source/consent preflight. Successful provider output is content-addressed
    before the checkpoint advances, so a restart observes the receipt and does
    not call the provider again.
    """

    def __init__(
        self,
        *,
        model: ModelPort,
        blobs: BlobStore,
        store: CheckpointStore,
        preflight: Preflight,
        verified_batch: VerifiedGeneratedBatchPort,
        artifact_command: ArtifactCommandPort,
        events: EventStore,
        clock: ClockPort | None = None,
    ) -> None:
        self._model = model
        self._blobs = blobs
        self._store = store
        if not callable(preflight):
            raise TypeError("canonical material preflight is required")
        self._preflight = preflight
        self._clock = clock or _SystemClock()
        self._verified_batch = verified_batch
        self._artifact_command = artifact_command
        self._events = events

    def request_pair(
        self,
        course_id: CourseId,
        session_id: SessionId,
        exact_source_pin: PinnedTranscriptInput,
        request_id: str,
    ) -> MaterialGenerationView:
        if exact_source_pin.course_id != course_id or exact_source_pin.session_id != session_id:
            raise MaterialGenerationConflict("source pin belongs to another course or session")
        request = MaterialGenerationRequest(exact_source_pin, request_id)
        self._assert_pin_readable(request.pin)
        run_id = RunId(request.job_id)
        state = MaterialGenerationState(request=request, run_id=run_id)
        encoded = state.to_bytes()
        if not self._store.create(request.job_id, encoded):
            existing = self._load_state(request.job_id)
            if existing.request.fingerprint != request.fingerprint:
                raise MaterialGenerationConflict(
                    "request_id was reused with a different pin or pipeline"
                )
            return MaterialGenerationView.from_state(existing)
        return MaterialGenerationView.from_state(state)

    async def reconcile(
        self,
        job_id: str,
        bounded_budget: int = 8,
        context: ExecutionContext | None = None,
    ) -> MaterialGenerationView:
        if not isinstance(job_id, str) or not job_id.strip():
            raise ValueError("job_id must be non-empty text")
        if type(bounded_budget) is not int or not 1 <= bounded_budget <= 64:
            raise ValueError("bounded_budget must be between 1 and 64")
        state, raw = self._load_pair(job_id)
        service_context = self._context(state, context)
        provider_calls = 0
        while provider_calls < bounded_budget:
            if state.stage in (
                MaterialGenerationStage.PROPOSED,
                MaterialGenerationStage.STALE,
                MaterialGenerationStage.FAILED_TERMINAL,
            ):
                break
            if state.stage is MaterialGenerationStage.RETRYABLE:
                state, raw = self._transition(
                    state,
                    raw,
                    state.retry_stage or MaterialGenerationStage.QUEUED,
                )
            if state.stage is MaterialGenerationStage.QUEUED:
                try:
                    state, raw = self._prepare_units(state, raw)
                except MaterialGenerationStale as error:
                    state, raw = self._stale(raw, state, error)
                    break
                except ValueError as error:
                    state, raw = self._terminal(raw, state, error)
                    break
                continue
            if state.stage is MaterialGenerationStage.BOUNDARIES:
                before_raw = raw
                state, raw = await self._run_boundaries(state, raw, service_context)
                if raw == before_raw:
                    break
                provider_calls += 1
                continue
            if state.stage is MaterialGenerationStage.COMPLETE_SEGMENT:
                if len(state.segments) < len(self._read_boundaries(state).segments):
                    before_raw = raw
                    state, raw = await self._segment(
                        state, raw, service_context, len(state.segments)
                    )
                    if raw == before_raw:
                        break
                    provider_calls += 1
                    continue
                state, raw = self._transition(state, raw, MaterialGenerationStage.COMPLETE_MERGE)
                continue
            if state.stage is MaterialGenerationStage.COMPLETE_MERGE:
                before_raw = raw
                state, raw = await self._merge(state, raw, service_context)
                if raw == before_raw:
                    break
                provider_calls += 1
                continue
            if state.stage is MaterialGenerationStage.STUDY:
                before_raw = raw
                state, raw = await self._study(state, raw, service_context)
                if raw == before_raw:
                    break
                provider_calls += 1
                continue
            if state.stage is MaterialGenerationStage.PROPOSAL:
                state, raw = self._proposal(state, raw, service_context)
                continue
            raise MaterialGenerationConflict(
                f"unsupported material generation stage {state.stage.value}"
            )
        return MaterialGenerationView.from_state(state)

    def get(self, job_id: str, context: ExecutionContext) -> MaterialGenerationView:
        state = self._load_state(job_id)
        self._context(state, context)
        return MaterialGenerationView.from_state(state)

    async def _run_boundaries(
        self, state: MaterialGenerationState, raw: bytes, context: ExecutionContext
    ) -> tuple[MaterialGenerationState, bytes]:
        manifest = self._manifest(state)
        if state.unit_manifest is None:
            raise MaterialGenerationConflict("unit manifest checkpoint is missing")
        claimed, claimed_raw = self._claim(state, raw, MaterialGenerationStage.BOUNDARIES)
        if not claimed:
            return state, raw
        try:
            self._preflight(state.request.pin, context, MaterialGenerationStage.BOUNDARIES.value)
            model_request = boundary_request(manifest, state.request.pins)
            self._assert_model_request_bounded(claimed, model_request)
            response = await self._model.generate(model_request)
            validate_provider_response(response, pins=state.request.pins, structured=True)
            structured = response.structured_output
            if structured is None:
                raise MaterialValidationError("boundary response lacks structured JSON")
            boundaries = parse_segment_boundaries(structured, manifest)
            if len(boundaries.segments) > state.request.max_segments:
                raise MaterialValidationError(
                    "boundary count exceeds the request bound", code="oversize"
                )
            blob = self._blobs.put(boundaries.to_bytes())
            result = replace(
                claimed,
                stage=MaterialGenerationStage.COMPLETE_SEGMENT,
                boundaries=blob,
                retry_stage=None,
                receipts=(
                    *claimed.receipts,
                    self._receipt(
                        claimed,
                        MaterialGenerationStage.BOUNDARIES,
                        0,
                        (state.request.pin.normalized_blob, state.unit_manifest),
                        blob,
                        model_request,
                        response,
                        None,
                    ),
                ),
                lease_stage=None,
                lease_token=None,
                lease_until=None,
            )
            return self._commit(claimed_raw, result)
        except ModelError as error:
            return self._failure(claimed_raw, claimed, error)
        except MaterialGenerationStale as error:
            return self._stale(claimed_raw, claimed, error)
        except ProviderConsentRequiredError as error:
            return self._stale(
                claimed_raw,
                claimed,
                error,
                code=MaterialGenerationErrorCode.CONSENT_REQUIRED,
            )
        except (MaterialValidationError, ValueError) as error:
            return self._terminal(claimed_raw, claimed, error)

    async def _segment(
        self, state: MaterialGenerationState, raw: bytes, context: ExecutionContext, position: int
    ) -> tuple[MaterialGenerationState, bytes]:
        manifest = self._manifest(state)
        boundaries = self._read_boundaries(state)
        if state.unit_manifest is None or state.boundaries is None:
            raise MaterialGenerationConflict("segment ancestry checkpoints are missing")
        boundary = boundaries.segments[position]
        claimed, claimed_raw = self._claim(state, raw, MaterialGenerationStage.COMPLETE_SEGMENT)
        if not claimed:
            return state, raw
        try:
            self._preflight(
                state.request.pin, context, MaterialGenerationStage.COMPLETE_SEGMENT.value
            )
            model_request = complete_segment_request(manifest, boundary, pins=state.request.pins)
            self._assert_model_request_bounded(claimed, model_request)
            response = await self._model.generate(model_request)
            validate_provider_response(response, pins=state.request.pins, structured=True)
            markdown, limitations = parse_material_output(response, stage="complete segment")
            validated = validate_markdown(
                markdown,
                source_text=units_for_boundary(manifest, boundary),
                stage="complete segment",
                max_characters=60_000,
                limitations=limitations,
            )
            blob = self._blobs.put(validated.text.encode("utf-8"))
            result = replace(
                claimed,
                segments=(*claimed.segments, blob),
                retry_stage=None,
                receipts=(
                    *claimed.receipts,
                    self._receipt(
                        claimed,
                        MaterialGenerationStage.COMPLETE_SEGMENT,
                        position,
                        (
                            state.request.pin.normalized_blob,
                            state.unit_manifest,
                            state.boundaries,
                        ),
                        blob,
                        model_request,
                        response,
                        validated,
                    ),
                ),
                lease_stage=None,
                lease_token=None,
                lease_until=None,
            )
            return self._commit(claimed_raw, result)
        except ModelError as error:
            return self._failure(claimed_raw, claimed, error)
        except MaterialGenerationStale as error:
            return self._stale(claimed_raw, claimed, error)
        except ProviderConsentRequiredError as error:
            return self._stale(
                claimed_raw,
                claimed,
                error,
                code=MaterialGenerationErrorCode.CONSENT_REQUIRED,
            )
        except (MaterialValidationError, ValueError) as error:
            return self._terminal(claimed_raw, claimed, error)

    async def _merge(
        self, state: MaterialGenerationState, raw: bytes, context: ExecutionContext
    ) -> tuple[MaterialGenerationState, bytes]:
        claimed, claimed_raw = self._claim(state, raw, MaterialGenerationStage.COMPLETE_MERGE)
        if not claimed:
            return state, raw
        try:
            self._preflight(
                state.request.pin, context, MaterialGenerationStage.COMPLETE_MERGE.value
            )
            manifest = self._manifest(claimed)
            boundaries = self._read_boundaries(claimed)
            if claimed.unit_manifest is None or claimed.boundaries is None or not claimed.segments:
                raise MaterialGenerationConflict("merge ancestry checkpoints are incomplete")
            segments = tuple(self._blobs.get(item).decode("utf-8") for item in claimed.segments)
            model_request = complete_merge_request(
                segments, title=claimed.request.pin.title, pins=claimed.request.pins
            )
            self._assert_model_request_bounded(claimed, model_request)
            response = await self._model.generate(model_request)
            validate_provider_response(response, pins=claimed.request.pins, structured=True)
            markdown, limitations = parse_material_output(response, stage="complete merge")
            validated = validate_complete_markdown(
                markdown,
                transcript_text=self._text(claimed),
                segment_texts=segments,
                limitations=limitations,
            )
            blob = self._blobs.put(validated.text.encode("utf-8"))
            result = replace(
                claimed,
                stage=MaterialGenerationStage.STUDY,
                complete=blob,
                complete_limitations=validated.limitations,
                retry_stage=None,
                receipts=(
                    *claimed.receipts,
                    self._receipt(
                        claimed,
                        MaterialGenerationStage.COMPLETE_MERGE,
                        0,
                        (
                            claimed.request.pin.normalized_blob,
                            claimed.unit_manifest,
                            claimed.boundaries,
                            *claimed.segments,
                        ),
                        blob,
                        model_request,
                        response,
                        validated,
                    ),
                ),
                lease_stage=None,
                lease_token=None,
                lease_until=None,
            )
            del manifest, boundaries
            return self._commit(claimed_raw, result)
        except ModelError as error:
            return self._failure(claimed_raw, claimed, error)
        except MaterialGenerationStale as error:
            return self._stale(claimed_raw, claimed, error)
        except ProviderConsentRequiredError as error:
            return self._stale(
                claimed_raw,
                claimed,
                error,
                code=MaterialGenerationErrorCode.CONSENT_REQUIRED,
            )
        except (MaterialValidationError, UnicodeDecodeError, ValueError) as error:
            return self._terminal(claimed_raw, claimed, error)

    async def _study(
        self, state: MaterialGenerationState, raw: bytes, context: ExecutionContext
    ) -> tuple[MaterialGenerationState, bytes]:
        claimed, claimed_raw = self._claim(state, raw, MaterialGenerationStage.STUDY)
        if not claimed or claimed.complete is None:
            return state, raw
        try:
            self._preflight(state.request.pin, context, MaterialGenerationStage.STUDY.value)
            complete = self._blobs.get(claimed.complete).decode("utf-8")
            model_request = study_from_complete_request(
                complete, title=claimed.request.pin.title, pins=claimed.request.pins
            )
            self._assert_model_request_bounded(claimed, model_request)
            response = await self._model.generate(model_request)
            validate_provider_response(response, pins=claimed.request.pins, structured=True)
            markdown, limitations = parse_material_output(response, stage="study material")
            validated = validate_study_markdown(
                markdown, complete_text=complete, limitations=limitations
            )
            blob = self._blobs.put(validated.text.encode("utf-8"))
            result = replace(
                claimed,
                stage=MaterialGenerationStage.PROPOSAL,
                study=blob,
                study_limitations=validated.limitations,
                retry_stage=None,
                receipts=(
                    *claimed.receipts,
                    self._receipt(
                        claimed,
                        MaterialGenerationStage.STUDY,
                        0,
                        (claimed.complete,),
                        blob,
                        model_request,
                        response,
                        validated,
                    ),
                ),
                lease_stage=None,
                lease_token=None,
                lease_until=None,
            )
            return self._commit(claimed_raw, result)
        except ModelError as error:
            return self._failure(claimed_raw, claimed, error)
        except MaterialGenerationStale as error:
            return self._stale(claimed_raw, claimed, error)
        except ProviderConsentRequiredError as error:
            return self._stale(
                claimed_raw,
                claimed,
                error,
                code=MaterialGenerationErrorCode.CONSENT_REQUIRED,
            )
        except (MaterialValidationError, UnicodeDecodeError, ValueError) as error:
            return self._terminal(claimed_raw, claimed, error)

    def _proposal(
        self, state: MaterialGenerationState, raw: bytes, context: ExecutionContext
    ) -> tuple[MaterialGenerationState, bytes]:
        try:
            # Bind the artifact CAS expectation before the canonical preflight.
            # If the source/session changes while preflight is running, the
            # artifact command must reject this stale snapshot.
            stream = tuple(self._events.read(context.course_id))
            expected_sequence = stream[-1].course_sequence if stream else 0
            self._preflight(state.request.pin, context, MaterialGenerationStage.PROPOSAL.value)
            proposal_context = replace(
                context,
                session_id=state.request.pin.session_id,
                idempotency_key=state.request.request_id,
            )
            verified = self._verified_batch.recover(state.run_id, proposal_context)
            if len(verified.proposals) != 2:
                raise MaterialGenerationConflict("material proposal batch must contain two outputs")
            snapshot = self._artifact_command.record_generated(
                state.run_id, proposal_context, expected_sequence
            )
            matching = tuple(batch for batch in snapshot.batches if batch.run_id == state.run_id)
            if len(matching) != 1 or len(matching[0].revision_ids) != 2:
                raise MaterialGenerationConflict("artifact command did not commit the paired batch")
            proposal = matching[0]
            result = replace(
                state,
                stage=MaterialGenerationStage.PROPOSED,
                proposal_batch_id=str(proposal.id),
                proposal_sequence=snapshot.sequence,
                error_code=None,
                error_message=None,
                retry_stage=None,
            )
            return self._commit(raw, result)
        except MaterialGenerationStale as error:
            return self._stale(raw, state, error)
        except RetryableArtifactConflictError as error:
            return self._failure_artifact(raw, state, error)
        except ProviderConsentRequiredError as error:
            return self._stale(
                raw,
                state,
                error,
                code=MaterialGenerationErrorCode.CONSENT_REQUIRED,
            )
        except ValueError as error:
            return self._terminal(raw, state, error)

    def _prepare_units(
        self, state: MaterialGenerationState, raw: bytes
    ) -> tuple[MaterialGenerationState, bytes]:
        text = self._text(state)
        manifest = build_unit_manifest(text)
        if len(manifest.units) > state.request.max_units:
            raise MaterialValidationError("unit count exceeds the request bound", code="oversize")
        blob = self._blobs.put(manifest.to_bytes())
        result = replace(state, stage=MaterialGenerationStage.BOUNDARIES, unit_manifest=blob)
        return self._commit(raw, result)

    def _manifest(self, state: MaterialGenerationState) -> UnitManifest:
        if state.unit_manifest is None:
            raise MaterialGenerationConflict("unit manifest checkpoint is missing")
        return UnitManifest.from_bytes(self._blobs.get(state.unit_manifest))

    def _read_boundaries(self, state: MaterialGenerationState) -> SegmentBoundaries:
        if state.boundaries is None:
            raise MaterialGenerationConflict("segment boundary checkpoint is missing")
        return SegmentBoundaries.from_bytes(self._blobs.get(state.boundaries))

    def _text(self, state: MaterialGenerationState) -> str:
        try:
            data = self._blobs.get(state.request.pin.normalized_blob)
        except LookupError as error:
            raise MaterialGenerationStale("pinned transcript blob is unavailable") from error
        if sha256(data).hexdigest() != state.request.pin.normalized_blob.checksum_sha256:
            raise MaterialGenerationStale("pinned transcript blob changed")
        try:
            text = data.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise MaterialGenerationStale("pinned transcript is not UTF-8") from error
        if (
            normalize("NFC", text) != text
            or len(text) != state.request.pin.normalized_character_length
        ):
            raise MaterialGenerationStale("pinned transcript normalization or length changed")
        return text

    def _assert_pin_readable(self, pin: PinnedTranscriptInput) -> None:
        if pin.source_kind.value not in ("text", "markdown"):
            raise MaterialGenerationStale("unsupported transcript source kind")
        try:
            original = self._blobs.get(pin.blob)
        except LookupError as error:
            raise MaterialGenerationStale("pinned source blob is unavailable") from error
        if sha256(original).hexdigest() != pin.blob.checksum_sha256:
            raise MaterialGenerationStale("pinned source blob changed")
        self._text(
            MaterialGenerationState(MaterialGenerationRequest(pin, "pin-check"), RunId("pin-check"))
        )

    @staticmethod
    def _assert_model_request_bounded(
        state: MaterialGenerationState, request: ModelRequest
    ) -> None:
        if state.claim_attempt > MAX_JOB_PROVIDER_ATTEMPTS:
            raise MaterialValidationError(
                "material job exceeds its provider-attempt bound", code="oversize"
            )
        character_length = sum(len(message.content) for message in request.messages)
        if character_length > MAX_MODEL_REQUEST_CHARACTERS:
            raise MaterialValidationError(
                "material model request exceeds its input bound", code="oversize"
            )
        output_tokens = request.max_output_tokens
        if (
            type(output_tokens) is not int
            or output_tokens < 1
            or output_tokens * state.claim_attempt > MAX_JOB_OUTPUT_TOKEN_CEILING
        ):
            raise MaterialValidationError(
                "material job exceeds its output-token ceiling", code="oversize"
            )

    def _context(
        self, state: MaterialGenerationState, context: ExecutionContext | None
    ) -> ExecutionContext:
        if context is None:
            raise MaterialGenerationConflict("reconcile requires a trusted execution context")
        if context.course_id != state.request.pin.course_id:
            raise MaterialGenerationConflict("execution context belongs to another course")
        if context.session_id is None or context.session_id != state.request.pin.session_id:
            raise MaterialGenerationConflict("execution context belongs to another session")
        if context.principal_kind is not PrincipalKind.SERVICE:
            raise MaterialGenerationConflict("material generation requires SERVICE authority")
        return context

    def _load_state(self, job_id: str) -> MaterialGenerationState:
        return MaterialGenerationState.from_bytes(self._store.load(job_id))

    def _load_state_by_run(self, run_id: object) -> MaterialGenerationState:
        # The material run id is the deterministic job key, so the existing
        # namespaced operational store remains the sole checkpoint record.
        if hasattr(self._store, "load"):
            try:
                return MaterialGenerationState.from_bytes(self._store.load(str(run_id)))
            except (KeyError, ValueError):
                pass
        raise KeyError(run_id)

    def _load_pair(self, job_id: str) -> tuple[MaterialGenerationState, bytes]:
        raw = self._store.load(job_id)
        return MaterialGenerationState.from_bytes(raw), raw

    def _claim(
        self,
        state: MaterialGenerationState,
        raw: bytes,
        stage: MaterialGenerationStage,
    ) -> tuple[MaterialGenerationState | None, bytes]:
        now = self._clock.now()
        if (
            state.lease_stage is not None
            and state.lease_until is not None
            and state.lease_until > now
        ):
            return None, raw
        token = sha256(raw + stage.value.encode()).hexdigest()
        claimed = replace(
            state,
            lease_stage=stage,
            lease_token=token,
            lease_until=now + timedelta(minutes=5),
            generation=state.generation + 1,
            claim_attempt=state.claim_attempt + 1,
        )
        claimed_raw = claimed.to_bytes()
        if not self._store.compare_and_set(state.job_id, raw, claimed_raw):
            return None, raw
        return claimed, claimed_raw

    def _commit(
        self, expected_raw: bytes, state: MaterialGenerationState
    ) -> tuple[MaterialGenerationState, bytes]:
        committed = replace(state, generation=state.generation + 1)
        replacement = committed.to_bytes()
        if not self._store.compare_and_set(state.job_id, expected_raw, replacement):
            raise MaterialGenerationConflict("material checkpoint changed during reconciliation")
        return committed, replacement

    def _transition(
        self, state: MaterialGenerationState, raw: bytes, stage: MaterialGenerationStage
    ) -> tuple[MaterialGenerationState, bytes]:
        return self._commit(raw, replace(state, stage=stage, error_code=None, error_message=None))

    def _receipt(
        self,
        state: MaterialGenerationState,
        stage: MaterialGenerationStage,
        stage_ordinal: int,
        input_blobs: tuple[BlobRef, ...],
        output: BlobRef,
        model_request: ModelRequest,
        response: ModelResponse,
        validated: ValidatedText | None,
    ) -> StageReceipt:
        if not isinstance(output, BlobRef):
            raise TypeError("stage output must be a BlobRef")
        material_prompt = model_request.metadata.get("material_prompt")
        if not isinstance(material_prompt, str) or "@" not in material_prompt:
            raise MaterialGenerationConflict("stage request lacks a versioned material prompt")
        prompt_pin, prompt_version = material_prompt.rsplit("@", 1)
        blob = output
        inputs = tuple(input_blobs)
        if any(not isinstance(item, BlobRef) for item in inputs):
            raise TypeError("stage inputs must be BlobRefs")
        validated_limitations = validated.limitations if validated is not None else ()
        validated_limitations_fingerprint = limitations_fingerprint(validated_limitations)
        validator_fingerprint = sha256(
            canonical_json_bytes(
                {
                    "validator": state.request.pins.validator,
                    "stage": stage.value,
                    "output": blob.checksum_sha256,
                    "limitations": validated_limitations_fingerprint,
                }
            )
        ).hexdigest()
        completed_at = self._clock.now()
        fingerprint = StageReceipt.fingerprint_for(
            stage,
            stage_ordinal,
            state.claim_attempt,
            inputs,
            blob,
            prompt_pin,
            prompt_version,
            request_fingerprint(model_request),
            response.invocation.adapter_id,
            response.invocation.adapter_version,
            response.invocation.model_id,
            response.invocation.response_id,
            response.usage,
            response.finish_reason,
            bool(response.tool_calls),
            validator_fingerprint,
            validated_limitations_fingerprint,
            completed_at,
        )
        return StageReceipt(
            stage,
            stage_ordinal,
            state.claim_attempt,
            inputs,
            blob,
            prompt_pin,
            prompt_version,
            request_fingerprint(model_request),
            response.invocation.adapter_id,
            response.invocation.adapter_version,
            response.invocation.model_id,
            response.invocation.response_id,
            response.usage,
            response.finish_reason,
            bool(response.tool_calls),
            validator_fingerprint,
            validated_limitations_fingerprint,
            fingerprint,
            completed_at,
        )

    def _failure(
        self, expected_raw: bytes, state: MaterialGenerationState, error: ModelError
    ) -> tuple[MaterialGenerationState, bytes]:
        retryable = error.code in {
            ModelErrorCode.TIMEOUT,
            ModelErrorCode.UNAVAILABLE,
            ModelErrorCode.MODEL_UNAVAILABLE,
            ModelErrorCode.RATE_LIMITED,
        }
        if not retryable:
            return self._terminal(expected_raw, state, error)
        return self._commit(
            expected_raw,
            replace(
                state,
                stage=MaterialGenerationStage.RETRYABLE,
                error_code=MaterialGenerationErrorCode(error.code.value),
                error_message=str(error),
                retry_stage=state.lease_stage or state.stage,
                lease_stage=None,
                lease_token=None,
                lease_until=None,
            ),
        )

    def _failure_artifact(
        self, expected_raw: bytes, state: MaterialGenerationState, error: Exception
    ) -> tuple[MaterialGenerationState, bytes]:
        return self._commit(
            expected_raw,
            replace(
                state,
                stage=MaterialGenerationStage.RETRYABLE,
                error_code=MaterialGenerationErrorCode.CONFLICT,
                error_message=str(error)[:1_000] or "artifact stream conflict",
                retry_stage=MaterialGenerationStage.PROPOSAL,
                lease_stage=None,
                lease_token=None,
                lease_until=None,
            ),
        )

    def _terminal(
        self, expected_raw: bytes, state: MaterialGenerationState, error: Exception
    ) -> tuple[MaterialGenerationState, bytes]:
        raw_code = getattr(error, "code", MaterialGenerationErrorCode.MALFORMED_OUTPUT.value)
        if raw_code == MaterialGenerationErrorCode.MALFORMED_OUTPUT.value and any(
            marker in str(error).lower() for marker in ("coverage", "gap", "overlap")
        ):
            raw_code = MaterialGenerationErrorCode.COVERAGE_GAP.value
        try:
            error_code = MaterialGenerationErrorCode(str(raw_code))
        except ValueError:
            error_code = MaterialGenerationErrorCode.MALFORMED_OUTPUT
        return self._commit(
            expected_raw,
            replace(
                state,
                stage=MaterialGenerationStage.FAILED_TERMINAL,
                error_code=error_code,
                error_message=str(error)[:1_000] or "material generation failed",
                retry_stage=None,
                lease_stage=None,
                lease_token=None,
                lease_until=None,
            ),
        )

    def _stale(
        self,
        expected_raw: bytes,
        state: MaterialGenerationState,
        error: Exception,
        *,
        code: MaterialGenerationErrorCode = MaterialGenerationErrorCode.STALE_INPUT,
    ) -> tuple[MaterialGenerationState, bytes]:
        return self._commit(
            expected_raw,
            replace(
                state,
                stage=MaterialGenerationStage.STALE,
                error_code=code,
                error_message=str(error)[:1_000] or "pinned transcript is stale",
                lease_stage=None,
                lease_token=None,
                lease_until=None,
                retry_stage=None,
            ),
        )


__all__ = [
    "CheckpointStore",
    "MaterialGenerationConflict",
    "MaterialGenerationService",
    "MaterialGenerationStale",
]
