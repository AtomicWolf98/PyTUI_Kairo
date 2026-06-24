"""Core LLM interaction loop, separated from command handling."""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from agent import tui_widgets
from agent.config import Config
from agent.context_manager import ConversationManager
from agent.llm import LLMClient
from tools.base import ToolRegistry
from tools.policy import is_authorized, AUTHORIZATION_AUTO, AUTHORIZATION_YOLO


class InteractionRunner:
    """Runs a single user interaction through the LLM/tool loop."""

    def __init__(
        self,
        config: Config,
        registry: ToolRegistry,
        conversations: ConversationManager,
        console: Console,
        system_instruction: str,
    ):
        self.config = config
        self.registry = registry
        self.conversations = conversations
        self.console = console
        self.system_instruction = system_instruction
        self.llm = LLMClient(config)
        self.current_task = "Idle"
        self.task_status = "Idle"

    @property
    def history(self) -> List[Dict[str, Any]]:
        return self.conversations.active.history

    @history.setter
    def history(self, value: List[Dict[str, Any]]) -> None:
        self.conversations.active.history = value
        self.conversations.refresh_context()

    @property
    def token_tracker(self):
        return self.conversations.active.token_tracker

    def _tool_target_path(self, arguments: Any) -> str:
        try:
            values = json.loads(arguments) if isinstance(arguments, str) else arguments
        except (TypeError, json.JSONDecodeError):
            return ""
        return str(values.get("path", "")) if isinstance(values, dict) else ""

    def _compression_messages(self, source: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        source_text = json.dumps(source, ensure_ascii=False, indent=2, default=str)
        return [
            {
                "role": "system",
                "content": (
                    "Summarize the supplied conversation history for another assistant that will continue "
                    "the same conversation. Preserve user goals, constraints, decisions, file paths, commands, "
                    "tool outcomes, errors, unresolved work, and exact facts that remain important. Do not include "
                    "hidden reasoning. Return only a concise structured summary."
                ),
            },
            {"role": "user", "content": source_text},
        ]

    def _generate_context_summary(self, source: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str]]:
        messages = self._compression_messages(source)
        safety_margin = max(1024, int(self.config.context_window * 0.02))
        estimated_prompt = self.conversations.estimator.estimate_messages(messages)
        available_output = self.config.context_window - estimated_prompt - safety_margin
        if available_output < 256:
            return None, "Compression input is too large for the current context window."

        summary_limit = min(4096, max(256, available_output))
        summary = ""
        error = None
        usage_recorded = False
        for type_, data in self.llm.stream_response(
            messages,
            tools=None,
            max_tokens_override=summary_limit,
            temperature_override=0.1,
            profile_role="compress",
        ):
            if type_ == "content":
                summary += data
            elif type_ == "usage":
                if not usage_recorded:
                    self.token_tracker.add_tokens(
                        data.get("prompt_tokens", 0),
                        data.get("completion_tokens", 0),
                    )
                    usage_recorded = True
            elif type_ in ("error", "context_error"):
                error = str(data)
                break
        if error:
            return None, error
        if not summary.strip():
            return None, "The compression request returned an empty summary."
        return summary.strip(), None

    def compress_context(self, manual: bool = False, tools=None) -> Tuple[bool, str]:
        settings = self.config.context_management
        preserve_turns = settings["preserve_recent_turns"]
        self.conversations.refresh_context(tools)
        before = self.token_tracker.context_used_tokens
        parts = self.conversations.compression_parts(preserve_turns)
        if not parts:
            return False, f"Nothing to compress; fewer than {preserve_turns + 1} complete turns are available."

        source, retained = parts
        summary, error = self._generate_context_summary(source)
        if error:
            return False, f"Context compression failed: {error}"

        self.conversations.apply_summary(summary, retained)
        after = self.conversations.refresh_context(tools)
        self.conversations.save_active(reason="compress")
        label = "Manual" if manual else "Automatic"
        return True, (
            f"{label} context compression completed: ~{before:,} -> ~{after:,} tokens "
            f"(kept {preserve_turns} recent turns)."
        )

    def ensure_context_capacity(self, tools=None, emergency: bool = False) -> bool:
        settings = self.config.context_management
        used = self.conversations.refresh_context(tools)
        window = self.config.context_window
        safety_margin = max(1024, int(window * 0.02))
        safe_prompt_budget = max(0, window - self.config.max_tokens - safety_margin)
        trigger_budget = int(window * settings["trigger_percent"] / 100.0)
        target_budget = min(
            int(window * settings["target_percent"] / 100.0),
            safe_prompt_budget,
        )

        needs_management = emergency or used >= trigger_budget or used > safe_prompt_budget
        if not needs_management:
            return True
        if not settings["enabled"]:
            if used <= safe_prompt_budget:
                return True
            self.console.print("[bold red]Context limit reached and context management is disabled.[/bold red]")
            return False

        if settings["auto_compress"]:
            compressed, message = self.compress_context(manual=False, tools=tools)
            if compressed:
                self.console.print(f"[bold yellow]{message}[/bold yellow]")
            else:
                self.console.print(f"[yellow]{message}[/yellow]")

        used = self.conversations.refresh_context(tools)
        if used > target_budget:
            removed, fits = self.conversations.trim_oldest_to_budget(target_budget, tools)
            if removed:
                self.console.print(
                    f"[bold yellow]Trimmed {removed} oldest conversation segment(s) to protect the context window.[/bold yellow]"
                )
            if not fits:
                self.console.print(
                    "[bold red]The system prompt, current user request, and output budget cannot fit in the "
                    "configured context window. Lower max_tokens or increase context_window.[/bold red]"
                )
                return False
        self.conversations.save_active(reason="trim")
        return True

    def get_plan(self, task: str) -> str:
        """Queries the LLM to generate a step-by-step plan for a given task."""
        self.current_task = f"Drafting plan: {task}"
        self.task_status = "In Progress"

        plan_prompt = (
            f"You are in PLAN MODE. Please review the following user request and create a detailed, "
            f"step-by-step implementation plan explaining how you intend to accomplish it. "
            f"Do NOT execute any tools yet, and do NOT write any code. Just write the plan.\n\n"
            f"User request: {task}"
        )

        plan_messages = [
            {"role": "system", "content": self.system_instruction},
            {"role": "user", "content": plan_prompt},
        ]

        safety_margin = max(1024, int(self.config.context_window * 0.02))
        plan_budget = self.config.context_window - self.config.max_tokens - safety_margin
        if self.conversations.estimator.estimate_messages(plan_messages) > plan_budget:
            self.console.print(
                "[bold red]The plan request cannot fit in the configured context window. "
                "Lower max_tokens or shorten the request.[/bold red]"
            )
            return ""

        self.console.print("\n[bold magenta]Drafting Implementation Plan...[/bold magenta]")

        plan_content = ""
        has_official_usage = False
        with Live(Text("Generating...", style="italic dim"), console=self.console, refresh_per_second=10) as live:
            for type_, data in self.llm.stream_response(plan_messages, profile_role="plan"):
                if type_ == "content":
                    plan_content += data
                    live.update(Markdown(plan_content))
                elif type_ == "thought" and self.config.thinking_mode:
                    pass
                elif type_ == "usage":
                    prompt_tokens = data.get("prompt_tokens", 0)
                    completion_tokens = data.get("completion_tokens", 0)
                    if not has_official_usage:
                        self.token_tracker.add_tokens(prompt_tokens, completion_tokens)
                    has_official_usage = True
                elif type_ in ("error", "context_error"):
                    live.console.print(f"[bold red]{data}[/bold red]")
                    return ""

        if not has_official_usage:
            input_text = self.system_instruction + "\n\n" + plan_prompt
            self.token_tracker.add_text(input_text, plan_content)

        self.current_task = "Idle"
        self.task_status = "Idle"
        return plan_content

    def run_interaction_events(
        self,
        user_input: str,
        emit: Callable[[str, Any], None],
        approve: Optional[Callable[[str, List[str], int], int]] = None,
        request_text: Optional[Callable[[str], str]] = None,
    ) -> None:
        """Run one interaction without terminal rendering, emitting structured UI events."""
        self.current_task = user_input.strip()
        self.task_status = "In Progress"
        emit("task_status", {"task": self.current_task, "status": self.task_status})

        try:
            if self.config.plan_mode:
                plan_prompt = (
                    "You are in PLAN MODE. Create a detailed implementation plan for the user request. "
                    "Do not execute tools or write code yet.\n\nUser request: " + user_input
                )
                plan_messages = [
                    {"role": "system", "content": self.system_instruction},
                    {"role": "user", "content": plan_prompt},
                ]
                emit("state", "thinking")
                emit("message_started", {"kind": "plan"})
                plan_content = ""
                for type_, data in self.llm.stream_response(plan_messages, profile_role="plan"):
                    if type_ == "content":
                        plan_content += data
                        emit("content_delta", data)
                    elif type_ == "thought":
                        emit("thought_delta", data)
                    elif type_ == "usage":
                        self.token_tracker.add_tokens(
                            data.get("prompt_tokens", 0), data.get("completion_tokens", 0)
                        )
                    elif type_ in ("error", "context_error"):
                        emit("error", str(data))
                        return
                emit("message_finished", None)

                if approve:
                    choice = approve(
                        "Approve plan?",
                        ["Approve and run", "Cancel task", "Edit plan instructions"],
                        0,
                    )
                    if choice == 1 or choice < 0:
                        emit("notice", "Task cancelled.")
                        return
                    if choice == 2 and request_text:
                        feedback = request_text("Plan modifications")
                        if feedback:
                            user_input += f"\n\n[User Plan Modification]: {feedback}"

            self.history.append({"role": "user", "content": user_input})
            self.conversations.mark_dirty(reason="user_message")

            while True:
                schemas = self.registry.get_schemas()
                if not self.ensure_context_capacity(schemas):
                    emit("error", "The request cannot fit in the configured context window.")
                    return

                retry_count = 0
                while True:
                    current_thought = ""
                    current_content = ""
                    current_tool_calls: List[Dict[str, Any]] = []
                    context_error = None
                    official_context_tokens = None
                    has_usage = False

                    emit("state", "connecting")
                    emit("message_started", {"kind": "assistant"})
                    for type_, data in self.llm.stream_response(self.history, tools=schemas, profile_role="chat"):
                        if type_ == "context_error":
                            context_error = str(data)
                            break
                        if type_ == "error":
                            emit("error", str(data))
                            return
                        if type_ == "thought":
                            self.task_status = "Thinking"
                            current_thought += data
                            emit("state", "thinking")
                            emit("thought_delta", data)
                        elif type_ == "content":
                            self.task_status = "Responding"
                            current_content += data
                            emit("state", "streaming")
                            emit("content_delta", data)
                        elif type_ == "tool_calls":
                            current_tool_calls = data
                        elif type_ == "usage":
                            prompt_tokens = data.get("prompt_tokens", 0)
                            completion_tokens = data.get("completion_tokens", 0)
                            if not has_usage:
                                self.token_tracker.add_tokens(prompt_tokens, completion_tokens)
                            official_context_tokens = prompt_tokens + completion_tokens
                            has_usage = True

                    if context_error is None:
                        break
                    if retry_count >= 1:
                        emit("error", context_error)
                        return
                    emit("notice", "Context limit reached; compacting and retrying once.")
                    emit("state", "compressing")
                    if not self.ensure_context_capacity(schemas, emergency=True):
                        emit("error", "Emergency context compaction failed.")
                        return
                    retry_count += 1

                emit("message_finished", None)
                if not has_usage:
                    input_text = "".join(message.get("content", "") or "" for message in self.history)
                    self.token_tracker.add_text(input_text, current_content + current_thought)

                assistant_message = {"role": "assistant", "content": current_content or ""}
                if current_tool_calls:
                    assistant_message["tool_calls"] = [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["function"]["name"],
                                "arguments": call["function"]["arguments"],
                            },
                        }
                        for call in current_tool_calls
                    ]
                self.history.append(assistant_message)
                self.conversations.refresh_context(schemas)
                self.conversations.mark_dirty(reason="assistant_message")
                if official_context_tokens is not None:
                    self.token_tracker.set_context_used(official_context_tokens)
                emit("usage_updated", None)

                if not current_tool_calls:
                    emit("state", "success")
                    break

                for tool_call in current_tool_calls:
                    name = tool_call["function"]["name"]
                    arguments = tool_call["function"]["arguments"]
                    call_id = tool_call["id"]
                    target_path = self._tool_target_path(arguments)
                    tool = self.registry.tools.get(name)
                    scope = tool.classify_scope(arguments) if tool else None
                    scope_label = scope.value.upper() if scope else "UNKNOWN"

                    emit("tool_requested", {
                        "name": name,
                        "arguments": arguments,
                        "target_path": target_path,
                        "scope": scope_label,
                    })

                    approved = is_authorized(self.config.authorization_level, scope) if scope else False
                    if not approved and approve:
                        emit("state", "tool_wait")
                        if self.config.authorization_level == AUTHORIZATION_AUTO:
                            options = ["Run once", "Skip", "Enable YOLO Mode", "Stop task"]
                        else:
                            options = ["Run once", "Skip", f"Enable {AUTHORIZATION_AUTO.upper()} Mode", "Stop task"]
                        choice = approve(
                            f"[{scope_label}] Execute tool '{name}'?",
                            options,
                            0,
                        )
                        if choice == 0:
                            approved = True
                        elif choice == 1 or choice < 0:
                            approved = False
                        elif choice == 2:
                            self.config.authorization_level = AUTHORIZATION_YOLO if self.config.authorization_level == AUTHORIZATION_AUTO else AUTHORIZATION_AUTO
                            self.config.save()
                            approved = True
                        elif choice == 3:
                            emit("notice", "Task stopped by user.")
                            return

                    if approved:
                        emit("state", "tool_run")
                        emit("tool_started", {
                            "name": name,
                            "arguments": arguments,
                            "target_path": target_path,
                        })
                        result = self.registry.execute_tool(name, arguments)
                        success = not str(result).lstrip().lower().startswith("error")
                        emit("tool_finished", {
                            "name": name,
                            "arguments": arguments,
                            "target_path": target_path,
                            "result": result,
                            "success": success,
                        })
                    else:
                        result = "Error: Tool execution was rejected by the user."
                        emit("tool_finished", {
                            "name": name,
                            "arguments": arguments,
                            "target_path": target_path,
                            "result": result,
                            "success": False,
                        })

                    self.history.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": result,
                    })
                    self.conversations.refresh_context(schemas)
                    self.conversations.mark_dirty(reason="tool_result")
                    emit("usage_updated", None)
        except Exception as exc:
            emit("error", str(exc))
        finally:
            self.current_task = "Idle"
            self.task_status = "Idle"
            emit("task_status", {"task": "Idle", "status": "Idle"})

    def run_interaction(self, user_input: str) -> None:
        """Executes the agent logic for a single user interaction in the local console."""
        self.current_task = user_input.strip()
        self.task_status = "In Progress"

        try:
            if self.config.plan_mode:
                plan = self.get_plan(user_input)
                if not plan:
                    return
                self.console.print("\n[bold yellow]Please review the plan above.[/bold yellow]")
                options = [
                    "Yes, approve and run",
                    "No, cancel task",
                    "Edit plan instructions",
                ]
                idx = tui_widgets.select_menu("Approve and proceed?", options)

                if idx == 1:
                    self.console.print("[bold red]Task cancelled.[/bold red]")
                    return
                elif idx == 2:
                    feedback = input("Enter modifications for the plan: ")
                    user_input = f"{user_input}\n\n[User Plan Modification]: {feedback}"
                    self.console.print("[bold green]Plan updated. Proceeding with execution...[/bold green]")
                else:
                    self.console.print("[bold green]Plan approved. Executing...[/bold green]")

            self.history.append({"role": "user", "content": user_input})
            self.conversations.mark_dirty(reason="user_message")

            while True:
                schemas = self.registry.get_schemas()
                if not self.ensure_context_capacity(schemas):
                    return

                context_retry_count = 0
                while True:
                    current_thought = ""
                    current_content = ""
                    current_tool_calls: List[Dict[str, Any]] = []
                    context_error = None

                    self.console.print()

                    group = Group()
                    group.renderables.append(Spinner("dots", text="[bold yellow]Connecting to API...[/bold yellow]"))

                    in_thinking_block = False
                    first_token = True
                    has_official_usage = False
                    official_context_tokens = None

                    with Live(group, console=self.console, auto_refresh=True, refresh_per_second=10) as live:
                        for type_, data in self.llm.stream_response(self.history, tools=schemas, profile_role="chat"):
                            if first_token:
                                first_token = False
                                group.renderables.clear()

                            if type_ == "context_error":
                                context_error = str(data)
                                break
                            if type_ == "error":
                                live.console.print(f"\n[bold red]{data}[/bold red]")
                                return

                            if type_ == "thought":
                                self.task_status = "Thinking"
                                if self.config.thinking_mode:
                                    if not in_thinking_block:
                                        in_thinking_block = True
                                        group.renderables.append(Panel(
                                            Text(""),
                                            title="Thinking Process",
                                            border_style="yellow",
                                            subtitle="[italic yellow]thinking...[/italic yellow]",
                                        ))

                                    current_thought += data
                                    group.renderables[0] = Panel(
                                        Markdown(current_thought),
                                        title="Thinking Process",
                                        border_style="yellow",
                                        subtitle="[italic yellow]thinking...[/italic yellow]",
                                    )

                            elif type_ == "content":
                                self.task_status = "Responding"
                                current_content += data
                                renderables = []
                                if in_thinking_block:
                                    renderables.append(Panel(
                                        Markdown(current_thought),
                                        title="Thinking Process",
                                        border_style="yellow",
                                        subtitle="[italic green]thought for a moment[/italic green]",
                                    ))
                                renderables.append(Markdown(current_content))
                                group.renderables.clear()
                                group.renderables.extend(renderables)

                            elif type_ == "tool_calls":
                                current_tool_calls = data

                            elif type_ == "usage":
                                prompt_tokens = data.get("prompt_tokens", 0)
                                completion_tokens = data.get("completion_tokens", 0)
                                if not has_official_usage:
                                    self.token_tracker.add_tokens(prompt_tokens, completion_tokens)
                                official_context_tokens = prompt_tokens + completion_tokens
                                has_official_usage = True

                    if context_error is None:
                        break
                    if context_retry_count >= 1:
                        self.console.print(f"[bold red]{context_error}[/bold red]")
                        return
                    self.console.print("[bold yellow]Provider rejected the context; attempting one emergency compaction.[/bold yellow]")
                    if not self.ensure_context_capacity(schemas, emergency=True):
                        return
                    context_retry_count += 1

                if first_token:
                    group.renderables.clear()

                if not has_official_usage:
                    input_text = ""
                    for msg in self.history:
                        input_text += msg.get("content", "") or ""
                    output_text = current_content + current_thought
                    self.token_tracker.add_text(input_text, output_text)

                assistant_msg = {"role": "assistant"}
                if current_content:
                    assistant_msg["content"] = current_content
                else:
                    assistant_msg["content"] = ""

                if current_tool_calls:
                    openai_tool_calls = []
                    for tc in current_tool_calls:
                        openai_tool_calls.append({
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["function"]["name"],
                                "arguments": tc["function"]["arguments"],
                            },
                        })
                    assistant_msg["tool_calls"] = openai_tool_calls

                self.history.append(assistant_msg)
                self.conversations.refresh_context(schemas)
                self.conversations.mark_dirty(reason="assistant_message")
                if official_context_tokens is not None:
                    self.token_tracker.set_context_used(official_context_tokens)

                if not current_tool_calls:
                    break

                for tool_call in current_tool_calls:
                    name = tool_call["function"]["name"]
                    args = tool_call["function"]["arguments"]
                    call_id = tool_call["id"]
                    tool = self.registry.tools.get(name)
                    scope = tool.classify_scope(args) if tool else None
                    scope_label = scope.value.upper() if scope else "UNKNOWN"

                    self.console.print(Panel(
                        f"[bold cyan]Tool:[/bold cyan] {name}\n"
                        f"[bold cyan]Scope:[/bold cyan] {scope_label}\n"
                        f"[bold cyan]Arguments:[/bold cyan] {args}",
                        title="Tool Call Request",
                        border_style="cyan",
                    ))

                    approved = is_authorized(self.config.authorization_level, scope) if scope else False
                    if not approved:
                        self.task_status = f"Awaiting tool approval: {name}"
                        if self.config.authorization_level == AUTHORIZATION_AUTO:
                            options = [
                                "Yes, run tool once",
                                "No, skip this tool",
                                "All, enable YOLO Mode",
                                "Exit session",
                            ]
                        else:
                            options = [
                                "Yes, run tool once",
                                "No, skip this tool",
                                f"All, enable {AUTHORIZATION_AUTO.upper()} Mode",
                                "Exit session",
                            ]
                        idx = tui_widgets.select_menu(f"[{scope_label}] Execute tool '{name}'?", options)

                        if idx == 0:
                            approved = True
                        elif idx == 1:
                            approved = False
                        elif idx == 2:
                            self.config.authorization_level = AUTHORIZATION_YOLO if self.config.authorization_level == AUTHORIZATION_AUTO else AUTHORIZATION_AUTO
                            self.config.save()
                            self.console.print(f"[bold green]{self.config.authorization_level.upper()} Mode enabled for remaining tools.[/bold green]")
                            approved = True
                        elif idx == 3:
                            self.console.print("[bold red]Session terminated by user.[/bold red]")
                            return

                    if approved:
                        self.task_status = f"Executing tool: {name}"
                        with self.console.status(f"[bold green]Executing tool '{name}'...[/bold green]", spinner="line"):
                            result = self.registry.execute_tool(name, args)

                        short_result = result
                        if len(result) > 1000:
                            short_result = result[:1000] + "\n\n... (output truncated for console display, full output sent to agent) ..."

                        self.console.print(Panel(short_result, title="Tool Execution Output", border_style="green"))
                    else:
                        self.console.print(f"[bold red]Tool '{name}' execution was rejected by user.[/bold red]")
                        result = "Error: Tool execution was rejected by the user."

                    self.history.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": result,
                    })
                    self.conversations.refresh_context(schemas)
                    self.conversations.mark_dirty(reason="tool_result")
                    self.task_status = "Processing results"

        finally:
            self.current_task = "Idle"
            self.task_status = "Idle"
