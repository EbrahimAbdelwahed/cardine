"""Bounded subprocess adapter for the qualified PageIndex structural subset."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
QUALIFIED_UPSTREAM_COMMIT = "9470b639609e3113e66a58e23f36bd6b0221fd85"
QUALIFIED_ARCHIVE_SHA256 = (
    "c46682f3a9259087fefc8df79f4ed6b1d901c5177a4f25fa9ceced430fbb98bd"
)


class PageIndexWorkerError(RuntimeError):
    """A bounded structural worker failed closed."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PageIndexWorker:
    timeout_seconds: float = 3.0
    max_input_bytes: int = MAX_INPUT_BYTES

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0 or self.timeout_seconds > 30:
            raise ValueError("timeout_seconds is outside the worker bound")
        if self.max_input_bytes <= 0 or self.max_input_bytes > MAX_INPUT_BYTES:
            raise ValueError("max_input_bytes is outside the worker bound")

    @property
    def source_path(self) -> Path:
        return Path(__file__).with_name("page_index_md.py.data")

    @property
    def child_path(self) -> Path:
        return Path(__file__).with_name("worker_child.py")

    def run(self, markdown: str) -> list[object]:
        if type(markdown) is not str or not markdown:
            raise PageIndexWorkerError("pageindex_input_limit")
        if len(markdown.encode("utf-8")) > self.max_input_bytes:
            raise PageIndexWorkerError("pageindex_input_limit")
        request = json.dumps(
            {"markdown": markdown}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        if len(request) > self.max_input_bytes:
            raise PageIndexWorkerError("pageindex_input_limit")
        command = [
            sys.executable,
            "-I",
            "-S",
            "-B",
            str(self.child_path),
            str(self.source_path),
        ]
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={"PATH": os.defpath, "PYTHONIOENCODING": "utf-8"},
                start_new_session=True,
            )
            stdout, stderr = process.communicate(request, timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            with suppress(OSError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise PageIndexWorkerError("pageindex_timeout") from None
        except OSError:
            raise PageIndexWorkerError("pageindex_worker_unavailable") from None
        if process.returncode != 0 or stderr:
            payload = _decode(stdout)
            raise PageIndexWorkerError(str(payload.get("code", "pageindex_worker_protocol")))
        payload = _decode(stdout)
        if payload.get("v") != 1 or payload.get("ok") is not True or set(payload) != {
            "v",
            "ok",
            "tree",
        }:
            raise PageIndexWorkerError("pageindex_worker_protocol")
        tree = payload["tree"]
        if not isinstance(tree, list):
            raise PageIndexWorkerError("pageindex_worker_protocol")
        return tree

    convert = run
    build_tree = run


def _decode(raw: bytes) -> dict[str, object]:
    if not raw or len(raw) > MAX_OUTPUT_BYTES or raw.rstrip() != raw:
        raise PageIndexWorkerError("pageindex_worker_protocol")
    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in values:
            if key in result:
                raise ValueError("duplicate_key")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise PageIndexWorkerError("pageindex_worker_protocol") from None
    if not isinstance(value, dict):
        raise PageIndexWorkerError("pageindex_worker_protocol")
    return value


__all__ = ["PageIndexWorker", "PageIndexWorkerError"]
