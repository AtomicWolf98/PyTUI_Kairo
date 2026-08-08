from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from kairo_kernel.contracts.content import (
    AudioBlock,
    FileBlock,
    ImageBlock,
    Message,
    ResourceBlock,
    TextBlock,
    ToolCallBlock,
)
from kairo_kernel.contracts.enums import ErrorCode, MessageKind, MessageRole, ProviderFailureKind, ProviderStreamKind
from kairo_kernel.contracts.identifiers import MessageId, ProfileId, ResourceId
from kairo_kernel.contracts.json import JsonObject, thaw_json
from kairo_kernel.contracts.providers import ProviderProfile, ProviderRequest
from kairo_kernel.contracts.tools import ToolDescriptor
from kairo_kernel.providers import (
    AdapterOptions,
    AnthropicMessagesAdapter,
    HttpRequest,
    OpenAIChatCompletionsAdapter,
    OpenAIResponsesAdapter,
)
from kairo_kernel.providers.base import failure_from_http
from kairo_kernel.providers.http import iter_sse

FIXTURES = Path(__file__).parents[2] / "fixtures" / "providers"


class Cancellation:
    def __init__(self, cancelled: bool = False):
        self._cancelled = cancelled
        self._event = asyncio.Event()
        if cancelled:
            self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True
        self._event.set()

    async def wait(self) -> None:
        await self._event.wait()


class Secret:
    async def resolve(self, secret_id: str) -> str:
        return f"secret-for-{secret_id}"


class MockStream:
    def __init__(self, body: bytes, status_code: int = 200, chunks: tuple[bytes, ...] | None = None):
        self._body = body
        self._chunks = chunks or (body,)
        self._status_code = status_code
        self.closed = False

    @property
    def status_code(self) -> int:
        return self._status_code

    @property
    def headers(self) -> tuple[tuple[str, str], ...]:
        return ()

    async def read(self) -> bytes:
        return self._body

    async def close(self) -> None:
        self.closed = True

    async def iter_bytes(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            await asyncio.sleep(0)
            yield chunk


class MockTransport:
    def __init__(self, *responses: MockStream | Exception):
        self.responses = list(responses)
        self.requests: list[HttpRequest] = []

    async def open(self, request: HttpRequest) -> MockStream:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class CancellingStream(MockStream):
    def __init__(self, chunks: tuple[bytes, ...], cancellation: Cancellation):
        super().__init__(b"".join(chunks), chunks=chunks)
        self._cancellation = cancellation

    async def iter_bytes(self) -> AsyncIterator[bytes]:
        yield self._chunks[0]
        self._cancellation.cancel()
        for chunk in self._chunks[1:]:
            yield chunk


def profile(provider: str) -> ProviderProfile:
    return ProviderProfile(
        ProfileId(f"{provider}/model"),
        "Model",
        provider,
        "model-1",
        "https://provider.example/v1",
        128_000,
        4_000,
        0.2,
        "key-1",
    )


def request(provider: str) -> ProviderRequest:
    message = Message(
        MessageId("message-1"),
        MessageRole.USER,
        MessageKind.CHAT,
        (
            TextBlock("hello"),
            ImageBlock("image/png", base64_data="aW1hZ2U="),
            AudioBlock("audio/wav", base64_data="YXVkaW8="),
            FileBlock("guide.pdf", "application/pdf", "https://files.example/guide.pdf"),
            ResourceBlock(ResourceId("resource-1"), "https://files.example/resource.txt", "resource"),
        ),
    )
    tool = ToolDescriptor(
        "read_file",
        "Read a file",
        JsonObject.from_pairs(("type", "object")),
        ("workspace:read",),
    )
    return ProviderRequest(profile(provider), (message,), (tool,))


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


async def collect(adapter, provider: str):
    return [event async for event in adapter.stream(request(provider), Cancellation())]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adapter_type", "provider", "fixture_name"),
    [
        (OpenAIResponsesAdapter, "openai_responses", "openai_responses.sse"),
        (OpenAIChatCompletionsAdapter, "openai_chat", "openai_chat.sse"),
        (AnthropicMessagesAdapter, "anthropic", "anthropic.sse"),
    ],
)
async def test_streams_normalize_content_reasoning_tools_usage_and_completion(adapter_type, provider, fixture_name):
    transport = MockTransport(MockStream(fixture(fixture_name)))
    adapter = adapter_type((profile(provider),), transport=transport, secrets=Secret())

    events = await collect(adapter, provider)

    assert [event.kind for event in events] == [
        ProviderStreamKind.REASONING if provider != "openai_responses" else ProviderStreamKind.CONTENT,
        ProviderStreamKind.CONTENT if provider != "openai_responses" else ProviderStreamKind.REASONING,
        ProviderStreamKind.TOOL_CALL,
        ProviderStreamKind.USAGE,
        ProviderStreamKind.COMPLETED,
    ]
    call = next(event.tool_call for event in events if event.tool_call is not None)
    assert call == ToolCallBlock(call.tool_call_id, "read_file", call.arguments)
    assert thaw_json(call.arguments) == {"path": "README.md"}
    usage = next(event.usage for event in events if event.usage is not None)
    assert (usage.input_tokens, usage.output_tokens, usage.cached_tokens) == (11, 7, 3)
    assert transport.requests[0].url.endswith("/responses" if provider == "openai_responses" else "/messages" if provider == "anthropic" else "/chat/completions")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adapter_type", "provider"),
    [
        (OpenAIResponsesAdapter, "openai_responses"),
        (OpenAIChatCompletionsAdapter, "openai_chat"),
        (AnthropicMessagesAdapter, "anthropic"),
    ],
)
async def test_requests_include_auth_tools_limits_and_multimodal_blocks(adapter_type, provider):
    terminal = b"data: [DONE]\n\n" if provider != "anthropic" else b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    transport = MockTransport(MockStream(terminal))
    adapter = adapter_type((profile(provider),), transport=transport, secrets=Secret())

    await collect(adapter, provider)

    sent = transport.requests[0]
    payload = json.loads(sent.body)
    headers = dict(sent.headers)
    assert headers["x-api-key" if provider == "anthropic" else "authorization"].endswith("secret-for-key-1")
    assert payload["model"] == "model-1"
    assert payload["tools"][0]["name" if provider != "openai_chat" else "function"]
    wire_messages = payload["input"] if provider == "openai_responses" else payload["messages"]
    assert "image" in json.dumps(wire_messages)
    assert "guide.pdf" in json.dumps(wire_messages)


@pytest.mark.asyncio
async def test_profile_resolution_uses_explicit_role_and_default_profiles():
    first = profile("openai_chat")
    second = ProviderProfile(ProfileId("openai_chat/second"), "Second", "openai_chat", "model-2", first.base_url, 1, 1, 0)
    adapter = OpenAIChatCompletionsAdapter(
        (first, second),
        transport=MockTransport(),
        role_profiles={"plan": second.profile_id},
    )
    assert (await adapter.resolve_profile(None, "chat")).value == first
    assert (await adapter.resolve_profile(None, "plan")).value == second
    assert (await adapter.resolve_profile(second.profile_id, "chat")).value == second
    assert not (await adapter.resolve_profile(ProfileId("missing"), "chat")).ok


@pytest.mark.asyncio
async def test_retry_then_success_and_probe_use_mock_transport_only():
    rate = MockStream(b'{"error":{"message":"rate limited"}}', 429)
    ok = MockStream(fixture("openai_chat.sse"))
    probe = MockStream(b"{}")
    transport = MockTransport(rate, ok, probe)
    adapter = OpenAIChatCompletionsAdapter(
        (profile("openai_chat"),),
        transport=transport,
        options=AdapterOptions(max_retries=1, retry_base_delay=0),
    )
    events = await collect(adapter, "openai_chat")
    assert events[-1].kind is ProviderStreamKind.COMPLETED
    result = await adapter.probe(ProfileId("openai_chat/model"))
    assert result.ok
    assert len(transport.requests) == 3


@pytest.mark.asyncio
async def test_connection_exhaustion_and_precancel_are_normalized_failures():
    adapter = OpenAIResponsesAdapter(
        (profile("openai_responses"),),
        transport=MockTransport(OSError("offline")),
        options=AdapterOptions(max_retries=0),
    )
    events = await collect(adapter, "openai_responses")
    assert events[0].failure is not None
    assert events[0].failure.kind is ProviderFailureKind.CONNECTION

    cancelled = Cancellation(True)
    transport = MockTransport(MockStream(fixture("openai_responses.sse")))
    adapter = OpenAIResponsesAdapter((profile("openai_responses"),), transport=transport)
    events = [event async for event in adapter.stream(request("openai_responses"), cancelled)]
    assert events[0].failure is not None
    assert events[0].failure.kind is ProviderFailureKind.CANCELLED
    assert transport.requests == []


@pytest.mark.parametrize(
    ("status", "message", "kind", "retryable"),
    [
        (401, "bad key", ProviderFailureKind.AUTH, False),
        (429, "slow down", ProviderFailureKind.RATE_LIMIT, True),
        (503, "unavailable", ProviderFailureKind.SERVER, True),
        (400, "context length exceeded", ProviderFailureKind.CONTEXT, False),
        (422, "bad request", ProviderFailureKind.CLIENT, False),
    ],
)
def test_http_failures_are_classified(status, message, kind, retryable):
    failure = failure_from_http(status, json.dumps({"error": {"message": message}}).encode())
    assert (failure.kind, failure.retryable, failure.status_code) == (kind, retryable, status)


@pytest.mark.asyncio
async def test_sse_parser_handles_arbitrary_chunk_boundaries_and_multiline_data():
    stream = MockStream(b"", chunks=(b"event: delta\nda", b"ta: first\n", b"data: second\n\n"))

    frames = [frame async for frame in iter_sse(stream)]

    assert frames == [("delta", "first\nsecond")]


@pytest.mark.asyncio
async def test_cancellation_during_stream_emits_cancelled_and_closes_transport():
    cancellation = Cancellation()
    stream = CancellingStream(
        (
            b'data: {"type":"response.output_text.delta","delta":"first"}\n\n',
            b'data: {"type":"response.output_text.delta","delta":"second"}\n\n',
        ),
        cancellation,
    )
    adapter = OpenAIResponsesAdapter((profile("openai_responses"),), transport=MockTransport(stream))

    events = [event async for event in adapter.stream(request("openai_responses"), cancellation)]

    assert [event.kind for event in events] == [ProviderStreamKind.CONTENT, ProviderStreamKind.FAILED]
    assert events[-1].failure is not None
    assert events[-1].failure.kind is ProviderFailureKind.CANCELLED
    assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adapter_type", "provider", "body"),
    [
        (
            OpenAIResponsesAdapter,
            "openai_responses",
            b'data: {"type":"error","error":{"message":"provider failed"}}\n\n',
        ),
        (
            OpenAIChatCompletionsAdapter,
            "openai_chat",
            b'data: {"error":{"message":"provider failed"}}\n\n',
        ),
        (
            AnthropicMessagesAdapter,
            "anthropic",
            b'event: error\ndata: {"type":"error","error":{"type":"api_error","message":"provider failed"}}\n\n',
        ),
    ],
)
async def test_embedded_provider_errors_are_terminal_failures(adapter_type, provider, body):
    stream = MockStream(body)
    adapter = adapter_type((profile(provider),), transport=MockTransport(stream))

    events = await collect(adapter, provider)

    assert [event.kind for event in events] == [ProviderStreamKind.FAILED]
    assert events[0].failure is not None
    assert events[0].failure.message == "provider failed"
    assert stream.closed


@pytest.mark.asyncio
async def test_malformed_chat_tool_arguments_fail_instead_of_disappearing():
    body = (
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","function":'
        b'{"name":"read_file","arguments":"{"}}]},"finish_reason":"tool_calls"}]}\n\n'
        b"data: [DONE]\n\n"
    )
    adapter = OpenAIChatCompletionsAdapter(
        (profile("openai_chat"),),
        transport=MockTransport(MockStream(body)),
    )

    events = await collect(adapter, "openai_chat")

    assert [event.kind for event in events] == [ProviderStreamKind.FAILED]
    assert events[0].failure is not None
    assert "Invalid tool arguments" in events[0].failure.message


@pytest.mark.asyncio
async def test_probe_maps_auth_failure_to_kernel_error_and_closes_response():
    response = MockStream(b'{"error":{"message":"bad key"}}', 401)
    adapter = OpenAIResponsesAdapter(
        (profile("openai_responses"),),
        transport=MockTransport(response),
    )

    result = await adapter.probe(ProfileId("openai_responses/model"))

    assert not result.ok
    assert result.error is not None
    assert result.error.code is ErrorCode.PROVIDER_AUTH
    assert response.closed
