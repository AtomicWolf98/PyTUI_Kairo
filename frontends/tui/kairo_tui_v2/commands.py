"""TUI-local display commands merged into the palette with kernel commands."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LocalCommand:
    name: str
    description: str


LOCAL_COMMANDS: tuple[LocalCommand, ...] = (
    LocalCommand("sessions", "Open the session picker"),
    LocalCommand("models", "Open the model picker"),
    LocalCommand("sidebar", "Toggle the context sidebar"),
    LocalCommand("settings", "Open settings"),
    LocalCommand("connect", "Connect a model"),
    LocalCommand("memory", "Open memory"),
    LocalCommand("extensions", "Open extensions"),
    LocalCommand("doctor", "Open diagnostics"),
)
