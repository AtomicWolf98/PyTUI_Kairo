"""Provider wire-format serialization helpers."""

from __future__ import annotations

import json

from kairo_kernel.contracts.content import (
    AudioBlock,
    ContentBlock,
    FileBlock,
    ImageBlock,
    Message,
    ReasoningBlock,
    ResourceBlock,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from kairo_kernel.contracts.enums import MessageRole
from kairo_kernel.contracts.json import JsonObject, freeze_json, thaw_json
from kairo_kernel.contracts.providers import ProviderRequest
from kairo_kernel.contracts.tools import ToolDescriptor


def json_body(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def parse_arguments(value: str) -> JsonObject:
    parsed = json.loads(value or "{}")
    if not isinstance(parsed, dict):
        raise ValueError("Tool arguments must be a JSON object.")
    frozen = freeze_json(parsed)
    if not isinstance(frozen, JsonObject):
        raise ValueError("Tool arguments must be a JSON object.")
    return frozen


def tool_schema(tool: ToolDescriptor, *, anthropic: bool = False) -> dict[str, object]:
    schema = thaw_json(tool.parameters_schema)
    if anthropic:
        return {"name": tool.name, "description": tool.description, "input_schema": schema}
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": schema,
    }


def chat_tool_schema(tool: ToolDescriptor) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": thaw_json(tool.parameters_schema),
        },
    }


def openai_responses_input(messages: tuple[Message, ...]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for message in messages:
        ordinary: list[dict[str, object]] = []
        for block in message.content:
            if isinstance(block, ToolCallBlock):
                items.append(
                    {
                        "type": "function_call",
                        "call_id": str(block.tool_call_id),
                        "name": block.name,
                        "arguments": json.dumps(thaw_json(block.arguments), separators=(",", ":")),
                    }
                )
            elif isinstance(block, ToolResultBlock):
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": str(block.tool_call_id),
                        "output": blocks_text(block.content),
                    }
                )
            else:
                ordinary.append(_responses_block(block))
        if ordinary:
            items.append({"role": message.role.value, "content": ordinary})
    return items


def openai_chat_messages(messages: tuple[Message, ...]) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for message in messages:
        tool_results = [block for block in message.content if isinstance(block, ToolResultBlock)]
        for result in tool_results:
            output.append(
                {
                    "role": "tool",
                    "tool_call_id": str(result.tool_call_id),
                    "name": result.name,
                    "content": blocks_text(result.content),
                }
            )
        tool_calls = [block for block in message.content if isinstance(block, ToolCallBlock)]
        ordinary = [block for block in message.content if not isinstance(block, (ToolCallBlock, ToolResultBlock))]
        if ordinary or tool_calls:
            item: dict[str, object] = {"role": message.role.value, "content": [_chat_block(block) for block in ordinary]}
            if tool_calls:
                item["tool_calls"] = [
                    {
                        "id": str(call.tool_call_id),
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(thaw_json(call.arguments), separators=(",", ":")),
                        },
                    }
                    for call in tool_calls
                ]
            output.append(item)
    return output


def anthropic_messages(messages: tuple[Message, ...]) -> tuple[str, list[dict[str, object]]]:
    systems: list[str] = []
    output: list[dict[str, object]] = []
    for message in messages:
        if message.role is MessageRole.SYSTEM:
            systems.append(blocks_text(message.content))
            continue
        role = "assistant" if message.role is MessageRole.ASSISTANT else "user"
        content: list[dict[str, object]] = []
        for block in message.content:
            content.append(_anthropic_block(block))
        output.append({"role": role, "content": content})
    return "\n\n".join(part for part in systems if part), output


def request_limits(request: ProviderRequest) -> tuple[int, float]:
    return (
        request.max_output_tokens if request.max_output_tokens is not None else request.profile.max_output_tokens,
        request.temperature if request.temperature is not None else request.profile.temperature,
    )


def blocks_text(blocks: tuple[ContentBlock, ...]) -> str:
    values: list[str] = []
    for block in blocks:
        if isinstance(block, (TextBlock, ReasoningBlock)):
            values.append(block.text)
        elif isinstance(block, ImageBlock):
            values.append(block.alt_text or block.uri or "[image]")
        elif isinstance(block, AudioBlock):
            values.append(block.transcript or block.uri or "[audio]")
        elif isinstance(block, (FileBlock, ResourceBlock)):
            values.append(block.name or block.uri)
    return "\n".join(value for value in values if value)


def _data_uri(media_type: str, data: str) -> str:
    return f"data:{media_type};base64,{data}"


def _responses_block(block: ContentBlock) -> dict[str, object]:
    if isinstance(block, TextBlock):
        return {"type": "input_text", "text": block.text}
    if isinstance(block, ReasoningBlock):
        return {"type": "input_text", "text": block.text}
    if isinstance(block, ImageBlock):
        return {"type": "input_image", "image_url": block.uri or _data_uri(block.media_type, block.base64_data)}
    if isinstance(block, AudioBlock):
        return {
            "type": "input_audio",
            "input_audio": {"data": block.base64_data, "format": _audio_format(block.media_type)},
        }
    if isinstance(block, FileBlock):
        value: dict[str, object] = {"type": "input_file", "filename": block.name}
        value["file_url" if block.uri else "file_data"] = block.uri or ""
        return value
    if isinstance(block, ResourceBlock):
        return {"type": "input_file", "file_url": block.uri, "filename": block.name}
    raise TypeError(f"Unsupported Responses content block: {type(block).__name__}")


def _chat_block(block: ContentBlock) -> dict[str, object]:
    if isinstance(block, (TextBlock, ReasoningBlock)):
        return {"type": "text", "text": block.text}
    if isinstance(block, ImageBlock):
        return {"type": "image_url", "image_url": {"url": block.uri or _data_uri(block.media_type, block.base64_data)}}
    if isinstance(block, AudioBlock):
        return {"type": "input_audio", "input_audio": {"data": block.base64_data, "format": _audio_format(block.media_type)}}
    if isinstance(block, (FileBlock, ResourceBlock)):
        return {"type": "file", "file": {"file_url": block.uri}}
    raise TypeError(f"Unsupported Chat Completions content block: {type(block).__name__}")


def _anthropic_block(block: ContentBlock) -> dict[str, object]:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ReasoningBlock):
        return {"type": "thinking", "thinking": block.text}
    if isinstance(block, ImageBlock):
        source = (
            {"type": "url", "url": block.uri}
            if block.uri
            else {"type": "base64", "media_type": block.media_type, "data": block.base64_data}
        )
        return {"type": "image", "source": source}
    if isinstance(block, AudioBlock):
        return {"type": "text", "text": block.transcript or f"[audio: {block.uri}]"}
    if isinstance(block, FileBlock):
        return {"type": "document", "source": {"type": "url", "url": block.uri}, "title": block.name}
    if isinstance(block, ResourceBlock):
        return {"type": "document", "source": {"type": "url", "url": block.uri}, "title": block.name}
    if isinstance(block, ToolCallBlock):
        return {"type": "tool_use", "id": str(block.tool_call_id), "name": block.name, "input": thaw_json(block.arguments)}
    if isinstance(block, ToolResultBlock):
        return {
            "type": "tool_result",
            "tool_use_id": str(block.tool_call_id),
            "content": blocks_text(block.content),
            "is_error": block.status.value != "succeeded",
        }
    raise TypeError(f"Unsupported Anthropic content block: {type(block).__name__}")


def _audio_format(media_type: str) -> str:
    return media_type.rsplit("/", 1)[-1].replace("mpeg", "mp3")
