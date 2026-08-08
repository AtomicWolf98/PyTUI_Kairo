"""OpenAI-compatible Chat Completions adapter."""

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
    chat_tool_schema,
    json_body,
    openai_chat_messages,
    parse_arguments,
    request_limits,
)


class OpenAIChatCompletionsAdapter(ProviderAdapterBase):
    provider_name = "openai_chat"

    async def _probe_request(self, profile: ProviderProfile) -> HttpRequest:
        return HttpRequest(
            _endpoint(profile.base_url),
            await self._headers(profile),
            json_body({"model": profile.model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}),
            self._options.timeout_seconds,
        )

    def stream(self, request: ProviderRequest, cancellation: CancellationToken) -> AsyncIterator[ProviderStreamEvent]:
        return self._stream(request, cancellation)

    async def _stream(self, request: ProviderRequest, cancellation: CancellationToken) -> AsyncIterator[ProviderStreamEvent]:
        maximum, temperature = request_limits(request)
        payload: dict[str, object] = {
            "model": request.profile.model,
            "messages": openai_chat_messages(request.messages),
            "max_tokens": maximum,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if request.tools:
            payload["tools"] = [chat_tool_schema(tool) for tool in request.tools]
        http_request = HttpRequest(
            _endpoint(request.profile.base_url),
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
        finish_reason = ""
        calls_emitted = False
        try:
            async for _event_name, data in iter_sse(stream):
                if cancellation.cancelled:
                    yield cancelled_event()
                    return
                if data == "[DONE]":
                    if not calls_emitted:
                        tool_events = _tool_events(calls)
                        for event in tool_events:
                            yield event
                        if _has_failure(tool_events):
                            return
                    yield ProviderStreamEvent(ProviderStreamKind.COMPLETED, finish_reason=finish_reason or "stop")
                    return
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    yield provider_error("Chat Completions stream contained invalid JSON.")
                    return
                if not isinstance(chunk, dict):
                    continue
                if isinstance(chunk.get("error"), dict):
                    yield provider_error(str(chunk["error"].get("message") or "Chat completion failed."))
                    return
                usage = chunk.get("usage")
                if isinstance(usage, dict):
                    details = usage.get("prompt_tokens_details")
                    details = details if isinstance(details, dict) else {}
                    yield ProviderStreamEvent(
                        ProviderStreamKind.USAGE,
                        usage=ProviderUsage(
                            int(usage.get("prompt_tokens", 0)),
                            int(usage.get("completion_tokens", 0)),
                            int(details.get("cached_tokens", 0)),
                        ),
                    )
                choices = chunk.get("choices")
                if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                    continue
                choice = choices[0]
                finish_reason = str(choice.get("finish_reason") or finish_reason)
                delta = choice.get("delta")
                if not isinstance(delta, dict):
                    continue
                content = delta.get("content")
                if isinstance(content, str) and content:
                    yield ProviderStreamEvent(ProviderStreamKind.CONTENT, content=(TextBlock(content),))
                reasoning = delta.get("reasoning_content")
                if isinstance(reasoning, str) and reasoning:
                    yield ProviderStreamEvent(ProviderStreamKind.REASONING, content=(ReasoningBlock(reasoning),))
                tool_calls = delta.get("tool_calls")
                if isinstance(tool_calls, list):
                    _merge_calls(calls, tool_calls)
                if finish_reason == "tool_calls" and calls and not calls_emitted:
                    tool_events = _tool_events(calls)
                    for event in tool_events:
                        yield event
                    if _has_failure(tool_events):
                        return
                    calls_emitted = True
        except Exception as error:
            yield ProviderStreamEvent(
                ProviderStreamKind.FAILED,
                failure=ProviderFailure(ProviderFailureKind.CONNECTION, f"Error reading Chat Completions stream: {error}", True),
            )
            return
        finally:
            await stream.close()
        if cancellation.cancelled:
            yield cancelled_event()
        else:
            if not calls_emitted:
                tool_events = _tool_events(calls)
                for event in tool_events:
                    yield event
                if _has_failure(tool_events):
                    return
            yield ProviderStreamEvent(ProviderStreamKind.COMPLETED, finish_reason=finish_reason or "stop")


def _endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base if base.endswith("/chat/completions") else f"{base}/chat/completions"


def _merge_calls(calls: dict[int, dict[str, str]], updates: list[object]) -> None:
    for update in updates:
        if not isinstance(update, dict):
            continue
        index = int(update.get("index", 0))
        call = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
        if update.get("id"):
            call["id"] = str(update["id"])
        function = update.get("function")
        if isinstance(function, dict):
            if function.get("name"):
                call["name"] = str(function["name"])
            if function.get("arguments"):
                call["arguments"] += str(function["arguments"])


def _tool_events(calls: dict[int, dict[str, str]]) -> list[ProviderStreamEvent]:
    events: list[ProviderStreamEvent] = []
    for index in sorted(calls):
        call = calls[index]
        try:
            arguments = parse_arguments(call["arguments"])
        except (ValueError, json.JSONDecodeError) as error:
            events.append(provider_error(f"Invalid tool arguments: {error}"))
            break
        events.append(
            ProviderStreamEvent(
                ProviderStreamKind.TOOL_CALL,
                tool_call=ToolCallBlock(ToolCallId(call["id"]), call["name"], arguments),
            )
        )
    return events


def _has_failure(events: list[ProviderStreamEvent]) -> bool:
    return any(event.kind is ProviderStreamKind.FAILED for event in events)
