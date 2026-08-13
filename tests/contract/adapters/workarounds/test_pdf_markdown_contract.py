from __future__ import annotations

from cardine.feedback import (
    WorkaroundApprovalPolicy,
    WorkaroundEffect,
    WorkaroundInputKind,
    WorkaroundOutputKind,
)
from study_agent.adapters.workarounds import (
    PDF_MARKDOWN_MANIFEST,
    PDF_MARKDOWN_PARSER_IDENTITY,
    PDF_MARKDOWN_RENDERER_POLICY_VERSION,
    pdf_markdown_provenance_bytes,
)


def test_pdf_manifest_is_the_closed_static_grant() -> None:
    assert PDF_MARKDOWN_MANIFEST.identity == "pdf-to-markdown-pypdf@1"
    assert PDF_MARKDOWN_MANIFEST.input_kind is WorkaroundInputKind.PDF
    assert PDF_MARKDOWN_MANIFEST.output_kind is WorkaroundOutputKind.MARKDOWN
    assert PDF_MARKDOWN_MANIFEST.effects == (
        WorkaroundEffect.READ_LOCAL,
        WorkaroundEffect.WRITE_DERIVED,
    )
    assert PDF_MARKDOWN_MANIFEST.approval_policy is WorkaroundApprovalPolicy.HOST_APPROVAL
    assert {
        "no-ocr",
        "layout-may-be-incomplete",
        "tables-may-be-incomplete",
        "images-omitted",
        "equations-may-be-incomplete",
        "reading-order-may-be-incomplete",
    }.issubset(PDF_MARKDOWN_MANIFEST.quality_limitations)


def test_provenance_bytes_are_canonical_and_path_free() -> None:
    data = pdf_markdown_provenance_bytes(input_fingerprint="a" * 64, output_fingerprint="b" * 64)
    assert data == pdf_markdown_provenance_bytes(
        input_fingerprint="a" * 64, output_fingerprint="b" * 64
    )
    assert b"a" * 64 in data
    assert b"b" * 64 in data
    assert PDF_MARKDOWN_PARSER_IDENTITY.encode() in data
    assert PDF_MARKDOWN_RENDERER_POLICY_VERSION.encode() in data
    assert b"/" not in data
