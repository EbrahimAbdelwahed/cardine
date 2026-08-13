from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from cardine.feedback import (
    WorkaroundApprovalReceipt,
    WorkaroundInputKind,
    WorkaroundOutputKind,
    WorkaroundReceiptStatus,
    WorkaroundTask,
)
from study_agent.adapters.workarounds import (
    PDF_MARKDOWN_EXECUTOR_FINGERPRINT,
    PDF_MARKDOWN_LIMITATION_FINGERPRINT,
    PDF_MARKDOWN_MANIFEST,
    PdfMarkdownExecutor,
)


def _task(pdf: bytes) -> WorkaroundTask:
    return WorkaroundTask(
        WorkaroundInputKind.PDF,
        WorkaroundOutputKind.MARKDOWN,
        sha256(pdf).hexdigest(),
    )


def test_bound_executor_publishes_deterministic_output_without_paths_in_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = b"%PDF-1.7\nfixture"
    task = _task(pdf)
    approval = WorkaroundApprovalReceipt(
        task.fingerprint,
        PDF_MARKDOWN_MANIFEST.identity,
        PDF_MARKDOWN_MANIFEST.version,
        PDF_MARKDOWN_MANIFEST.fingerprint,
        PDF_MARKDOWN_MANIFEST.effect_fingerprint,
        "a" * 64,
    )
    (tmp_path / "input.pdf").write_bytes(pdf)
    output = b"> Warning: fixture\n\n# PDF document\n\n## Page 1\n\nHello\n"
    monkeypatch.setattr(
        "study_agent.adapters.workarounds.pdf_markdown.parse_in_worker",
        lambda content, timeout: output,
    )

    receipt = PdfMarkdownExecutor(
        tmp_path,
        "input.pdf",
        "derived.md",
        task.input_fingerprint,
        approval,
    ).execute(task, PDF_MARKDOWN_MANIFEST.identity)

    assert receipt.status is WorkaroundReceiptStatus.ATTEMPTED_SUCCEEDED
    assert receipt.output_fingerprint == sha256(output).hexdigest()
    assert receipt.limitation_fingerprint == PDF_MARKDOWN_LIMITATION_FINGERPRINT
    assert receipt.executor_fingerprint == PDF_MARKDOWN_EXECUTOR_FINGERPRINT
    assert (tmp_path / "derived.md").read_bytes() == output


def test_failed_parser_does_not_create_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = b"%PDF-1.7\nfixture"
    task = _task(pdf)
    approval = WorkaroundApprovalReceipt(
        task.fingerprint,
        PDF_MARKDOWN_MANIFEST.identity,
        PDF_MARKDOWN_MANIFEST.version,
        PDF_MARKDOWN_MANIFEST.fingerprint,
        PDF_MARKDOWN_MANIFEST.effect_fingerprint,
        "b" * 64,
    )
    (tmp_path / "input.pdf").write_bytes(pdf)
    def fail_parser(content: bytes, timeout: float) -> bytes:
        raise RuntimeError("timeout")

    monkeypatch.setattr(
        "study_agent.adapters.workarounds.pdf_markdown.parse_in_worker", fail_parser
    )

    receipt = PdfMarkdownExecutor(
        tmp_path,
        "input.pdf",
        "derived.md",
        task.input_fingerprint,
        approval,
    ).execute(task, PDF_MARKDOWN_MANIFEST.identity)

    assert receipt.status is WorkaroundReceiptStatus.ATTEMPTED_FAILED
    assert receipt.output_fingerprint is None
    assert not (tmp_path / "derived.md").exists()
