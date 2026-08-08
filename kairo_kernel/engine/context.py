"""Provider-neutral context estimation and deterministic history packing."""

from __future__ import annotations

from dataclasses import dataclass

from kairo_kernel.contracts.content import Message, TextBlock
from kairo_kernel.contracts.enums import MessageKind, MessageRole
from kairo_kernel.contracts.identifiers import MessageId
from kairo_kernel.contracts.tools import ToolDescriptor


def estimate_context_tokens(messages: tuple[Message, ...], tools: tuple[ToolDescriptor, ...] = ()) -> int:
    total = 3
    for message in messages:
        total += 4 + _estimate_text(message.to_json())
    for tool in tools:
        total += _estimate_text(tool.to_json())
    return max(0, total)


def _estimate_text(text: str) -> int:
    cjk = sum(1 for character in text if ord(character) > 255)
    ascii_length = len(text) - cjk
    return cjk + ((ascii_length + 3) // 4 if ascii_length else 0)


@dataclass(frozen=True)
class PackedContext:
    messages: tuple[Message, ...]
    compression_count: int
    changed: bool


class ContextPacker:
    def __init__(self, trigger_percent: float = 85.0, target_percent: float = 60.0, preserve_turns: int = 4):
        self.trigger_percent = min(max(trigger_percent, 1.0), 100.0)
        self.target_percent = min(max(target_percent, 1.0), self.trigger_percent)
        self.preserve_turns = max(0, preserve_turns)

    def needs_compaction(
        self,
        messages: tuple[Message, ...],
        tools: tuple[ToolDescriptor, ...],
        context_window: int,
    ) -> bool:
        return estimate_context_tokens(messages, tools) >= int(max(1, context_window) * self.trigger_percent / 100.0)

    def source_and_retained(
        self,
        messages: tuple[Message, ...],
        *,
        emergency: bool = False,
    ) -> tuple[tuple[Message, ...], tuple[Message, ...]]:
        prefix, turns = _split_turns(messages)
        preserve = min(len(turns), self.preserve_turns)
        if emergency and len(turns) <= preserve:
            preserve = min(1, len(turns))
        old = turns[:-preserve] if preserve else turns
        recent = turns[-preserve:] if preserve else ()
        source = tuple(message for turn in old for message in turn)
        retained = prefix + tuple(message for turn in recent for message in turn)
        return source, retained

    def insert_summary(
        self,
        retained: tuple[Message, ...],
        summary: str,
        message_id: MessageId,
    ) -> tuple[Message, ...]:
        prefix, turns = _split_turns(retained)
        summary_message = Message(
            message_id,
            MessageRole.SYSTEM,
            MessageKind.SUMMARY,
            (TextBlock(summary.strip()),),
        )
        return prefix + (summary_message,) + tuple(message for turn in turns for message in turn)

    def trim_to_target(
        self,
        messages: tuple[Message, ...],
        tools: tuple[ToolDescriptor, ...],
        context_window: int,
        *,
        minimum_turns: int = 1,
    ) -> tuple[Message, ...]:
        budget = int(max(1, context_window) * self.target_percent / 100.0)
        prefix, turns = _split_turns(messages)
        mutable = list(turns)
        minimum = min(len(mutable), max(1, minimum_turns))
        while len(mutable) > minimum:
            candidate = prefix + tuple(message for turn in mutable for message in turn)
            if estimate_context_tokens(candidate, tools) <= budget:
                return candidate
            mutable.pop(0)
        return prefix + tuple(message for turn in mutable for message in turn)


def _split_turns(messages: tuple[Message, ...]) -> tuple[tuple[Message, ...], tuple[tuple[Message, ...], ...]]:
    prefix: list[Message] = []
    turns: list[list[Message]] = []
    current: list[Message] | None = None
    for message in messages:
        if message.role is MessageRole.USER:
            current = [message]
            turns.append(current)
        elif current is None:
            prefix.append(message)
        else:
            current.append(message)
    return tuple(prefix), tuple(tuple(turn) for turn in turns)
