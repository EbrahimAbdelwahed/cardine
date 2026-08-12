"""Closed identity and provenance metadata for the PDF workaround."""

from __future__ import annotations

from hashlib import sha256

from cardine.feedback.workarounds import (
    WorkaroundApprovalPolicy,
    WorkaroundEffect,
    WorkaroundInputKind,
    WorkaroundManifest,
    WorkaroundOutputKind,
)
from study_agent.state import canonical_json_bytes

PDF_MARKDOWN_PARSER_IDENTITY = "pypdf==6.14.2"
PDF_MARKDOWN_RENDERER_POLICY_VERSION = "pdf-markdown-renderer@1"
PDF_MARKDOWN_MANIFEST = WorkaroundManifest(
    identity="pdf-to-markdown-pypdf@1",
    version=1,
    input_kind=WorkaroundInputKind.PDF,
    output_kind=WorkaroundOutputKind.MARKDOWN,
    effects=(WorkaroundEffect.READ_LOCAL, WorkaroundEffect.WRITE_DERIVED),
    approval_policy=WorkaroundApprovalPolicy.HOST_APPROVAL,
    preconditions=(
        "portable-pdf-input",
        "portable-markdown-output",
        "trusted-root-bound-by-host",
        "input-digest-bound-by-host",
        "approval-receipt-bound-by-host",
        "text-bearing-pdf-only",
        "no-network-no-shell-no-model",
    ),
    quality_limitations=(
        "no-ocr",
        "layout-may-be-incomplete",
        "tables-may-be-incomplete",
        "images-omitted",
        "equations-may-be-incomplete",
        "reading-order-may-be-incomplete",
        "resource-containment-is-not-a-security-sandbox",
    ),
    provenance_obligations=(
        "input-digest",
        "output-digest",
        "manifest-fingerprint",
        "executor-fingerprint",
        "parser-identity",
        "renderer-policy-version",
        "limitation-fingerprint",
    ),
)

PDF_MARKDOWN_LIMITATION_FINGERPRINT = PDF_MARKDOWN_MANIFEST.quality_limitation_fingerprint

_EXECUTOR_IDENTITY = {
    "executor": "pdf-markdown-local-worker@1",
    "manifest": PDF_MARKDOWN_MANIFEST.fingerprint,
    "parser": PDF_MARKDOWN_PARSER_IDENTITY,
    "renderer": PDF_MARKDOWN_RENDERER_POLICY_VERSION,
}
PDF_MARKDOWN_EXECUTOR_FINGERPRINT = sha256(
    b"study-agent-pdf-markdown-executor-v1\0"
    + canonical_json_bytes(_EXECUTOR_IDENTITY)
).hexdigest()

# The alias makes the adapter-specific manifest pleasant to discover without
# changing the provider-neutral feedback contracts.
PdfMarkdownManifest = WorkaroundManifest

__all__ = [
    "PDF_MARKDOWN_EXECUTOR_FINGERPRINT",
    "PDF_MARKDOWN_LIMITATION_FINGERPRINT",
    "PDF_MARKDOWN_MANIFEST",
    "PDF_MARKDOWN_PARSER_IDENTITY",
    "PDF_MARKDOWN_RENDERER_POLICY_VERSION",
    "PdfMarkdownManifest",
]
