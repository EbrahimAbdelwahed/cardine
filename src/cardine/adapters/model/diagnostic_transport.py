"""Observe the existing HTTP transport without changing its error semantics."""

from collections.abc import Mapping

from cardine.diagnostics.turn_trace import trace_operation
from study_agent.adapters.model.openai_compatible import HttpResponse, HttpTransport


class DiagnosticHttpTransport:
    def __init__(self, delegate: HttpTransport) -> None:
        self._delegate = delegate

    def post(
        self,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> HttpResponse:
        with trace_operation("provider_http") as operation:
            response = self._delegate.post(url, headers, body, timeout_seconds)
            if isinstance(response, HttpResponse):
                operation.observe_http_status(response.status)
            return response
