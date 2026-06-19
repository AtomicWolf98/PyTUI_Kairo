import sys
import os
import shutil

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
    return "│ " + styled_content + " " * padding + " │\033[K\n"


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
    """
    Renders a double-framed input box and a permanent status dock below the cursor.
    Supports Shift+Enter (\n) for dynamic multi-line text input, autocomplete dropdowns,
    and shows real-time tokens, context usage, active switches, and active task progress.
    """
    if not WINDOWS or not sys.stdout.isatty():
        # Fallback to standard input
        try:
            return input().strip()
        except (KeyboardInterrupt, EOFError):
            raise KeyboardInterrupt

    buffer = []
    dropdown_open = False
    dropdown_matches = []
    dropdown_idx = 0
    
    # Track the total lines drawn in the previous loop iteration to clear them cleanly
    last_total_drawn_lines = 0
    last_input_lines_count = 1

    try:
        while True:
            # 1. Fetch Terminal dimensions
            term_width = shutil.get_terminal_size((80, 20)).columns
            # Limit width to 80 characters to keep it compact and pretty
            width = min(80, term_width)

            # 2. Process command matches
            current_text = "".join(buffer)
            # Find if user is typing a slash command
            last_line = current_text.split('\n')[-1] if current_text else ""
            if last_line.startswith('/'):
                dropdown_matches = [
                    (cmd, desc) for cmd, desc in commands_dict.items() if cmd.startswith(last_line)
                ]
                dropdown_open = len(dropdown_matches) > 0
            else:
                dropdown_matches = []
                dropdown_open = False
                dropdown_idx = 0

            if dropdown_open:
                dropdown_idx = max(0, min(dropdown_idx, len(dropdown_matches) - 1))

            # 3. Move cursor to the top of the widget
            if last_total_drawn_lines > 0:
                offset_to_top = last_input_lines_count - 1
                if offset_to_top > 0:
                    sys.stdout.write(f"\033[{offset_to_top}A")
                sys.stdout.write("\r")
                sys.stdout.flush()

            # 4. DRAW WIDGETS
            num_drawn_lines = 0

            # -- A. Input Text Area --
            input_lines = current_text.split('\n')
            for i, line in enumerate(input_lines):
                if i == 0:
                    sys.stdout.write("\033[1;36mkairo > \033[0m" + line + "\033[K\n")
                else:
                    sys.stdout.write("        " + line + "\033[K\n")
                num_drawn_lines += 1

            # -- B. Autocomplete Dropdown --
            num_dropdown_lines = 0
            if dropdown_open:
                max_visible = 5
                total_matches = len(dropdown_matches)
                
                if dropdown_idx < max_visible:
                    start_idx = 0
                else:
                    start_idx = dropdown_idx - max_visible + 1
                end_idx = min(start_idx + max_visible, total_matches)
                
                # Top indicator
                if start_idx > 0:
                    sys.stdout.write(f"\033[90m  ▲ ... and {start_idx} more ...\033[0m\033[K\n")
                    num_dropdown_lines += 1
                
                # Matches
                for i in range(start_idx, end_idx):
                    cmd, desc = dropdown_matches[i]
                    if i == dropdown_idx:
                        sys.stdout.write(f"\033[1;36m  > {cmd:<10} - {desc}\033[0m\033[K\n")
                    else:
                        sys.stdout.write(f"    {cmd:<10} - {desc}\033[0m\033[K\n")
                    num_dropdown_lines += 1
                
                # Bottom indicator
                if end_idx < total_matches:
                    sys.stdout.write(f"\033[90m  ▼ ... and {total_matches - end_idx} more ...\033[0m\033[K\n")
                    num_dropdown_lines += 1
                
                num_drawn_lines += num_dropdown_lines

            # -- C. Permanent Status Dock --
            # Top Border
            sys.stdout.write("┌── Status Dock ─" + "─" * (width - 18) + "┐\033[K\n")
            num_drawn_lines += 1
            
            # Model profile
            in_t = token_tracker.session_input_tokens
            out_t = token_tracker.session_output_tokens
            pct = token_tracker.context_percent
            profile_text = model_profile or model_name
            sys.stdout.write(format_dock_line(f"Model: {profile_text} ({model_name})", width))
            num_drawn_lines += 1

            # Current context occupancy
            context_text = (
                f"Context: ~{token_tracker.context_used_tokens:,} / "
                f"{token_tracker.context_window:,} ({pct:.1f}%)"
            )
            context_style = "1;31" if pct >= context_trigger_percent else "1;33" if pct >= context_trigger_percent * 0.8 else ""
            sys.stdout.write(format_dock_line(context_text, width, context_style))
            num_drawn_lines += 1

            # Active conversation and cumulative API usage
            usage_text = f"Session: {session_name} | Tokens: In {in_t:,} / Out {out_t:,}"
            sys.stdout.write(format_dock_line(usage_text, width))
            num_drawn_lines += 1

            # Modes Switces
            auto_str = "ON" if auto_mode else "OFF"
            think_str = "ON" if thinking_mode else "OFF"
            plan_str = "ON" if plan_mode else "OFF"
            modes_text = f"Modes: Auto [{auto_str}]  Thinking [{think_str}]  Plan [{plan_str}]"
            sys.stdout.write(format_dock_line(modes_text, width))
            num_drawn_lines += 1

            # Active Task & Progress
            task_disp = current_task if len(current_task) <= 45 else current_task[:42] + "..."
            task_text = f"Task: {task_disp} ({task_status})"
            sys.stdout.write(format_dock_line(task_text, width))
            num_drawn_lines += 1

            # Bottom Border
            sys.stdout.write("└─" + "─" * (width - 4) + "─┘\033[K\n")
            num_drawn_lines += 1

            # Clear leftover lines if the widget has shrunk
            if last_total_drawn_lines > num_drawn_lines:
                leftover = last_total_drawn_lines - num_drawn_lines
                for _ in range(leftover):
                    sys.stdout.write("\033[2K\r\n")
                # Move cursor back up to the bottom of the status dock
                sys.stdout.write(f"\033[{leftover}A")
                sys.stdout.flush()

            # Save the count of lines drawn to clear them in the next iteration
            last_total_drawn_lines = num_drawn_lines
            last_input_lines_count = len(input_lines)

            # 5. Position cursor inside the input box at the end of the text
            # Total lines drawn is (num_drawn_lines)
            # Input text is on lines 1 to len(input_lines)
            # Therefore, offset from the cursor after the drawn block to the last line of input is:
            # (num_drawn_lines) - len(input_lines) + 1, because each drawn line ends with "\n".
            offset_up = num_drawn_lines - len(input_lines) + 1
            
            # Move cursor up
            if offset_up > 0:
                sys.stdout.write(f"\033[{offset_up}A")
            
            # Calculate column index: 9 (prompt and space) + width of the last line of input
            cursor_col = 9 + get_string_width(input_lines[-1])
            sys.stdout.write(f"\033[{cursor_col}G")
            sys.stdout.flush()

            # 6. READ KEY
            ch = msvcrt.getwch()

            # Handle special key sequences (arrows, etc.)
            if ch in ('\x00', '\xe0'):
                sub_ch = msvcrt.getwch()
                if dropdown_open:
                    if sub_ch == 'H': # Up Arrow: navigate dropdown
                        dropdown_idx = (dropdown_idx - 1) % len(dropdown_matches)
                        continue
                    elif sub_ch == 'P': # Down Arrow: navigate dropdown
                        dropdown_idx = (dropdown_idx + 1) % len(dropdown_matches)
                        continue
                continue

            # 7. Enter Key (ASCII 13, '\r') -> Submit
            if ch == '\r':
                # Move cursor to the bottom of the drawn block before returning
                # to prevent overwriting the widgets on the terminal
                sys.stdout.write(f"\033[{offset_up}B\n")
                sys.stdout.flush()
                return "".join(buffer).strip()

            # 8. Shift+Enter / Ctrl+Enter (ASCII 10, '\n') -> Insert Newline
            elif ch == '\n':
                buffer.append('\n')
                continue

            # 9. Tab Key (ASCII 9, '\t') -> Autocomplete command
            elif ch == '\t':
                if dropdown_open:
                    selected_cmd = dropdown_matches[dropdown_idx][0]
                    # Erase last typed word (the command prefix)
                    # Get index of last word starting with /
                    last_slash_idx = current_text.rfind('/')
                    chars_to_delete = len(current_text) - last_slash_idx
                    buffer = buffer[:-chars_to_delete]
                    buffer.extend(list(selected_cmd))
                    dropdown_open = False
                    dropdown_idx = 0
                continue

            # 10. Backspace (ASCII 8, '\x08')
            elif ch == '\x08':
                if buffer:
                    popped = buffer.pop()
                    # CJK wide-character deletion support
                    if is_wide_char(popped):
                        sys.stdout.write('\b\b  \b\b')
                    else:
                        sys.stdout.write('\b \b')
                    sys.stdout.flush()
                continue

            # 11. Ctrl+C (ASCII 3, '\x03')
            elif ch == '\x03':
                # Ensure cursor is restored below the widget before raising
                sys.stdout.write(f"\033[{offset_up}B\n")
                sys.stdout.flush()
                raise KeyboardInterrupt

            # 12. Normal printable character
            elif ch.isprintable() or ord(ch) > 255:
                buffer.append(ch)
                sys.stdout.write(ch)
                sys.stdout.flush()

    except KeyboardInterrupt:
        raise KeyboardInterrupt
