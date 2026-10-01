"""Product entrypoints over the existing material coordinator and artifact lifecycle."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import replace
from hashlib import sha256
from typing import cast
from unicodedata import normalize

from cardine.adapters.audio.groq import GroqAudioTranscriber
from cardine.cli.repository import LocalRepository, ModelAdapterConfigurationError
from cardine.integrations.study_agent.course_policy import ProviderConsentRequiredError
from cardine.materials.generation_contracts import (
    MAX_SOURCE_BYTES,
    MAX_TRANSCRIPT_CHARACTERS,
    MaterialGenerationErrorCode,
    MaterialGenerationStage,
    MaterialGenerationState,
)
from cardine.materials.materializer import (
    GeneratedSourceMaterializationConflictError,
    GeneratedSourceMaterializationError,
    GeneratedSourceMaterializer,
)
from study_agent.adapters.sqlite.namespaced_run_store import NamespacedSQLiteRunStore
from study_agent.artifacts.content import LessonMaterialContent
from study_agent.domain import (
    ArtifactDecision,
    ArtifactRevisionId,
    BlobId,
    BlobRef,
    ContentOrigin,
    ExecutionContext,
    PrincipalKind,
    SourceId,
)
from study_agent.domain._validation import JsonObject, freeze_object
from study_agent.domain.provenance import TextExtractionProvenance
from study_agent.ingestion import TextIngestionError, TextIngestionResult
from study_agent.retrieval import SourceRevisionRecord
from study_agent.state import canonical_json_bytes

MAX_EXTRACTION_MANIFEST_BYTES = 2 * 1024 * 1024


class TranscriptTooLargeError(ValueError):
    """Input cannot enter the bounded single-lesson pipeline."""


class MaterialProduct:
    def __init__(self, repository: LocalRepository, context: ExecutionContext):
        self.repo = repository
        self.context = context
        if context.session_id is None:
            raise ValueError("an active session is required")
        self.course = context.course_id
        self.session = context.session_id
        self.registry = NamespacedSQLiteRunStore(repository.runs, "material-product")
        self.states = NamespacedSQLiteRunStore(repository.runs, "material-generation")
        self.key = f"{self.course}/{self.session}"

    def worker_error(self, job_id: str) -> None:
        errors = NamespacedSQLiteRunStore(self.repo.runs, "material-product-errors")
        payload = canonical_json_bytes(
            {"message": "Lavorazione interrotta: controlla provider e consenso, poi riprendi."}
        )
        if not errors.create(job_id, payload):
            old = errors.load(job_id)
            errors.compare_and_set(job_id, old, payload)

    def clear_worker_error(self, job_id: str) -> None:
        self.descriptor(job_id)
        store = NamespacedSQLiteRunStore(self.repo.runs, "material-product-errors")
        try:
            old = store.load(job_id)
            store.compare_and_set(job_id, old, b'{"message":null}')
        except KeyError:
            pass

    def _worker_error(self, job_id: str) -> str | None:
        try:
            value = NamespacedSQLiteRunStore(self.repo.runs, "material-product-errors").load(job_id)
            message = json.loads(value)["message"]
            return message if isinstance(message, str) else None
        except KeyError:
            return None

    def _publication_pending(self, job_id: str) -> bool:
        try:
            raw = NamespacedSQLiteRunStore(self.repo.runs, "material-publication").load(job_id)
            return json.loads(raw)["pending"] is True
        except KeyError:
            return False

    def _set_publication_pending(self, job_id: str, pending: bool) -> None:
        store = NamespacedSQLiteRunStore(self.repo.runs, "material-publication")
        payload = canonical_json_bytes({"pending": pending})
        for _ in range(8):
            try:
                old = store.load(job_id)
            except KeyError:
                if not pending or store.create(job_id, payload):
                    return
                continue
            if old == payload or store.compare_and_set(job_id, old, payload):
                return
        raise ValueError("Publication checkpoint is busy; retry.")

    def _mark_stale(self, job_id: str, state: MaterialGenerationState) -> None:
        stale = replace(
            state,
            stage=MaterialGenerationStage.STALE,
            error_code=MaterialGenerationErrorCode.STALE_INPUT,
            error_message="Pinned source was retired or superseded before publication.",
        )
        self.states.compare_and_set(job_id, state.to_bytes(), stale.to_bytes())

    def jobs(self) -> list[JsonObject]:
        try:
            raw = self.registry.load(self.key)
        except KeyError:
            return []
        return cast(list[JsonObject], json.loads(raw)["jobs"])

    def _register(self, descriptor: JsonObject) -> None:
        for _ in range(8):
            try:
                raw = self.registry.load(self.key)
            except KeyError:
                raw = b'{"jobs":[]}'
                self.registry.create(self.key, raw)
            jobs = cast(list[JsonObject], json.loads(raw)["jobs"])
            if any(item["job_id"] == descriptor["job_id"] for item in jobs):
                return
            if len(jobs) >= 256:
                raise ValueError(
                    "Questo corso ha raggiunto il limite di generazioni della sessione."
                )
            jobs.append(descriptor)
            if self.registry.compare_and_set(
                self.key, raw, canonical_json_bytes({"jobs": tuple(jobs)})
            ):
                return
        raise ValueError("Registro generazioni occupato; riprova.")

    def descriptor(self, job_id: str) -> JsonObject:
        matches = [item for item in self.jobs() if item["job_id"] == job_id]
        if len(matches) != 1:
            raise ValueError("Generazione non trovata in questa sessione.")
        return matches[0]

    def source(self, source_id: str, revision_id: str) -> SourceRevisionRecord:
        records = self.repo.for_course(self.course).content.catalog()
        record = next(
            (
                item
                for item in records
                if str(item.source.source_id) == source_id
                and str(item.source.revision_id) == revision_id
            ),
            None,
        )
        if (
            record is None
            or not record.is_current_revision
            or record.source.source_id
            in (self.repo.source_lifetime.retired_source_ids(self.course))
        ):
            raise ValueError("La fonte è stata rimossa o aggiornata; scegli la revisione corrente.")
        if record.source.content_origin is ContentOrigin.GENERATED:
            raise ValueError("Scegli una fonte originale o una trascrizione estratta.")
        return record

    def start(self, source_id: str, revision_id: str, request_id: str) -> JsonObject:
        record = self.source(source_id, revision_id)
        if record.source.conversion_provenance is not None:
            raise ValueError("Conferma i confini delle lezioni PDF prima di generare le note.")
        pin = self.repo.material_transcript_pin(
            self.course,
            self.session,
            record.source.source_id,
            record.source.revision_id,
        )
        service = self.repo.material_generation(
            pin, replace(self.context, principal_kind=PrincipalKind.SERVICE)
        )
        view = service.request_pair(self.course, self.session, pin, request_id)
        self._register(
            {
                "job_id": view.job_id,
                "source_id": source_id,
                "revision_id": revision_id,
                "title": pin.title,
                "kind": "material",
            }
        )
        return self.status(view.job_id)

    def advance(self, job_id: str) -> None:
        self.descriptor(job_id)
        state = MaterialGenerationState.from_bytes(self.states.load(job_id))
        if state.stage.value in {"proposed", "stale", "failed_terminal"}:
            return
        context = replace(self.context, principal_kind=PrincipalKind.SERVICE)
        try:
            service = self.repo.material_generation(state.request.pin, context)
        except ProviderConsentRequiredError:
            self.states.compare_and_set(
                job_id,
                state.to_bytes(),
                replace(
                    state,
                    stage=MaterialGenerationStage.STALE,
                    error_code=MaterialGenerationErrorCode.CONSENT_REQUIRED,
                    error_message="Provider consent was revoked.",
                ).to_bytes(),
            )
            return
        except ModelAdapterConfigurationError:
            raise
        except ValueError:
            self.states.compare_and_set(
                job_id,
                state.to_bytes(),
                replace(
                    state,
                    stage=MaterialGenerationStage.STALE,
                    error_code=MaterialGenerationErrorCode.STALE_INPUT,
                    error_message="Pinned source or session is no longer current.",
                ).to_bytes(),
            )
            return
        asyncio.run(service.reconcile(job_id, bounded_budget=64, context=context))

    def status(self, job_id: str, *, include_markdown: bool = True) -> JsonObject:
        descriptor = self.descriptor(job_id)
        state = MaterialGenerationState.from_bytes(self.states.load(job_id))
        snapshot = self.repo.artifacts.get(self.course)
        outputs: list[JsonObject] = []
        generated = self.repo.for_course(self.course).content.catalog()
        for batch in snapshot.batches:
            if str(batch.id) != state.proposal_batch_id:
                continue
            for revision_id in batch.revision_ids:
                revision = snapshot.revision(revision_id)
                content = revision.content.content
                if not isinstance(content, LessonMaterialContent):
                    continue
                published = next(
                    (
                        item.source
                        for item in generated
                        if item.source.generated_provenance
                        and item.source.generated_provenance.artifact_revision_id == revision_id
                    ),
                    None,
                )
                outputs.append(
                    {
                        "revision_id": str(revision_id),
                        "variant": content.variant.value,
                        "title": content.title,
                        "status": revision.status.value,
                        "markdown": self.repo.blobs.get(content.markdown_blob).decode("utf-8")
                        if include_markdown
                        else None,
                        "limitations": content.limitations,
                        "published_source_id": None
                        if published is None
                        else str(published.source_id),
                        "published_revision_id": None
                        if published is None
                        else str(published.revision_id),
                        "publication": "published"
                        if published
                        else (
                            "approved_blocked" if revision.status.value == "accepted" else "pending"
                        ),
                    }
                )
        worker_error = self._worker_error(job_id)
        stage = state.stage.value
        publication_pending = self._publication_pending(job_id) and any(
            item["status"] == "accepted" and item["publication"] != "published" for item in outputs
        )
        if publication_pending:
            stage = "publication_retryable"
        if worker_error and stage not in {"proposed", "stale", "failed_terminal"}:
            stage = "retryable"
        return {
            "schema_version": 1,
            **descriptor,
            "stage": stage,
            "segment_count": len(state.segments),
            "error_code": None if state.error_code is None else state.error_code.value,
            "error": "Salvataggio in attesa: il sistema riproverà senza cambiare le approvazioni."
            if publication_pending
            else worker_error
            if state.error_code is None
            else "La generazione richiede attenzione; riprova o scegli una fonte aggiornata.",
            "outputs": tuple(outputs),
            "high_water_sequence": snapshot.sequence,
        }

    def decide(
        self, job_id: str, revision_id: str, decision: str, expected_sequence: int, request_id: str
    ) -> JsonObject:
        view = self.status(job_id)
        outputs = cast(tuple[JsonObject, ...], view["outputs"])
        if not any(item["revision_id"] == revision_id for item in outputs):
            raise ValueError("La proposta non appartiene a questa generazione.")
        if decision not in {"accept", "reject"}:
            raise ValueError("Decisione non valida.")
        self.repo.artifact_service.record_human_decision(
            ArtifactRevisionId(revision_id),
            ArtifactDecision.ACCEPT if decision == "accept" else ArtifactDecision.REJECT,
            None,
            replace(self.context, idempotency_key=request_id),
            expected_sequence,
        )
        self.publish(job_id)
        return self.status(job_id)

    def publish(self, job_id: str) -> None:
        view = self.status(job_id)
        state = MaterialGenerationState.from_bytes(self.states.load(job_id))
        materializer = GeneratedSourceMaterializer(
            blobs=self.repo.blobs,
            events=self.repo.events,
            clock=self.repo.clock,
            load_projection=self.repo.events.projection,
        )
        pending = False
        source_stale = False
        for item in sorted(
            cast(tuple[JsonObject, ...], view["outputs"]),
            key=lambda output: output["variant"] != "complete",
        ):
            if item["status"] != "accepted" or item["publication"] == "published":
                continue
            for attempt in range(4):
                # Bind parent/root validation to the same canonical sequence
                # used for admission. Every retry repeats that validation.
                sequence = self.repo.events.projection(self.course).sequence
                try:
                    self.repo.material_transcript_pin(
                        self.course,
                        self.session,
                        state.request.pin.source_id,
                        state.request.pin.revision_id,
                    )
                except ValueError:
                    self._mark_stale(job_id, state)
                    source_stale = True
                    break
                try:
                    materializer.materialize(
                        artifact_revision_id=ArtifactRevisionId(str(item["revision_id"])),
                        context=replace(self.context, principal_kind=PrincipalKind.SERVICE),
                        expected_sequence=sequence,
                    )
                    break
                except GeneratedSourceMaterializationConflictError:
                    if attempt == 3:
                        pending = True
                except GeneratedSourceMaterializationError:
                    # A missing accepted dependency or stale input is blocked,
                    # not permission to publish or invent acceptance.
                    break
        self._set_publication_pending(job_id, pending)
        if not source_stale and state.stage is not MaterialGenerationStage.STALE and any(
            item["status"] == "accepted" for item in cast(tuple[JsonObject, ...], view["outputs"])
        ):
            self.repo.queue_indexing()

    def lessons(self, source_id: str, revision_id: str) -> JsonObject:
        record = self.source(source_id, revision_id)
        provenance = record.source.conversion_provenance
        if provenance is None or not provenance.page_spans:
            return {
                "source_id": source_id,
                "revision_id": revision_id,
                "lessons": (),
                "page_count": None,
            }
        starts: list[tuple[int, str]] = []
        for span in provenance.page_spans:
            text = record.text[span.start_offset : span.end_offset]
            heading = re.search(r"(?im)^\s*#{0,6}\s*(?:lezione|lecture)\s+\d+[^\n]*", text)
            if heading:
                starts.append((span.page, heading.group(0).lstrip("# ").strip()[:240]))
        if not starts or starts[0][0] != 1:
            starts.insert(0, (1, record.source.title))
        count = len(provenance.page_spans)
        lessons = tuple(
            {
                "title": title,
                "start_page": page,
                "end_page": starts[index + 1][0] - 1 if index + 1 < len(starts) else count,
            }
            for index, (page, title) in enumerate(starts)
        )
        return {
            "source_id": source_id,
            "revision_id": revision_id,
            "lessons": cast(tuple[JsonObject, ...], lessons),
            "page_count": count,
        }

    def start_lessons(
        self, source_id: str, revision_id: str, lessons: list[dict[str, object]], request_id: str
    ) -> tuple[JsonObject, ...]:
        record = self.source(source_id, revision_id)
        conversion = record.source.conversion_provenance
        if conversion is None or not conversion.page_spans or not 1 <= len(lessons) <= 64:
            raise ValueError("Il PDF deve avere una mappa di pagine verificata.")
        previous = 0
        for lesson in lessons:
            if set(lesson) != {"title", "start_page", "end_page"}:
                raise ValueError("Confini lezione non validi.")
            start, end, title = lesson["start_page"], lesson["end_page"], lesson["title"]
            if (
                type(start) is not int
                or type(end) is not int
                or start != previous + 1
                or not start <= end <= len(conversion.page_spans)
                or not isinstance(title, str)
                or not title.strip()
                or len(title) > 240
            ):
                raise ValueError(
                    "Le lezioni devono coprire tutte le pagine in ordine, "
                    "senza vuoti o sovrapposizioni."
                )
            previous = end
        if previous != len(conversion.page_spans):
            raise ValueError("Mancano pagine nella divisione per lezioni.")
        for lesson in lessons:
            start = int(cast(int, lesson["start_page"]))
            end = int(cast(int, lesson["end_page"]))
            first, last = conversion.page_spans[start - 1], conversion.page_spans[end - 1]
            slice_text = record.text[first.start_offset : last.end_offset]
            normalized = normalize("NFC", slice_text.replace("\r\n", "\n").replace("\r", "\n"))
            self._validate_transcript_size(normalized)
        # Resolve consent before admitting any selected lesson.
        if not (consent := self.repo.provider_consent.get(self.course)) or not consent.granted:
            raise ProviderConsentRequiredError("provider consent is required")
        results: list[JsonObject] = []
        for index, lesson in enumerate(lessons):
            start, end = int(cast(int, lesson["start_page"])), int(cast(int, lesson["end_page"]))
            first, last = conversion.page_spans[start - 1], conversion.page_spans[end - 1]
            text = record.text[first.start_offset : last.end_offset]
            manifest: JsonObject = {
                "parent_source_id": source_id,
                "parent_revision_id": revision_id,
                "start_page": start,
                "end_page": end,
                "start_offset": first.start_offset,
                "end_offset": last.end_offset,
                "title": str(lesson["title"]),
            }
            admitted = self.admit_extraction(
                original=self.repo.blobs.get(record.source.blob),
                text=text,
                title=str(lesson["title"]),
                manifest=manifest,
                adapter="pdf-lesson-extraction@1",
                media_type="application/pdf",
                limitations=(
                    "Estratto della sbobina; pagine e confini sono stati confermati dall'utente.",
                ),
            )
            results.append(
                self.start(
                    str(admitted.source.source_id),
                    str(admitted.source.revision_id),
                    request_id + f"-lesson-{index}",
                )
            )
        return tuple(results)

    def admit_extraction(
        self,
        *,
        original: bytes,
        text: str,
        title: str,
        manifest: JsonObject,
        adapter: str,
        media_type: str,
        limitations: tuple[str, ...],
    ) -> TextIngestionResult:
        normalized = normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
        self._validate_transcript_size(normalized)
        manifest_blob = self.repo.blobs.put(canonical_json_bytes(manifest))
        provenance = TextExtractionProvenance(
            sha256(original).hexdigest(),
            sha256(normalized.encode()).hexdigest(),
            manifest_blob.checksum_sha256,
            adapter,
            media_type,
            limitations,
            manifest_blob.byte_length,
        )
        identity = sha256(
            canonical_json_bytes({"provenance": provenance.to_json(), "title": title})
        ).hexdigest()
        return self.repo.for_course(self.course).ingestion.ingest(
            filename="transcript.md",
            content=normalized.encode(),
            original_content=original,
            source_id=SourceId("extracted-sha256:" + identity),
            title=title,
            source_role="lesson",
            trust_level=0,
            content_origin=ContentOrigin.EXTRACTED,
            extraction_provenance=provenance,
            context=self.context,
        )

    @staticmethod
    def _validate_transcript_size(text: str) -> None:
        if len(text) > MAX_TRANSCRIPT_CHARACTERS or len(text.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise TranscriptTooLargeError(
                "Trascrizione troppo lunga per una lezione: carica registrazioni più brevi."
            )

    def start_audio(self, data: bytes, filename: str, title: str, request_id: str) -> JsonObject:
        from cardine.adapters.audio.groq import AUDIO_EXTENSIONS, MAX_AUDIO_BYTES

        extension = filename.rsplit(".", 1)[-1].lower()
        if extension not in AUDIO_EXTENSIONS or not 0 < len(data) <= MAX_AUDIO_BYTES:
            raise ValueError("Formato o dimensione audio non supportati.")
        self._audio_preflight()
        blob = self.repo.blobs.put(data)
        job_id = (
            "audio-"
            + sha256(
                canonical_json_bytes(
                    {
                        "course": str(self.course),
                        "session": str(self.session),
                        "request": request_id,
                    }
                )
            ).hexdigest()
        )
        store = NamespacedSQLiteRunStore(self.repo.runs, "audio-generation")
        state: JsonObject = {
            "blob": {"sha256": blob.checksum_sha256, "bytes": blob.byte_length},
            "extension": extension,
            "title": title,
            "chunks": (),
            "stage": "queued",
            "material_job_id": None,
            "error": None,
            "lease_until": 0,
            "lease_token": None,
        }
        if not store.create(job_id, canonical_json_bytes(state)):
            existing = json.loads(store.load(job_id))
            if existing["blob"] != state["blob"] or existing["title"] != title:
                raise ValueError("Identità di richiesta audio già utilizzata per un altro file.")
        self._register({"job_id": job_id, "title": title, "kind": "audio"})
        return self.audio_status(job_id)

    def _audio_preflight(self) -> None:
        from study_agent.domain import SessionStatus

        if (
            self.repo.sessions.get_session(self.course, self.session).status
            is not SessionStatus.ACTIVE
        ):
            raise ValueError("La sessione non è attiva.")
        if not (consent := self.repo.provider_consent.get(self.course)) or not consent.granted:
            raise ProviderConsentRequiredError("provider consent is required")

    def audio_status(self, job_id: str, *, include_markdown: bool = True) -> JsonObject:
        descriptor = self.descriptor(job_id)
        store = NamespacedSQLiteRunStore(self.repo.runs, "audio-generation")
        state = json.loads(store.load(job_id))
        material_id = state["material_job_id"]
        if material_id is not None:
            material = self.status(material_id, include_markdown=include_markdown)
            worker_error = self._worker_error(job_id)
            error = worker_error or material.get("error")
            return {
                **material,
                **descriptor,
                "material_job_id": material_id,
                "error": error,
                "stage": "retryable"
                if worker_error
                and material["stage"] not in {"proposed", "stale", "failed_terminal"}
                else material["stage"],
                "transcribed_chunks": len(state["chunks"]),
            }
        return {
            "schema_version": 1,
            **descriptor,
            "stage": state["stage"],
            "transcribed_chunks": len(state["chunks"]),
            "outputs": (),
            "error": state["error"] or self._worker_error(job_id),
            "high_water_sequence": self.repo.events.projection(self.course).sequence,
        }

    def advance_audio(self, job_id: str, transcriber: GroqAudioTranscriber) -> None:
        from time import time
        from uuid import uuid4

        self.descriptor(job_id)
        store = NamespacedSQLiteRunStore(self.repo.runs, "audio-generation")
        raw = store.load(job_id)
        state = json.loads(raw)
        if state["stage"] == "failed_terminal":
            return
        if state["material_job_id"] is not None:
            self.clear_worker_error(state["material_job_id"])
            self.advance(state["material_job_id"])
            return
        if state["lease_until"] > time():
            return
        token = uuid4().hex
        state.update(stage="transcribing", error=None, lease_token=token, lease_until=time() + 600)
        claimed = canonical_json_bytes(freeze_object(state))
        if not store.compare_and_set(job_id, raw, claimed):
            return
        raw = claimed

        def save(chunks: list[JsonObject]) -> None:
            nonlocal raw
            # Checkpoints contain only content-addressed transcript references.
            refs = []
            for chunk in chunks:
                ref = self.repo.blobs.put(canonical_json_bytes(chunk))
                refs.append({"sha256": ref.checksum_sha256, "bytes": ref.byte_length})
            state.update(chunks=refs, lease_until=time() + 600)
            new = canonical_json_bytes(freeze_object(state))
            if not store.compare_and_set(job_id, raw, new):
                raise ValueError("La trascrizione è stata ripresa da un altro processo.")
            raw = new
            self._validate_transcript_size("\n\n".join(str(item["text"]) for item in chunks))

        try:
            self._audio_preflight()
            blob = state["blob"]
            original = self.repo.blobs.get(
                BlobRef(BlobId("sha256:" + blob["sha256"]), blob["sha256"], blob["bytes"])
            )
            recovered = [
                cast(
                    JsonObject,
                    json.loads(
                        self.repo.blobs.get(
                            BlobRef(BlobId("sha256:" + ref["sha256"]), ref["sha256"], ref["bytes"])
                        )
                    ),
                )
                for ref in state["chunks"]
            ]
            text, manifest = transcriber.transcribe(
                original, state["extension"], recovered, save, self._audio_preflight
            )
            self._audio_preflight()
            manifest_bytes = canonical_json_bytes(manifest)
            if len(manifest_bytes) > MAX_EXTRACTION_MANIFEST_BYTES:
                manifest_blob = self.repo.blobs.put(manifest_bytes)
                state.update(
                    stage="failed_terminal",
                    error=(
                        "Manifesto della trascrizione troppo grande; "
                        "carica una registrazione più breve."
                    ),
                    manifest={
                        "sha256": manifest_blob.checksum_sha256,
                        "bytes": manifest_blob.byte_length,
                    },
                    lease_until=0,
                    lease_token=None,
                )
                store.compare_and_set(job_id, raw, canonical_json_bytes(freeze_object(state)))
                return
            admitted = self.admit_extraction(
                original=original,
                text=text,
                title=state["title"],
                manifest=manifest,
                adapter="groq-whisper-large-v3-turbo@1",
                media_type="audio/" + state["extension"],
                limitations=(
                    "Trascrizione automatica: verificare termini tecnici "
                    "e passaggi incerti rispetto all'audio.",
                ),
            )
            material = self.start(
                str(admitted.source.source_id), str(admitted.source.revision_id), job_id
            )
            state.update(
                stage="transcribed",
                material_job_id=material["job_id"],
                lease_until=0,
                lease_token=None,
            )
            new = canonical_json_bytes(freeze_object(state))
            if not store.compare_and_set(job_id, raw, new):
                raise ValueError("Checkpoint audio aggiornato da un altro processo.")
            self.advance(str(material["job_id"]))
        except TextIngestionError as error:
            state.update(
                stage="transcribing" if error.retryable else "failed_terminal",
                error="Salvataggio in attesa: nuovo tentativo automatico."
                if error.retryable
                else "Impossibile registrare la trascrizione.",
                lease_until=0,
                lease_token=None,
            )
            # Retryable admission races keep the dispatched worker loop alive.
            # Completed transcript chunks remain durable and are reused.
            store.compare_and_set(job_id, raw, canonical_json_bytes(freeze_object(state)))
        except TranscriptTooLargeError as error:
            state.update(
                stage="failed_terminal",
                error=str(error),
                lease_until=0,
                lease_token=None,
            )
            store.compare_and_set(job_id, raw, canonical_json_bytes(freeze_object(state)))
        except (ValueError, OSError, RuntimeError):
            state.update(
                stage="retryable",
                error="Trascrizione interrotta: controlla configurazione, consenso e file; "
                "puoi riprendere.",
                lease_until=0,
                lease_token=None,
            )
            store.compare_and_set(job_id, raw, canonical_json_bytes(freeze_object(state)))
            raise
