"""Optional, host-authorized local PDF-to-Markdown workaround executor."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from cardine.feedback.workarounds import (
    WorkaroundApprovalReceipt,
    WorkaroundExecutionReceipt,
    WorkaroundReceiptStatus,
    WorkaroundTask,
)
from study_agent.state import canonical_json_bytes

from .filesystem import (
    MAX_PDF_BYTES,
    PDF_MAGIC,
    PdfMarkdownFilesystemError,
    capture_pdf,
    capture_root_identity,
    publish_markdown,
    validate_portable_path,
)
from .manifest import (
    PDF_MARKDOWN_EXECUTOR_FINGERPRINT,
    PDF_MARKDOWN_LIMITATION_FINGERPRINT,
    PDF_MARKDOWN_MANIFEST,
    PDF_MARKDOWN_PARSER_IDENTITY,
    PDF_MARKDOWN_RENDERER_POLICY_VERSION,
)
from .renderer import MAX_PDF_MARKDOWN_OUTPUT_BYTES, canonical_pdf_markdown
from .worker import PdfWorkerError, parse_in_worker

MAX_PDF_PAGES = 256
DEFAULT_PDF_MARKDOWN_TIMEOUT_SECONDS = 5.0


class PdfMarkdownExecutionError(ValueError):
    """A trusted binding is invalid before an execution attempt can begin."""


@dataclass(frozen=True, slots=True)
class PdfMarkdownBinding:
    """Host-owned execution context; no path is part of :class:`WorkaroundTask`."""

    trusted_root: Path
    input_relative_path: str
    output_relative_path: str
    input_fingerprint: str
    approval: WorkaroundApprovalReceipt

    def __post_init__(self) -> None:
        if not isinstance(self.trusted_root, Path):
            raise PdfMarkdownExecutionError("trusted_root_must_be_path")
        if not self.trusted_root.is_absolute():
            raise PdfMarkdownExecutionError("trusted_root_must_be_absolute")
        validate_portable_path(self.input_relative_path, suffix=".pdf")
        validate_portable_path(self.output_relative_path, suffix=".md")
        if self.input_relative_path == self.output_relative_path:
            raise PdfMarkdownExecutionError("input_output_alias")
        if not isinstance(self.input_fingerprint, str) or len(self.input_fingerprint) != 64:
            raise PdfMarkdownExecutionError("invalid_input_fingerprint")
        if any(character not in "0123456789abcdef" for character in self.input_fingerprint):
            raise PdfMarkdownExecutionError("invalid_input_fingerprint")
        if not isinstance(self.approval, WorkaroundApprovalReceipt):
            raise PdfMarkdownExecutionError("approval_receipt_required")
        if (
            self.approval.manifest_identity != PDF_MARKDOWN_MANIFEST.identity
            or self.approval.manifest_version != PDF_MARKDOWN_MANIFEST.version
            or self.approval.manifest_fingerprint != PDF_MARKDOWN_MANIFEST.fingerprint
            or self.approval.effect_fingerprint != PDF_MARKDOWN_MANIFEST.effect_fingerprint
        ):
            raise PdfMarkdownExecutionError("approval_receipt_mismatch")


class PdfMarkdownExecutor:
    """Conforming workaround executor with a trusted host-bound context."""

    manifest = PDF_MARKDOWN_MANIFEST

    def __init__(
        self,
        trusted_root: str | Path,
        input_relative_path: str,
        output_relative_path: str,
        input_fingerprint: str,
        approval: WorkaroundApprovalReceipt,
        *,
        wall_timeout_seconds: float = DEFAULT_PDF_MARKDOWN_TIMEOUT_SECONDS,
    ) -> None:
        root, root_identity = capture_root_identity(trusted_root)
        self._binding = PdfMarkdownBinding(
            root,
            input_relative_path,
            output_relative_path,
            input_fingerprint,
            approval,
        )
        self._root_identity = root_identity
        if not 0 < wall_timeout_seconds <= 30:
            raise PdfMarkdownExecutionError("invalid_wall_timeout")
        self._wall_timeout_seconds = wall_timeout_seconds

    @property
    def binding(self) -> PdfMarkdownBinding:
        return self._binding

    @property
    def executor_fingerprint(self) -> str:
        return PDF_MARKDOWN_EXECUTOR_FINGERPRINT

    def execute(self, task: WorkaroundTask, manifest_identity: str) -> WorkaroundExecutionReceipt:
        """Execute only the pre-bound path pair for an exact digest-bearing task."""

        if manifest_identity != PDF_MARKDOWN_MANIFEST.identity:
            raise PdfMarkdownExecutionError("manifest_identity_mismatch")
        if task.input_kind.value != "pdf" or task.output_kind.value != "markdown":
            raise PdfMarkdownExecutionError("task_kind_mismatch")
        if task.input_fingerprint != self._binding.input_fingerprint:
            raise PdfMarkdownExecutionError("input_fingerprint_mismatch")
        if self._binding.approval.task_fingerprint != task.fingerprint:
            raise PdfMarkdownExecutionError("approval_task_mismatch")

        try:
            captured = capture_pdf(
                self._binding.trusted_root,
                self._root_identity,
                self._binding.input_relative_path,
            )
            content = captured.content
            input_fingerprint = sha256(content).hexdigest()
            if input_fingerprint != self._binding.input_fingerprint:
                return self._failed_receipt("input_digest_mismatch")
            output = parse_in_worker(content, self._wall_timeout_seconds)
            if len(output) > MAX_PDF_MARKDOWN_OUTPUT_BYTES:
                return self._failed_receipt("output_limit_exceeded")
            published = publish_markdown(
                self._binding.trusted_root,
                self._root_identity,
                self._binding.output_relative_path,
                output,
                output_limit=MAX_PDF_MARKDOWN_OUTPUT_BYTES,
                input_identity=captured.identity,
            )
            output_fingerprint = sha256(published).hexdigest()
            provenance = pdf_markdown_provenance_bytes(
                input_fingerprint=input_fingerprint,
                output_fingerprint=output_fingerprint,
            )
            return WorkaroundExecutionReceipt(
                status=WorkaroundReceiptStatus.ATTEMPTED_SUCCEEDED,
                manifest_identity=PDF_MARKDOWN_MANIFEST.identity,
                manifest_version=PDF_MARKDOWN_MANIFEST.version,
                input_fingerprint=input_fingerprint,
                output_fingerprint=output_fingerprint,
                provenance_fingerprint=sha256(provenance).hexdigest(),
                limitation_fingerprint=PDF_MARKDOWN_LIMITATION_FINGERPRINT,
                manifest_fingerprint=PDF_MARKDOWN_MANIFEST.fingerprint,
                effect_fingerprint=PDF_MARKDOWN_MANIFEST.effect_fingerprint,
                approval_fingerprint=self._binding.approval.approval_fingerprint,
                executor_fingerprint=PDF_MARKDOWN_EXECUTOR_FINGERPRINT,
            )
        except (PdfMarkdownFilesystemError, PdfWorkerError) as error:
            return self._failed_receipt(error.code)
        except (OSError, RuntimeError, ValueError, TypeError):
            return self._failed_receipt("adapter_execution_failed")

    def _failed_receipt(self, _reason: str) -> WorkaroundExecutionReceipt:
        return WorkaroundExecutionReceipt(
            status=WorkaroundReceiptStatus.ATTEMPTED_FAILED,
            manifest_identity=PDF_MARKDOWN_MANIFEST.identity,
            manifest_version=PDF_MARKDOWN_MANIFEST.version,
            input_fingerprint=self._binding.input_fingerprint,
            limitation_fingerprint=PDF_MARKDOWN_LIMITATION_FINGERPRINT,
            manifest_fingerprint=PDF_MARKDOWN_MANIFEST.fingerprint,
            effect_fingerprint=PDF_MARKDOWN_MANIFEST.effect_fingerprint,
            approval_fingerprint=self._binding.approval.approval_fingerprint,
            executor_fingerprint=PDF_MARKDOWN_EXECUTOR_FINGERPRINT,
        )


def bind_pdf_markdown_executor(
    trusted_root: str | Path,
    input_relative_path: str,
    output_relative_path: str,
    input_fingerprint: str,
    approval: WorkaroundApprovalReceipt,
    *,
    wall_timeout_seconds: float = DEFAULT_PDF_MARKDOWN_TIMEOUT_SECONDS,
) -> PdfMarkdownExecutor:
    """Bind trusted host paths once; subsequent execute calls receive no path."""

    return PdfMarkdownExecutor(
        trusted_root,
        input_relative_path,
        output_relative_path,
        input_fingerprint,
        approval,
        wall_timeout_seconds=wall_timeout_seconds,
    )


def pdf_markdown_provenance_bytes(*, input_fingerprint: str, output_fingerprint: str) -> bytes:
    """Return canonical adapter provenance bytes, intentionally path-free."""

    from typing import Any, cast

    return canonical_json_bytes(
        cast(
            Any,
            {
                "executor_fingerprint": PDF_MARKDOWN_EXECUTOR_FINGERPRINT,
                "input_fingerprint": input_fingerprint,
                "limitation_fingerprint": PDF_MARKDOWN_LIMITATION_FINGERPRINT,
                "manifest_fingerprint": PDF_MARKDOWN_MANIFEST.fingerprint,
                "output_fingerprint": output_fingerprint,
                "parser_identity": PDF_MARKDOWN_PARSER_IDENTITY,
                "renderer_policy_version": PDF_MARKDOWN_RENDERER_POLICY_VERSION,
                "schema_version": 1,
            },
        )
    )


__all__ = [
    "DEFAULT_PDF_MARKDOWN_TIMEOUT_SECONDS",
    "MAX_PDF_BYTES",
    "MAX_PDF_MARKDOWN_OUTPUT_BYTES",
    "MAX_PDF_PAGES",
    "PDF_MAGIC",
    "PdfMarkdownBinding",
    "PdfMarkdownExecutionError",
    "PdfMarkdownExecutor",
    "PdfMarkdownFilesystemError",
    "bind_pdf_markdown_executor",
    "canonical_pdf_markdown",
    "pdf_markdown_provenance_bytes",
]
