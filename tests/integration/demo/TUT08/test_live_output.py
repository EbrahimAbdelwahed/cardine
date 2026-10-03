from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import cast

import pytest

from cardine.cli.repository import ModelAdapterRegistry
from cardine.demo.ui_application import RepositoryUiApplication
from study_agent.adapters.model import (
    GPT_5_6_LUNA_ADAPTER_ID,
    GPT_5_6_LUNA_ADAPTER_VERSION,
    OpenAIGpt56LunaConfig,
    OpenAIGpt56LunaModel,
)
from study_agent.repository_config import LocalRepositoryConfig, ModelAdapterConfig
from tests.integration.demo.TUT08.test_repository_backed_chat import (
    _EVIDENCE_ID,
    COURSE,
    SESSION,
    _command,
    _LunaWireTransport,
    _repository,
)

pytest.importorskip("pydantic_core", reason="native streaming requires the OpenAI extra")


class _GatedStream:
    def __init__(self, invalid: bool) -> None:
        self.started = Event()
        self.finish = Event()
        self.invalid = invalid
        self.calls = 0

    async def events(
        self, url: str, headers: Mapping[str, str], body: bytes, timeout_seconds: float
    ) -> AsyncGenerator[str, None]:
        del url, headers, timeout_seconds
        self.calls += 1
        request = json.loads(body)
        assert request["stream"] is True
        rendered = "\n".join(item["content"] for item in request["messages"])
        evidence = _EVIDENCE_ID.search(rendered)
        assert evidence is not None
        prefix = '{"status":"answered","segments":[{"kind":"supported_claim","text":"The aortic'
        yield json.dumps({"choices": [{"index": 0, "delta": {"content": prefix}}]})
        # This resumes after the consumer has published the first real delta.
        self.started.set()
        assert await asyncio.to_thread(self.finish.wait, 10)
        suffix = (
            ' valve has three cusps.","evidence_ids":["'
            + ("unknown-evidence" if self.invalid else evidence.group(1))
            + '"]}],"unsupported_information_note":null}'
        )
        yield json.dumps({"choices": [{"index": 0, "delta": {"content": suffix}}]})
        yield json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        yield "[DONE]"


@pytest.mark.parametrize("invalid", [False, True])
def test_provider_draft_arrives_before_completion_and_never_authorizes_answer(
    tmp_path: Path, invalid: bool
) -> None:
    root, _, _ = _repository(tmp_path)
    (root / "study-agent.json").write_bytes(
        LocalRepositoryConfig(
            ModelAdapterConfig(GPT_5_6_LUNA_ADAPTER_ID, {"timeout_seconds": 30}, "OPENAI_API_KEY")
        ).to_bytes()
    )
    stream = _GatedStream(invalid)
    model = OpenAIGpt56LunaModel(
        OpenAIGpt56LunaConfig("offline-fixture"),
        transport=_LunaWireTransport(),
        streaming_transport=stream,
    )
    adapters = ModelAdapterRegistry(
        {GPT_5_6_LUNA_ADAPTER_ID: lambda _config, _credential: model},
        versions={GPT_5_6_LUNA_ADAPTER_ID: GPT_5_6_LUNA_ADAPTER_VERSION},
    )
    app = RepositoryUiApplication(
        root,
        COURSE,
        SESSION,
        model_adapters=adapters,
        environment={"OPENAI_API_KEY": "offline-fixture"},
    )
    snapshot = app.get("/api/v1/session")
    command = _command(
        "stream-test", cast(int, snapshot["high_water_sequence"]), "Explain the aortic valve"
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(app.post, "/api/v1/session/turns", command)
        try:
            assert stream.started.wait(10), "provider generation never began"
            assert not pending.done()
            assert app.get("/api/v1/turns/stream-test/output") == {
                "schema_version": 1,
                "state": "generating",
                "text": "The aortic",
                "verified": False,
            }
            # A partial draft must not enter the persisted conversation.
            during = app.get("/api/v1/session")
            assert "The aortic" not in json.dumps(during["timeline"])
        finally:
            stream.finish.set()
        receipt = pending.result(timeout=10)
    assert app.get("/api/v1/turns/stream-test/output")["text"] == ""
    timeline = json.dumps(receipt["result"])
    if invalid:
        assert "The aortic valve has three cusps." not in timeline
    else:
        assert receipt["status"] == "completed"
        assert "The aortic valve has three cusps." in timeline
        assert stream.calls == 1
