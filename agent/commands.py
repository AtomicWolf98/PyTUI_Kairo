from typing import Dict, List


COMMAND_CATALOG: List[Dict[str, str]] = [
    {
        "name": "/help",
        "summary": "Show commands",
        "help": "Show this help message",
    },
    {
        "name": "/exit",
        "summary": "Exit Kairo",
        "help": "Exit the program",
    },
    {
        "name": "/plan",
        "summary": "Toggle Plan Mode",
        "help": "Toggle Plan Mode (agent drafts a plan before starting)",
    },
    {
        "name": "/manual",
        "summary": "Set MANUAL authorization",
        "help": "Set authorization level to MANUAL (confirm every tool)",
    },
    {
        "name": "/auto",
        "summary": "Set AUTO authorization",
        "help": "Set authorization level to AUTO (workspace-internal tools run automatically)",
    },
    {
        "name": "/yolo",
        "summary": "Set YOLO authorization",
        "help": "Set authorization level to YOLO (run all tools without confirmation)",
    },
    {
        "name": "/think",
        "summary": "Toggle Thinking Mode",
        "help": "Toggle Thinking Mode (display chain-of-thought)",
    },
    {
        "name": "/skills",
        "summary": "List tools and skills",
        "help": "List loaded custom and built-in skills",
    },
    {
        "name": "/clear",
        "summary": "Clear active conversation",
        "help": "Clear the conversation history",
    },
    {
        "name": "/compress",
        "summary": "Compress older context",
        "help": "Summarize older context while keeping recent turns",
    },
    {
        "name": "/new",
        "summary": "Create a conversation",
        "help": "Create and switch to a new in-memory conversation",
    },
    {
        "name": "/sessions",
        "summary": "Switch conversations",
        "help": "Switch between in-memory conversations",
    },
    {
        "name": "/config",
        "summary": "Show configuration",
        "help": "Show current settings",
    },
    {
        "name": "/model",
        "summary": "Select provider/model",
        "help": "Interactive menu to select the active provider and model",
    },
    {
        "name": "/undo",
        "summary": "Undo latest turn",
        "help": "Undo the last dialogue turn (user input and assistant response)",
    },
    {
        "name": "/workspace",
        "summary": "Workspace review / move",
        "help": "Show current workspace or use '/workspace move <path>' to switch",
    },
]


def get_command_map() -> Dict[str, str]:
    return {item["name"]: item["summary"] for item in COMMAND_CATALOG}


def build_help_markdown() -> str:
    lines = ["### Available Slash Commands", ""]
    for item in COMMAND_CATALOG:
        name = item["name"]
        if name == "/new":
            name = "/new [name]"
        lines.append(f"- `{name}` : {item['help']}")
    lines.append("")
    lines.append("### Keyboard Shortcuts")
    lines.append("")
    lines.append("- `Ctrl+B` : Toggle Workspace focus")
    lines.append("- `Ctrl+A` : Cycle authorization level (Manual → Auto → YOLO)")
    lines.append("- `Ctrl+P` : Toggle Plan Mode")
    lines.append("- `Ctrl+T` : Toggle Thinking Mode")
    lines.append("")
    return "\n".join(lines)
