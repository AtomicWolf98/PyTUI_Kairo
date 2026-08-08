"""Anthropic Messages API adapter."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from kairo_kernel.contracts.content import ReasoningBlock, TextBlock, ToolCallBlock
from kairo_kernel.contracts.enums import ProviderFailureKind, ProviderStreamKind
from kairo_kernel.contracts.identifiers import ToolCallId
from kairo_kernel.contracts.providers import (
    ProviderFailure,
    ProviderProfile,
    ProviderRequest,
    ProviderStreamEvent,
    ProviderUsage,
)
from kairo_kernel.ports.control import CancellationToken
from kairo_kernel.providers.base import ProviderAdapterBase, cancelled_event, provider_error
from kairo_kernel.providers.http import HttpRequest, iter_sse
from kairo_kernel.providers.serialization import (
    anthropic_messages,
    json_body,
    parse_arguments,
    request_limits,
    tool_schema,
)


class AnthropicMessagesAdapter(ProviderAdapterBase):
    provider_name = "anthropic"

    def _auth_headers(self, secret: str) -> tuple[tuple[str, str], ...]:
        return (("x-api-key", secret),) if secret else ()

    async def _probe_request(self, profile: ProviderProfile) -> HttpRequest:
        return HttpRequest(
            _endpoint(profile.base_url),
            await self._headers(profile, (("anthropic-version", "2023-06-01"),)),
            json_body({"model": profile.model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}),
            self._options.timeout_seconds,
        )

    def stream(self, request: ProviderRequest, cancellation: CancellationToken) -> AsyncIterator[ProviderStreamEvent]:
        return self._stream(request, cancellation)

    async def _stream(self, request: ProviderRequest, cancellation: CancellationToken) -> AsyncIterator[ProviderStreamEvent]:
        maximum, temperature = request_limits(request)
        system, messages = anthropic_messages(request.messages)
        payload: dict[str, object] = {
            "model": request.profile.model,
            "messages": messages,
            "max_tokens": maximum,
            "temperature": temperature,
            "stream": True,
        }
        if system:
            payload["system"] = system
        if request.tools:
            payload["tools"] = [tool_schema(tool, anthropic=True) for tool in request.tools]
        http_request = HttpRequest(
            _endpoint(request.profile.base_url),
            await self._headers(request.profile, (("anthropic-version", "2023-06-01"),)),
            json_body(payload),
            self._options.timeout_seconds,
        )
        stream, failure = await self._open_with_retries(http_request, cancellation)
        if failure is not None:
            yield ProviderStreamEvent(ProviderStreamKind.FAILED, failure=failure)
            return
        assert stream is not None
        tools: dict[int, dict[str, str]] = {}
        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0
        finish_reason = ""
        try:
            async for _event_name, data in iter_sse(stream):
                if cancellation.cancelled:
                    yield cancelled_event()
                    return
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    yield provider_error("Anthropic stream contained invalid JSON.")
                    return
                if not isinstance(event, dict):
                    continue
                event_type = str(event.get("type", ""))
                if event_type == "message_start":
                    message = event.get("message")
                    usage = message.get("usage") if isinstance(message, dict) else None
                    if isinstance(usage, dict):
                        input_tokens = int(usage.get("input_tokens", 0))
                        cached_tokens = int(usage.get("cache_read_input_tokens", 0))
                elif event_type == "content_block_start":
                    index = int(event.get("index", 0))
                    block = event.get("content_block")
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tools[index] = {
                            "id": str(block.get("id") or ""),
                            "name": str(block.get("name") or ""),
                            "arguments": "",
                        }
                elif event_type == "content_block_delta":
                    index = int(event.get("index", 0))
                    delta = event.get("delta")
                    if not isinstance(delta, dict):
                        continue
                    delta_type = delta.get("type")
                    if delta_type == "text_delta" and isinstance(delta.get("text"), str):
                        yield ProviderStreamEvent(ProviderStreamKind.CONTENT, content=(TextBlock(delta["text"]),))
                    elif delta_type == "thinking_delta" and isinstance(delta.get("thinking"), str):
                        yield ProviderStreamEvent(ProviderStreamKind.REASONING, content=(ReasoningBlock(delta["thinking"]),))
                    elif delta_type == "input_json_delta" and index in tools:
                        tools[index]["arguments"] += str(delta.get("partial_json") or "")
                elif event_type == "content_block_stop":
                    index = int(event.get("index", 0))
                    call = tools.pop(index, None)
                    if call is not None:
                        try:
                            arguments = parse_arguments(call["arguments"])
                        except (ValueError, json.JSONDecodeError) as error:
                            yield provider_error(f"Invalid tool arguments: {error}")
                            return
                        yield ProviderStreamEvent(
                            ProviderStreamKind.TOOL_CALL,
                            tool_call=ToolCallBlock(ToolCallId(call["id"]), call["name"], arguments),
                        )
                elif event_type == "message_delta":
                    delta = event.get("delta")
                    if isinstance(delta, dict):
                        finish_reason = str(delta.get("stop_reason") or finish_reason)
                    usage = event.get("usage")
                    if isinstance(usage, dict):
                        output_tokens = int(usage.get("output_tokens", output_tokens))
                elif event_type == "message_stop":
                    yield ProviderStreamEvent(
                        ProviderStreamKind.USAGE,
                        usage=ProviderUsage(input_tokens, output_tokens, cached_tokens),
                    )
                    yield ProviderStreamEvent(ProviderStreamKind.COMPLETED, finish_reason=finish_reason or "end_turn")
                    return
                elif event_type == "error":
                    error_payload = event.get("error")
                    error_data = error_payload if isinstance(error_payload, dict) else {}
                    error_type = str(error_data.get("type") or "")
                    kind = ProviderFailureKind.RATE_LIMIT if error_type == "rate_limit_error" else ProviderFailureKind.SERVER
                    yield ProviderStreamEvent(
                        ProviderStreamKind.FAILED,
                        failure=ProviderFailure(
                            kind,
                            str(error_data.get("message") or "Anthropic response failed."),
                            kind is ProviderFailureKind.RATE_LIMIT,
                        ),
                    )
                    return
        except Exception as error:
            yield ProviderStreamEvent(
                ProviderStreamKind.FAILED,
                failure=ProviderFailure(ProviderFailureKind.CONNECTION, f"Error reading Anthropic stream: {error}", True),
            )
            return
        finally:
            await stream.close()
        if cancellation.cancelled:
            yield cancelled_event()
        else:
            yield ProviderStreamEvent(ProviderStreamKind.USAGE, usage=ProviderUsage(input_tokens, output_tokens, cached_tokens))
            yield ProviderStreamEvent(ProviderStreamKind.COMPLETED, finish_reason=finish_reason or "end_turn")


def _endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base if base.endswith("/messages") else f"{base}/messages"
