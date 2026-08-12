"""Sandboxed AnyDoc child; communicates only one bounded JSON receipt."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import socket
import sys
import unicodedata
from pathlib import Path
from typing import Any

_ERRORS = {
    "EncryptedError": "pdf_encrypted",
    "MalformedError": "pdf_malformed",
    "MissingPartError": "pdf_missing_part",
    "ResourceLimitError": "pdf_resource_limit",
    "UnsupportedError": "pdf_unsupported",
}


def _emit(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _deny_network(*_args: object, **_kwargs: object) -> Any:
    raise OSError("network disabled")


def _apply_limits(output_bytes: int, timeout_seconds: int) -> None:
    import resource

    for name, target in (
        ("RLIMIT_CORE", 0),
        ("RLIMIT_CPU", timeout_seconds),
        ("RLIMIT_FSIZE", output_bytes),
        ("RLIMIT_NOFILE", 32),
    ):
        constant = getattr(resource, name)
        resource.setrlimit(constant, (target, target))


def _page_count(path: bytes) -> int:
    core_graphics = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    core_graphics.CGDataProviderCreateWithFilename.argtypes = [ctypes.c_char_p]
    core_graphics.CGDataProviderCreateWithFilename.restype = ctypes.c_void_p
    core_graphics.CGPDFDocumentCreateWithProvider.argtypes = [ctypes.c_void_p]
    core_graphics.CGPDFDocumentCreateWithProvider.restype = ctypes.c_void_p
    core_graphics.CGPDFDocumentGetNumberOfPages.argtypes = [ctypes.c_void_p]
    core_graphics.CGPDFDocumentGetNumberOfPages.restype = ctypes.c_size_t
    core_graphics.CGPDFDocumentRelease.argtypes = [ctypes.c_void_p]
    core_graphics.CGDataProviderRelease.argtypes = [ctypes.c_void_p]
    provider = core_graphics.CGDataProviderCreateWithFilename(path)
    if not provider:
        raise ValueError("pdf_malformed")
    try:
        document = core_graphics.CGPDFDocumentCreateWithProvider(provider)
        if not document:
            raise ValueError("pdf_malformed")
        try:
            return int(core_graphics.CGPDFDocumentGetNumberOfPages(document))
        finally:
            core_graphics.CGPDFDocumentRelease(document)
    finally:
        core_graphics.CGDataProviderRelease(provider)


def _normalize(value: str, maximum: int) -> bytes:
    if len(value) > maximum:
        raise ValueError("pdf_output_limit")
    text = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    text = text.replace("\ufeff", "")
    text = re.sub(r"!\[([^\]]*)\]\(\s*(?:<)?(?:https?|data):[^)]*(?:>)?\)", r"\1", text)
    text = "\n".join(line.rstrip() for line in text.split("\n")).strip()
    if not text:
        raise ValueError("pdf_unsupported")
    output = (text + "\n").encode()
    if len(output) > maximum:
        raise ValueError("pdf_output_limit")
    return output


def main() -> int:
    if len(sys.argv) != 7:
        _emit({"v": 1, "ok": False, "code": "pdf_worker_protocol"})
        return 2
    vendor, input_name, output_name = sys.argv[1], sys.argv[2], sys.argv[3]
    output_bytes, max_pages, timeout = int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6])
    try:
        _apply_limits(output_bytes, timeout)
        socket.socket = _deny_network  # type: ignore[misc,assignment]
        socket.create_connection = _deny_network
        socket.socketpair = _deny_network
        pages = _page_count(os.fsencode(input_name))
        if pages < 1:
            raise ValueError("pdf_unsupported")
        if pages > max_pages:
            raise ValueError("pdf_resource_limit")
        sys.path.insert(0, vendor)
        import anydoc

        data = Path(input_name).read_bytes()
        if anydoc.format_from_bytes(data) != "pdf":
            raise ValueError("pdf_unsupported")
        markdown = anydoc.to_markdown_bytes(data, "pdf")
        if not isinstance(markdown, str):
            raise ValueError("pdf_worker_protocol")
        output = _normalize(markdown, output_bytes)
        descriptor = os.open(
            output_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(output)
                stream.flush()
        finally:
            os.close(descriptor)
        _emit(
            {
                "v": 1,
                "ok": True,
                "pages": pages,
                "markdown_bytes": len(output),
                "markdown_sha256": hashlib.sha256(output).hexdigest(),
            }
        )
        return 0
    except Exception as error:
        code = _ERRORS.get(type(error).__name__)
        if code is None and isinstance(error, ValueError) and str(error).startswith("pdf_"):
            code = str(error)
        _emit({"v": 1, "ok": False, "code": code or "pdf_worker_protocol"})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
