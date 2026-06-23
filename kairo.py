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


def needs_first_run_setup(config: Config) -> bool:
    """Detect conditions that should trigger the first-run setup wizard."""
    providers = getattr(config, "llm", {}).get("providers", []) if getattr(config, "llm", None) else []
    if not providers:
        return True
    active_provider = config.llm.get("active_provider", "")
    active_model = config.llm.get("active_model", "")
    if not active_provider and not active_provider:
        return True
    if not any(p["name"] == active_provider and any(m["name"] == active_model for m in p["models"]) for p in providers):
        return True
    # Missing API key only triggers setup if user is about to call the LLM, so
    # do NOT block start-up here.
    return False


def run_first_run_wizard(config: Config) -> None:
    """Plain-mode interactive wizard for adding a first provider/model."""
    from agent.plain_io import ask, ask_choice, ask_float, ask_int, banner, confirm, notice, select

    banner("First-run setup")
    notice("No usable provider/model configuration was found. Let's add one.")
    if not confirm("Run the first-run setup wizard now? You can also skip and configure later via '/provider add'.", default=True):
        return

    from agent.provider_templates import all_templates, get_template
    templates = all_templates()
    template_names = list(templates.keys())
    idx = select("Choose a template", template_names)
    if idx < 0:
        notice("Skipped. You can use '/provider add' later.")
        return

    template = list(templates.values())[idx]
    notice(f"Template: {template.name} ({template.description})")
    name = ask("Provider name", default=template.name)
    base_url = ask("Base URL", default=template.base_url)
    api_key_mode = ask_choice("API key mode", ["env", "inline", "empty"], default="env")
    api_key_env = ""
    api_key = ""
    if api_key_mode == "env":
        api_key_env = ask("API key env name", default=template.api_key_env or f"KAIRO_{name.upper().replace('-', '_')}_API_KEY")
    elif api_key_mode == "inline":
        api_key = ask("API key value (writes to config.json)")
    default_model = template.models[0].name if template.models else ""
    model_name = ask("Model name", default=default_model)
    if not model_name:
        notice("Model name is required; wizard cancelled.")
        return
    context_window = ask_int("Context window", default=template.models[0].context_window if template.models else config.llm["defaults"]["context_window"], minimum=1)
    max_tokens = ask_int("Max tokens", default=template.models[0].max_tokens if template.models else config.llm["defaults"]["max_tokens"], minimum=1)
    temperature = ask_float("Temperature", default=template.models[0].temperature if template.models else float(config.llm["defaults"]["temperature"]), minimum=0.0, maximum=2.0)

    api_key_to_save = api_key if api_key_mode == "inline" else ""
    allow_inline = bool(api_key_to_save)
    if allow_inline:
        notice("Inline API keys are written to config.json.")
        if not confirm("Save inline key? Env names are safer.", default=False):
            api_key_to_save = ""
            allow_inline = False

    from agent.config_editor import ConfigDraft
    draft = ConfigDraft.from_config(config)
    if not draft.add_provider(
        name=name,
        base_url=base_url,
        api_key=api_key_to_save,
        api_key_env=api_key_env if not api_key_to_save else "",
        models=[{
            "name": model_name,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "context_window": context_window,
        }],
    ):
        notice("Failed to add provider; wizard aborted.")
        return
    draft.set_active_model(name, model_name)
    report = draft.apply_to(config, backup=True, allow_inline_key=allow_inline)
    if not report.ok:
        notice("Save failed:\n" + report.to_text())
        return
    notice(f"Provider '{name}' saved and is now active. Active target: {config.active_model_profile}")
    if api_key_mode == "env" and api_key_env and not os.environ.get(api_key_env):
        notice(f"Hint: set the environment variable '{api_key_env}' before sending messages to the model.")


def run_plain(agent):
    agent.print_welcome()
    # 0.2.3: if no usable provider, run the first-run wizard once before the prompt loop.
    if needs_first_run_setup(agent.config):
        try:
            run_first_run_wizard(agent.config)
        except Exception as exc:
            print(f"[Kairo] First-run wizard failed: {exc}", file=sys.stderr)
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
