from __future__ import annotations

import importlib.util
from hashlib import sha256
from pathlib import Path

import pytest

from study_agent.adapters.workarounds import PDF_MARKDOWN_MANIFEST, PdfMarkdownExecutor
from study_agent.adapters.workarounds.worker import containment_supported
from cardine.feedback import (
    WorkaroundApprovalReceipt,
    WorkaroundInputKind,
    WorkaroundOutputKind,
    WorkaroundReceiptStatus,
    WorkaroundTask,
)

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("pypdf") is None or not containment_supported(),
    reason="install the pdf extra on a platform with verified worker containment",
)


def _minimal_text_pdf() -> bytes:
    """Build a tiny text-bearing PDF without a second test dependency."""

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length 41 >>\nstream\nBT /F1 12 Tf 72 720 Td (Hello PDF) Tj ET\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    document = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, value in enumerate(objects, start=1):
        offsets.append(len(document))
        document.extend(f"{index} 0 obj\n".encode("ascii"))
        document.extend(value)
        document.extend(b"\nendobj\n")
    xref_offset = len(document)
    document.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    document.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    document.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(document)


def test_real_pypdf_worker_produces_deterministic_markdown(tmp_path: Path) -> None:
    pdf = _minimal_text_pdf()
    task = WorkaroundTask(
        WorkaroundInputKind.PDF,
        WorkaroundOutputKind.MARKDOWN,
        sha256(pdf).hexdigest(),
    )
    (tmp_path / "input.pdf").write_bytes(pdf)
    approval = WorkaroundApprovalReceipt(
        task.fingerprint,
        PDF_MARKDOWN_MANIFEST.identity,
        PDF_MARKDOWN_MANIFEST.version,
        PDF_MARKDOWN_MANIFEST.fingerprint,
        PDF_MARKDOWN_MANIFEST.effect_fingerprint,
        "a" * 64,
    )
    executor = PdfMarkdownExecutor(
        tmp_path,
        "input.pdf",
        "derived.md",
        task.input_fingerprint,
        approval,
    )
    first = executor.execute(task, PDF_MARKDOWN_MANIFEST.identity)
    second = executor.execute(task, PDF_MARKDOWN_MANIFEST.identity)
    assert first.status is WorkaroundReceiptStatus.ATTEMPTED_SUCCEEDED
    assert second.to_bytes() == first.to_bytes()
    assert (tmp_path / "derived.md").read_text(encoding="utf-8").endswith("Hello PDF\n")
