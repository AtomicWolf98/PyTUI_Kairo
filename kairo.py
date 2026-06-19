import argparse
import os
import sys
from pathlib import Path

# Reconfigure stdout/stderr to UTF-8 to prevent GBK encoding issues on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Add current directory to path just in case
sys.path.append(str(Path(__file__).parent.resolve()))

from agent.commands import get_command_map
from agent.config import Config
from agent.bootstrap import build_agent, build_registry
from agent.tui_widgets import input_framed_with_dock


def should_use_textual(args, config) -> bool:
    if args.tui:
        return True
    if args.plain or config.ui.get("mode") == "plain":
        return False
    if os.environ.get("TERM", "").lower() == "dumb":
        return False
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def run_plain(agent):
    agent.print_welcome()
    try:
        while True:
            try:
                user_input = input_framed_with_dock(
                    commands_dict=get_command_map(),
                    model_name=agent.config.model,
                    token_tracker=agent.token_tracker,
                    auto_mode=agent.config.auto_mode,
                    thinking_mode=agent.config.thinking_mode,
                    plan_mode=agent.config.plan_mode,
                    current_task=agent.current_task,
                    task_status=agent.task_status,
                    model_profile=agent.config.active_model_profile,
                    session_name=agent.active_session_name,
                    context_trigger_percent=agent.config.context_management["trigger_percent"],
                )
            except EOFError:
                break

            if not user_input:
                continue
            try:
                agent.run_interaction(user_input)
            except Exception as exc:
                agent.console.print(f"\n[bold red]An error occurred in interaction: {exc}[/bold red]")
    except KeyboardInterrupt:
        agent.console.print("\n[bold red]Kairo interrupted. Goodbye.[/bold red]")
    finally:
        agent.shutdown()


def main():
    parser = argparse.ArgumentParser(description="Kairo - an animated terminal-native coding agent.")
    parser.add_argument("--config", default="config.json", help="Path to config.json file.")
    parser.add_argument("--auto", action="store_true", help="Start directly in Auto authorization level (workspace-internal tools run automatically).")
    parser.add_argument("--authorization", choices=["manual", "auto", "yolo"], default=None, help="Set the authorization level (manual, auto, yolo).")
    parser.add_argument("--plan", action="store_true", help="Start directly in Plan Mode (drafts plans before actions).")
    parser.add_argument("--think", action="store_true", help="Start directly in Thinking Mode (display reasoning).")
    parser.add_argument("--plain", action="store_true", help="Use the compatible non-Textual interface.")
    parser.add_argument("--tui", action="store_true", help="Force the Textual interface even in non-TTY environments.")
    parser.add_argument("--no-animation", action="store_true", help="Disable Kai and transition animations.")
    parser.add_argument("--reduced-motion", action="store_true", help="Use static, reduced-motion UI states.")
    parser.add_argument("--theme", default=None, help="Textual theme name (default: kairo-dark).")
    args = parser.parse_args()

    config = Config(config_path=args.config)

    if args.authorization:
        config.authorization_level = args.authorization
    elif args.auto:
        config.authorization_level = "auto"
    if args.plan:
        config.plan_mode = True
    if args.think:
        config.thinking_mode = True

    if args.theme:
        config.ui["theme"] = args.theme
    reduced_motion = args.reduced_motion or bool(config.ui.get("reduced_motion"))
    animation = not args.no_animation and config.ui.get("animation") != "none"

    if should_use_textual(args, config):
        try:
            from agent.ui import KairoApp
        except ImportError as exc:
            print(f"[Kairo] Textual unavailable ({exc}); falling back to plain mode.")
            run_plain(build_agent(config))
            return
        KairoApp(
            config,
            build_registry(config),
            animation=animation,
            reduced_motion=reduced_motion,
        ).run()
    else:
        run_plain(build_agent(config))


if __name__ == "__main__":
    main()
