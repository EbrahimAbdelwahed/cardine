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
_MAX_TEMPORARY_FILE_BYTES = 384 * 1024 * 1024


def _emit(payload: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _deny_network(*_args: object, **_kwargs: object) -> Any:
    raise OSError("network disabled")


def _apply_limits(output_bytes: int, timeout_seconds: int) -> None:
    import resource

    for name, target in (
        ("RLIMIT_CORE", 0),
        ("RLIMIT_CPU", timeout_seconds),
        # CoreGraphics materializes one temporary single-page PDF before AnyDoc
        # emits Markdown.  It needs its own fixed ceiling: the canonical Markdown
        # remains independently bounded by ``output_bytes`` below.
        ("RLIMIT_FSIZE", _MAX_TEMPORARY_FILE_BYTES),
        ("RLIMIT_NOFILE", 32),
    ):
        constant = getattr(resource, name)
        resource.setrlimit(constant, (target, target))


def _page_count(path: bytes) -> int:
    document, provider = _open_pdf(path)
    try:
        core_graphics = _core_graphics()
        return int(core_graphics.CGPDFDocumentGetNumberOfPages(document))
    finally:
        _core_graphics().CGPDFDocumentRelease(document)
        _core_graphics().CGDataProviderRelease(provider)


class _CGPoint(ctypes.Structure):
    _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]


class _CGSize(ctypes.Structure):
    _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]


class _CGRect(ctypes.Structure):
    _fields_ = [("origin", _CGPoint), ("size", _CGSize)]


def _core_graphics() -> Any:
    core_graphics = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    core_graphics.CGDataProviderCreateWithFilename.argtypes = [ctypes.c_char_p]
    core_graphics.CGDataProviderCreateWithFilename.restype = ctypes.c_void_p
    core_graphics.CGPDFDocumentCreateWithProvider.argtypes = [ctypes.c_void_p]
    core_graphics.CGPDFDocumentCreateWithProvider.restype = ctypes.c_void_p
    core_graphics.CGPDFDocumentGetNumberOfPages.argtypes = [ctypes.c_void_p]
    core_graphics.CGPDFDocumentGetNumberOfPages.restype = ctypes.c_size_t
    core_graphics.CGPDFDocumentRelease.argtypes = [ctypes.c_void_p]
    core_graphics.CGDataProviderRelease.argtypes = [ctypes.c_void_p]
    core_graphics.CGPDFDocumentGetPage.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    core_graphics.CGPDFDocumentGetPage.restype = ctypes.c_void_p
    core_graphics.CGPDFPageGetBoxRect.argtypes = [ctypes.c_void_p, ctypes.c_int]
    core_graphics.CGPDFPageGetBoxRect.restype = _CGRect
    core_graphics.CGDataConsumerCreateWithURL.argtypes = [ctypes.c_void_p]
    core_graphics.CGDataConsumerCreateWithURL.restype = ctypes.c_void_p
    core_graphics.CGPDFContextCreate.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_CGRect),
        ctypes.c_void_p,
    ]
    core_graphics.CGPDFContextCreate.restype = ctypes.c_void_p
    core_graphics.CGContextBeginPage.argtypes = [ctypes.c_void_p, ctypes.POINTER(_CGRect)]
    core_graphics.CGContextDrawPDFPage.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    core_graphics.CGContextEndPage.argtypes = [ctypes.c_void_p]
    core_graphics.CGPDFContextClose.argtypes = [ctypes.c_void_p]
    core_graphics.CGContextRelease.argtypes = [ctypes.c_void_p]
    core_graphics.CGDataConsumerRelease.argtypes = [ctypes.c_void_p]
    return core_graphics


def _open_pdf(path: bytes) -> tuple[int, int]:
    core_graphics = _core_graphics()
    provider = core_graphics.CGDataProviderCreateWithFilename(path)
    if not provider:
        raise ValueError("pdf_malformed")
    document = core_graphics.CGPDFDocumentCreateWithProvider(provider)
    if not document:
        core_graphics.CGDataProviderRelease(provider)
        raise ValueError("pdf_malformed")
    return document, provider


def _single_page_pdf(source: str, page_number: int, target: str) -> None:
    core_graphics = _core_graphics()
    core_foundation = ctypes.CDLL(
        "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
    )
    core_foundation.CFURLCreateFromFileSystemRepresentation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_long,
        ctypes.c_bool,
    ]
    core_foundation.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
    core_foundation.CFRelease.argtypes = [ctypes.c_void_p]
    document, provider = _open_pdf(os.fsencode(source))
    url = consumer = context = None
    try:
        page = core_graphics.CGPDFDocumentGetPage(document, page_number)
        if not page:
            raise ValueError("pdf_malformed")
        media_box = core_graphics.CGPDFPageGetBoxRect(page, 0)
        target_bytes = os.fsencode(target)
        url = core_foundation.CFURLCreateFromFileSystemRepresentation(
            None, target_bytes, len(target_bytes), False
        )
        consumer = core_graphics.CGDataConsumerCreateWithURL(url)
        context = core_graphics.CGPDFContextCreate(consumer, ctypes.byref(media_box), None)
        if not url or not consumer or not context:
            raise ValueError("pdf_worker_protocol")
        core_graphics.CGContextBeginPage(context, ctypes.byref(media_box))
        core_graphics.CGContextDrawPDFPage(context, page)
        core_graphics.CGContextEndPage(context)
        core_graphics.CGPDFContextClose(context)
    finally:
        if context:
            core_graphics.CGContextRelease(context)
        if consumer:
            core_graphics.CGDataConsumerRelease(consumer)
        if url:
            core_foundation.CFRelease(url)
        core_graphics.CGPDFDocumentRelease(document)
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
    if len(sys.argv) != 8:
        _emit({"v": 1, "ok": False, "code": "pdf_worker_protocol"})
        return 2
    vendor, input_name, output_name, page_map_name = (
        sys.argv[1],
        sys.argv[2],
        sys.argv[3],
        sys.argv[4],
    )
    output_bytes, max_pages, timeout = int(sys.argv[5]), int(sys.argv[6]), int(sys.argv[7])
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
        page_parts: list[bytes] = []
        page_spans: list[dict[str, int]] = []
        current_offset = 0
        current_bytes = 0
        for page_number in range(1, pages + 1):
            page_pdf = str(Path(output_name).with_name(f"page-{page_number}.pdf"))
            _single_page_pdf(input_name, page_number, page_pdf)
            try:
                page_markdown = anydoc.to_markdown_bytes(Path(page_pdf).read_bytes(), "pdf")
            finally:
                Path(page_pdf).unlink(missing_ok=True)
            if not isinstance(page_markdown, str):
                raise ValueError("pdf_worker_protocol")
            page_output = _normalize(page_markdown, output_bytes)
            if page_parts:
                page_parts.append(b"\n")
                current_offset += 1
                current_bytes += 1
            start = current_offset
            page_parts.append(page_output)
            current_offset += len(page_output.decode("utf-8"))
            current_bytes += len(page_output)
            page_spans.append(
                {
                    "page": page_number,
                    "start_offset": start,
                    "end_offset": current_offset,
                }
            )
            if current_bytes > output_bytes:
                raise ValueError("pdf_output_limit")
        output = b"".join(page_parts)
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
        page_map = json.dumps(
            page_spans, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        page_map_descriptor = os.open(
            page_map_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
        try:
            os.write(page_map_descriptor, page_map)
        finally:
            os.close(page_map_descriptor)
        _emit(
            {
                "v": 1,
                "ok": True,
                "pages": pages,
                "page_map_bytes": len(page_map),
                "page_map_sha256": hashlib.sha256(page_map).hexdigest(),
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
