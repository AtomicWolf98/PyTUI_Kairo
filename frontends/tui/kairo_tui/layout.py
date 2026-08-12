"""Responsive breakpoints per tui_plan.md."""

from __future__ import annotations

from enum import Enum


class Breakpoint(str, Enum):
    FULL = "full"      # >= 140 columns: chat + optional context drawer
    NARROW = "narrow"  # 100-139: chat, drawer opens as a controlled overlay
    OVERLAY = "overlay"  # 80-99: chat-only default, every utility is a modal
    COMPAT = "compat"  # <80 wide or <24 tall: compact but still usable chat


def responsive_layout(size: tuple[int, int]) -> Breakpoint:
    width, height = size
    if width < 80 or height < 24:
        return Breakpoint.COMPAT
    if width >= 140:
        return Breakpoint.FULL
    if width >= 100:
        return Breakpoint.NARROW
    return Breakpoint.OVERLAY
