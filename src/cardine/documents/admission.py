"""One canonical PDF admission owner shared by browser and CLI."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from study_agent.domain import ExecutionContext, SourceId
from study_agent.domain.provenance import (
    ContentOrigin,
    DocumentConversionProvenance,
    DocumentPageSpan,
)
from study_agent.ingestion import TextIngestionResult, TextIngestionService

from .anydoc_runtime import AnyDocConversion, convert_pdf_in_worker
from .config import DocumentImportPolicy


class PdfAdmissionError(ValueError):
    """The streamed source identity changed before canonical admission."""


@dataclass(frozen=True, slots=True)
class PdfAdmissionReceipt:
    result: TextIngestionResult
    conversion: AnyDocConversion
    provenance: DocumentConversionProvenance


def _read_bound_pdf(path: Path, *, byte_size: int, digest: str) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    content = bytearray()
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size != byte_size:
            raise PdfAdmissionError("PDF changed during conversion")
        while block := os.read(descriptor, 1024 * 1024):
            content.extend(block)
            if len(content) > byte_size:
                raise PdfAdmissionError("PDF changed during conversion")
    finally:
        os.close(descriptor)
    value = bytes(content)
    if len(value) != byte_size or sha256(value).hexdigest() != digest:
        raise PdfAdmissionError("PDF changed during conversion")
    return value


def admit_pdf(
    *,
    input_path: Path,
    expected_sha256: str | None,
    byte_size: int,
    filename: str,
    title: str,
    source_id: SourceId | None,
    trust_level: int,
    source_role: str,
    context: ExecutionContext,
    ingestion: TextIngestionService,
    policy: DocumentImportPolicy,
    expected_sequence: int | None = None,
) -> PdfAdmissionReceipt:
    conversion = convert_pdf_in_worker(input_path, policy=policy)
    if expected_sha256 is not None and conversion.pdf_sha256 != expected_sha256:
        raise PdfAdmissionError("PDF changed during conversion")
    original = _read_bound_pdf(
        input_path,
        byte_size=byte_size,
        digest=conversion.pdf_sha256,
    )
    canonical_source_id = source_id or SourceId(
        "source-pdf-sha256:" + conversion.pdf_sha256
    )
    provenance = DocumentConversionProvenance(
        pdf_sha256=conversion.pdf_sha256,
        markdown_sha256=conversion.markdown_sha256,
        adapter_identity="pdf-to-markdown-anydoc@1",
        adapter_version=conversion.anydoc_version,
        manifest_fingerprint=conversion.manifest_fingerprint,
        normalizer_policy="gfm-normalizer@1",
        limitations=conversion.limitations,
        page_count=conversion.page_count,
        page_spans=tuple(
            DocumentPageSpan(span.page, span.start_offset, span.end_offset)
            for span in conversion.page_spans
        ),
    )
    result = ingestion.ingest(
        filename=(filename[:-4] or "document") + ".md",
        content=conversion.markdown,
        original_content=original,
        source_id=canonical_source_id,
        title=title,
        trust_level=trust_level,
        source_role=source_role,
        content_origin=ContentOrigin.EXTRACTED,
        conversion_provenance=provenance,
        context=context,
        expected_sequence=expected_sequence,
    )
    return PdfAdmissionReceipt(result, conversion, provenance)


__all__ = ["PdfAdmissionError", "PdfAdmissionReceipt", "admit_pdf"]
