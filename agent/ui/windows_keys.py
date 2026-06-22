"""Preserve modified Enter keys dropped by Textual's Windows input monitor."""

from __future__ import annotations

import os
from typing import Any


VK_RETURN = 0x0D
RIGHT_ALT_PRESSED = 0x0001
LEFT_ALT_PRESSED = 0x0002
RIGHT_CTRL_PRESSED = 0x0004
LEFT_CTRL_PRESSED = 0x0008
SHIFT_PRESSED = 0x0010


def encode_windows_key(character: str, virtual_key: int, control_state: int) -> str:
    """Encode modified Enter using the Kitty protocol understood by Textual."""
    if virtual_key != VK_RETURN:
        return character

    modifier = 0
    if control_state & SHIFT_PRESSED:
        modifier |= 1
    if control_state & (LEFT_ALT_PRESSED | RIGHT_ALT_PRESSED):
        modifier |= 2
    if control_state & (LEFT_CTRL_PRESSED | RIGHT_CTRL_PRESSED):
        modifier |= 4
    if not modifier:
        return character

    # Kitty modifiers are encoded as 1 + the shift/alt/ctrl bit mask.
    return f"\x1b[13;{modifier + 1}u"


def install_windows_modified_enter_support() -> bool:
    """Patch Textual's Windows monitor before it starts reading input."""
    if os.name != "nt":
        return False

    from textual import constants
    from textual._xterm_parser import XTermParser
    from textual.drivers import win32

    if getattr(win32.EventMonitor, "_kairo_modified_enter", False):
        return True

    class KairoEventMonitor(win32.EventMonitor):
        _kairo_modified_enter = True

        def run(self) -> None:
            exit_requested = self.exit_event.is_set
            parser = XTermParser(debug=constants.DEBUG)

            try:
                read_count = win32.wintypes.DWORD(0)
                input_handle = win32.GetStdHandle(win32.STD_INPUT_HANDLE)
                max_events = 1024
                key_event_type = 0x0001
                resize_event_type = 0x0004
                input_records = (win32.INPUT_RECORD * max_events)()
                read_console_input = win32.KERNEL32.ReadConsoleInputW

                while not exit_requested():
                    for event in parser.tick():
                        self.process_event(event)

                    if win32.wait_for_handles([input_handle], 100) is None:
                        continue

                    read_console_input(
                        input_handle,
                        win32.byref(input_records),
                        max_events,
                        win32.byref(read_count),
                    )
                    keys: list[str] = []
                    new_size: tuple[int, int] | None = None

                    for input_record in input_records[: read_count.value]:
                        event_type = input_record.EventType
                        if event_type == key_event_type:
                            key_event: Any = input_record.Event.KeyEvent
                            if not key_event.bKeyDown:
                                continue
                            if key_event.dwControlKeyState and key_event.wVirtualKeyCode == 0:
                                continue
                            keys.append(
                                encode_windows_key(
                                    key_event.uChar.UnicodeChar,
                                    key_event.wVirtualKeyCode,
                                    key_event.dwControlKeyState,
                                )
                            )
                        elif event_type == resize_event_type:
                            size = input_record.Event.WindowBufferSizeEvent.dwSize
                            new_size = (size.X, size.Y)

                    if keys:
                        value = "".join(keys).encode("utf-16", "surrogatepass").decode("utf-16")
                        for event in parser.feed(value):
                            self.process_event(event)
                    if new_size is not None:
                        self.on_size_change(*new_size)
            except Exception as error:
                self.app.log.error("KAIRO EVENT MONITOR ERROR", error)

    win32.EventMonitor = KairoEventMonitor
    return True
