"""UI-neutral runtime and service layer for Kairo 0.3.2-preview."""
from __future__ import annotations

import copy
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from agent.bootstrap import build_agent
from agent.cancellation import CancellationToken
from agent.config import Config
from agent.config_editor import ConfigDraft, KEY_CLEAR
from agent.profile_resolver import describe_key_source, is_masked_key, list_profiles, mask_key
from agent.runtime_commands import handle_doctor
from agent.workspace import WorkspaceMonitor, WorkspaceSnapshot


@dataclass(frozen=True)
class RuntimeEvent:
    kind: str
    payload: Any = None
    timestamp: float = field(default_factory=time.time)


class RuntimeEventBus:
    """Thread-safe event bus shared by TUI, Web and tests."""

    def __init__(self, max_buffer: int = 1000):
        self.max_buffer = max(100, int(max_buffer))
        self._events: List[RuntimeEvent] = []
        self._subscribers: List[Callable[[RuntimeEvent], None]] = []
        self._lock = threading.RLock()

    def emit(self, kind: str, payload: Any = None) -> RuntimeEvent:
        event = RuntimeEvent(kind=kind, payload=payload)
        with self._lock:
            self._events.append(event)
            self._events = self._events[-self.max_buffer:]
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber(event)
            except Exception:
                pass
        return event

    def subscribe(self, callback: Callable[[RuntimeEvent], None]) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._subscribers.remove(callback)
                except ValueError:
                    pass

        return unsubscribe

    def snapshot(self) -> List[RuntimeEvent]:
        with self._lock:
            return list(self._events)


class KairoRuntime:
    """Application kernel used by plain, Textual and Web adapters."""

    def __init__(self, config: Config):
        self.config = config
        self.agent = build_agent(config)
        max_buffer = getattr(config, "web", {}).get("max_event_buffer", 1000)
        self.events = RuntimeEventBus(max_buffer=max_buffer)
        self.workspace = WorkspaceService(self)
        self.config_service = ConfigService(self)
        self.sessions = SessionService(self)
        self.chat = ChatService(self)
        self.skills = SkillService(self)
        self._task_lock = threading.RLock()
        self._task_thread: Optional[threading.Thread] = None
        self._cancel_token: Optional[CancellationToken] = None
        self._pending_approvals: Dict[str, "_PendingApproval"] = {}
        self._approval_lock = threading.RLock()

    def status(self) -> Dict[str, Any]:
        tracker = self.agent.token_tracker
        return {
            "version": _package_version(),
            "model": self.config.model,
            "profile": self.config.active_model_profile,
            "base_url": self.config.base_url,
            "api_key": self.config.describe_active_api_key(),
            "workspace_root": str(self.agent.workspace_context.root),
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
            },
        }

    def is_busy(self) -> bool:
        with self._task_lock:
            return bool(self._task_thread and self._task_thread.is_alive())

    def submit_message(self, text: str) -> Dict[str, Any]:
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "Message is empty."}
        with self._task_lock:
            if self.is_busy():
                return {"ok": False, "error": "Another task is already running."}
            turn_id = uuid.uuid4().hex
            token = CancellationToken()
            self._cancel_token = token
            self._task_thread = threading.Thread(
                target=self._run_message_worker,
                args=(turn_id, text, token),
                name=f"kairo-runtime-{turn_id[:8]}",
                daemon=True,
            )
            self._task_thread.start()
        return {"ok": True, "turn_id": turn_id}

    def _run_message_worker(self, turn_id: str, text: str, token: CancellationToken) -> None:
        self.events.emit("turn_started", {"turn_id": turn_id, "text": text})

        def emit(kind: str, payload: Any = None) -> None:
            self.events.emit(kind, payload)

        try:
            self.agent.runner.run_interaction_events(
                text,
                emit=emit,
                approve=self._approve_tool,
                request_text=self._request_text,
                cancel_token=token,
            )
        finally:
            self.agent.conversations.save_active(reason="web_turn")
            self.workspace.refresh()
            self.events.emit("turn_finished", {"turn_id": turn_id})

    def stop_current_task(self) -> Dict[str, Any]:
        with self._task_lock:
            if self._cancel_token is None or not self.is_busy():
                return {"ok": False, "message": "No active task."}
            self._cancel_token.cancel()
        self.events.emit("stop_requested", None)
        return {"ok": True, "message": "Stop requested."}

    def _approve_tool(self, prompt: str, options: List[str], default_index: int) -> int:
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

    def resolve_approval(self, request_id: str, choice: int) -> Dict[str, Any]:
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
        self.stop_current_task()
        self.agent.shutdown()


class ConfigService:
    def __init__(self, runtime: KairoRuntime):
        self.runtime = runtime

    def redacted(self) -> Dict[str, Any]:
        draft = ConfigDraft.from_config(self.runtime.config)
        data = draft.export_config(with_keys=False)
        data["active"] = self.runtime.status()
        data["profiles_summary"] = [
            {
                "id": profile.id,
                "label": profile.label,
                "provider": profile.provider,
                "model": profile.model,
                "base_url": profile.base_url,
                "api_key": mask_key(profile.api_key),
                "api_key_source": describe_key_source(profile.api_key, profile.api_key_source),
                "context_window": profile.context_window,
                "max_tokens": profile.max_tokens,
                "temperature": profile.temperature,
            }
            for profile in list_profiles(self.runtime.config)
        ]
        return data

    def settings_view(self) -> Dict[str, Any]:
        config = self.runtime.config
        redacted = self.redacted()
        profiles = redacted.get("profiles_summary", [])
        provider_map: Dict[str, Dict[str, Any]] = {}
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
                    "model_count": 0,
                    "profiles": [],
                },
            )
            entry["model_count"] += 1
            entry["profiles"].append(profile.get("id", ""))
            if not entry.get("base_url") and profile.get("base_url"):
                entry["base_url"] = profile.get("base_url", "")
            if entry.get("api_key_source") in ("missing", "none") and profile.get("api_key_source"):
                entry["api_key"] = profile.get("api_key", "")
                entry["api_key_source"] = profile.get("api_key_source", "")
        assistant_extra = config._extra_fields.get("assistant", {}) if isinstance(config._extra_fields.get("assistant"), dict) else {}
        user_extra = config._extra_fields.get("user", {}) if isinstance(config._extra_fields.get("user"), dict) else {}
        appearance_extra = config._extra_fields.get("appearance", {}) if isinstance(config._extra_fields.get("appearance"), dict) else {}
        return {
            "version": _package_version(),
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

    def update(self, section: str, values: Dict[str, Any]) -> Dict[str, Any]:
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
                draft.workspace_root = str(values["workspace_root"])
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
        report = draft.apply_to(config, backup=True)
        if not report.ok:
            return {"ok": False, "error": report.to_text()}
        config._sync_runtime_fields()
        self.runtime.agent.conversations.set_context_window(config.context_window)
        self.runtime.agent.conversations.update_runtime_state(
            model_profile=config.active_model_profile,
            authorization_level=config.authorization_level,
        )
        self.runtime.agent.conversations.save_all(reason="web_config_update")
        self.runtime.events.emit("config_updated", {"section": section})
        return {"ok": True, "config": self.redacted()}

    def update_settings(self, section: str, values: Dict[str, Any]) -> Dict[str, Any]:
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
                draft.workspace_root = str(values["workspace_root"])
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

    def save_provider(self, provider_id: str, values: Dict[str, Any], *, create: bool = False) -> Dict[str, Any]:
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

    def delete_provider(self, provider_id: str) -> Dict[str, Any]:
        draft = ConfigDraft.from_config(self.runtime.config)
        if not draft.remove_provider(provider_id):
            return {"ok": False, "error": f"Provider '{provider_id}' not found."}
        return self._commit_draft(draft, f"provider-delete:{provider_id}", view=True)

    def test_provider(self, provider_id: str) -> Dict[str, Any]:
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

    def save_profile(self, profile_id: str, values: Dict[str, Any], *, create: bool = False) -> Dict[str, Any]:
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

    def delete_profile(self, profile_id: str) -> Dict[str, Any]:
        draft = ConfigDraft.from_config(self.runtime.config)
        if not draft.remove_profile(profile_id):
            return {"ok": False, "error": f"Profile '{profile_id}' not found."}
        return self._commit_draft(draft, f"profile-delete:{profile_id}", view=True)

    def switch_profile(self, profile_id: str) -> Dict[str, Any]:
        result = self.runtime.agent.switch_model_profile(profile_id, source="web")
        self.runtime.events.emit("config_updated", {"section": "model", "result": result.data})
        return {"ok": result.success, "message": result.message, "data": result.data}

    def export_config(self, *, with_keys: bool = False, confirm: str = "") -> Dict[str, Any]:
        if with_keys and confirm != "EXPORT_KEYS":
            return {"ok": False, "error": "Exporting keys requires confirm='EXPORT_KEYS'."}
        draft = ConfigDraft.from_config(self.runtime.config)
        return {
            "ok": True,
            "with_keys": with_keys,
            "config": draft.export_config(with_keys=with_keys),
        }

    def import_config(self, path: str) -> Dict[str, Any]:
        draft = ConfigDraft.from_config(self.runtime.config)
        report = draft.import_config(path)
        if not report.ok:
            return {"ok": False, "error": report.to_text()}
        report = draft.apply_to(self.runtime.config, backup=True, allow_inline_key=True)
        if not report.ok:
            return {"ok": False, "error": report.to_text()}
        self.runtime.config._sync_runtime_fields()
        self.runtime.agent.conversations.set_context_window(self.runtime.config.context_window)
        self.runtime.agent.conversations.update_runtime_state(
            model_profile=self.runtime.config.active_model_profile,
            authorization_level=self.runtime.config.authorization_level,
        )
        self.runtime.agent.conversations.save_all(reason="web_config_import")
        self.runtime.events.emit("config_updated", {"section": "import"})
        return {"ok": True, "config": self.redacted()}

    def _profile_payload(self, values: Dict[str, Any]) -> Dict[str, Any]:
        api_key = values.get("api_key")
        if isinstance(api_key, str) and (not api_key.strip() or is_masked_key(api_key)):
            api_key = None
        if values.get("clear_key"):
            api_key = KEY_CLEAR
        payload: Dict[str, Any] = {
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

    def _commit_draft(self, draft: ConfigDraft, section: str, *, view: bool = False) -> Dict[str, Any]:
        report = draft.apply_to(self.runtime.config, backup=True)
        if not report.ok:
            return {"ok": False, "error": report.to_text()}
        self.runtime.config._sync_runtime_fields()
        self.runtime.agent.conversations.set_context_window(self.runtime.config.context_window)
        self.runtime.agent.conversations.update_runtime_state(
            model_profile=self.runtime.config.active_model_profile,
            authorization_level=self.runtime.config.authorization_level,
        )
        self.runtime.agent.conversations.save_all(reason="web_settings_update")
        self.runtime.events.emit("config_updated", {"section": section})
        return {"ok": True, "settings": self.settings_view() if view else None, "config": self.redacted()}


class SessionService:
    def __init__(self, runtime: KairoRuntime):
        self.runtime = runtime

    def list(self) -> Dict[str, Any]:
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

    def create(self, name: Optional[str] = None) -> Dict[str, Any]:
        session = self.runtime.agent.conversations.create_session(name)
        self.runtime.events.emit("session_changed", self.list())
        return {"ok": True, "session": {"id": session.id, "name": session.name}}

    def switch(self, session_id: str) -> Dict[str, Any]:
        ok = self.runtime.agent.conversations.switch_session(session_id)
        if ok:
            self.runtime.events.emit("session_changed", self.list())
        return {"ok": ok}

    def rename(self, session_id: str, name: str) -> Dict[str, Any]:
        manager = self.runtime.agent.conversations
        session = next((item for item in manager.sessions if item.id == session_id), None)
        if session is None:
            return {"ok": False, "error": "Session not found."}
        store = manager.session_store
        if store is not None and not store.rename_session(session_id, name):
            return {"ok": False, "error": "Failed to rename session."}
        session.name = name.strip()
        session.touch()
        manager.save_all(reason="web_session_rename")
        self.runtime.events.emit("session_changed", self.list())
        return {"ok": True}

    def delete(self, session_id: str) -> Dict[str, Any]:
        manager = self.runtime.agent.conversations
        if len(manager.sessions) <= 1:
            return {"ok": False, "error": "Cannot delete the last session."}
        store = manager.session_store
        if store is not None and not store.delete_session(session_id):
            return {"ok": False, "error": "Failed to delete session."}
        manager.sessions = [item for item in manager.sessions if item.id != session_id]
        if manager.active_session_id == session_id:
            manager.active_session_id = manager.sessions[0].id
            manager.refresh_context()
        manager.save_all(reason="web_session_delete")
        self.runtime.events.emit("session_changed", self.list())
        return {"ok": True}

    def search(self, keyword: str) -> Dict[str, Any]:
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

    def export(self, session_id: str, fmt: str = "markdown") -> Dict[str, Any]:
        store = self.runtime.agent.conversations.session_store
        if store is None:
            return {"ok": False, "error": "Session persistence is disabled."}
        dest = store.export_session(session_id, fmt=fmt)
        if not dest:
            return {"ok": False, "error": "Export failed."}
        return {"ok": True, "path": str(dest)}


class ChatService:
    def __init__(self, runtime: KairoRuntime):
        self.runtime = runtime

    def history(self) -> Dict[str, Any]:
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
        self.refresh()

    def snapshot(self, selected_file: str = "") -> Dict[str, Any]:
        return _snapshot_to_dict(self.monitor.refresh(selected_file))

    def file_preview(self, relative: str) -> Dict[str, Any]:
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

    def bookmarks(self) -> Dict[str, Any]:
        return {"bookmarks": list(self.runtime.config.workspace_bookmarks)}

    def add_bookmark(self, name: str, path: str) -> Dict[str, Any]:
        draft = ConfigDraft.from_config(self.runtime.config)
        if not draft.add_workspace_bookmark(name, path):
            return {"ok": False, "error": "Bookmark name and path are required."}
        report = draft.apply_to(self.runtime.config, backup=True)
        if not report.ok:
            return {"ok": False, "error": report.to_text()}
        self.runtime.events.emit("config_updated", {"section": "workspace_bookmarks"})
        return {"ok": True, **self.bookmarks()}

    def remove_bookmark(self, name: str) -> Dict[str, Any]:
        draft = ConfigDraft.from_config(self.runtime.config)
        if not draft.remove_workspace_bookmark(name):
            return {"ok": False, "error": f"Bookmark '{name}' not found."}
        report = draft.apply_to(self.runtime.config, backup=True)
        if not report.ok:
            return {"ok": False, "error": report.to_text()}
        self.runtime.events.emit("config_updated", {"section": "workspace_bookmarks"})
        return {"ok": True, **self.bookmarks()}

    def refresh(self) -> Dict[str, Any]:
        data = self.snapshot()
        self.runtime.events.emit("workspace_updated", data)
        return data

    def move(self, target: str) -> Dict[str, Any]:
        result = self.runtime.agent.move_workspace(target)
        data = {"ok": result.success, "message": result.message, "root": result.data.get("root")}
        self.runtime.events.emit("workspace_updated", self.snapshot())
        return data


class SkillService:
    def __init__(self, runtime: KairoRuntime):
        self.runtime = runtime

    def list(self) -> Dict[str, Any]:
        tools = []
        for name, tool in self.runtime.agent.registry.tools.items():
            tools.append({
                "name": name,
                "description": getattr(tool, "description", ""),
                "permission": getattr(getattr(tool, "permission", None), "value", str(getattr(tool, "permission", ""))),
                "source": getattr(tool, "source", "builtin"),
                "parameters": getattr(tool, "parameters", {}),
            })
        return {"tools": tools}

    def reload(self) -> Dict[str, Any]:
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


class DoctorService:
    def __init__(self, runtime: KairoRuntime):
        self.runtime = runtime

    def run(self, *, local_only: bool = True) -> Dict[str, Any]:
        result = handle_doctor(self.runtime.agent, "/doctor", ["/doctor"], local_only=local_only)
        return {"ok": result.success, "message": result.message, "checks": result.data.get("checks", [])}


class _PendingApproval:
    def __init__(self, options: List[str], default_index: int):
        self.options = options
        self.default_index = default_index
        self._choice: Optional[int] = None
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


def _snapshot_to_dict(snapshot: WorkspaceSnapshot) -> Dict[str, Any]:
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


def event_to_dict(event: RuntimeEvent) -> Dict[str, Any]:
    return {"kind": event.kind, "payload": event.payload, "timestamp": event.timestamp}


def event_stream(runtime: KairoRuntime) -> Iterable[Dict[str, Any]]:
    q: "queue.Queue[RuntimeEvent]" = queue.Queue()
    unsubscribe = runtime.events.subscribe(q.put)
    try:
        for event in runtime.events.snapshot():
            yield event_to_dict(event)
        while True:
            yield event_to_dict(q.get())
    finally:
        unsubscribe()


def _package_version() -> str:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    if pyproject.exists():
        try:
            import tomllib

            with open(pyproject, "rb") as handle:
                return str(tomllib.load(handle)["project"]["version"])
        except Exception:
            pass
    try:
        from importlib.metadata import version

        return version("kairo-agent")
    except Exception:
        return "0.3.2-preview"


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
