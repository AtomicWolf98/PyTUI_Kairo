"""Structured, recursively redacted observability with optional OTel export."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Protocol

from kairo_kernel.contracts.json import JsonArray, JsonMember, JsonObject, JsonValue, thaw_json
from kairo_kernel.contracts.support import LogRecord, MetricRecord, SpanRecord, TraceContext

REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "secret_id",
    "token",
}
_BEARER = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/-]+={0,2}")
_OPENAI_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")


class StructuredSink(Protocol):
    async def write(self, line: str) -> None: ...


class OpenTelemetryBackend(Protocol):
    async def emit_log(self, record: LogRecord) -> None: ...

    async def emit_metric(self, record: MetricRecord) -> None: ...

    async def emit_span(self, record: SpanRecord) -> None: ...


class OpenTelemetryAdapter:
    """Optional bridge; absence of a backend is an intentional no-op."""

    def __init__(self, backend: OpenTelemetryBackend | None = None):
        self.backend = backend

    @property
    def enabled(self) -> bool:
        return self.backend is not None

    async def log(self, record: LogRecord) -> None:
        if self.backend is not None:
            await self.backend.emit_log(record)

    async def metric(self, record: MetricRecord) -> None:
        if self.backend is not None:
            await self.backend.emit_metric(record)

    async def span(self, record: SpanRecord) -> None:
        if self.backend is not None:
            await self.backend.emit_span(record)


class StructuredObservability:
    def __init__(self, sink: StructuredSink, otel: OpenTelemetryAdapter | None = None):
        self.sink = sink
        self.otel = otel or OpenTelemetryAdapter()

    async def log(self, record: LogRecord) -> None:
        sanitized = replace(record, message=redact_text(record.message), fields=redact_fields(record.fields))
        payload: dict[str, object] = {
            "timestamp": sanitized.timestamp.isoformat(),
            "level": sanitized.level,
            "message": sanitized.message,
            "fields": thaw_json(sanitized.fields),
        }
        if sanitized.trace is not None:
            payload["trace"] = _trace(sanitized.trace)
        await self._write("log", payload)
        await self.otel.log(sanitized)

    async def metric(self, record: MetricRecord) -> None:
        sanitized = replace(record, attributes=redact_fields(record.attributes))
        await self._write(
            "metric",
            {
                "name": sanitized.name,
                "value": sanitized.value,
                "timestamp": sanitized.timestamp.isoformat(),
                "unit": sanitized.unit,
                "attributes": thaw_json(sanitized.attributes),
            },
        )
        await self.otel.metric(sanitized)

    async def span(self, record: SpanRecord) -> None:
        sanitized = replace(record, attributes=redact_fields(record.attributes))
        await self._write(
            "span",
            {
                "context": _trace(sanitized.context),
                "name": sanitized.name,
                "started_at": sanitized.started_at.isoformat(),
                "finished_at": sanitized.finished_at.isoformat() if sanitized.finished_at is not None else None,
                "attributes": thaw_json(sanitized.attributes),
            },
        )
        await self.otel.span(sanitized)

    async def _write(self, event_type: str, payload: dict[str, object]) -> None:
        line = json.dumps(
            {"type": event_type, "record": payload},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        await self.sink.write(line)


class InMemoryStructuredSink:
    def __init__(self) -> None:
        self.lines: list[str] = []

    async def write(self, line: str) -> None:
        self.lines.append(line)


def redact_fields(fields: JsonObject) -> JsonObject:
    redacted = _redact_value(fields, "")
    if not isinstance(redacted, JsonObject):
        raise TypeError("Redacted structured fields must remain a JSON object.")
    return redacted


def redact_text(value: str) -> str:
    return _OPENAI_KEY.sub(REDACTED, _BEARER.sub(f"Bearer {REDACTED}", value))


def _redact_value(value: JsonValue, key: str) -> JsonValue:
    if _sensitive(key):
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, JsonArray):
        return JsonArray(tuple(_redact_value(item, key) for item in value.items))
    if isinstance(value, JsonObject):
        return JsonObject(tuple(JsonMember(item.key, _redact_value(item.value, item.key)) for item in value.items))
    return value


def _sensitive(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return lowered in _SENSITIVE_KEYS or lowered.endswith("_token") or lowered.endswith("_secret")


def _trace(trace: TraceContext) -> dict[str, str | None]:
    return {
        "trace_id": str(trace.trace_id),
        "span_id": str(trace.span_id),
        "parent_span_id": str(trace.parent_span_id) if trace.parent_span_id is not None else None,
    }
