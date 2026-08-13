"""Restart-safe status for Cardine's discardable local indexes."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from enum import StrEnum

from study_agent.adapters.sqlite import NamespacedSQLiteRunStore
from study_agent.state import canonical_json_bytes


class IndexingStatus(StrEnum):
    EMPTY = "empty"
    QUEUED = "queued"
    INDEXING = "indexing"
    READY = "ready"
    DEGRADED = "degraded"
    FAILED = "failed"


class IndexingPhase(StrEnum):
    IDLE = "idle"
    QUEUED = "queued"
    LEXICAL = "lexical"
    STRUCTURE = "structure"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class IndexingRecord:
    target_fingerprint: str
    status: IndexingStatus
    phase: IndexingPhase
    indexed_chunks: int = 0
    error_code: str | None = None

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(
            {
                "schema_version": 1,
                "target_fingerprint": self.target_fingerprint,
                "status": self.status.value,
                "phase": self.phase.value,
                "indexed_chunks": self.indexed_chunks,
                "error_code": self.error_code,
            }
        )

    @classmethod
    def from_bytes(cls, payload: bytes) -> IndexingRecord:
        try:
            value = json.loads(payload)
            if set(value) != {
                "schema_version",
                "target_fingerprint",
                "status",
                "phase",
                "indexed_chunks",
                "error_code",
            } or value["schema_version"] != 1:
                raise ValueError
            fingerprint = value["target_fingerprint"]
            chunks = value["indexed_chunks"]
            error_code = value["error_code"]
            if (
                not isinstance(fingerprint, str)
                or len(fingerprint) != 64
                or any(character not in "0123456789abcdef" for character in fingerprint)
                or type(chunks) is not int
                or chunks < 0
                or (error_code is not None and not isinstance(error_code, str))
            ):
                raise ValueError
            return cls(
                fingerprint,
                IndexingStatus(value["status"]),
                IndexingPhase(value["phase"]),
                chunks,
                error_code,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise ValueError("indexing record is invalid") from error


class IndexingCoordinator:
    """CAS one repository-wide derived-index target in the existing run store."""

    _KEY = "repository-index"

    def __init__(self, store: NamespacedSQLiteRunStore) -> None:
        self._store = store

    def get(self) -> IndexingRecord | None:
        try:
            return IndexingRecord.from_bytes(self._store.load(self._KEY))
        except KeyError:
            return None

    def queue(self, target_fingerprint: str) -> IndexingRecord:
        queued = IndexingRecord(
            target_fingerprint, IndexingStatus.QUEUED, IndexingPhase.QUEUED
        )
        while True:
            current = self.get()
            if current is None:
                if self._store.create(self._KEY, queued.to_bytes()):
                    return queued
                continue
            if current.target_fingerprint == target_fingerprint and current.status in {
                IndexingStatus.QUEUED,
                IndexingStatus.INDEXING,
                IndexingStatus.READY,
                IndexingStatus.DEGRADED,
            }:
                return current
            if self._store.compare_and_set(self._KEY, current.to_bytes(), queued.to_bytes()):
                return queued

    def transition(
        self,
        expected: IndexingRecord,
        *,
        status: IndexingStatus,
        phase: IndexingPhase,
        indexed_chunks: int | None = None,
        error_code: str | None = None,
    ) -> IndexingRecord | None:
        replacement = replace(
            expected,
            status=status,
            phase=phase,
            indexed_chunks=(expected.indexed_chunks if indexed_chunks is None else indexed_chunks),
            error_code=error_code,
        )
        if self._store.compare_and_set(self._KEY, expected.to_bytes(), replacement.to_bytes()):
            return replacement
        return None


__all__ = [
    "IndexingCoordinator",
    "IndexingPhase",
    "IndexingRecord",
    "IndexingStatus",
]
