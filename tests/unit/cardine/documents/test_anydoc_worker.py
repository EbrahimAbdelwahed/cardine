from __future__ import annotations

from pathlib import Path

import pytest

from cardine.documents import AnyDocErrorCode, AnyDocWorkerError, convert_pdf_in_worker


def _minimal_text_pdf() -> bytes:
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
        document.extend(f"{index} 0 obj\n".encode())
        document.extend(value)
        document.extend(b"\nendobj\n")
    xref_offset = len(document)
    document.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    document.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode())
    document.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode()
    )
    return bytes(document)


def test_verified_anydoc_converts_text_pdf_in_isolated_worker(tmp_path: Path) -> None:
    source = tmp_path / "lesson.pdf"
    source.write_bytes(_minimal_text_pdf())

    receipt = convert_pdf_in_worker(source)

    assert b"Hello PDF" in receipt.markdown
    assert receipt.anydoc_version == "0.1.7"
    assert "no-ocr" in receipt.limitations
    assert tuple(tmp_path.iterdir()) == (source,)


def test_non_pdf_fails_without_derived_output(tmp_path: Path) -> None:
    source = tmp_path / "lesson.pdf"
    source.write_bytes(b"not a pdf")

    with pytest.raises(AnyDocWorkerError) as captured:
        convert_pdf_in_worker(source)

    assert captured.value.code == AnyDocErrorCode.UNSUPPORTED.value
    assert tuple(tmp_path.iterdir()) == (source,)


def test_missing_input_is_a_closed_worker_failure(tmp_path: Path) -> None:
    with pytest.raises(AnyDocWorkerError) as captured:
        convert_pdf_in_worker(tmp_path / "missing.pdf")

    assert captured.value.code == AnyDocErrorCode.WORKER_UNAVAILABLE.value
    assert tuple(tmp_path.iterdir()) == ()
