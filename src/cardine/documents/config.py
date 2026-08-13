"""Resource policy for private PDF admission."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_DOCUMENT_MAX_BYTES = 256 * 1024 * 1024
DEFAULT_DOCUMENT_OUTPUT_BYTES = 16 * 1024 * 1024
DEFAULT_DOCUMENT_MEMORY_BYTES = 768 * 1024 * 1024
DEFAULT_DOCUMENT_PAGE_LIMIT = 1024
DEFAULT_DOCUMENT_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class DocumentImportPolicy:
    """One immutable input/output/time policy shared by parent and worker."""

    max_document_bytes: int = DEFAULT_DOCUMENT_MAX_BYTES
    max_output_bytes: int = DEFAULT_DOCUMENT_OUTPUT_BYTES
    worker_memory_bytes: int = DEFAULT_DOCUMENT_MEMORY_BYTES
    max_pages: int = DEFAULT_DOCUMENT_PAGE_LIMIT
    timeout_seconds: float = DEFAULT_DOCUMENT_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        for name in (
            "max_document_bytes",
            "max_output_bytes",
            "worker_memory_bytes",
            "max_pages",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.max_document_bytes > DEFAULT_DOCUMENT_MAX_BYTES:
            raise ValueError("max_document_bytes exceeds the supported 256 MiB ceiling")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= 120
        ):
            raise ValueError("timeout_seconds must be between 0 and 120 seconds")
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))


def _bounded_size(environment: Mapping[str, str], name: str, default: int) -> int:
    raw = environment.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip().replace("_", ""))
    except ValueError:
        raise ValueError(f"{name} must be a positive byte count") from None
    return value


def document_import_policy(
    environment: Mapping[str, str] | None = None,
) -> DocumentImportPolicy:
    values = os.environ if environment is None else environment
    return DocumentImportPolicy(
        max_document_bytes=_bounded_size(
            values, "CARDINE_MAX_DOCUMENT_BYTES", DEFAULT_DOCUMENT_MAX_BYTES
        ),
        max_output_bytes=_bounded_size(
            values, "CARDINE_MAX_DOCUMENT_OUTPUT_BYTES", DEFAULT_DOCUMENT_OUTPUT_BYTES
        ),
        worker_memory_bytes=_bounded_size(
            values, "CARDINE_DOCUMENT_MEMORY_BYTES", DEFAULT_DOCUMENT_MEMORY_BYTES
        ),
    )


__all__ = ["DocumentImportPolicy", "document_import_policy"]
