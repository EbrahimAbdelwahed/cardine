from __future__ import annotations

from pathlib import Path

import pytest

import cardine.documents.anydoc_runtime as anydoc_runtime
from cardine.documents import AnyDocErrorCode, AnyDocWorkerError, convert_pdf_in_worker
from cardine.documents.config import DocumentImportPolicy


def _text_pdf(*page_texts: str) -> bytes:
    font_id = 3 + 2 * len(page_texts)
    pages: list[bytes] = []
    children: list[str] = []
    for index, text in enumerate(page_texts):
        page_id = 3 + 2 * index
        stream_id = page_id + 1
        children.append(f"{page_id} 0 R")
        pages.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
                f"/Contents {stream_id} 0 R >>"
            ).encode()
        )
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        pages.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode()
            + stream
            + b"\nendstream"
        )
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{' '.join(children)}] /Count {len(page_texts)} >>".encode(),
        *pages,
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


def _minimal_text_pdf() -> bytes:
    return _text_pdf("Hello PDF")


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


def test_page_map_binds_each_pdf_page_to_exact_markdown_offsets(tmp_path: Path) -> None:
    source = tmp_path / "two-pages.pdf"
    source.write_bytes(_text_pdf("First page", "Second page"))

    receipt = convert_pdf_in_worker(source)

    text = receipt.markdown.decode("utf-8")
    assert receipt.page_count == 2
    assert tuple(span.page for span in receipt.page_spans) == (1, 2)
    assert "First page" in text[
        receipt.page_spans[0].start_offset : receipt.page_spans[0].end_offset
    ]
    assert "Second page" in text[
        receipt.page_spans[1].start_offset : receipt.page_spans[1].end_offset
    ]
    assert receipt.page_spans[0].end_offset <= receipt.page_spans[1].start_offset


def test_missing_input_is_a_closed_worker_failure(tmp_path: Path) -> None:
    with pytest.raises(AnyDocWorkerError) as captured:
        convert_pdf_in_worker(tmp_path / "missing.pdf")

    assert captured.value.code == AnyDocErrorCode.WORKER_UNAVAILABLE.value
    assert tuple(tmp_path.iterdir()) == ()


def test_malformed_and_scanned_pdfs_fail_without_derived_output(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.pdf"
    malformed.write_bytes(b"%PDF-1.4\nnot a document")
    with pytest.raises(AnyDocWorkerError) as malformed_error:
        convert_pdf_in_worker(malformed)
    assert malformed_error.value.code == AnyDocErrorCode.MALFORMED.value

    scanned = tmp_path / "scanned.pdf"
    scanned.write_bytes(_text_pdf(""))
    with pytest.raises(AnyDocWorkerError) as scanned_error:
        convert_pdf_in_worker(scanned)
    assert scanned_error.value.code == AnyDocErrorCode.UNSUPPORTED.value
    assert set(tmp_path.iterdir()) == {malformed, scanned}


def test_input_page_and_output_limits_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "bounded.pdf"
    source.write_bytes(_text_pdf("First page", "Second page"))

    policies = (
        (
            DocumentImportPolicy(max_document_bytes=len(source.read_bytes()) - 1),
            AnyDocErrorCode.RESOURCE_LIMIT,
        ),
        (DocumentImportPolicy(max_pages=1), AnyDocErrorCode.RESOURCE_LIMIT),
        (DocumentImportPolicy(max_output_bytes=4), AnyDocErrorCode.OUTPUT_LIMIT),
    )
    for policy, expected in policies:
        with pytest.raises(AnyDocWorkerError) as captured:
            convert_pdf_in_worker(source, policy=policy)
        assert captured.value.code == expected.value
    assert tuple(tmp_path.iterdir()) == (source,)


def _install_fake_anydoc(destination: Path, source: str) -> None:
    package = destination / "anydoc"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(source, encoding="utf-8")


@pytest.mark.parametrize(
    ("module_source", "expected"),
    (
        (
            """class EncryptedError(Exception): pass
def format_from_bytes(data): return 'pdf'
def to_markdown_bytes(data, kind): raise EncryptedError()
""",
            AnyDocErrorCode.ENCRYPTED,
        ),
        (
            """def format_from_bytes(data): return 'pdf'
def to_markdown_bytes(data, kind):
    import socket
    socket.socket()
    return 'network unexpectedly available'
""",
            AnyDocErrorCode.WORKER_PROTOCOL,
        ),
    ),
)
def test_worker_maps_encryption_and_denies_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module_source: str,
    expected: AnyDocErrorCode,
) -> None:
    source = tmp_path / "lesson.pdf"
    source.write_bytes(_minimal_text_pdf())
    monkeypatch.setattr(anydoc_runtime, "_verified_wheel", lambda: b"fixture")
    monkeypatch.setattr(
        anydoc_runtime,
        "_extract_verified_wheel",
        lambda _wheel, destination: _install_fake_anydoc(destination, module_source),
    )

    with pytest.raises(AnyDocWorkerError) as captured:
        convert_pdf_in_worker(source)

    assert captured.value.code == expected.value
    assert tuple(tmp_path.iterdir()) == (source,)


def test_timed_out_worker_is_terminated_and_leaves_no_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "lesson.pdf"
    source.write_bytes(_minimal_text_pdf())
    monkeypatch.setattr(anydoc_runtime, "_verified_wheel", lambda: b"fixture")
    monkeypatch.setattr(
        anydoc_runtime,
        "_extract_verified_wheel",
        lambda _wheel, destination: _install_fake_anydoc(
            destination,
            """import time
def format_from_bytes(data): return 'pdf'
def to_markdown_bytes(data, kind):
    time.sleep(10)
    return 'late output'
""",
        ),
    )

    with pytest.raises(AnyDocWorkerError) as captured:
        convert_pdf_in_worker(source, policy=DocumentImportPolicy(timeout_seconds=1))

    assert captured.value.code == AnyDocErrorCode.WORKER_TIMEOUT.value
    assert tuple(tmp_path.iterdir()) == (source,)
