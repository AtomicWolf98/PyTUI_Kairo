from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from kairo_kernel.contracts.identifiers import SpanId, TraceId
from kairo_kernel.contracts.json import JsonArray, JsonObject
from kairo_kernel.contracts.support import LogRecord, MetricRecord, SpanRecord, TraceContext
from kairo_kernel.services.observability import (
    InMemoryStructuredSink,
    OpenTelemetryAdapter,
    StructuredObservability,
    redact_fields,
    redact_text,
)

NOW = datetime(2026, 8, 8, tzinfo=timezone.utc)


class Backend:
    def __init__(self):
        self.logs = []
        self.metrics = []
        self.spans = []

    async def emit_log(self, record):
        self.logs.append(record)

    async def emit_metric(self, record):
        self.metrics.append(record)

    async def emit_span(self, record):
        self.spans.append(record)


def fields() -> JsonObject:
    return JsonObject.from_pairs(
        ("authorization", "Bearer abc.def.secret"),
        ("nested", JsonObject.from_pairs(("api-key", "sk-abcdefghijk"), ("safe", "visible"))),
        ("items", JsonArray((JsonObject.from_pairs(("refresh_token", "token-value")), "plain"))),
    )


@pytest.mark.asyncio
async def test_structured_logging_recursively_redacts_keys_and_secret_patterns():
    sink = InMemoryStructuredSink()
    service = StructuredObservability(sink)

    await service.log(LogRecord(NOW, "info", "using Bearer raw-token and sk-abcdefghijkl", fields()))

    payload = json.loads(sink.lines[0])
    encoded = sink.lines[0]
    assert payload["type"] == "log"
    assert "raw-token" not in encoded
    assert "sk-abcdefghijkl" not in encoded
    assert "token-value" not in encoded
    assert payload["record"]["fields"]["nested"]["safe"] == "visible"
    assert payload["record"]["fields"]["authorization"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_metrics_and_spans_are_structured_and_otel_receives_sanitized_records():
    sink = InMemoryStructuredSink()
    backend = Backend()
    service = StructuredObservability(sink, OpenTelemetryAdapter(backend))
    trace = TraceContext(TraceId("trace-1"), SpanId("span-1"))

    await service.metric(MetricRecord("queue.depth", 2, NOW, "items", fields()))
    await service.span(SpanRecord(trace, "turn", NOW, NOW, fields()))

    assert [json.loads(line)["type"] for line in sink.lines] == ["metric", "span"]
    assert backend.metrics[0].attributes.get("authorization") == "[REDACTED]"
    assert backend.spans[0].attributes.get("authorization") == "[REDACTED]"


@pytest.mark.asyncio
async def test_optional_otel_adapter_is_noop_when_backend_is_absent():
    adapter = OpenTelemetryAdapter()
    trace = TraceContext(TraceId("trace-1"), SpanId("span-1"))

    await adapter.log(LogRecord(NOW, "info", "ok"))
    await adapter.metric(MetricRecord("x", 1, NOW))
    await adapter.span(SpanRecord(trace, "x", NOW))

    assert not adapter.enabled


def test_redaction_helpers_preserve_safe_values_and_are_deterministic():
    redacted = redact_fields(fields())

    assert redacted.get("authorization") == "[REDACTED]"
    assert redact_text("safe") == "safe"
    assert redact_text("Bearer abc") == "Bearer [REDACTED]"
