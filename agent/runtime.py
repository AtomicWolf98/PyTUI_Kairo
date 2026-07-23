"""UI-neutral runtime and service layer for Kairo."""
from __future__ import annotations

import copy
import os
import queue
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.bootstrap import build_agent
from agent.cancellation import CancellationToken
from agent.config import Config
from agent.config_editor import KEY_CLEAR, ConfigDraft
from agent.profile_resolver import describe_key_source, is_masked_key, list_profiles, mask_key
from agent.runtime_commands import handle_doctor
from agent.session_store import InvalidSessionIdError, SessionStore
from agent.workspace import WorkspaceMonitor, WorkspaceSnapshot


@dataclass(frozen=True)
class RuntimeEvent:
    kind: str
    payload: Any = None
    timestamp: float = field(default_factory=time.time)
    sequence: int = 0


@dataclass(frozen=True)
class ActiveTurn:
    turn_id: str
    session_id: str
    started_at: float = field(default_factory=time.time)


class RuntimeEventBus:
    """Thread-safe event bus shared by TUI, Web and tests."""

    def __init__(self, max_buffer: int = 1000):
        self.max_buffer = max(100, int(max_buffer))
        self._events: list[RuntimeEvent] = []
        self._subscribers: list[Callable[[RuntimeEvent], None]] = []
        self._subscriber_errors: list[dict[str, Any]] = []
        self._sequence = 0
        self._lock = threading.RLock()

    def emit(self, kind: str, payload: Any = None) -> RuntimeEvent:
        with self._lock:
            self._sequence += 1
            event = RuntimeEvent(kind=kind, payload=payload, sequence=self._sequence)
            self._events.append(event)
            self._events = self._events[-self.max_buffer:]
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber(event)
            except Exception as exc:
                with self._lock:
                    self._subscriber_errors.append({
                        "kind": kind,
                        "error": str(exc),
                        "timestamp": time.time(),
                    })
                    self._subscriber_errors = self._subscriber_errors[-20:]
        return event

    def subscribe(self, callback: Callable[[RuntimeEvent], None]) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._lock, suppress(ValueError):
                self._subscribers.remove(callback)

        return unsubscribe

    def snapshot(self) -> list[RuntimeEvent]:
        with self._lock:
            return list(self._events)

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            return {"subscriber_errors": list(self._subscriber_errors)}


class KairoRuntime:
    """Application kernel used by plain, Textual and Web adapters."""

    def __init__(self, config: Config):
        self.config = config
        self.agent = build_agent(config)
        self.runtime_id = uuid.uuid4().hex
        self.workspace_revision = 0
        self.previous_workspace_root = str(self.agent.workspace_context.root)
        max_buffer = getattr(config, "web", {}).get("max_event_buffer", 1000)
        self.events = RuntimeEventBus(max_buffer=max_buffer)
        self.workspace = WorkspaceService(self)
        self.config_service = ConfigService(self)
        self.sessions = SessionService(self)
        self.chat = ChatService(self)
        self.skills = SkillService(self)
        self._task_lock = threading.RLock()
        self._mutation_lock = threading.RLock()
        self._task_thread: threading.Thread | None = None
        self._cancel_token: CancellationToken | None = None
        self._active_turn: ActiveTurn | None = None
        self._closing = False
        self._degraded = False
        self._degraded_reason = ""
        self._pending_approvals: dict[str, _PendingApproval] = {}
        self._approval_lock = threading.RLock()

    def status(self) -> dict[str, Any]:
        tracker = self.agent.token_tracker
        diagnostics = self.events.diagnostics()
        diagnostics.update(
            {
                "degraded": self._degraded,
                "degraded_reason": self._degraded_reason,
            }
        )
        return {
            "version": _package_version(),
            "runtime_id": self.runtime_id,
            "workspace_revision": self.workspace_revision,
            "model": self.config.model,
            "profile": self.config.active_model_profile,
            "base_url": self.config.base_url,
            "api_key": self.config.describe_active_api_key(),
            "workspace_root": str(self.agent.workspace_context.root),
            "previous_workspace_root": self.previous_workspace_root,
            "session": {
                "id": self.agent.conversations.active.id,
                "name": self.agent.active_session_name,
                "message_count": len(self.agent.history),
            },
            "context": {
                "used": tracker.context_used_tokens,
                "limit": tracker.context_window,
                "percent": tracker.context_percent,
            },
            "modes": {
                "authorization": self.config.authorization_level,
                "plan": self.config.plan_mode,
                "thinking": self.config.thinking_mode,
            },
            "task": {
                "current": self.agent.current_task,
                "status": self.agent.task_status,
                "busy": self.is_busy(),
                "turn_id": self._active_turn.turn_id if self._active_turn else "",
                "session_id": self._active_turn.session_id if self._active_turn else "",
            },
            "lifecycle": {
                "closing": self._closing,
                "degraded": self._degraded,
                "degraded_reason": self._degraded_reason,
            },
            "diagnostics": diagnostics,
        }

    def is_busy(self) -> bool:
        with self._task_lock:
            return self._active_turn is not None

    def mutation_error(self, operation: str) -> dict[str, Any] | None:
        """Return the stable failure payload for a prohibited state mutation."""
        with self._task_lock:
            if self._closing:
                return _failure("runtime_closing", "Kairo is shutting down.", retryable=False)
            if self._degraded:
                detail = self._degraded_reason or "Runtime reconciliation failed."
                return _failure("runtime_degraded", detail, retryable=False)
            if self._active_turn is not None:
                return _failure(
                    "runtime_busy",
                    f"Cannot {operation} while turn {self._active_turn.turn_id} is running.",
                    retryable=True,
                )
        return None

    def run_mutation(self, operation: str, callback: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        """Serialize state changes and reject them while a turn is active."""
        with self._mutation_lock:
            blocked = self.mutation_error(operation)
            if blocked is not None:
                return blocked
            try:
                return callback()
            except Exception as exc:
                return _failure("mutation_failed", f"{operation} failed: {exc}", retryable=False)

    def mark_degraded(self, reason: str) -> None:
        with self._task_lock:
            self._degraded = True
            self._degraded_reason = str(reason)
        self.events.emit("runtime_degraded", {"reason": self._degraded_reason, **self._runtime_identity()})

    def _runtime_identity(self) -> dict[str, Any]:
        return {
            "runtime_id": self.runtime_id,
            "workspace_revision": self.workspace_revision,
            "workspace_root": str(self.agent.workspace_context.root),
        }

    def submit_message(self, text: str) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            return _failure("invalid_message", "Message is empty.", retryable=False)
        with self._mutation_lock, self._task_lock:
            if self._closing:
                return _failure("runtime_closing", "Kairo is shutting down.", retryable=False)
            if self._degraded:
                return _failure(
                    "runtime_degraded",
                    self._degraded_reason or "Runtime reconciliation failed.",
                    retryable=False,
                )
            if self._active_turn is not None:
                return _failure("runtime_busy", "Another task is already running.", retryable=True)
            turn = ActiveTurn(
                turn_id=uuid.uuid4().hex,
                session_id=self.agent.conversations.active.id,
            )
            token = CancellationToken()
            self._active_turn = turn
            self._cancel_token = token
            self._task_thread = threading.Thread(
                target=self._run_message_worker,
                args=(turn, text, token),
                name=f"kairo-runtime-{turn.turn_id[:8]}",
                daemon=True,
            )
            try:
                self._task_thread.start()
            except Exception:
                self._active_turn = None
                self._cancel_token = None
                self._task_thread = None
                raise
        return {"ok": True, "turn_id": turn.turn_id, "session_id": turn.session_id}

    def _run_message_worker(self, turn: ActiveTurn, text: str, token: CancellationToken) -> None:
        turn_id = turn.turn_id
        self.events.emit(
            "turn_started",
            {"turn_id": turn_id, "session_id": turn.session_id, "text": text, **self._runtime_identity()},
        )
        current_message_id = ""
        local_sequence = 0
        failed = False

        def emit(kind: str, payload: Any = None) -> None:
            nonlocal current_message_id, local_sequence, failed
            local_sequence += 1
            normalized = self._normalize_worker_payload(kind, payload, turn_id, current_message_id, local_sequence)
            normalized.setdefault("session_id", turn.session_id)
            normalized.update({key: value for key, value in self._runtime_identity().items() if key not in normalized})
            if kind == "message_started":
                current_message_id = str(normalized.get("message_id") or current_message_id)
            if kind == "error":
                failed = True
            self.events.emit(kind, normalized)
            if kind == "message_finished":
                current_message_id = ""

        try:
            with self.agent.conversations.bind_session(turn.session_id):
                self.agent.runner.run_interaction_events(
                    text,
                    emit=emit,
                    approve=self._approve_tool,
                    request_text=self._request_text,
                    cancel_token=token,
                )
                if not self.agent.conversations.save_active(reason="web_turn"):
                    raise RuntimeError("Failed to persist the completed turn.")
        except Exception as exc:
            failed = True
            emit("error", str(exc))
        finally:
            try:
                self.workspace.refresh()
            except Exception as exc:
                failed = True
                self.events.emit("runtime_warning", {"turn_id": turn_id, "error": str(exc)})
            status = "failed" if failed else ("stopped" if token.cancelled else "finished")
            with self._task_lock:
                if self._active_turn and self._active_turn.turn_id == turn_id:
                    self._active_turn = None
                    self._cancel_token = None
                    self._task_thread = None
                self.events.emit(
                    "turn_finished",
                    {
                        "turn_id": turn_id,
                        "session_id": turn.session_id,
                        "status": status,
                        **self._runtime_identity(),
                    },
                )

    def _normalize_worker_payload(
        self,
        kind: str,
        payload: Any,
        turn_id: str,
        current_message_id: str,
        sequence: int,
    ) -> dict[str, Any]:
        if isinstance(payload, dict):
            data = dict(payload)
        elif kind in ("content_delta", "thought_delta"):
            data = {"delta": "" if payload is None else str(payload)}
        elif payload is None:
            data = {}
        else:
            data = {"value": payload}
        data.setdefault("turn_id", turn_id)
        data.setdefault("sequence", sequence)
        if kind == "message_started":
            data.setdefault("message_id", uuid.uuid4().hex)
        elif kind in ("content_delta", "thought_delta", "message_finished") and current_message_id:
            data.setdefault("message_id", current_message_id)
        if kind.startswith("tool_"):
            tool_id = str(data.get("tool_call_id") or data.get("id") or "")
            if tool_id:
                data.setdefault("id", tool_id)
                data.setdefault("tool_call_id", tool_id)
        return data

    def stop_current_task(self) -> dict[str, Any]:
        with self._task_lock:
            if self._cancel_token is None or self._active_turn is None:
                return {"ok": False, "message": "No active task."}
            self._cancel_token.cancel()
        resolved: list[str] = []
        with self._approval_lock:
            pending_items = list(self._pending_approvals.items())
        for request_id, pending in pending_items:
            pending.resolve(pending.stop_choice())
            resolved.append(request_id)
        for request_id in resolved:
            self.events.emit("tool_approval_resolved", {"id": request_id, "choice": "stopped"})
        self.events.emit("stop_requested", {"status": "stopped", "resolved_approvals": resolved})
        return {"ok": True, "message": "Stop requested."}

    def _approve_tool(self, prompt: str, options: list[str], default_index: int) -> int:
        request_id = uuid.uuid4().hex
        pending = _PendingApproval(options=options, default_index=default_index)
        with self._approval_lock:
            self._pending_approvals[request_id] = pending
        self.events.emit("tool_approval_requested", {
            "id": request_id,
            "prompt": prompt,
            "options": options,
            "default_index": default_index,
        })
        choice = pending.wait(timeout=3600)
        with self._approval_lock:
            self._pending_approvals.pop(request_id, None)
        return choice

    def resolve_approval(self, request_id: str, choice: int) -> dict[str, Any]:
        with self._approval_lock:
            pending = self._pending_approvals.get(request_id)
        if pending is None:
            return {"ok": False, "error": "Approval request not found."}
        pending.resolve(choice)
        self.events.emit("tool_approval_resolved", {"id": request_id, "choice": choice})
        return {"ok": True}

    def _request_text(self, prompt: str) -> str:
        self.events.emit("text_requested", {"prompt": prompt})
        return ""

    def shutdown(self) -> None:
        with self._mutation_lock, self._task_lock:
            if self._closing:
                return
            self._closing = True
            thread = self._task_thread
        self.stop_current_task()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)
            if thread.is_alive():
                self.mark_degraded("Task worker did not stop before the shutdown timeout.")
        self.agent.shutdown()


class ConfigService:
    def __init__(self, runtime: KairoRuntime):
        self.runtime = runtime

    def redacted(self) -> dict[str, Any]:
        draft = ConfigDraft.from_config(self.runtime.config)
        data = draft.export_config(with_keys=False)
        data["active"] = self.runtime.status()
        raw_profiles = self._raw_profile_map()
        data["profiles_summary"] = [
            {
                "id": profile.id,
                "label": profile.label,
                "provider": profile.provider,
                "model": profile.model,
                "base_url": profile.base_url,
                "api_key": mask_key(profile.api_key),
                "api_key_source": describe_key_source(profile.api_key, profile.api_key_source),
                "api_key_env": str(raw_profiles.get(profile.id, {}).get("api_key_env", "")),
                "has_inline_key": bool(str(raw_profiles.get(profile.id, {}).get("api_key", "")).strip()),
                "context_window": profile.context_window,
                "max_tokens": profile.max_tokens,
                "temperature": profile.temperature,
            }
            for profile in list_profiles(self.runtime.config)
        ]
        return data

    def _raw_profile_map(self) -> dict[str, dict[str, Any]]:
        llm = self.runtime.config.llm if hasattr(self.runtime.config, "llm") else {}
        values: dict[str, dict[str, Any]] = {}
        if isinstance(llm.get("profiles"), list) and llm.get("profiles"):
            for profile in llm.get("profiles", []):
                if not isinstance(profile, dict):
                    continue
                profile_id = str(profile.get("id", "")).strip()
                if profile_id:
                    values[profile_id] = profile
            return values
        for provider in llm.get("providers", []):
            if not isinstance(provider, dict):
                continue
            provider_name = str(provider.get("name", "")).strip()
            for model in provider.get("models", []):
                if not isinstance(model, dict):
                    continue
                model_name = str(model.get("name", "")).strip()
                profile_id = f"{provider_name}/{model_name}" if provider_name and model_name else (provider_name or model_name)
                if profile_id:
                    values[profile_id] = {
                        "api_key": provider.get("api_key", ""),
                        "api_key_env": provider.get("api_key_env", ""),
                    }
        return values

    def settings_view(self) -> dict[str, Any]:
        config = self.runtime.config
        redacted = self.redacted()
        profiles = redacted.get("profiles_summary", [])
        provider_map: dict[str, dict[str, Any]] = {}
        for profile in profiles:
            provider_id = str(profile.get("provider") or profile.get("id", "").split("/", 1)[0]).strip()
            if not provider_id:
                provider_id = str(profile.get("id", "provider")).strip()
            entry = provider_map.setdefault(
                provider_id,
                {
                    "id": provider_id,
                    "name": provider_id,
                    "base_url": profile.get("base_url", ""),
                    "api_key": profile.get("api_key", ""),
                    "api_key_source": profile.get("api_key_source", "missing"),
                    "api_key_env": profile.get("api_key_env", ""),
                    "has_inline_key": bool(profile.get("has_inline_key")),
                    "model_count": 0,
                    "profiles": [],
                    "profile_ids": [],
                },
            )
            entry["model_count"] += 1
            entry["profiles"].append(profile.get("id", ""))
            entry["profile_ids"].append(profile.get("id", ""))
            if not entry.get("base_url") and profile.get("base_url"):
                entry["base_url"] = profile.get("base_url", "")
            if not entry.get("api_key_env") and profile.get("api_key_env"):
                entry["api_key_env"] = profile.get("api_key_env", "")
            if profile.get("has_inline_key"):
                entry["has_inline_key"] = True
            if entry.get("api_key_source") in ("missing", "none") and profile.get("api_key_source"):
                entry["api_key"] = profile.get("api_key", "")
                entry["api_key_source"] = profile.get("api_key_source", "")
        assistant_extra = config._extra_fields.get("assistant", {}) if isinstance(config._extra_fields.get("assistant"), dict) else {}
        user_extra = config._extra_fields.get("user", {}) if isinstance(config._extra_fields.get("user"), dict) else {}
        appearance_extra = config._extra_fields.get("appearance", {}) if isinstance(config._extra_fields.get("appearance"), dict) else {}
        backend_version = _package_version()
        static_version = _web_static_version()
        return {
            "version": backend_version,
            "diagnostics": {
                "backend_version": backend_version,
                "static_version": static_version,
                "version_match": not static_version or static_version == backend_version,
            },
            "general": {
                "language": appearance_extra.get("language", "system"),
                "shell_type": config.shell_type,
                "authorization_level": config.authorization_level,
                "plan_mode": config.plan_mode,
                "thinking_mode": config.thinking_mode,
                "open_browser": bool(config.web.get("open_browser", True)),
                "show_thinking": bool(assistant_extra.get("show_thinking", config.thinking_mode)),
                "expand_tools": bool(assistant_extra.get("expand_tools", False)),
            },
            "providers": list(provider_map.values()),
            "profiles": profiles,
            "roles": dict(config.model_roles),
            "assistant": {
                "name": assistant_extra.get("name", "Kai"),
                "system_prompt": assistant_extra.get("system_prompt", ""),
                "default_mode": assistant_extra.get("default_mode", "chat"),
                "authorization_level": config.authorization_level,
                "plan_mode": config.plan_mode,
                "thinking_mode": config.thinking_mode,
                "context_management": copy.deepcopy(config.context_management_defaults),
            },
            "user": {
                "name": user_extra.get("name", ""),
                "timezone": user_extra.get("timezone", ""),
                "preferences": user_extra.get("preferences", ""),
                "default_instruction": user_extra.get("default_instruction", ""),
            },
            "workbench": {
                "workspace_root": config.workspace_root,
                "skills_dir": config.skills_dir,
                "shell_type": config.shell_type,
                "workspace_bookmarks": list(config.workspace_bookmarks),
                "workspace_max_files": config.ui.get("workspace_max_files", 2000),
                "workspace_diff_max_bytes": config.ui.get("workspace_diff_max_bytes", 204800),
                "workspace_refresh_seconds": config.ui.get("workspace_refresh_seconds", 2.0),
            },
            "appearance": {
                "theme": config.web.get("theme", config.ui.get("theme", "kairo-dark")),
                "tui_theme": config.ui.get("theme", "kairo-dark"),
                "density": appearance_extra.get("density", "comfortable"),
                "font_size": int(appearance_extra.get("font_size", 14)),
                "animation": config.ui.get("animation", "full"),
                "mascot": bool(config.ui.get("mascot", True)),
                "reduced_motion": bool(config.ui.get("reduced_motion", False)),
            },
            "skills": {
                "skills_dir": config.skills_dir,
                "require_hash": bool(config.policy.get("skills", {}).get("require_hash", False)),
            },
            "raw": redacted,
        }

    def update(self, section: str, values: dict[str, Any]) -> dict[str, Any]:
        config = self.runtime.config
        draft = ConfigDraft.from_config(config)
        section = (section or "").strip().lower()
        values = values or {}
        if section == "ui":
            draft.ui.update({key: values[key] for key in values if key in draft.ui})
        elif section == "web":
            draft.web.update(values)
            draft.web = config._normalize_web(draft.web)
        elif section == "assistant":
            if "authorization_level" in values:
                draft.authorization_level = str(values["authorization_level"])
            if "plan_mode" in values:
                draft.plan_mode = bool(values["plan_mode"])
            if "thinking_mode" in values:
                draft.thinking_mode = bool(values["thinking_mode"])
            if isinstance(values.get("context_management"), dict):
                draft.context_management_defaults.update(values["context_management"])
        elif section == "workbench":
            if "workspace_root" in values:
                workspace_root = self._validated_workspace_root(str(values["workspace_root"]))
                if workspace_root is None:
                    return {"ok": False, "error": f"Workspace root is invalid or not writable: {values['workspace_root']}"}
                draft.workspace_root = str(workspace_root)
            if "skills_dir" in values:
                draft.skills_dir = str(values["skills_dir"])
            if "shell_type" in values:
                draft.shell_type = str(values["shell_type"])
            if isinstance(values.get("workspace_bookmarks"), list):
                draft.workspace_bookmarks = list(values["workspace_bookmarks"])
        elif section == "roles":
            draft.model_roles = {str(k): str(v) for k, v in values.items() if str(k).strip()}
        elif section == "llm":
            if not isinstance(values, dict):
                return {"ok": False, "error": "llm update payload must be an object"}
            if isinstance(values.get("defaults"), dict):
                draft.llm.setdefault("defaults", {}).update(values["defaults"])
            for profile in values.get("profiles", []) if isinstance(values.get("profiles"), list) else []:
                if not isinstance(profile, dict):
                    continue
                profile_id = str(profile.get("id", "")).strip()
                if not profile_id:
                    continue
                updates = {
                    "label": str(profile.get("label", "")),
                    "provider": str(profile.get("provider", "")),
                    "base_url": str(profile.get("base_url", "")),
                    "api_key_env": str(profile.get("api_key_env", "")),
                    "model": str(profile.get("model", "")),
                    "temperature": float(profile.get("temperature", 0.2)),
                    "max_tokens": int(profile.get("max_tokens", 4000)),
                    "context_window": int(profile.get("context_window", 128000)),
                    "context_management": profile.get("context_management")
                    if isinstance(profile.get("context_management"), dict)
                    else None,
                }
                api_key = str(profile.get("api_key", ""))
                if api_key and not is_masked_key(api_key):
                    updates["api_key"] = api_key
                existing = draft.update_profile(profile_id, **updates)
                if not existing:
                    draft.add_profile(
                        id=profile_id,
                        label=updates["label"],
                        provider=updates["provider"],
                        base_url=updates["base_url"],
                        api_key=updates.get("api_key", ""),
                        api_key_env=updates["api_key_env"],
                        model=updates["model"],
                        temperature=updates["temperature"],
                        max_tokens=updates["max_tokens"],
                        context_window=updates["context_window"],
                        context_management=updates["context_management"],
                    )
            active_profile = str(values.get("active_profile", "")).strip()
            if active_profile:
                draft.llm["active_profile"] = active_profile
        elif section == "key":
            profile_id = str(values.get("profile_id", "")).strip()
            if not profile_id:
                return {"ok": False, "error": "profile_id is required"}
            action = str(values.get("action", "set"))
            if action == "clear":
                ok = draft.update_profile(profile_id, api_key=KEY_CLEAR)
            else:
                ok = draft.update_profile(profile_id, api_key=str(values.get("api_key", "")))
            if not ok:
                return {"ok": False, "error": f"Profile '{profile_id}' not found."}
        else:
            return {"ok": False, "error": f"Unsupported config section: {section}"}
        return self._commit_draft(draft, f"config:{section}")

    def update_settings(self, section: str, values: dict[str, Any]) -> dict[str, Any]:
        section = (section or "").strip().lower()
        values = values or {}
        draft = ConfigDraft.from_config(self.runtime.config)
        if section == "general":
            if "language" in values:
                appearance = dict(draft.extra_fields.get("appearance", {}))
                appearance["language"] = str(values.get("language") or "system")
                draft.extra_fields["appearance"] = appearance
            if "shell_type" in values:
                draft.shell_type = str(values["shell_type"])
            if "authorization_level" in values:
                draft.authorization_level = str(values["authorization_level"])
            if "plan_mode" in values:
                draft.plan_mode = bool(values["plan_mode"])
            if "thinking_mode" in values:
                draft.thinking_mode = bool(values["thinking_mode"])
            if "open_browser" in values:
                draft.web["open_browser"] = bool(values["open_browser"])
            assistant = dict(draft.extra_fields.get("assistant", {}))
            if "show_thinking" in values:
                assistant["show_thinking"] = bool(values["show_thinking"])
            if "expand_tools" in values:
                assistant["expand_tools"] = bool(values["expand_tools"])
            if assistant:
                draft.extra_fields["assistant"] = assistant
        elif section == "roles":
            draft.model_roles = {str(k): str(v) for k, v in values.items() if str(k).strip() and str(v).strip()}
        elif section == "assistant":
            assistant = dict(draft.extra_fields.get("assistant", {}))
            for key in ("name", "system_prompt", "default_mode"):
                if key in values:
                    assistant[key] = str(values.get(key, ""))
            if "authorization_level" in values:
                draft.authorization_level = str(values["authorization_level"])
            if "plan_mode" in values:
                draft.plan_mode = bool(values["plan_mode"])
            if "thinking_mode" in values:
                draft.thinking_mode = bool(values["thinking_mode"])
            if isinstance(values.get("context_management"), dict):
                draft.context_management_defaults.update(values["context_management"])
            draft.extra_fields["assistant"] = assistant
        elif section == "user":
            draft.extra_fields["user"] = {
                "name": str(values.get("name", "")),
                "timezone": str(values.get("timezone", "")),
                "preferences": str(values.get("preferences", "")),
                "default_instruction": str(values.get("default_instruction", "")),
            }
        elif section == "workbench":
            if "workspace_root" in values:
                workspace_root = self._validated_workspace_root(str(values["workspace_root"]))
                if workspace_root is None:
                    return {"ok": False, "error": f"Workspace root is invalid or not writable: {values['workspace_root']}"}
                draft.workspace_root = str(workspace_root)
            if "skills_dir" in values:
                draft.skills_dir = str(values["skills_dir"])
            if "shell_type" in values:
                draft.shell_type = str(values["shell_type"])
            if isinstance(values.get("workspace_bookmarks"), list):
                draft.workspace_bookmarks = list(values["workspace_bookmarks"])
            for key in ("workspace_max_files", "workspace_diff_max_bytes"):
                if key in values:
                    draft.ui[key] = int(values[key])
            if "workspace_refresh_seconds" in values:
                draft.ui["workspace_refresh_seconds"] = float(values["workspace_refresh_seconds"])
        elif section == "appearance":
            appearance = dict(draft.extra_fields.get("appearance", {}))
            if "theme" in values:
                draft.web["theme"] = str(values["theme"])
            if "tui_theme" in values:
                draft.ui["theme"] = str(values["tui_theme"])
            for key in ("density", "font_size"):
                if key in values:
                    appearance[key] = values[key]
            if "animation" in values:
                draft.ui["animation"] = str(values["animation"])
            if "mascot" in values:
                draft.ui["mascot"] = bool(values["mascot"])
            if "reduced_motion" in values:
                draft.ui["reduced_motion"] = bool(values["reduced_motion"])
            draft.extra_fields["appearance"] = appearance
        elif section == "skills":
            if "skills_dir" in values:
                draft.skills_dir = str(values["skills_dir"])
            if "require_hash" in values:
                draft.policy.setdefault("skills", {})["require_hash"] = bool(values["require_hash"])
        else:
            return {"ok": False, "error": f"Unsupported settings section: {section}"}
        return self._commit_draft(draft, f"settings:{section}", view=True)

    def save_provider(self, provider_id: str, values: dict[str, Any], *, create: bool = False) -> dict[str, Any]:
        provider_id = (provider_id or values.get("id") or values.get("name") or "").strip()
        if not provider_id:
            return {"ok": False, "error": "Provider id is required."}
        draft = ConfigDraft.from_config(self.runtime.config)
        api_key = values.get("api_key")
        if isinstance(api_key, str) and (not api_key.strip() or is_masked_key(api_key)):
            api_key = None
        if values.get("clear_key"):
            api_key = KEY_CLEAR
        if create:
            model_name = str(values.get("model") or f"{provider_id}-model").strip()
            ok = draft.add_profile(
                id=str(values.get("profile_id") or f"{provider_id}/{model_name}"),
                label=str(values.get("label") or model_name),
                provider=provider_id,
                base_url=str(values.get("base_url", "")),
                api_key=api_key if isinstance(api_key, str) else "",
                api_key_env=str(values.get("api_key_env", "")),
                model=model_name,
                temperature=float(values.get("temperature", draft.llm.get("defaults", {}).get("temperature", 0.2))),
                max_tokens=int(values.get("max_tokens", draft.llm.get("defaults", {}).get("max_tokens", 4000))),
                context_window=int(values.get("context_window", draft.llm.get("defaults", {}).get("context_window", 128000))),
            )
            if not ok:
                return {"ok": False, "error": f"Provider/profile '{provider_id}' already exists or is invalid."}
        else:
            ok = draft.update_provider(
                provider_id,
                base_url=str(values["base_url"]) if "base_url" in values else None,
                api_key=api_key,
                api_key_env=str(values["api_key_env"]) if "api_key_env" in values else None,
                rename=str(values["name"]) if "name" in values else None,
            )
            if not ok:
                return {"ok": False, "error": f"Provider '{provider_id}' not found."}
        return self._commit_draft(draft, f"provider:{provider_id}", view=True)

    def delete_provider(self, provider_id: str) -> dict[str, Any]:
        draft = ConfigDraft.from_config(self.runtime.config)
        if not draft.remove_provider(provider_id):
            return {"ok": False, "error": f"Provider '{provider_id}' not found."}
        return self._commit_draft(draft, f"provider-delete:{provider_id}", view=True)

    def test_provider(self, provider_id: str) -> dict[str, Any]:
        view = self.settings_view()
        provider = next((item for item in view["providers"] if item["id"] == provider_id), None)
        if not provider:
            return {"ok": False, "status": "missing", "message": f"Provider '{provider_id}' was not found."}
        base_url = str(provider.get("base_url", "")).strip()
        key_source = str(provider.get("api_key_source", "")).lower()
        issues = []
        if not (base_url.startswith("http://") or base_url.startswith("https://")):
            issues.append("Base URL must start with http:// or https://.")
        if "missing" in key_source or key_source in ("", "none"):
            issues.append("API key is missing.")
        if issues:
            return {"ok": False, "status": "warning", "message": " ".join(issues), "provider": provider}
        return {"ok": True, "status": "ready", "message": "Local provider configuration looks ready.", "provider": provider}

    def save_profile(self, profile_id: str, values: dict[str, Any], *, create: bool = False) -> dict[str, Any]:
        profile_id = (profile_id or values.get("id") or "").strip()
        if not profile_id:
            return {"ok": False, "error": "Profile id is required."}
        draft = ConfigDraft.from_config(self.runtime.config)
        payload = self._profile_payload(values)
        if create:
            ok = draft.add_profile(id=profile_id, **payload)
        else:
            ok = draft.update_profile(profile_id, **payload, new_id=str(values["new_id"]) if values.get("new_id") else None)
        if not ok:
            return {"ok": False, "error": f"Profile '{profile_id}' could not be saved."}
        active = str(values.get("active_profile", "")).strip()
        if active:
            draft.set_active_profile(active)
        return self._commit_draft(draft, f"profile:{profile_id}", view=True)

    def delete_profile(self, profile_id: str) -> dict[str, Any]:
        draft = ConfigDraft.from_config(self.runtime.config)
        if not draft.remove_profile(profile_id):
            return {"ok": False, "error": f"Profile '{profile_id}' not found."}
        return self._commit_draft(draft, f"profile-delete:{profile_id}", view=True)

    def switch_profile(self, profile_id: str) -> dict[str, Any]:
        def apply() -> dict[str, Any]:
            result = self.runtime.agent.switch_model_profile(profile_id, source="web")
            self.runtime.events.emit("config_updated", {"section": "model", "result": result.data})
            return {"ok": result.success, "message": result.message, "data": result.data}

        return self.runtime.run_mutation("switch model profile", apply)

    def export_config(self, *, with_keys: bool = False, confirm: str = "") -> dict[str, Any]:
        if with_keys and confirm != "EXPORT_KEYS":
            return {"ok": False, "error": "Exporting keys requires confirm='EXPORT_KEYS'."}
        draft = ConfigDraft.from_config(self.runtime.config)
        return {
            "ok": True,
            "with_keys": with_keys,
            "config": draft.export_config(with_keys=with_keys),
        }

    def import_config(self, path: str) -> dict[str, Any]:
        draft = ConfigDraft.from_config(self.runtime.config)
        report = draft.import_config(path)
        if not report.ok:
            return {"ok": False, "error": report.to_text()}
        return self._commit_draft(draft, "config:import")

    def _profile_payload(self, values: dict[str, Any]) -> dict[str, Any]:
        api_key = values.get("api_key")
        if isinstance(api_key, str) and (not api_key.strip() or is_masked_key(api_key)):
            api_key = None
        if values.get("clear_key"):
            api_key = KEY_CLEAR
        payload: dict[str, Any] = {
            "label": str(values.get("label", "")),
            "provider": str(values.get("provider", "")),
            "base_url": str(values.get("base_url", "")),
            "api_key_env": str(values.get("api_key_env", "")),
            "model": str(values.get("model", "")),
            "temperature": float(values.get("temperature", 0.2)),
            "max_tokens": int(values.get("max_tokens", 4000)),
            "context_window": int(values.get("context_window", 128000)),
        }
        if api_key is not None:
            payload["api_key"] = api_key
        if isinstance(values.get("context_management"), dict):
            payload["context_management"] = values["context_management"]
        return payload

    def _commit_draft(self, draft: ConfigDraft, section: str, *, view: bool = False) -> dict[str, Any]:
        return self.runtime.run_mutation(
            f"update {section}",
            lambda: self._commit_draft_locked(draft, section, view=view),
        )

    def _commit_draft_locked(self, draft: ConfigDraft, section: str, *, view: bool = False) -> dict[str, Any]:
        preflight = self._preflight_draft(draft)
        if not preflight.get("ok"):
            return {"ok": False, "error": preflight.get("error", "Configuration preflight failed.")}
        previous = {
            "workspace_root": str(self.runtime.agent.workspace_context.root),
            "skills_dir": self.runtime.config.skills_dir,
            "shell_type": self.runtime.config.shell_type,
            "skills_require_hash": bool(self.runtime.config.policy.get("skills", {}).get("require_hash", False)),
        }
        rollback = ConfigDraft.from_config(self.runtime.config)
        old_root = str(self.runtime.agent.workspace_context.root)
        report = draft.apply_to(self.runtime.config, backup=True)
        if not report.ok:
            return {"ok": False, "error": report.to_text()}
        sync_result = self._sync_runtime_after_commit(previous)
        if not sync_result.get("ok"):
            failed_state = {
                "workspace_root": str(self.runtime.agent.workspace_context.root),
                "skills_dir": self.runtime.config.skills_dir,
                "shell_type": self.runtime.config.shell_type,
                "skills_require_hash": bool(
                    self.runtime.config.policy.get("skills", {}).get("require_hash", False)
                ),
            }
            rollback_report = rollback.apply_to(self.runtime.config, backup=False)
            rollback_sync = self._sync_runtime_after_commit(failed_state)
            rollback_failed = not rollback_report.ok or not rollback_sync.get("ok")
            error = sync_result.get("error", "Runtime sync failed.")
            if rollback_failed:
                rollback_error = rollback_report.to_text() if not rollback_report.ok else rollback_sync.get("error", "")
                self.runtime.mark_degraded(f"{error} Rollback failed: {rollback_error}")
            self.runtime.events.emit(
                "workspace_change_failed",
                {
                    "error": error,
                    "rollback_failed": rollback_failed,
                    "previous_root": old_root,
                    **self.runtime._runtime_identity(),
                },
            )
            return _failure("runtime_sync_failed", error, retryable=not rollback_failed)

        new_root = str(self.runtime.agent.workspace_context.root)
        workspace_changed = Path(new_root).resolve() != Path(old_root).resolve()
        if workspace_changed:
            self.runtime.previous_workspace_root = old_root
            self.runtime.workspace_revision += 1
            snapshot = self.runtime.workspace.snapshot()
            event_payload = {
                "previous_root": old_root,
                "snapshot": snapshot,
                "status": self.runtime.status(),
                **self.runtime._runtime_identity(),
            }
            self.runtime.events.emit("workspace_changed", event_payload)
        if sync_result.get("skills_changed"):
            self.runtime.events.emit("skills_updated", self.runtime.skills.list())
        self.runtime.events.emit(
            "config_updated",
            {"section": section, **self.runtime._runtime_identity()},
        )
        return {
            "ok": True,
            "settings": self.settings_view() if view else None,
            "config": self.redacted(),
            "workspace_changed": workspace_changed,
            "previous_root": old_root,
            "snapshot": self.runtime.workspace.snapshot(),
            "status": self.runtime.status(),
            **self.runtime._runtime_identity(),
        }

    def _preflight_draft(self, draft: ConfigDraft) -> dict[str, Any]:
        current = self.runtime.config
        workspace_changed = str(draft.workspace_root) != str(current.workspace_root)
        skills_changed = str(draft.skills_dir) != str(current.skills_dir)
        if workspace_changed:
            workspace_root = self._validated_workspace_root(draft.workspace_root)
            if workspace_root is None:
                return {"ok": False, "error": f"Workspace root is invalid or not writable: {draft.workspace_root}"}
            draft.workspace_root = str(workspace_root)
        else:
            workspace_root = Path(self.runtime.agent.workspace_context.root)
        if skills_changed and draft.skills_dir:
            try:
                skills_path = Path(draft.skills_dir).expanduser()
                if not skills_path.is_absolute():
                    skills_path = workspace_root / skills_path
                if skills_path.exists() and not skills_path.is_dir():
                    return {"ok": False, "error": f"Skills path is not a directory: {draft.skills_dir}"}
            except Exception:
                return {"ok": False, "error": f"Skills path is invalid: {draft.skills_dir}"}
        return {"ok": True}

    def _validated_workspace_root(self, value: str) -> Path | None:
        try:
            path = Path(value).expanduser().resolve()
        except Exception:
            return None
        if not path.exists() or not path.is_dir():
            return None
        probe = path / f".kairo-write-probe-{uuid.uuid4().hex}.tmp"
        try:
            descriptor = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            probe.unlink()
        except Exception:
            with suppress(OSError):
                probe.unlink()
            return None
        return path

    def _sync_runtime_after_commit(self, previous: dict[str, Any]) -> dict[str, Any]:
        config = self.runtime.config
        agent = self.runtime.agent
        config._sync_runtime_fields()
        manager = agent.conversations
        if hasattr(agent, "refresh_system_instruction"):
            agent.refresh_system_instruction(update_histories=True)
        manager.set_context_window(config.context_window)
        manager._autosave = bool(config.sessions.get("autosave", True))
        manager._max_sessions = int(config.sessions.get("max_sessions", 0) or 0)
        manager._save_interval_seconds = float(config.sessions.get("save_interval_seconds", 0) or 0)

        target_root = Path(config.workspace_root).expanduser().resolve()
        current_root = Path(agent.workspace_context.root).expanduser().resolve()
        workspace_changed = target_root != current_root
        if workspace_changed:
            result = agent.move_workspace(target_root, persist=False)
            if not result.success:
                return {"ok": False, "error": result.message}
        else:
            manager.update_runtime_state(
                workspace_root=str(agent.workspace_context.root),
                model_profile=config.active_model_profile,
                authorization_level=config.authorization_level,
            )

        shell_changed = previous["shell_type"] != config.shell_type
        if shell_changed and not workspace_changed:
            shell = agent.registry.tools.get("run_command")
            if shell is not None and hasattr(shell, "_on_workspace_moved"):
                shell._on_workspace_moved(agent.workspace_context.root)

        skills_changed = (
            workspace_changed
            or previous["skills_dir"] != config.skills_dir
            or previous["skills_require_hash"] != bool(config.policy.get("skills", {}).get("require_hash", False))
        )
        if skills_changed and not workspace_changed and hasattr(agent.registry, "reload_custom_skills"):
            agent.registry.reload_custom_skills(
                config.skills_dir,
                require_hash=bool(config.policy.get("skills", {}).get("require_hash", False)),
                workspace_root=agent.workspace_context.root,
            )
        if not manager.save_all(reason="web_settings_update"):
            return {"ok": False, "error": "Failed to persist sessions after runtime sync."}
        return {"ok": True, "skills_changed": skills_changed}


class SessionService:
    def __init__(self, runtime: KairoRuntime):
        self.runtime = runtime

    def list(self) -> dict[str, Any]:
        manager = self.runtime.agent.conversations
        return {
            "active_session_id": manager.active_session_id,
            "sessions": [
                {
                    "id": session.id,
                    "name": session.name,
                    "message_count": len(session.history),
                    "created_at": session.created_at.isoformat(),
                    "updated_at": session.updated_at.isoformat(),
                    "context_used": session.token_tracker.context_used_tokens,
                }
                for session in manager.sessions
            ],
        }

    def create(self, name: str | None = None) -> dict[str, Any]:
        return self.runtime.run_mutation("create session", lambda: self._create(name))

    def _create(self, name: str | None = None) -> dict[str, Any]:
        try:
            session = self.runtime.agent.conversations.create_session(name)
        except RuntimeError as exc:
            if "max_sessions" in str(exc):
                return _failure("max_sessions", str(exc), retryable=False)
            raise
        self.runtime.events.emit("session_changed", self.list())
        return {"ok": True, "session": {"id": session.id, "name": session.name}}

    def switch(self, session_id: str) -> dict[str, Any]:
        return self.runtime.run_mutation("switch session", lambda: self._switch(session_id))

    def _switch(self, session_id: str) -> dict[str, Any]:
        validation = self._validate_member(session_id)
        if validation is not None:
            return validation
        ok = self.runtime.agent.conversations.switch_session(session_id)
        if ok:
            self.runtime.events.emit("session_changed", self.list())
        return {"ok": ok}

    def rename(self, session_id: str, name: str) -> dict[str, Any]:
        return self.runtime.run_mutation("rename session", lambda: self._rename(session_id, name))

    def _rename(self, session_id: str, name: str) -> dict[str, Any]:
        manager = self.runtime.agent.conversations
        validation = self._validate_member(session_id)
        if validation is not None:
            return validation
        session = next(item for item in manager.sessions if item.id == session_id)
        store = manager.session_store
        if store is not None and not store.rename_session(session_id, name):
            return _failure(
                "session_persistence_failed",
                "Failed to rename session.",
                retryable=True,
            )
        session.name = name.strip()
        session.touch()
        manager.save_all(reason="web_session_rename")
        self.runtime.events.emit("session_changed", self.list())
        return {"ok": True}

    def delete(self, session_id: str) -> dict[str, Any]:
        return self.runtime.run_mutation("delete session", lambda: self._delete(session_id))

    def _delete(self, session_id: str) -> dict[str, Any]:
        manager = self.runtime.agent.conversations
        validation = self._validate_member(session_id)
        if validation is not None:
            return validation
        if len(manager.sessions) <= 1:
            return _failure("last_session", "Cannot delete the last session.", retryable=False)
        store = manager.session_store
        if store is not None and not store.delete_session(session_id):
            return _failure(
                "session_persistence_failed",
                "Failed to delete session.",
                retryable=True,
            )
        manager.sessions = [item for item in manager.sessions if item.id != session_id]
        if manager.active_session_id == session_id:
            manager.active_session_id = manager.sessions[0].id
            manager.refresh_context()
        manager.save_all(reason="web_session_delete")
        self.runtime.events.emit("session_changed", self.list())
        return {"ok": True}

    def search(self, keyword: str) -> dict[str, Any]:
        keyword = (keyword or "").strip().lower()
        results = []
        if keyword:
            for index, session in enumerate(self.runtime.agent.conversations.sessions):
                haystack = session.name.lower() + "\n" + "\n".join(
                    str(message.get("content", "")).lower() for message in session.history
                )
                if keyword in haystack:
                    results.append({"index": index, "id": session.id, "name": session.name})
        return {"keyword": keyword, "results": results}

    def export(self, session_id: str, fmt: str = "markdown") -> dict[str, Any]:
        validation = self._validate_member(session_id)
        if validation is not None:
            return validation
        store = self.runtime.agent.conversations.session_store
        if store is None:
            return _failure(
                "session_persistence_failed",
                "Session persistence is disabled.",
                retryable=False,
            )
        try:
            dest = store.export_session(session_id, fmt=fmt)
        except Exception as exc:
            return _failure("session_persistence_failed", f"Export failed: {exc}", retryable=True)
        if not dest:
            return _failure("session_persistence_failed", "Export failed.", retryable=True)
        return {"ok": True, "path": str(dest)}

    def _validate_member(self, session_id: str) -> dict[str, Any] | None:
        try:
            SessionStore.validate_session_id(session_id)
        except InvalidSessionIdError as exc:
            return _failure("invalid_session_id", str(exc), retryable=False)
        manager = self.runtime.agent.conversations
        if not any(item.id == session_id for item in manager.sessions):
            return _failure("session_not_found", "Session not found.", retryable=False)
        return None


class ChatService:
    def __init__(self, runtime: KairoRuntime):
        self.runtime = runtime

    def history(self) -> dict[str, Any]:
        messages = []
        for index, message in enumerate(self.runtime.agent.history):
            role = str(message.get("role", ""))
            if role == "system":
                continue
            item = {
                "id": f"history-{index}",
                "role": role,
                "content": str(message.get("content", "") or ""),
            }
            if message.get("name"):
                item["name"] = str(message.get("name"))
            if message.get("tool_call_id"):
                item["tool_call_id"] = str(message.get("tool_call_id"))
            if message.get("tool_calls"):
                item["tool_calls"] = copy.deepcopy(message.get("tool_calls"))
            messages.append(item)
        return {
            "session": {
                "id": self.runtime.agent.conversations.active.id,
                "name": self.runtime.agent.active_session_name,
            },
            "messages": messages,
        }


class WorkspaceService:
    def __init__(self, runtime: KairoRuntime):
        self.runtime = runtime
        config = runtime.config
        self.monitor = WorkspaceMonitor(
            runtime.agent.workspace_context.root,
            max_files=config.ui.get("workspace_max_files", 2000),
            max_diff_bytes=config.ui.get("workspace_diff_max_bytes", 204800),
        )
        runtime.agent.workspace_changed = self._workspace_changed

    def _workspace_changed(self, root: str) -> None:
        self.monitor = WorkspaceMonitor(
            Path(root),
            max_files=self.runtime.config.ui.get("workspace_max_files", 2000),
            max_diff_bytes=self.runtime.config.ui.get("workspace_diff_max_bytes", 204800),
        )

    def snapshot(self, selected_file: str = "") -> dict[str, Any]:
        snapshot = self.monitor.refresh(selected_file)
        data = _snapshot_to_dict(snapshot)
        data["file_count"] = len(snapshot.files)
        data["file_limit"] = self.monitor.max_files
        data.update(self.runtime._runtime_identity())
        return data

    def file_preview(self, relative: str) -> dict[str, Any]:
        relative = (relative or "").strip().replace("\\", "/")
        if not relative:
            return {"ok": False, "error": "path is required"}
        try:
            root = self.monitor.root.resolve()
            path = (root / relative).resolve()
            path.relative_to(root)
        except (OSError, ValueError):
            return {"ok": False, "error": "path must stay inside the active workspace"}
        if not path.exists() or not path.is_file():
            return {"ok": False, "error": "file not found"}

        max_bytes = int(self.runtime.config.ui.get("workspace_diff_max_bytes", 204800))
        try:
            size = path.stat().st_size
            with open(path, "rb") as handle:
                raw = handle.read(max_bytes + 1)
        except OSError as exc:
            return {"ok": False, "error": f"unable to read file: {exc}"}

        truncated = len(raw) > max_bytes
        sample = raw[:max_bytes]
        binary = b"\0" in sample[:8192]
        return {
            "ok": True,
            "path": relative,
            "size": size,
            "binary": binary,
            "truncated": truncated,
            "language": _language_hint(relative),
            "content": "" if binary else sample.decode("utf-8", errors="replace"),
        }

    def bookmarks(self) -> dict[str, Any]:
        return {"bookmarks": list(self.runtime.config.workspace_bookmarks)}

    def add_bookmark(self, name: str, path: str) -> dict[str, Any]:
        return self.runtime.run_mutation(
            "add workspace bookmark",
            lambda: self._add_bookmark(name, path),
        )

    def _add_bookmark(self, name: str, path: str) -> dict[str, Any]:
        draft = ConfigDraft.from_config(self.runtime.config)
        if not draft.add_workspace_bookmark(name, path):
            return {"ok": False, "error": "Bookmark name and path are required."}
        report = draft.apply_to(self.runtime.config, backup=True)
        if not report.ok:
            return {"ok": False, "error": report.to_text()}
        self.runtime.events.emit("config_updated", {"section": "workspace_bookmarks"})
        return {"ok": True, **self.bookmarks()}

    def remove_bookmark(self, name: str) -> dict[str, Any]:
        return self.runtime.run_mutation(
            "remove workspace bookmark",
            lambda: self._remove_bookmark(name),
        )

    def _remove_bookmark(self, name: str) -> dict[str, Any]:
        draft = ConfigDraft.from_config(self.runtime.config)
        if not draft.remove_workspace_bookmark(name):
            return {"ok": False, "error": f"Bookmark '{name}' not found."}
        report = draft.apply_to(self.runtime.config, backup=True)
        if not report.ok:
            return {"ok": False, "error": report.to_text()}
        self.runtime.events.emit("config_updated", {"section": "workspace_bookmarks"})
        return {"ok": True, **self.bookmarks()}

    def refresh(self) -> dict[str, Any]:
        data = self.snapshot()
        self.runtime.events.emit("workspace_updated", data)
        return data

    def move(self, target: str) -> dict[str, Any]:
        return self.runtime.run_mutation(
            "move workspace",
            lambda: self._move(target),
        )

    def _move(self, target: str) -> dict[str, Any]:
        draft = ConfigDraft.from_config(self.runtime.config)
        validated = self.runtime.config_service._validated_workspace_root(target)
        if validated is None:
            return _failure(
                "invalid_workspace",
                f"Workspace root is invalid or not writable: {target}",
                retryable=False,
            )
        draft.workspace_root = str(validated)
        result = self.runtime.config_service._commit_draft(draft, "workspace:move")
        if result.get("ok"):
            result["message"] = f"Workspace moved to: {validated}"
            result["root"] = str(validated)
        return result


class SkillService:
    def __init__(self, runtime: KairoRuntime):
        self.runtime = runtime

    def list(self) -> dict[str, Any]:
        registry = self.runtime.agent.registry
        tools = []
        for name, tool in registry.tools.items():
            tools.append({
                "name": name,
                "description": getattr(tool, "description", ""),
                "permission": getattr(getattr(tool, "permission", None), "value", str(getattr(tool, "permission", ""))),
                "source": getattr(tool, "source", "builtin"),
                "parameters": getattr(tool, "parameters", {}),
            })
        candidates = registry.list_custom_skills() if hasattr(registry, "list_custom_skills") else []
        warnings = list(getattr(registry, "custom_skill_warnings", []))
        if warnings:
            custom = {
                "status": "error",
                "files": [str(item.get("relative_path", "")) for item in candidates],
                "error": "; ".join(warnings),
            }
        elif not candidates:
            custom = {"status": "absent", "files": []}
        else:
            digests = {str(item.get("digest", "")) for item in candidates}
            statuses = {str(item.get("status", "pending")) for item in candidates}
            custom = {
                "status": (
                    "trusted"
                    if statuses == {"trusted"}
                    else ("changed" if "changed" in statuses else "untrusted")
                ),
                "manifest_digest": next(iter(digests)) if len(digests) == 1 else "",
                "files": [str(item.get("relative_path", "")) for item in candidates],
            }
        return {"tools": tools, "custom": custom, "candidates": candidates}

    def reload(self) -> dict[str, Any]:
        return self.runtime.run_mutation("reload skills", self._reload)

    def _reload(self) -> dict[str, Any]:
        registry = self.runtime.agent.registry
        if not hasattr(registry, "reload_custom_skills"):
            return {"ok": False, "error": "Skill reload is unavailable."}
        registry.reload_custom_skills(
            self.runtime.config.skills_dir,
            require_hash=self.runtime.config.policy.get("skills", {}).get("require_hash", False),
            workspace_root=self.runtime.agent.workspace_context.root,
        )
        self.runtime.events.emit("skills_updated", self.list())
        return {"ok": True, **self.list()}

    def trust(self, manifest_digest: str) -> dict[str, Any]:
        return self.runtime.run_mutation(
            "trust workspace skills",
            lambda: self._trust(manifest_digest),
        )

    def _trust(self, manifest_digest: str) -> dict[str, Any]:
        registry = self.runtime.agent.registry
        candidates = registry.list_custom_skills() if hasattr(registry, "list_custom_skills") else []
        if not candidates:
            return _failure("skills_absent", "No workspace skills were found.", retryable=False)
        current_digests = {str(item.get("digest", "")) for item in candidates}
        if not manifest_digest or current_digests != {manifest_digest}:
            return _failure(
                "skill_manifest_changed",
                "The skill manifest changed after review; refresh and review it again.",
                retryable=True,
            )
        try:
            registry.trust_all(manifest_digest)
        except Exception as exc:
            message = str(exc)
            if "changed" in message.lower() or "digest" in message.lower():
                return _failure("skill_manifest_changed", message, retryable=True)
            return _failure("skill_trust_failed", message, retryable=False)
        payload = self.list()
        self.runtime.events.emit("skills_updated", payload)
        return {"ok": True, **payload}

    def revoke(self) -> dict[str, Any]:
        return self.runtime.run_mutation("revoke workspace skills", self._revoke)

    def _revoke(self) -> dict[str, Any]:
        registry = self.runtime.agent.registry
        try:
            registry.revoke_all()
        except Exception as exc:
            return _failure("skill_revoke_failed", str(exc), retryable=False)
        payload = self.list()
        self.runtime.events.emit("skills_updated", payload)
        return {"ok": True, **payload}


class DoctorService:
    def __init__(self, runtime: KairoRuntime):
        self.runtime = runtime

    def run(self, *, local_only: bool = True) -> dict[str, Any]:
        result = handle_doctor(self.runtime.agent, "/doctor", ["/doctor"], local_only=local_only)
        return {"ok": result.success, "message": result.message, "checks": result.data.get("checks", [])}


class _PendingApproval:
    def __init__(self, options: list[str], default_index: int):
        self.options = options
        self.default_index = default_index
        self._choice: int | None = None
        self._event = threading.Event()

    def wait(self, timeout: float) -> int:
        if not self._event.wait(timeout):
            return self.default_index
        return self._choice if self._choice is not None else self.default_index

    def resolve(self, choice: int) -> None:
        if choice < 0 or choice >= len(self.options):
            choice = self.default_index
        self._choice = choice
        self._event.set()

    def stop_choice(self) -> int:
        for index, option in enumerate(self.options):
            if "stop" in str(option).lower():
                return index
        return self.default_index


def _failure(code: str, detail: str, *, retryable: bool) -> dict[str, Any]:
    """Return the stable error envelope shared by runtime services."""
    return {
        "ok": False,
        "code": code,
        "error": detail,
        "detail": detail,
        "retryable": retryable,
    }


def _snapshot_to_dict(snapshot: WorkspaceSnapshot) -> dict[str, Any]:
    return {
        "root": snapshot.root,
        "files": list(snapshot.files),
        "changes": [
            {
                "path": item.path,
                "status": item.status,
                "session_touched": item.session_touched,
                "staged": item.staged,
                "untracked": item.untracked,
            }
            for item in snapshot.changes
        ],
        "session_touched": list(snapshot.session_touched),
        "active_file": snapshot.active_file,
        "selected_file": snapshot.selected_file,
        "diff": snapshot.diff,
        "diff_truncated": snapshot.diff_truncated,
        "tree_truncated": snapshot.tree_truncated,
        "error": snapshot.error,
    }


def event_to_dict(event: RuntimeEvent) -> dict[str, Any]:
    return {"kind": event.kind, "payload": event.payload, "timestamp": event.timestamp, "sequence": event.sequence}


def event_stream(runtime: KairoRuntime) -> Iterable[dict[str, Any]]:
    q: queue.Queue[RuntimeEvent] = queue.Queue()
    unsubscribe = runtime.events.subscribe(q.put)
    try:
        for event in runtime.events.snapshot():
            yield event_to_dict(event)
        while True:
            yield event_to_dict(q.get())
    finally:
        unsubscribe()


def _package_version() -> str:
    from agent._version import __version__

    return __version__


def _web_static_version() -> str:
    from agent.web.assets import static_root

    version_json = static_root() / "version.json"
    try:
        import json

        with open(version_json, encoding="utf-8-sig") as handle:
            value = json.load(handle)
        return str(value.get("version", ""))
    except Exception:
        return ""


def _language_hint(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return {
        ".bat": "batch",
        ".cmd": "batch",
        ".css": "css",
        ".html": "html",
        ".js": "javascript",
        ".json": "json",
        ".jsx": "javascript",
        ".md": "markdown",
        ".py": "python",
        ".ps1": "powershell",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".toml": "toml",
        ".txt": "text",
        ".yml": "yaml",
        ".yaml": "yaml",
    }.get(suffix, "text")
