"""Verified, sandboxed runtime for the approved AnyDoc 0.1.7 artifact."""

from __future__ import annotations

import ctypes
import hashlib
import io
import json
import os
import platform
import shutil
import signal
import stat
import struct
import subprocess
import sys
import threading
import time
import zipfile
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from tempfile import TemporaryDirectory

from .config import DocumentImportPolicy

ANYDOC_VERSION = "0.1.7"
ANYDOC_WHEEL_NAME = "firecrawl_anydoc-0.1.7-cp310-abi3-macosx_11_0_arm64.whl"
ANYDOC_WHEEL_SIZE = 3_169_866
ANYDOC_WHEEL_SHA256 = "4ab410d12eb7d339e60356eecf2375786acd2d3c6660dcbe1e079eb60addc004"
ANYDOC_RECORD_SHA256 = "cf24870791d8365d94ce0d58877ea0b4c31db4c530ae596e1c4c4f73855059cb"
ANYDOC_LIMITATIONS = (
    "no-ocr",
    "image-only-pdf-rejected",
    "images-omitted",
    "remote-assets-not-fetched",
    "layout-may-be-incomplete",
    "tables-may-be-incomplete",
    "reading-order-may-be-incomplete",
)
_MEMBERS = {
    "anydoc/__init__.py": (
        1_341,
        "aabd9c2d967f27074ccd16b9d65b671ffd6d0fc45ffac3d5ed49ced28e78b409",
    ),
    "anydoc/_anydoc.abi3.so": (
        6_978_288,
        "6758ac12a373a49904eb04d2a25959136f33d873cdb3bf9fe485dfa616fe0fee",
    ),
    "anydoc/_anydoc.pyi": (
        7_035,
        "26103397778eec8055b6e529a7b74323936db653b8efef60ac221f317b7ae763",
    ),
    "anydoc/py.typed": (1, "01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b"),
    "firecrawl_anydoc-0.1.7.dist-info/METADATA": (
        5_579,
        "f2a7fd635b5238453867b4f05fbec13aa37abbd03e157d000a8b5834d6761353",
    ),
    "firecrawl_anydoc-0.1.7.dist-info/WHEEL": (
        104,
        "ca281577fd7255b2a7950b3666101b20c212bfcaf8b61dc1881917302ffdb513",
    ),
    "firecrawl_anydoc-0.1.7.dist-info/sboms/anydoc-python.cyclonedx.json": (
        141_750,
        "a6ff07f597c6c6774b2ba39f9bfc84803563089314c89213cca85842ff42dcc8",
    ),
    "firecrawl_anydoc-0.1.7.dist-info/RECORD": (661, ANYDOC_RECORD_SHA256),
}
ANYDOC_MANIFEST_FINGERPRINT = hashlib.sha256(
    json.dumps(_MEMBERS, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()


class AnyDocErrorCode(StrEnum):
    UNSUPPORTED = "pdf_unsupported"
    MALFORMED = "pdf_malformed"
    ENCRYPTED = "pdf_encrypted"
    RESOURCE_LIMIT = "pdf_resource_limit"
    MISSING_PART = "pdf_missing_part"
    OUTPUT_LIMIT = "pdf_output_limit"
    WORKER_TIMEOUT = "pdf_timeout"
    WORKER_PROTOCOL = "pdf_worker_protocol"
    WORKER_UNAVAILABLE = "pdf_worker_unavailable"


class AnyDocWorkerError(RuntimeError):
    def __init__(self, code: AnyDocErrorCode | str) -> None:
        self.code = code.value if isinstance(code, AnyDocErrorCode) else code
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class AnyDocConversion:
    markdown: bytes
    pdf_sha256: str
    markdown_sha256: str
    page_count: int
    manifest_fingerprint: str = ANYDOC_MANIFEST_FINGERPRINT
    anydoc_version: str = ANYDOC_VERSION
    limitations: tuple[str, ...] = ANYDOC_LIMITATIONS


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _wheel_path() -> Path:
    return Path(__file__).with_name("_vendor") / ANYDOC_WHEEL_NAME


def _verified_wheel() -> bytes:
    if (
        sys.platform != "darwin"
        or platform.machine() != "arm64"
        or sys.version_info[:2] not in {(3, 12), (3, 13)}
        or shutil.which("sandbox-exec") != "/usr/bin/sandbox-exec"
    ):
        raise AnyDocWorkerError(AnyDocErrorCode.WORKER_UNAVAILABLE)
    path = _wheel_path()
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        with os.fdopen(descriptor, "rb") as stream:
            content = stream.read(ANYDOC_WHEEL_SIZE + 1)
    except OSError:
        raise AnyDocWorkerError(AnyDocErrorCode.WORKER_UNAVAILABLE) from None
    if not stat.S_ISREG(opened.st_mode) or len(content) != ANYDOC_WHEEL_SIZE:
        raise AnyDocWorkerError(AnyDocErrorCode.WORKER_UNAVAILABLE)
    if _digest_bytes(content) != ANYDOC_WHEEL_SHA256:
        raise AnyDocWorkerError(AnyDocErrorCode.WORKER_UNAVAILABLE)
    return content


def _extract_verified_wheel(wheel: bytes, destination: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
        infos = archive.infolist()
        if len(infos) != len(_MEMBERS) or {info.filename for info in infos} != set(_MEMBERS):
            raise AnyDocWorkerError(AnyDocErrorCode.WORKER_UNAVAILABLE)
        if len({info.filename for info in infos}) != len(infos):
            raise AnyDocWorkerError(AnyDocErrorCode.WORKER_UNAVAILABLE)
        for info in infos:
            expected_size, expected_digest = _MEMBERS[info.filename]
            if info.file_size != expected_size or info.flag_bits & 1:
                raise AnyDocWorkerError(AnyDocErrorCode.WORKER_UNAVAILABLE)
            parts = Path(info.filename).parts
            if not parts or any(part in {"", ".", ".."} for part in parts):
                raise AnyDocWorkerError(AnyDocErrorCode.WORKER_UNAVAILABLE)
            data = archive.read(info)
            if len(data) != expected_size or _digest_bytes(data) != expected_digest:
                raise AnyDocWorkerError(AnyDocErrorCode.WORKER_UNAVAILABLE)
            target = destination.joinpath(*parts)
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o500 if target.suffix == ".so" else 0o400,
            )
            try:
                os.write(descriptor, data)
            finally:
                os.close(descriptor)


def _copy_input(source: Path, target: Path, maximum: int) -> str:
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    target_fd = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o400,
    )
    digest = hashlib.sha256()
    total = 0
    try:
        if not stat.S_ISREG(os.fstat(source_fd).st_mode):
            raise AnyDocWorkerError(AnyDocErrorCode.RESOURCE_LIMIT)
        while block := os.read(source_fd, 1024 * 1024):
            total += len(block)
            if total > maximum:
                raise AnyDocWorkerError(AnyDocErrorCode.RESOURCE_LIMIT)
            digest.update(block)
            os.write(target_fd, block)
    finally:
        os.close(source_fd)
        os.close(target_fd)
    if total < 5:
        raise AnyDocWorkerError(AnyDocErrorCode.RESOURCE_LIMIT)
    with target.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise AnyDocWorkerError(AnyDocErrorCode.UNSUPPORTED)
    return digest.hexdigest()


def _sandbox_profile() -> str:
    return """(version 1)
(deny default)
(import \"system.sb\")
(allow file-read* (subpath \"/System\") (subpath \"/usr/lib\")
                  (subpath (param \"PYTHON_ROOT\")) (subpath (param \"PRIVATE_ROOT\")))
(allow file-write* (subpath (param \"PRIVATE_ROOT\")))
(allow process-exec (literal (param \"PYTHON\")))
(allow mach-lookup)
(allow sysctl-read)
(allow signal (target self))
(deny process-fork)
(deny network*)
"""


def _resident_bytes(pid: int) -> int | None:
    """Read one macOS process RSS without shelling out or trusting the child."""

    task_info = ctypes.create_string_buffer(128)
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    libproc.proc_pidinfo.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    libproc.proc_pidinfo.restype = ctypes.c_int
    size = libproc.proc_pidinfo(pid, 4, 0, task_info, len(task_info))
    if size < 16:
        return None
    return int(struct.unpack_from("Q", task_info.raw, 8)[0])


def _watch_memory(
    process: subprocess.Popen[bytes], maximum: int, stop: threading.Event, exceeded: list[bool]
) -> None:
    while not stop.wait(0.05):
        if process.poll() is not None:
            return
        resident = _resident_bytes(process.pid)
        if resident is not None and resident > maximum:
            exceeded.append(True)
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            return


def _parse_response(raw: bytes) -> dict[str, object]:
    if not raw or len(raw) > 1024 or raw.rstrip() != raw:
        raise AnyDocWorkerError(AnyDocErrorCode.WORKER_PROTOCOL)

    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise AnyDocWorkerError(AnyDocErrorCode.WORKER_PROTOCOL) from None
    if not isinstance(value, dict):
        raise AnyDocWorkerError(AnyDocErrorCode.WORKER_PROTOCOL)
    return value


def _convert_pdf_in_worker(
    input_path: str | Path,
    *,
    policy: DocumentImportPolicy | None = None,
) -> AnyDocConversion:
    """Convert a PDF only after artifact verification and OS sandbox admission."""
    effective = policy or DocumentImportPolicy()
    wheel = _verified_wheel()
    source = Path(input_path)
    child = Path(__file__).with_name("anydoc_worker_child.py")
    with TemporaryDirectory(prefix="cardine-anydoc-") as temp_name:
        private = Path(temp_name).resolve()
        os.chmod(private, 0o700)
        vendor, copied, output = private / "vendor", private / "input.pdf", private / "output.md"
        child_copy = private / "worker.py"
        vendor.mkdir(mode=0o700)
        _extract_verified_wheel(wheel, vendor)
        child_copy.write_bytes(child.read_bytes())
        os.chmod(child_copy, 0o400)
        pdf_sha256 = _copy_input(source, copied, effective.max_document_bytes)
        python = Path(sys.executable).resolve()
        command = [
            "/usr/bin/sandbox-exec",
            "-D",
            f"PYTHON_ROOT={python.parent.parent}",
            "-D",
            f"PRIVATE_ROOT={private}",
            "-D",
            f"PYTHON={python}",
            "-p",
            _sandbox_profile(),
            str(python),
            "-I",
            "-S",
            "-B",
            str(child_copy),
            str(vendor),
            str(copied),
            str(output),
            str(effective.max_output_bytes),
            str(effective.max_pages),
            str(int(effective.timeout_seconds)),
        ]
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin", "TMPDIR": str(private)},
            start_new_session=True,
        )
        memory_stop = threading.Event()
        memory_exceeded: list[bool] = []
        monitor = threading.Thread(
            target=_watch_memory,
            args=(process, effective.worker_memory_bytes, memory_stop, memory_exceeded),
            name="cardine-anydoc-memory-guard",
            daemon=True,
        )
        monitor.start()
        try:
            stdout, stderr = process.communicate(timeout=effective.timeout_seconds + 2)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            time.sleep(0.1)
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise AnyDocWorkerError(AnyDocErrorCode.WORKER_TIMEOUT) from None
        finally:
            memory_stop.set()
            monitor.join(timeout=1)
        if memory_exceeded:
            raise AnyDocWorkerError(AnyDocErrorCode.RESOURCE_LIMIT)
        if stderr or len(stderr) > 256:
            raise AnyDocWorkerError(AnyDocErrorCode.WORKER_PROTOCOL)
        response = _parse_response(stdout)
        if response.get("v") != 1 or type(response.get("ok")) is not bool:
            raise AnyDocWorkerError(AnyDocErrorCode.WORKER_PROTOCOL)
        if response["ok"] is False:
            if set(response) != {"v", "ok", "code"} or response.get("code") not in {
                item.value for item in AnyDocErrorCode
            }:
                raise AnyDocWorkerError(AnyDocErrorCode.WORKER_PROTOCOL)
            raise AnyDocWorkerError(str(response["code"]))
        if process.returncode != 0 or set(response) != {
            "v",
            "ok",
            "pages",
            "markdown_bytes",
            "markdown_sha256",
        }:
            raise AnyDocWorkerError(AnyDocErrorCode.WORKER_PROTOCOL)
        descriptor = os.open(output, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise AnyDocWorkerError(AnyDocErrorCode.WORKER_PROTOCOL)
            markdown = os.read(descriptor, effective.max_output_bytes + 1)
        finally:
            os.close(descriptor)
        digest = _digest_bytes(markdown)
        if (
            type(response["pages"]) is not int
            or not 1 <= response["pages"] <= effective.max_pages
            or response["markdown_bytes"] != len(markdown)
            or response["markdown_sha256"] != digest
        ):
            raise AnyDocWorkerError(AnyDocErrorCode.WORKER_PROTOCOL)
        return AnyDocConversion(markdown, pdf_sha256, digest, response["pages"])


def convert_pdf_in_worker(
    input_path: str | Path,
    *,
    policy: DocumentImportPolicy | None = None,
) -> AnyDocConversion:
    """Return one bounded receipt or a closed error without leaking OS details."""

    try:
        return _convert_pdf_in_worker(input_path, policy=policy)
    except AnyDocWorkerError:
        raise
    except (OSError, subprocess.SubprocessError, zipfile.BadZipFile):
        raise AnyDocWorkerError(AnyDocErrorCode.WORKER_UNAVAILABLE) from None


__all__ = [
    "ANYDOC_LIMITATIONS",
    "ANYDOC_MANIFEST_FINGERPRINT",
    "ANYDOC_VERSION",
    "AnyDocConversion",
    "AnyDocErrorCode",
    "AnyDocWorkerError",
    "convert_pdf_in_worker",
]
