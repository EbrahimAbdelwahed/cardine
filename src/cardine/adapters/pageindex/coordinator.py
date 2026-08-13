"""Restart-safe bounded lifecycle for derived PageIndex projections."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from itertools import islice
from typing import cast

from cardine.knowledge.pageindex_projection import (
    CanonicalSpanCandidate,
    PageIndexProjection,
    PageIndexStatus,
    map_structural_tree,
)
from study_agent.adapters.sqlite import NamespacedSQLiteRunStore, SQLiteRunStore

from .worker import PageIndexWorker, PageIndexWorkerError

_NAMESPACE = "cardine-pageindex"
_SCHEMA = 1
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

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value.strip() for value in (
            self.course_id,
            self.source_id,
            self.revision_id,
            self.content,
            self.content_sha256,
        )):
            raise ValueError("PageIndex revision fields must be non-empty text")
        if len(self.content.encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError("PageIndex revision exceeds the content bound")
        import hashlib

        if hashlib.sha256(self.content.encode()).hexdigest() != self.content_sha256:
            raise ValueError("content_sha256 does not match content")


class PageIndexCoordinator:
    """Own only derived PageIndex state; canonical source bytes remain external."""

    def __init__(
        self,
        runs: SQLiteRunStore,
        *,
        worker: PageIndexWorker | None = None,
        max_attempts: int = _MAX_ATTEMPTS,
        lease_seconds: float = 30.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not isinstance(runs, SQLiteRunStore):
            raise TypeError("PageIndex coordinator requires the repository run store")
        self._runs = NamespacedSQLiteRunStore(runs, _NAMESPACE)
        self._worker = worker or PageIndexWorker()
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
        )
        payload = _encode(queued)
        key = _key(revision)
        if self._runs.create(key, payload):
            return queued
        existing = self._load(revision)
        if existing.content_sha256 != revision.content_sha256:
            raise ValueError("PageIndex revision identity is stale")
        return existing

    enqueue = request

    def load(self, revision: PageIndexRevision) -> PageIndexProjection:
        return self._load(revision)

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
            tree = self._worker.run(revision.content)
            candidates = map_structural_tree(
                course_id=revision.course_id,
                source_id=revision.source_id,
                revision_id=revision.revision_id,
                content=revision.content,
                tree=tree,
            )
            final = _replace(
                indexing,
                status=PageIndexStatus.READY if candidates else PageIndexStatus.DEGRADED,
                candidates=candidates,
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
        except Exception:
            final = _replace(
                indexing,
                status=(
                    PageIndexStatus.FAILED
                    if indexing.attempt >= self._max_attempts
                    else PageIndexStatus.QUEUED
                ),
                error_code="pageindex_worker_protocol",
                lease_until=None,
            )
        self._cas(revision, indexing, final)
        return self._load(revision)

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
            error_code=error_code,
            lease_until=None,
        )
        self._cas(revision, current, disabled)
        return self._load(revision)

    def enable(self, revision: PageIndexRevision) -> PageIndexProjection:
        current = self.request(revision)
        if current.status is not PageIndexStatus.DISABLED:
            return current
        queued = _replace(current, status=PageIndexStatus.QUEUED, error_code=None, lease_until=None)
        self._cas(revision, current, queued)
        return self._load(revision)

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
        return projection

    def _cas(
        self,
        revision: PageIndexRevision,
        expected: PageIndexProjection,
        replacement: PageIndexProjection,
    ) -> bool:
        return self._runs.compare_and_set(
            _key(revision), _encode(expected), _encode(replacement)
        )


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
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


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
    if not isinstance(raw, dict) or set(raw) != {
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
    } or raw.get("schema") != _SCHEMA:
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
        )
        if _encode(projection) != payload:
            raise ValueError("projection payload is not canonical")
        return projection
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("PageIndex projection payload is invalid") from error


__all__ = ["PageIndexCoordinator", "PageIndexRevision"]
