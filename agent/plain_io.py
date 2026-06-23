"""Synchronous plain-mode prompt helpers used by the 0.2.3 command wizards.

These helpers wrap stdin/stdout in a way that is shared between ``/provider``,
``/model``, ``/settings``, ``/config validate|backup|restore`` and
``/session`` flows. They intentionally avoid Rich rendering so they can be
called without a Console, and they keep all interactive logic out of
``agent/commands.py`` so the dispatcher stays a thin router.
"""
from __future__ import annotations

import sys
from typing import Callable, List, Optional, Tuple


def _read_line(prompt: str = "") -> str:
    if prompt:
        print(prompt, end="", flush=True)
    try:
        return input().strip()
    except EOFError:
        return ""


def ask(prompt: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]: " if default else ": "
    value = _read_line(prompt + suffix)
    if not value and default is not None:
        return default
    return value


def ask_choice(prompt: str, options: List[str], default: Optional[str] = None) -> str:
    while True:
        rendered = "/".join(options)
        hint = f" ({rendered})"
        if default:
            hint += f" [{default}]"
        value = _read_line(prompt + hint + ": ")
        if not value and default:
            return default
        value = value.lower()
        for option in options:
            if value == option.lower():
                return option
        print(f"Please choose one of: {rendered}")


def ask_int(prompt: str, default: Optional[int] = None, minimum: Optional[int] = None) -> int:
    while True:
        default_text = str(default) if default is not None else ""
        value = _read_line(prompt + (f" [{default_text}]: " if default_text else ": "))
        if not value and default is not None:
            return default
        try:
            result = int(value)
        except ValueError:
            print("Please enter an integer.")
            continue
        if minimum is not None and result < minimum:
            print(f"Please enter a value >= {minimum}.")
            continue
        return result


def ask_float(prompt: str, default: Optional[float] = None, minimum: Optional[float] = None, maximum: Optional[float] = None) -> float:
    while True:
        default_text = str(default) if default is not None else ""
        value = _read_line(prompt + (f" [{default_text}]: " if default_text else ": "))
        if not value and default is not None:
            return default
        try:
            result = float(value)
        except ValueError:
            print("Please enter a number.")
            continue
        if minimum is not None and result < minimum:
            print(f"Please enter a value >= {minimum}.")
            continue
        if maximum is not None and result > maximum:
            print(f"Please enter a value <= {maximum}.")
            continue
        return result


def confirm(prompt: str, default: bool = False) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    value = _read_line(prompt + suffix)
    if not value:
        return default
    return value.lower() in ("y", "yes")


def select(prompt: str, options: List[str], cancel_label: str = "(cancel)") -> int:
    """Display numbered options and return the chosen index, or -1 for cancel."""
    print(prompt)
    for index, option in enumerate(options):
        print(f"  [{index}] {option}")
    print(f"  [c] {cancel_label}")
    while True:
        value = _read_line("Choose: ")
        if not value:
            continue
        if value.lower() in ("c", "cancel"):
            return -1
        try:
            index = int(value)
        except ValueError:
            continue
        if 0 <= index < len(options):
            return index


def banner(message: str, char: str = "-") -> None:
    print()
    print(message)
    print(char * min(60, max(10, len(message))))


def notice(message: str) -> None:
    print(message)


def error(message: str) -> None:
    print(f"Error: {message}", file=sys.stderr)