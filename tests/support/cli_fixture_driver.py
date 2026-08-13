"""Explicit offline host composition used only by subprocess release tests."""

from __future__ import annotations

import fcntl
import re
import sys
from collections.abc import AsyncIterator
from pathlib import Path

from cardine.cli.main import main
from cardine.cli.repository import ModelAdapterRegistry
from study_agent.ports import (
    CancellationToken,
    ModelCapabilities,
    ModelFinishReason,
    ModelInvocation,
    ModelRequest,
    ModelResponse,
    ModelStreamEvent,
    ModelStreamEventKind,
)

_EVIDENCE_ID = re.compile(r'"evidence_id":"([^"]+)"')


class FixtureModel:
    capabilities = ModelCapabilities(structured_output=True)

    def __init__(self, counter: Path) -> None:
        self._counter = counter

    async def generate(self, request: ModelRequest) -> ModelResponse:
        with self._counter.open("a+", encoding="utf-8") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            stream.seek(0)
            value = int(stream.read() or "0") + 1
            stream.seek(0)
            stream.truncate()
            stream.write(str(value))
            stream.flush()
        rendered = "\n".join(message.content for message in request.messages)
        match = _EVIDENCE_ID.search(rendered)
        if match is None:
            raise AssertionError("fixture expected canonical evidence")
        return ModelResponse(
            "",
            None,
            ModelFinishReason.STOP,
            ModelInvocation("test-fixture", "1.0.0", "offline-fixture", "fixture-response"),
            structured_output={
                "status": "answered",
                "segments": (
                    {
                        "kind": "supported_claim",
                        "text": "The brachial plexus is formed by C5 to T1 roots.",
                        "evidence_ids": (match.group(1),),
                    },
                ),
                "unsupported_information_note": None,
            },
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        del request
        if False:  # pragma: no cover
            yield ModelStreamEvent(ModelStreamEventKind.CANCELLED)
        raise AssertionError("fixture does not stream")

    async def cancel(self, token: CancellationToken) -> None:
        del token
        raise AssertionError("fixture does not support cancellation")


def run(arguments: list[str]) -> int:
    if len(arguments) < 2:
        raise SystemExit("usage: cli_fixture_driver COUNTER -- CLI_ARGS...")
    counter = Path(arguments[0])
    separator = arguments.index("--")

    def build(config: object, credential: str | None) -> FixtureModel:
        del config, credential
        return FixtureModel(counter)

    registry = ModelAdapterRegistry(
        {"test-fixture": build}, versions={"test-fixture": "1.0.0"}
    )
    return main(arguments[separator + 1 :], model_adapters=registry, environment={})


if __name__ == "__main__":
    raise SystemExit(run(sys.argv[1:]))
