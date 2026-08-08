"""OpenAI Responses API adapter."""

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
    json_body,
    openai_responses_input,
    parse_arguments,
    request_limits,
    tool_schema,
)


class OpenAIResponsesAdapter(ProviderAdapterBase):
    provider_name = "openai_responses"

    async def _probe_request(self, profile: ProviderProfile) -> HttpRequest:
        return HttpRequest(
            _endpoint(profile.base_url, "responses"),
            await self._headers(profile),
            json_body({"model": profile.model, "input": "ping", "max_output_tokens": 1, "stream": False}),
            self._options.timeout_seconds,
        )

    def stream(self, request: ProviderRequest, cancellation: CancellationToken) -> AsyncIterator[ProviderStreamEvent]:
        return self._stream(request, cancellation)

    async def _stream(self, request: ProviderRequest, cancellation: CancellationToken) -> AsyncIterator[ProviderStreamEvent]:
        maximum, temperature = request_limits(request)
        payload: dict[str, object] = {
            "model": request.profile.model,
            "input": openai_responses_input(request.messages),
            "max_output_tokens": maximum,
            "temperature": temperature,
            "stream": True,
        }
        if request.tools:
            payload["tools"] = [tool_schema(tool) for tool in request.tools]
        http_request = HttpRequest(
            _endpoint(request.profile.base_url, "responses"),
            await self._headers(request.profile),
            json_body(payload),
            self._options.timeout_seconds,
        )
        stream, failure = await self._open_with_retries(http_request, cancellation)
        if failure is not None:
            yield ProviderStreamEvent(ProviderStreamKind.FAILED, failure=failure)
            return
        assert stream is not None
        calls: dict[int, dict[str, str]] = {}
        emitted: set[int] = set()
        completed = False
        try:
            async for _event_name, data in iter_sse(stream):
                if cancellation.cancelled:
                    yield cancelled_event()
                    return
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    yield provider_error("OpenAI Responses stream contained invalid JSON.")
                    return
                if not isinstance(event, dict):
                    continue
                event_type = str(event.get("type", ""))
                if event_type == "response.output_text.delta":
                    delta = event.get("delta")
                    if isinstance(delta, str) and delta:
                        yield ProviderStreamEvent(ProviderStreamKind.CONTENT, content=(TextBlock(delta),))
                elif event_type in ("response.reasoning_text.delta", "response.reasoning_summary_text.delta"):
                    delta = event.get("delta")
                    if isinstance(delta, str) and delta:
                        yield ProviderStreamEvent(ProviderStreamKind.REASONING, content=(ReasoningBlock(delta),))
                elif event_type == "response.output_item.added":
                    index = _integer(event.get("output_index"))
                    item = event.get("item")
                    if isinstance(item, dict) and item.get("type") == "function_call":
                        calls[index] = {
                            "id": str(item.get("call_id") or item.get("id") or ""),
                            "name": str(item.get("name") or ""),
                            "arguments": str(item.get("arguments") or ""),
                        }
                elif event_type == "response.function_call_arguments.delta":
                    index = _integer(event.get("output_index"))
                    call = calls.setdefault(index, {"id": str(event.get("item_id") or ""), "name": "", "arguments": ""})
                    call["arguments"] += str(event.get("delta") or "")
                elif event_type in ("response.function_call_arguments.done", "response.output_item.done"):
                    index = _integer(event.get("output_index"))
                    item = event.get("item")
                    call = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                    if isinstance(item, dict) and item.get("type") == "function_call":
                        call.update(
                            id=str(item.get("call_id") or item.get("id") or call["id"]),
                            name=str(item.get("name") or call["name"]),
                            arguments=str(item.get("arguments") or call["arguments"]),
                        )
                    elif event_type.endswith("arguments.done"):
                        call["arguments"] = str(event.get("arguments") or call["arguments"])
                    if index not in emitted and call["id"] and call["name"]:
                        try:
                            arguments = parse_arguments(call["arguments"])
                        except (ValueError, json.JSONDecodeError) as error:
                            yield provider_error(f"Invalid tool arguments: {error}")
                            return
                        emitted.add(index)
                        yield ProviderStreamEvent(
                            ProviderStreamKind.TOOL_CALL,
                            tool_call=ToolCallBlock(ToolCallId(call["id"]), call["name"], arguments),
                        )
                elif event_type == "response.completed":
                    response = event.get("response")
                    response = response if isinstance(response, dict) else {}
                    usage = response.get("usage")
                    if isinstance(usage, dict):
                        yield ProviderStreamEvent(ProviderStreamKind.USAGE, usage=_usage(usage))
                    yield ProviderStreamEvent(
                        ProviderStreamKind.COMPLETED,
                        finish_reason=str(response.get("status") or "completed"),
                    )
                    completed = True
                elif event_type in ("response.failed", "error"):
                    error_payload = event.get("error")
                    message = (
                        str(error_payload.get("message"))
                        if isinstance(error_payload, dict)
                        else str(error_payload or "OpenAI response failed.")
                    )
                    yield ProviderStreamEvent(
                        ProviderStreamKind.FAILED,
                        failure=ProviderFailure(ProviderFailureKind.SERVER, message, False),
                    )
                    return
        except Exception as error:
            yield ProviderStreamEvent(
                ProviderStreamKind.FAILED,
                failure=ProviderFailure(ProviderFailureKind.CONNECTION, f"Error reading Responses stream: {error}", True),
            )
            return
        finally:
            await stream.close()
        if cancellation.cancelled:
            yield cancelled_event()
        elif not completed:
            yield ProviderStreamEvent(ProviderStreamKind.COMPLETED, finish_reason="completed")


def _endpoint(base_url: str, resource: str) -> str:
    base = base_url.rstrip("/")
    return base if base.endswith(f"/{resource}") else f"{base}/{resource}"


def _integer(value: object) -> int:
    return int(value) if isinstance(value, (int, str)) and str(value).isdigit() else 0


def _usage(value: dict[object, object]) -> ProviderUsage:
    details = value.get("input_tokens_details")
    details = details if isinstance(details, dict) else {}
    return ProviderUsage(
        _integer(value.get("input_tokens", 0)),
        _integer(value.get("output_tokens", 0)),
        _integer(details.get("cached_tokens", 0)),
    )
