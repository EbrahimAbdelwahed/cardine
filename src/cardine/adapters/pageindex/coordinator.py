"""Restart-safe bounded lifecycle for derived PageIndex projections."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Iterable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from itertools import islice
from typing import TYPE_CHECKING, cast

from cardine.knowledge.pageindex_projection import (
    CanonicalSpanCandidate,
    PageIndexProjection,
    PageIndexStatus,
    candidates_from_document_index,
)
from study_agent.adapters.sqlite import NamespacedSQLiteRunStore, SQLiteRunStore
from study_agent.domain._validation import JsonObject
from study_agent.domain.document_index import DocumentIndex
from study_agent.domain.identifiers import RevisionId, SourceId, SubstrateId
from study_agent.ports.document_index import DocumentIndexRequest

from .worker import QUALIFIED_UPSTREAM_COMMIT, PageIndexWorker, PageIndexWorkerError

if TYPE_CHECKING:
    from cardine.adapters.document_index.pageindex import PageIndexDocumentIndexAdapter

_NAMESPACE = "cardine-pageindex"
_SCHEMA = 2
_MAX_ATTEMPTS = 3
_MAX_RECONCILE = 32
_UNSET = object()


@dataclass(frozen=True, slots=True)
class PageIndexRevision:
    course_id: str
    source_id: str
    revision_id: str
    content: str
    content_sha256: str
    document_index_request: DocumentIndexRequest | None = None

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value.strip()
            for value in (
                self.course_id,
                self.source_id,
                self.revision_id,
                self.content,
                self.content_sha256,
            )
        ):
            raise ValueError("PageIndex revision fields must be non-empty text")
        if len(self.content.encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError("PageIndex revision exceeds the content bound")
        import hashlib

        if hashlib.sha256(self.content.encode()).hexdigest() != self.content_sha256:
            raise ValueError("content_sha256 does not match content")
        request = self.document_index_request
        if request is not None:
            if not isinstance(request, DocumentIndexRequest):
                raise ValueError("document_index_request must be a DocumentIndexRequest")
            request.__post_init__()
            if (
                str(request.source_id) != self.source_id
                or str(request.revision_id) != self.revision_id
                or request.normalized_text != self.content
            ):
                raise ValueError("index request does not bind the canonical revision")


class PageIndexCoordinator:
    """Own only derived PageIndex state; canonical source bytes remain external."""

    def __init__(
        self,
        runs: SQLiteRunStore,
        *,
        worker: PageIndexWorker | None = None,
        adapter: PageIndexDocumentIndexAdapter | None = None,
        max_attempts: int = _MAX_ATTEMPTS,
        lease_seconds: float = 30.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(runs, SQLiteRunStore):
            raise TypeError("PageIndex coordinator requires the repository run store")
        self._runs = NamespacedSQLiteRunStore(runs, _NAMESPACE)
        # Deferred: the adapter also imports the qualified worker package.
        from cardine.adapters.document_index.pageindex import PageIndexDocumentIndexAdapter

        if worker is not None and adapter is not None:
            raise ValueError("configure the PageIndex worker or adapter, not both")
        self._adapter = adapter or PageIndexDocumentIndexAdapter(worker=worker)
        if type(max_attempts) is not int or not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be between one and three")
        if lease_seconds <= 0 or lease_seconds > 300:
            raise ValueError("lease_seconds is outside the bound")
        self._max_attempts = max_attempts
        self._lease_seconds = lease_seconds
        self._clock = clock

    def request(self, revision: PageIndexRevision) -> PageIndexProjection:
        queued = PageIndexProjection(
            revision.course_id,
            revision.source_id,
            revision.revision_id,
            revision.content_sha256,
            PageIndexStatus.QUEUED,
            0,
            cache_fingerprint=self._cache_fingerprint(revision),
        )
        payload = _encode(queued)
        key = _key(revision)
        if self._runs.create(key, payload):
            return queued
        existing = self.load(revision)
        if existing.content_sha256 != revision.content_sha256:
            raise ValueError("PageIndex revision identity is stale")
        return existing

    enqueue = request

    def load(self, revision: PageIndexRevision) -> PageIndexProjection:
        current = self._load(revision)
        # Historical schema-1 structure is migration data only. Rebuild it
        # through the single DocumentIndex producer; never trust its text map.
        stale = current.cache_fingerprint != self._cache_fingerprint(revision)
        missing_index = current.status in {PageIndexStatus.READY, PageIndexStatus.DEGRADED} and (
            current.document_index is None
        )
        if current.status is not PageIndexStatus.DISABLED and (stale or missing_index):
            if current.status is PageIndexStatus.INDEXING and not _lease_expired(
                current, self._clock()
            ):
                return current
            queued = _replace(
                current,
                status=PageIndexStatus.QUEUED,
                attempt=0,
                candidates=(),
                document_index=None,
                cache_fingerprint=self._cache_fingerprint(revision),
                error_code=None,
                lease_until=None,
            )
            if not self._cas(revision, current, queued):
                return self._load(revision)
            current = queued
        return current

    status = load

    def process(self, revision: PageIndexRevision) -> PageIndexProjection:
        current = self.request(revision)
        if current.status in {
            PageIndexStatus.READY,
            PageIndexStatus.DEGRADED,
            PageIndexStatus.DISABLED,
        }:
            return current
        if current.status is PageIndexStatus.INDEXING and not _lease_expired(
            current, self._clock()
        ):
            return current
        if current.attempt >= self._max_attempts:
            failed = _replace(
                current,
                status=PageIndexStatus.FAILED,
                error_code="pageindex_retry_exhausted",
                lease_until=None,
            )
            self._cas(revision, current, failed)
            return self._load(revision)
        indexing = _replace(
            current,
            status=PageIndexStatus.INDEXING,
            attempt=current.attempt + 1,
            error_code=None,
            lease_until=self._clock() + self._lease_seconds,
        )
        if not self._cas(revision, current, indexing):
            return self._load(revision)
        try:
            request = _document_request(revision)
            index = self._adapter.build_sync(request)
            if (
                index.producer_id != "pageindex-qualified-structural"
                or index.producer_version != QUALIFIED_UPSTREAM_COMMIT
                or index.config_fingerprint != self._adapter.configuration_fingerprint(request)
            ):
                raise ValueError("PageIndex index producer/configuration mismatch")
            candidates = candidates_from_document_index(
                course_id=revision.course_id,
                source_id=revision.source_id,
                revision_id=revision.revision_id,
                content=revision.content,
                index=index,
            )
            final = _replace(
                indexing,
                status=PageIndexStatus.READY if candidates else PageIndexStatus.DEGRADED,
                candidates=candidates,
                document_index=index,
                error_code=None if candidates else "pageindex_no_mappable_nodes",
                lease_until=None,
            )
        except PageIndexWorkerError as error:
            final = _replace(
                indexing,
                status=(
                    PageIndexStatus.FAILED
                    if indexing.attempt >= self._max_attempts
                    else PageIndexStatus.QUEUED
                ),
                error_code=error.code,
                lease_until=None,
            )
        except Exception as error:
            from cardine.adapters.document_index.pageindex import PageIndexProviderError

            final = _replace(
                indexing,
                status=(
                    PageIndexStatus.FAILED
                    if indexing.attempt >= self._max_attempts
                    else PageIndexStatus.QUEUED
                ),
                error_code=error.code
                if isinstance(error, PageIndexProviderError)
                else "pageindex_worker_protocol",
                lease_until=None,
            )
        self._cas(revision, indexing, final)
        return self._load(revision)

    async def process_async(self, revision: PageIndexRevision) -> PageIndexProjection:
        """Join bounded synchronous worker cleanup before propagating cancellation."""
        task = asyncio.create_task(asyncio.to_thread(self.process, revision))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    # A second cancellation cannot detach the bounded worker.
                    continue
                except Exception:
                    break
            with suppress(Exception, asyncio.CancelledError):
                task.result()
            raise

    run_once = process

    def reconcile(
        self,
        active_revisions: Iterable[PageIndexRevision],
        *,
        budget: int = _MAX_RECONCILE,
    ) -> tuple[PageIndexProjection, ...]:
        if type(budget) is not int or not 0 <= budget <= _MAX_RECONCILE:
            raise ValueError("reconcile budget is outside the bound")
        result: list[PageIndexProjection] = []
        for revision in islice(active_revisions, budget):
            result.append(self.process(revision))
        return tuple(result)

    def rebuild(self, revision: PageIndexRevision) -> PageIndexProjection:
        current = self.request(revision)
        if current.status is PageIndexStatus.DISABLED:
            raise ValueError("disabled PageIndex projection must be enabled first")
        queued = _replace(
            current,
            status=PageIndexStatus.QUEUED,
            attempt=0,
            candidates=(),
            document_index=None,
            cache_fingerprint=self._cache_fingerprint(revision),
            error_code=None,
            lease_until=None,
        )
        self._cas(revision, current, queued)
        return self._load(revision)

    def disable(
        self,
        revision: PageIndexRevision,
        *,
        error_code: str = "pageindex_disabled",
    ) -> PageIndexProjection:
        current = self.request(revision)
        disabled = _replace(
            current,
            status=PageIndexStatus.DISABLED,
            candidates=(),
            document_index=None,
            cache_fingerprint=self._cache_fingerprint(revision),
            error_code=error_code,
            lease_until=None,
        )
        self._cas(revision, current, disabled)
        return self._load(revision)

    def enable(self, revision: PageIndexRevision) -> PageIndexProjection:
        current = self.request(revision)
        if current.status is not PageIndexStatus.DISABLED:
            return current
        queued = _replace(
            current,
            status=PageIndexStatus.QUEUED,
            attempt=0,
            cache_fingerprint=self._cache_fingerprint(revision),
            document_index=None,
            candidates=(),
            error_code=None,
            lease_until=None,
        )
        self._cas(revision, current, queued)
        return self._load(revision)

    def _cache_fingerprint(self, revision: PageIndexRevision) -> str:
        request = _document_request(revision)
        payload = {
            "producer_id": "pageindex-qualified-structural",
            "producer_version": QUALIFIED_UPSTREAM_COMMIT,
            "adapter_config": self._adapter.config_fingerprint,
            "media_type": request.media_type,
            "content_sha256": sha256(request.content).hexdigest(),
            "substrate_id": str(request.substrate_id),
            "metadata": _plain(request.metadata),
        }
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()

    def _load(self, revision: PageIndexRevision) -> PageIndexProjection:
        try:
            payload = self._runs.load(_key(revision))
        except KeyError:
            raise KeyError("PageIndex projection has not been requested") from None
        projection = _decode(payload)
        if (
            projection.content_sha256 != revision.content_sha256
            or projection.course_id != revision.course_id
            or projection.source_id != revision.source_id
            or projection.revision_id != revision.revision_id
        ):
            raise ValueError("stored PageIndex projection is stale")
        if projection.document_index is not None:
            index = projection.document_index
            if projection.cache_fingerprint == self._cache_fingerprint(revision) and (
                index.producer_id != "pageindex-qualified-structural"
                or index.producer_version != QUALIFIED_UPSTREAM_COMMIT
                or index.config_fingerprint
                != self._adapter.configuration_fingerprint(_document_request(revision))
            ):
                raise ValueError("stored document index producer/configuration mismatch")
            expected_candidates = candidates_from_document_index(
                course_id=revision.course_id,
                source_id=revision.source_id,
                revision_id=revision.revision_id,
                content=revision.content,
                index=projection.document_index,
            )
            if projection.candidates != expected_candidates:
                raise ValueError("stored navigation differs from its document index")
        return projection

    def _cas(
        self,
        revision: PageIndexRevision,
        expected: PageIndexProjection,
        replacement: PageIndexProjection,
    ) -> bool:
        # Compare the exact historical bytes rather than re-encoding schema 1
        # as schema 2. This is also the atomic migration boundary.
        key = _key(revision)
        raw = self._runs.load(key)
        if _decode(raw) != expected:
            return False
        return self._runs.compare_and_set(key, raw, _encode(replacement))


def _document_request(revision: PageIndexRevision) -> DocumentIndexRequest:
    if revision.document_index_request is not None:
        return revision.document_index_request
    return DocumentIndexRequest(
        SourceId(revision.source_id),
        RevisionId(revision.revision_id),
        SubstrateId(f"substrate:sha256:{revision.content_sha256}"),
        "text/markdown",
        revision.content.encode("utf-8"),
        revision.content,
    )


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _key(revision: PageIndexRevision) -> str:
    return "\0".join((revision.course_id, revision.source_id, revision.revision_id))


def _lease_expired(projection: PageIndexProjection, now: float) -> bool:
    return projection.lease_until is not None and projection.lease_until <= now


def _replace(
    projection: PageIndexProjection,
    *,
    status: PageIndexStatus | object = _UNSET,
    attempt: int | object = _UNSET,
    candidates: tuple[CanonicalSpanCandidate, ...] | object = _UNSET,
    error_code: str | object | None = _UNSET,
    lease_until: float | object | None = _UNSET,
    cache_fingerprint: str | object | None = _UNSET,
    document_index: DocumentIndex | object | None = _UNSET,
) -> PageIndexProjection:
    return PageIndexProjection(
        projection.course_id,
        projection.source_id,
        projection.revision_id,
        projection.content_sha256,
        projection.status if status is _UNSET else cast(PageIndexStatus, status),
        projection.attempt if attempt is _UNSET else cast(int, attempt),
        projection.candidates
        if candidates is _UNSET
        else cast(tuple[CanonicalSpanCandidate, ...], candidates),
        projection.error_code if error_code is _UNSET else cast(str | None, error_code),
        projection.lease_until if lease_until is _UNSET else cast(float | None, lease_until),
        projection.cache_fingerprint
        if cache_fingerprint is _UNSET
        else cast(str | None, cache_fingerprint),
        projection.document_index
        if document_index is _UNSET
        else cast(DocumentIndex | None, document_index),
    )


def _encode(projection: PageIndexProjection) -> bytes:
    payload = {
        "schema": _SCHEMA,
        "course_id": projection.course_id,
        "source_id": projection.source_id,
        "revision_id": projection.revision_id,
        "content_sha256": projection.content_sha256,
        "status": projection.status.value,
        "attempt": projection.attempt,
        "error_code": projection.error_code,
        "lease_until": projection.lease_until,
        "cache_fingerprint": projection.cache_fingerprint,
        "document_index": None
        if projection.document_index is None
        else _plain(projection.document_index.to_json()),
        "candidates": [
            {
                "course_id": item.course_id,
                "source_id": item.source_id,
                "revision_id": item.revision_id,
                "node_id": item.node_id,
                "title": item.title,
                "start_offset": item.start_offset,
                "end_offset": item.end_offset,
                "content_sha256": item.content_sha256,
            }
            for item in projection.candidates
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _decode(payload: bytes) -> PageIndexProjection:
    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        raw = json.loads(
            payload,
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")),
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("PageIndex projection payload is invalid JSON") from None
    if not isinstance(raw, dict):
        raise ValueError("PageIndex projection payload schema is invalid")
    schema = raw.get("schema")
    expected_fields = {
        "schema",
        "course_id",
        "source_id",
        "revision_id",
        "content_sha256",
        "status",
        "attempt",
        "error_code",
        "lease_until",
        "candidates",
    }
    if schema == _SCHEMA:
        expected_fields |= {"cache_fingerprint", "document_index"}
    if type(schema) is not int or schema not in {1, _SCHEMA} or set(raw) != expected_fields:
        raise ValueError("PageIndex projection payload schema is invalid")
    candidates = raw["candidates"]
    if not isinstance(candidates, list):
        raise ValueError("PageIndex candidates payload is invalid")
    try:
        values = tuple(
            CanonicalSpanCandidate(
                item["course_id"],
                item["source_id"],
                item["revision_id"],
                item["node_id"],
                item["title"],
                item["start_offset"],
                item["end_offset"],
                item["content_sha256"],
            )
            for item in candidates
            if isinstance(item, dict)
            and set(item)
            == {
                "course_id",
                "source_id",
                "revision_id",
                "node_id",
                "title",
                "start_offset",
                "end_offset",
                "content_sha256",
            }
        )
        if len(values) != len(candidates):
            raise ValueError("candidate schema")
        projection = PageIndexProjection(
            raw["course_id"],
            raw["source_id"],
            raw["revision_id"],
            raw["content_sha256"],
            PageIndexStatus(raw["status"]),
            raw["attempt"],
            values,
            raw["error_code"],
            raw["lease_until"],
            raw.get("cache_fingerprint"),
            None
            if raw.get("document_index") is None
            else DocumentIndex.from_json(cast(JsonObject, raw["document_index"])),
        )
        canonical = (
            _encode(projection)
            if schema == _SCHEMA
            else json.dumps(raw, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        )
        if canonical != payload:
            raise ValueError("projection payload is not canonical")
        return projection
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("PageIndex projection payload is invalid") from error


__all__ = ["PageIndexCoordinator", "PageIndexRevision"]
