"""Typed kernel business command contracts."""

from __future__ import annotations

from dataclasses import dataclass

from kairo_kernel.contracts.identifiers import SessionId
from kairo_kernel.contracts.json import Contract, JsonObject


@dataclass(frozen=True)
class CommandArgument(Contract):
    name: str
    required: bool = False
    greedy: bool = False
    value_hint: str = ""


@dataclass(frozen=True)
class KernelCommand(Contract):
    name: str
    summary: str
    help: str
    arguments: tuple[CommandArgument, ...] = ()
    mutates: bool = False
    needs_session: bool = False


@dataclass(frozen=True)
class ParsedCommand(Contract):
    name: str
    arguments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.startswith("/") or len(self.name) < 2:
            raise ValueError("Command names must start with '/'.")


@dataclass(frozen=True)
class CommandOutcome(Contract):
    command: str
    message: str
    session_id: SessionId | None = None
    data: JsonObject = JsonObject()
