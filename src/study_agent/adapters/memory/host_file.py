"""Bounded in-memory operational storage for trusted host file snapshots."""

from __future__ import annotations

from hashlib import sha256
from threading import RLock

from cardine.hosts.contracts import MAX_HOST_FILES
from study_agent.domain.identifiers import CourseId, SessionId
from study_agent.ports.host_file import HostFileIdentityPort
from study_agent.ports.source_input import MAX_TOTAL_SOURCE_BYTES


class MemoryHostFileSnapshotStore:
    """An explicit bounded store with compare-before-mutate semantics."""

    def __init__(
        self,
        *,
        max_snapshot_count: int = MAX_HOST_FILES,
        max_aggregate_bytes: int = MAX_TOTAL_SOURCE_BYTES,
    ) -> None:
        if type(max_snapshot_count) is not int or not 0 < max_snapshot_count <= MAX_HOST_FILES:
            raise ValueError("max_snapshot_count must be between one and MAX_HOST_FILES")
        if (
            type(max_aggregate_bytes) is not int
            or not 0 < max_aggregate_bytes <= MAX_TOTAL_SOURCE_BYTES
        ):
            raise ValueError(
                "max_aggregate_bytes must be between one and MAX_TOTAL_SOURCE_BYTES"
            )
        self._max_snapshot_count = max_snapshot_count
        self._max_aggregate_bytes = max_aggregate_bytes
        self._payloads: dict[str, bytes] = {}
        self._total_bytes = 0
        self._lock = RLock()

    @property
    def snapshot_count(self) -> int:
        with self._lock:
            return len(self._payloads)

    @property
    def aggregate_bytes(self) -> int:
        with self._lock:
            return self._total_bytes

    def create(self, file_id: str, payload: bytes) -> bool:
        _require_file_id(file_id)
        if type(payload) is not bytes:
            raise ValueError("host snapshot payload must be bytes")
        # Decode before acquiring mutation state, then validate the key and
        # exact canonical representation.  No untrusted length field can bypass
        # the aggregate bound or poison accounting.
        from cardine.hosts.files import HostFileSnapshot

        with self._lock:
            try:
                snapshot = HostFileSnapshot.from_bytes(payload)
            except (TypeError, ValueError) as error:
                raise ValueError("host snapshot payload is invalid") from error
            if snapshot.file_id != file_id:
                raise ValueError("host snapshot file id does not match key")
            accounted_size = snapshot.byte_size
            existing = self._payloads.get(file_id)
            if existing is not None:
                if existing != payload:
                    raise ValueError("host snapshot identity already contains different bytes")
                return False
            if len(self._payloads) >= self._max_snapshot_count:
                raise ValueError("host snapshot count limit exceeded")
            if self._total_bytes + accounted_size > self._max_aggregate_bytes:
                raise ValueError("host snapshot aggregate byte limit exceeded")
            self._payloads[file_id] = payload
            self._total_bytes += accounted_size
            return True

    def load(self, file_id: str) -> bytes:
        _require_file_id(file_id)
        with self._lock:
            try:
                return self._payloads[file_id]
            except KeyError:
                raise KeyError(f"unknown host file id: {file_id}") from None


class MemoryHostFileIdentity(HostFileIdentityPort):
    """Deterministic identity issuer suitable for local/offline composition."""

    def __init__(self) -> None:
        self._bindings: dict[str, tuple[CourseId, SessionId, str, str]] = {}

    def issue(
        self,
        course_id: CourseId,
        session_id: SessionId,
        checksum: str,
        declaration_fingerprint: str,
    ) -> str:
        if not isinstance(course_id, CourseId) or not isinstance(session_id, SessionId):
            raise TypeError("host file identity owners must use CourseId and SessionId")
        _require_digest(checksum, "checksum")
        _require_digest(declaration_fingerprint, "declaration_fingerprint")
        material = "\0".join(
            (str(course_id), str(session_id), checksum, declaration_fingerprint)
        ).encode("utf-8")
        file_id = "host-file-sha256:" + sha256(material).hexdigest()
        binding = (course_id, session_id, checksum, declaration_fingerprint)
        previous = self._bindings.get(file_id)
        if previous is not None and previous != binding:
            raise ValueError("host file identity collision")
        self._bindings[file_id] = binding
        return file_id


def _require_file_id(value: object) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 255
        or any(character.isspace() for character in value)
        or "/" in value
        or "\\" in value
    ):
        raise ValueError("host file id must be opaque")


def _require_digest(value: object, name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or value != value.lower()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


__all__ = [
    "MemoryHostFileIdentity",
    "MemoryHostFileSnapshotStore",
]
