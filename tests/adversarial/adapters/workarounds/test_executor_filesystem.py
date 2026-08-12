from __future__ import annotations

import os
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
    PDF_MARKDOWN_MANIFEST,
    PdfMarkdownExecutor,
    PdfMarkdownFilesystemError,
)
from study_agent.adapters.workarounds.filesystem import (
    MAX_PDF_BYTES,
    CapturedPdf,
    _staging_name,
    capture_root_identity,
    publish_markdown,
    validate_portable_path,
)
from study_agent.adapters.workarounds.worker import PdfWorkerError

type _PathValue = str | bytes | os.PathLike[str] | os.PathLike[bytes]


def _task(pdf: bytes) -> WorkaroundTask:
    return WorkaroundTask(
        WorkaroundInputKind.PDF,
        WorkaroundOutputKind.MARKDOWN,
        sha256(pdf).hexdigest(),
    )


def _approval(task: WorkaroundTask, *, marker: str = "a") -> WorkaroundApprovalReceipt:
    return WorkaroundApprovalReceipt(
        task.fingerprint,
        PDF_MARKDOWN_MANIFEST.identity,
        PDF_MARKDOWN_MANIFEST.version,
        PDF_MARKDOWN_MANIFEST.fingerprint,
        PDF_MARKDOWN_MANIFEST.effect_fingerprint,
        marker * 64,
    )


def _executor(root: Path, pdf: bytes, *, output: str = "derived.md") -> PdfMarkdownExecutor:
    task = _task(pdf)
    return PdfMarkdownExecutor(
        root,
        "input.pdf",
        output,
        task.input_fingerprint,
        _approval(task),
    )


@pytest.mark.parametrize(
    "path",
    [
        "../input.pdf",
        "nested/../../input.pdf",
        "/absolute.pdf",
        r"nested\\input.pdf",
        "input.PDF",
        "input.pdf\x00suffix",
        "input?.pdf",
        "input*.pdf",
        "input<.pdf",
        "input>.pdf",
        'input".pdf',
        "input|.pdf",
        "CON.pdf",
        "input.pdf ",
        "input.pdf.",
    ],
)
def test_portable_input_and_output_paths_reject_before_publication(
    tmp_path: Path, path: str
) -> None:
    pdf = b"%PDF-1.7\nfixture"
    task = _task(pdf)
    with pytest.raises((ValueError, PdfMarkdownFilesystemError)):
        PdfMarkdownExecutor(
            tmp_path,
            path,
            "derived.md",
            task.input_fingerprint,
            _approval(task),
        )
    with pytest.raises(PdfMarkdownFilesystemError):
        validate_portable_path(path, suffix=".pdf")
    assert not (tmp_path / "derived.md").exists()


def test_output_alias_and_mismatches_are_rejected_before_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = b"%PDF-1.7\nfixture"
    task = _task(pdf)
    (tmp_path / "input.pdf").write_bytes(pdf)
    with pytest.raises(ValueError, match="invalid_portable_path"):
        _executor(tmp_path, pdf, output="input.pdf")
    executor = _executor(tmp_path, pdf)
    called = False

    def parser(*_: object) -> bytes:
        nonlocal called
        called = True
        return b"unused"

    monkeypatch.setattr("study_agent.adapters.workarounds.pdf_markdown.parse_in_worker", parser)
    wrong_task = WorkaroundTask(
        WorkaroundInputKind.TEXT, WorkaroundOutputKind.MARKDOWN, task.input_fingerprint
    )
    with pytest.raises(ValueError, match="task_kind_mismatch"):
        executor.execute(wrong_task, PDF_MARKDOWN_MANIFEST.identity)
    with pytest.raises(ValueError, match="manifest_identity_mismatch"):
        executor.execute(task, "other@1")
    assert not called
    assert not (tmp_path / "derived.md").exists()


def test_symlink_root_input_parent_and_output_fail_closed_without_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = b"%PDF-1.7\nfixture"
    task = _task(pdf)
    target = tmp_path / "target"
    target.mkdir()
    (target / "input.pdf").write_bytes(pdf)
    (tmp_path / "root-link").symlink_to(target, target_is_directory=True)
    with pytest.raises(PdfMarkdownFilesystemError, match="trusted_root_unavailable"):
        PdfMarkdownExecutor(
            tmp_path / "root-link",
            "input.pdf",
            "derived.md",
            task.input_fingerprint,
            _approval(task),
        )

    (tmp_path / "input.pdf").symlink_to(target / "input.pdf")
    executor = _executor(tmp_path, pdf)
    failed = executor.execute(task, PDF_MARKDOWN_MANIFEST.identity)
    assert failed.status is WorkaroundReceiptStatus.ATTEMPTED_FAILED
    assert not (tmp_path / "derived.md").exists()
    (tmp_path / "input.pdf").unlink()
    (tmp_path / "nested").symlink_to(target, target_is_directory=True)
    nested_executor = PdfMarkdownExecutor(
        tmp_path,
        "nested/input.pdf",
        "derived.md",
        task.input_fingerprint,
        _approval(
            WorkaroundTask(
                WorkaroundInputKind.PDF,
                WorkaroundOutputKind.MARKDOWN,
                task.input_fingerprint,
            ),
            marker="b",
        ),
    )
    failed = nested_executor.execute(task, PDF_MARKDOWN_MANIFEST.identity)
    assert failed.status is WorkaroundReceiptStatus.ATTEMPTED_FAILED
    (tmp_path / "input.pdf").write_bytes(pdf)
    (tmp_path / "derived.md").symlink_to(target / "input.pdf")
    monkeypatch.setattr(
        "study_agent.adapters.workarounds.pdf_markdown.parse_in_worker",
        lambda *_: b"# derived\n",
    )
    failed = _executor(tmp_path, pdf).execute(task, PDF_MARKDOWN_MANIFEST.identity)
    assert failed.status is WorkaroundReceiptStatus.ATTEMPTED_FAILED
    assert (tmp_path / "derived.md").is_symlink()


def test_fifo_and_oversized_or_bad_magic_inputs_are_rejected_without_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parser_called = False

    def parser(*_: object) -> bytes:
        nonlocal parser_called
        parser_called = True
        return b"never"

    monkeypatch.setattr("study_agent.adapters.workarounds.pdf_markdown.parse_in_worker", parser)
    fifo = tmp_path / "input.pdf"
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO fixtures require POSIX")
    os.mkfifo(fifo)
    payload = b"%PDF-1.7\nfixture"
    task = _task(payload)
    receipt = PdfMarkdownExecutor(
        tmp_path, "input.pdf", "derived.md", task.input_fingerprint, _approval(task)
    ).execute(task, PDF_MARKDOWN_MANIFEST.identity)
    assert receipt.status is WorkaroundReceiptStatus.ATTEMPTED_FAILED
    assert not parser_called
    fifo.unlink()

    bad = b"not a pdf"
    (tmp_path / "input.pdf").write_bytes(bad)
    bad_task = _task(bad)
    receipt = PdfMarkdownExecutor(
        tmp_path,
        "input.pdf",
        "derived.md",
        bad_task.input_fingerprint,
        _approval(bad_task, marker="b"),
    ).execute(bad_task, PDF_MARKDOWN_MANIFEST.identity)
    assert receipt.status is WorkaroundReceiptStatus.ATTEMPTED_FAILED
    assert not parser_called

    oversized = b"%PDF-1.7\n" + b"x" * (MAX_PDF_BYTES + 1)
    (tmp_path / "input.pdf").write_bytes(oversized)
    oversized_task = _task(oversized)
    receipt = PdfMarkdownExecutor(
        tmp_path,
        "input.pdf",
        "derived.md",
        oversized_task.input_fingerprint,
        _approval(oversized_task, marker="c"),
    ).execute(oversized_task, PDF_MARKDOWN_MANIFEST.identity)
    assert receipt.status is WorkaroundReceiptStatus.ATTEMPTED_FAILED
    assert not parser_called


def test_input_digest_mismatch_and_source_rebinding_leave_no_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    actual = b"%PDF-1.7\nactual"
    expected = b"%PDF-1.7\nexpected"
    (tmp_path / "input.pdf").write_bytes(actual)
    expected_task = _task(expected)
    called = False

    def parser(*_: object) -> bytes:
        nonlocal called
        called = True
        return b"unexpected"

    monkeypatch.setattr("study_agent.adapters.workarounds.pdf_markdown.parse_in_worker", parser)
    receipt = PdfMarkdownExecutor(
        tmp_path,
        "input.pdf",
        "derived.md",
        expected_task.input_fingerprint,
        _approval(expected_task),
    ).execute(expected_task, PDF_MARKDOWN_MANIFEST.identity)
    assert receipt.status is WorkaroundReceiptStatus.ATTEMPTED_FAILED
    assert receipt.input_fingerprint == expected_task.input_fingerprint
    assert not called
    assert not (tmp_path / "derived.md").exists()
    assert (tmp_path / "input.pdf").read_bytes() == actual

    task = _task(actual)
    executor = _executor(tmp_path, actual)
    monkeypatch.setattr(
        "study_agent.adapters.workarounds.pdf_markdown.capture_pdf",
        lambda *_: (_ for _ in ()).throw(PdfMarkdownFilesystemError("input_path_rebound")),
    )
    receipt = executor.execute(task, PDF_MARKDOWN_MANIFEST.identity)
    assert receipt.status is WorkaroundReceiptStatus.ATTEMPTED_FAILED
    assert not (tmp_path / "derived.md").exists()


def test_executor_uses_identity_captured_with_the_bytes_without_reopening_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = b"%PDF-1.7\ncaptured"
    task = _task(pdf)
    (tmp_path / "input.pdf").write_bytes(pdf)
    root, root_identity = capture_root_identity(tmp_path)
    captured_identity = (1, 2)
    monkeypatch.setattr(
        "study_agent.adapters.workarounds.pdf_markdown.capture_pdf",
        lambda *_: CapturedPdf(pdf, captured_identity),
    )
    monkeypatch.setattr(
        "study_agent.adapters.workarounds.pdf_markdown.parse_in_worker",
        lambda *_: b"# derived\n",
    )
    observed: dict[str, object] = {}

    def publish(*args: object, **kwargs: object) -> bytes:
        observed["input_identity"] = kwargs["input_identity"]
        return b"# derived\n"

    monkeypatch.setattr("study_agent.adapters.workarounds.pdf_markdown.publish_markdown", publish)
    executor = _executor(tmp_path, pdf)
    receipt = executor.execute(task, PDF_MARKDOWN_MANIFEST.identity)
    assert receipt.status is WorkaroundReceiptStatus.ATTEMPTED_SUCCEEDED
    assert observed["input_identity"] == captured_identity
    assert root == tmp_path
    assert root_identity[0] > 0


def test_portable_path_rejects_oversized_utf8_components() -> None:
    with pytest.raises(PdfMarkdownFilesystemError, match="invalid_portable_path"):
        validate_portable_path("é" * 128 + ".pdf", suffix=".pdf")


def test_directory_rebound_before_link_fails_without_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, root_identity = capture_root_identity(tmp_path)
    calls = 0
    original_verify = __import__(
        "study_agent.adapters.workarounds.filesystem", fromlist=["_verify_parent_binding"]
    )._verify_parent_binding

    def verify(*args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PdfMarkdownFilesystemError("output_path_rebound")
        original_verify(*args, **kwargs)

    monkeypatch.setattr(
        "study_agent.adapters.workarounds.filesystem._verify_parent_binding", verify
    )
    with pytest.raises(PdfMarkdownFilesystemError, match="output_path_rebound"):
        publish_markdown(root, root_identity, "derived.md", b"stable\n", output_limit=100)
    assert calls == 2
    assert not (tmp_path / "derived.md").exists()


def test_hardlink_destination_is_rejected_without_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    destination = tmp_path / "derived.md"
    source.write_bytes(b"user-owned\n")
    os.link(source, destination)
    root, root_identity = capture_root_identity(tmp_path)
    with pytest.raises(PdfMarkdownFilesystemError, match="output_collision"):
        publish_markdown(root, root_identity, "derived.md", b"generated\n", output_limit=100)
    assert destination.read_bytes() == b"user-owned\n"


def test_identical_existing_output_rechecks_parent_before_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = b"stable\n"
    (tmp_path / "derived.md").write_bytes(output)
    root, root_identity = capture_root_identity(tmp_path)
    calls = 0
    filesystem = __import__(
        "study_agent.adapters.workarounds.filesystem", fromlist=["_verify_parent_binding"]
    )
    original_verify = filesystem._verify_parent_binding

    def verify(*args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PdfMarkdownFilesystemError("output_path_rebound")
        original_verify(*args, **kwargs)

    monkeypatch.setattr(
        "study_agent.adapters.workarounds.filesystem._verify_parent_binding", verify
    )
    with pytest.raises(PdfMarkdownFilesystemError, match="output_path_rebound"):
        publish_markdown(root, root_identity, "derived.md", output, output_limit=100)
    assert calls == 2


def test_final_parent_verification_rejects_destination_rebind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = b"stable\n"
    destination = tmp_path / "derived.md"
    destination.write_bytes(output)
    root, root_identity = capture_root_identity(tmp_path)
    filesystem = __import__(
        "study_agent.adapters.workarounds.filesystem", fromlist=["_verify_parent_binding"]
    )
    original_verify = filesystem._verify_parent_binding
    calls = 0

    def verify(*args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        original_verify(*args, **kwargs)
        if calls == 2:
            destination.unlink()
            destination.write_bytes(output)

    monkeypatch.setattr(
        "study_agent.adapters.workarounds.filesystem._verify_parent_binding", verify
    )
    with pytest.raises(PdfMarkdownFilesystemError, match="output_rebound"):
        publish_markdown(root, root_identity, "derived.md", output, output_limit=100)
    assert calls == 2
    assert destination.read_bytes() == output


def test_identical_raced_output_rechecks_parent_before_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = b"stable\n"
    root, root_identity = capture_root_identity(tmp_path)
    calls = 0
    filesystem = __import__(
        "study_agent.adapters.workarounds.filesystem", fromlist=["_verify_parent_binding"]
    )
    original_verify = filesystem._verify_parent_binding

    def verify(*args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PdfMarkdownFilesystemError("output_path_rebound")
        original_verify(*args, **kwargs)

    monkeypatch.setattr(
        "study_agent.adapters.workarounds.filesystem._verify_parent_binding", verify
    )
    original_link = os.link

    def race_link(
        src: _PathValue,
        dst: _PathValue,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        if dst != "derived.md":
            original_link(
                src,
                dst,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
                follow_symlinks=follow_symlinks,
            )
            return
        (tmp_path / "derived.md").write_bytes(output)
        raise FileExistsError

    monkeypatch.setattr(os, "link", race_link)
    with pytest.raises(PdfMarkdownFilesystemError, match="output_path_rebound"):
        publish_markdown(root, root_identity, "derived.md", output, output_limit=100)
    assert calls == 2
    monkeypatch.setattr(os, "link", original_link)


def test_collision_reconciliation_is_byte_exact_and_never_overwrites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = b"%PDF-1.7\nfixture"
    output = b"> Warning\n\n# PDF document\n\n## Page 1\n\nText\n"
    (tmp_path / "input.pdf").write_bytes(pdf)
    task = _task(pdf)
    executor = _executor(tmp_path, pdf)
    monkeypatch.setattr(
        "study_agent.adapters.workarounds.pdf_markdown.parse_in_worker",
        lambda *_: output,
    )
    first = executor.execute(task, PDF_MARKDOWN_MANIFEST.identity)
    first_bytes = first.to_bytes()
    assert first.status is WorkaroundReceiptStatus.ATTEMPTED_SUCCEEDED
    assert (tmp_path / "derived.md").read_bytes() == output

    second = executor.execute(task, PDF_MARKDOWN_MANIFEST.identity)
    assert second.to_bytes() == first_bytes
    assert second.output_fingerprint == sha256(output).hexdigest()

    (tmp_path / "derived.md").write_bytes(b"user-owned\n")
    before = (tmp_path / "derived.md").read_bytes()
    failed = executor.execute(task, PDF_MARKDOWN_MANIFEST.identity)
    assert failed.status is WorkaroundReceiptStatus.ATTEMPTED_FAILED
    assert (tmp_path / "derived.md").read_bytes() == before

    (tmp_path / "derived.md").unlink()
    (tmp_path / "derived.md").mkdir()
    failed = executor.execute(task, PDF_MARKDOWN_MANIFEST.identity)
    assert failed.status is WorkaroundReceiptStatus.ATTEMPTED_FAILED
    assert (tmp_path / "derived.md").is_dir()


def test_parser_timeout_protocol_and_containment_failures_create_no_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = b"%PDF-1.7\nfixture"
    (tmp_path / "input.pdf").write_bytes(pdf)
    task = _task(pdf)
    for code in ("worker_timeout", "worker_protocol_failed", "resource_containment_unavailable"):
        monkeypatch.setattr(
            "study_agent.adapters.workarounds.pdf_markdown.parse_in_worker",
            lambda *_args, _code=code: (_ for _ in ()).throw(PdfWorkerError(_code)),
        )
        receipt = _executor(tmp_path, pdf).execute(task, PDF_MARKDOWN_MANIFEST.identity)
        assert receipt.status is WorkaroundReceiptStatus.ATTEMPTED_FAILED
        assert not (tmp_path / "derived.md").exists()


def test_publish_reconciles_identical_race_without_replacement(tmp_path: Path) -> None:
    root, root_identity = __import__(
        "study_agent.adapters.workarounds.filesystem", fromlist=["capture_root_identity"]
    ).capture_root_identity(tmp_path)
    output = b"stable\n"
    original_link = os.link

    def race_link(
        src: _PathValue,
        dst: _PathValue,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        if dst != "derived.md":
            original_link(
                src,
                dst,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
                follow_symlinks=follow_symlinks,
            )
            return
        destination = tmp_path / "derived.md"
        destination.write_bytes(output)
        raise FileExistsError

    os.link = race_link
    try:
        assert publish_markdown(
            root, root_identity, "derived.md", output, output_limit=100
        ) == output
    finally:
        os.link = original_link
    assert (tmp_path / "derived.md").read_bytes() == output


def test_deterministic_staging_recovers_before_link_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = b"recoverable\n"
    root, root_identity = capture_root_identity(tmp_path)
    staging = tmp_path / _staging_name("derived.md", output)
    staging.write_bytes(output)
    original_link = os.link

    def crash_link(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated process loss")

    monkeypatch.setattr(os, "link", crash_link)
    with pytest.raises(RuntimeError, match="simulated process loss"):
        publish_markdown(root, root_identity, "derived.md", output, output_limit=100)
    assert staging.read_bytes() == output
    assert not (tmp_path / "derived.md").exists()

    monkeypatch.setattr(os, "link", original_link)
    assert publish_markdown(root, root_identity, "derived.md", output, output_limit=100) == output
    assert not staging.exists()


def test_deterministic_staging_recovers_after_private_link_before_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = b"recoverable\n"
    root, root_identity = capture_root_identity(tmp_path)
    staging = tmp_path / _staging_name("derived.md", output)
    filesystem = __import__(
        "study_agent.adapters.workarounds.filesystem", fromlist=["_cleanup_private_temp"]
    )
    original_link = os.link
    original_cleanup = filesystem._cleanup_private_temp

    def crash_after_stage_link(
        src: _PathValue,
        dst: _PathValue,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        if dst == staging.name:
            original_link(
                src,
                dst,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
                follow_symlinks=follow_symlinks,
            )
            raise RuntimeError("simulated process loss")
        original_link(
            src,
            dst,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(filesystem, "_cleanup_private_temp", lambda *_: None)
    monkeypatch.setattr(os, "link", crash_after_stage_link)
    with pytest.raises(RuntimeError, match="simulated process loss"):
        publish_markdown(root, root_identity, "derived.md", output, output_limit=100)
    assert staging.read_bytes() == output
    assert staging.stat().st_nlink == 2
    assert not (tmp_path / "derived.md").exists()

    monkeypatch.setattr(filesystem, "_cleanup_private_temp", original_cleanup)
    monkeypatch.setattr(os, "link", original_link)
    assert publish_markdown(root, root_identity, "derived.md", output, output_limit=100) == output
    assert not staging.exists()


def test_partial_private_write_never_creates_deterministic_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = b"recoverable\n"
    root, root_identity = capture_root_identity(tmp_path)
    staging = tmp_path / _staging_name("derived.md", output)
    original_write = os.write
    wrote_partial = False

    def partial_write(descriptor: int, data: bytes) -> int:
        nonlocal wrote_partial
        if not wrote_partial:
            wrote_partial = True
            return original_write(descriptor, data[:1])
        raise OSError("simulated crash during write")

    monkeypatch.setattr(os, "write", partial_write)
    with pytest.raises(PdfMarkdownFilesystemError, match="output_write_failed"):
        publish_markdown(root, root_identity, "derived.md", output, output_limit=100)
    assert wrote_partial
    assert not staging.exists()
    assert not (tmp_path / "derived.md").exists()


def test_deterministic_staging_recovers_after_link_before_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = b"recoverable\n"
    root, root_identity = capture_root_identity(tmp_path)
    staging = tmp_path / _staging_name("derived.md", output)
    original_unlink = os.unlink
    failed = False

    def crash_unlink(path: _PathValue, *, dir_fd: int | None = None) -> None:
        nonlocal failed
        if path == staging.name and not failed:
            failed = True
            raise OSError("simulated process loss")
        original_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", crash_unlink)
    with pytest.raises(PdfMarkdownFilesystemError, match="output_publish_failed"):
        publish_markdown(root, root_identity, "derived.md", output, output_limit=100)
    assert failed
    assert (tmp_path / "derived.md").read_bytes() == output
    assert staging.exists()
    assert staging.stat().st_nlink == 2

    monkeypatch.setattr(os, "unlink", original_unlink)
    assert publish_markdown(root, root_identity, "derived.md", output, output_limit=100) == output
    assert not staging.exists()


@pytest.mark.parametrize("kind", ["partial", "mismatched", "unrelated_hardlink"])
def test_deterministic_staging_rejects_attacker_collisions(
    tmp_path: Path, kind: str
) -> None:
    output = b"expected\n"
    root, root_identity = capture_root_identity(tmp_path)
    staging = tmp_path / _staging_name("derived.md", output)
    if kind == "partial":
        staging.write_bytes(output[:1])
    elif kind == "mismatched":
        staging.write_bytes(b"attacker\n")
    else:
        source = tmp_path / "attacker.tmp"
        source.write_bytes(output)
        os.link(source, staging)
    with pytest.raises(PdfMarkdownFilesystemError, match="output_collision"):
        publish_markdown(root, root_identity, "derived.md", output, output_limit=100)
    assert not (tmp_path / "derived.md").exists()
