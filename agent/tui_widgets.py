import sys

# Import WINDOWS check and msvcrt
try:
    import msvcrt
    WINDOWS = True
except ImportError:
    WINDOWS = False

def is_wide_char(c: str) -> bool:
    """Returns True if the character is a CJK or wide character (takes 2 terminal columns)."""
    try:
        return ord(c) > 255
    except Exception:
        return False

def get_char_width(c: str) -> int:
    """Returns terminal column width of a character (2 for wide/CJK, 1 for ASCII)."""
    return 2 if is_wide_char(c) else 1

def get_string_width(s: str) -> int:
    """Returns total column width of a string with mixed CJK/ASCII characters."""
    return sum(get_char_width(c) for c in s)


def truncate_to_width(content: str, max_width: int) -> str:
    if get_string_width(content) <= max_width:
        return content
    if max_width <= 3:
        return "." * max(0, max_width)
    result = []
    used = 0
    for char in content:
        char_width = get_char_width(char)
        if used + char_width > max_width - 3:
            break
        result.append(char)
        used += char_width
    return "".join(result) + "..."


def select_menu(prompt: str, options: list, default_index: int = 0) -> int:
    """
    Renders an interactive arrow-key selection menu.
    Allows Up/Down arrow navigation and selection with Enter.
    Falls back to simple CLI input on non-Windows.
    """
    if not WINDOWS or not sys.stdout.isatty():
        # Fallback for non-TTY / non-Windows
        print(prompt)
        for i, opt in enumerate(options):
            print(f"  [{i}] {opt}")
        while True:
            try:
                choice = input("Enter option index: ").strip()
                idx = int(choice)
                if 0 <= idx < len(options):
                    return idx
            except Exception:
                pass
            print(f"Invalid choice. Enter a number between 0 and {len(options)-1}.")

    # Hide cursor
    sys.stdout.write("\033[?25l")
    sys.stdout.flush()

    current_idx = default_index
    num_options = len(options)

    # Print prompt
    print(prompt)

    try:
        while True:
            # Print choices
            for i, opt in enumerate(options):
                if i == current_idx:
                    # Highlight selected option (cyan text + bold)
                    sys.stdout.write(f"\033[1;36m  > {opt}\033[0m\n")
                else:
                    sys.stdout.write(f"    {opt}\n")
            sys.stdout.flush()

            # Wait for key press
            ch = msvcrt.getwch()

            # Erase printed choices (move cursor back up and clear lines)
            sys.stdout.write(f"\033[{num_options}A")
            for _ in range(num_options):
                sys.stdout.write("\033[K\n")
            sys.stdout.write(f"\033[{num_options}A")
            sys.stdout.flush()

            # Process key
            if ch in ('\x00', '\xe0'): # Special key prefix
                sub_ch = msvcrt.getwch()
                if sub_ch == 'H': # Up Arrow
                    current_idx = (current_idx - 1) % num_options
                elif sub_ch == 'P': # Down Arrow
                    current_idx = (current_idx + 1) % num_options
            elif ch == '\r': # Enter
                # Print the selected option cleanly and exit
                print(f"  Selected: \033[1;32m{options[current_idx]}\033[0m")
                return current_idx
            elif ch == '\x03': # Ctrl+C
                raise KeyboardInterrupt
    finally:
        # Show cursor
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()


def format_dock_line(content: str, width: int, style: str = "") -> str:
    """Helper to pad CJK/ASCII string with spaces to fit standard terminal box width."""
    content = truncate_to_width(content, max(0, width - 4))
    text_width = get_string_width(content)
    padding = max(0, width - text_width - 4)
    styled_content = f"\033[{style}m{content}\033[0m" if style else content
    return "| " + styled_content + " " * padding + " |\033[K\n"


def input_framed_with_dock(
    commands_dict: dict,
    model_name: str,
    token_tracker,
    auto_mode: bool,
    thinking_mode: bool,
    plan_mode: bool,
    current_task: str,
    task_status: str,
    model_profile: str = "",
    session_name: str = "Conversation 1",
    context_trigger_percent: float = 85.0,
) -> str:
    """Stable single-line input for plain mode (0.2.4 degraded).

    The previous implementation rendered a hand-written ANSI widget with a
    permanent status dock and autocomplete dropdown.  That caused redraw
    glitches, encoding pollution and repeated lines on Windows terminals.

    For 0.2.4 we prioritise stability: a simple ``kairo > `` prompt that
    reads one line at a time.  Slash commands are still dispatched by
    ``Agent.run_interaction``; we just do not offer dynamic completion.
    """
    # Show a minimal status line before the prompt.
    try:
        pct = token_tracker.context_percent
        profile_text = model_profile or model_name
        status = (
            f"[{profile_text}] ctx {token_tracker.context_used_tokens:,}/"
            f"{token_tracker.context_window:,} ({pct:.0f}%) | {session_name}"
        )
        sys.stdout.write(f"\033[90m{status}\033[0m\n")
        sys.stdout.flush()
    except Exception:
        pass

    try:
        return input("\033[1;36mkairo > \033[0m").strip()
    except (KeyboardInterrupt, EOFError):
        raise KeyboardInterrupt
