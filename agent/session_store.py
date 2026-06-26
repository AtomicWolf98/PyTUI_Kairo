"""Persistent session storage for Kairo conversations.

Sessions are stored as individual JSON files alongside an index that tracks
metadata and the last active session. All writes are atomic (temp file +
os.replace) and UTF-8 encoded with ``ensure_ascii=False``.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.context_manager import ConversationSession, utc_now
from agent.token_tracker import TokenTracker


RUNTIME_STATE_NAME = "kairo_runtime_state"
INDEX_VERSION = 1
SESSION_VERSION = 1


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


class SessionStore:
    """Manages loading and saving of conversation sessions to disk."""

    def __init__(self, storage_dir: str, config_path: str):
        self._config_path = Path(config_path).expanduser().resolve()
        storage = Path(storage_dir)
        if storage.is_absolute():
            self._storage_dir = storage
        else:
            self._storage_dir = (self._config_path.parent / storage).resolve()
        self._index_path = self._storage_dir / "index.json"
        self._warnings: List[str] = []

    @property
    def storage_dir(self) -> Path:
        return self._storage_dir

    @property
    def warnings(self) -> List[str]:
        return list(self._warnings)

    def _ensure_storage(self) -> None:
        self._storage_dir.mkdir(parents=True, exist_ok=True)

    def _session_path(self, session_id: str) -> Path:
        return self._storage_dir / f"{session_id}.json"

    def _atomic_write(self, path: Path, data: Dict[str, Any]) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise

    def _create_empty_session(self, name: Optional[str] = None) -> ConversationSession:
        """Low-level helper: create a session with empty history.

        This is a private method intended only for test fixtures.  Business
        code must use :meth:`ConversationManager.create_session` which
        provides a valid system-prefixed history.
        """
        self._ensure_storage()
        session = ConversationSession(
            id=uuid.uuid4().hex,
            name=(name or "").strip() or "Conversation 1",
            history=[],
            token_tracker=TokenTracker(context_window=128000),
        )
        return session

    def save_session(
        self,
        session: ConversationSession,
        *,
        is_active: bool = False,
        reason: str = "",
    ) -> None:
        """Persist a single session to disk and update the index."""
        if not isinstance(session.history, list) or not session.history:
            raise ValueError("Session history must not be empty")
        if session.history[0].get("role") != "system":
            raise ValueError("Session history must start with a system message")

        self._ensure_storage()
        session.touch()

        token_tracker = session.token_tracker
        data: Dict[str, Any] = {
            "version": SESSION_VERSION,
            "id": session.id,
            "name": session.name,
            "created_at": _format_timestamp(session.created_at),
            "updated_at": _format_timestamp(session.updated_at),
            "workspace_root": "",
            "model_profile": "",
            "compression_count": session.compression_count,
            "token_usage": {
                "session_input_tokens": token_tracker.session_input_tokens,
                "session_output_tokens": token_tracker.session_output_tokens,
                "context_used_tokens": token_tracker.context_used_tokens,
                "context_window": token_tracker.context_window,
            },
            "history": session.history,
        }

        # Extract runtime state fields from the dedicated system message if present.
        for message in session.history:
            if message.get("role") == "system" and message.get("name") == RUNTIME_STATE_NAME:
                content = message.get("content", "")
                for line in content.splitlines():
                    if line.startswith("- Current workspace root:"):
                        data["workspace_root"] = line.split(":", 1)[1].strip()
                    elif line.startswith("- Active model profile:"):
                        data["model_profile"] = line.split(":", 1)[1].strip()
                break

        path = self._session_path(session.id)
        self._atomic_write(path, data)
        self._update_index(session, is_active=is_active)

    def _load_index(self) -> Optional[Dict[str, Any]]:
        if not self._index_path.exists():
            return None
        try:
            with open(self._index_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                raise ValueError("index is not a JSON object")
            return data
        except Exception as exc:
            self._warnings.append(f"Failed to load session index: {exc}")
            return None

    def _update_index(self, session: ConversationSession, *, is_active: bool) -> None:
        index = self._load_index() or {"version": INDEX_VERSION, "active_session_id": "", "sessions": []}
        index["version"] = INDEX_VERSION
        sessions = index.setdefault("sessions", [])

        updated = {
            "id": session.id,
            "name": session.name,
            "file": f"{session.id}.json",
            "created_at": _format_timestamp(session.created_at),
            "updated_at": _format_timestamp(session.updated_at),
        }
        found = False
        for i, item in enumerate(sessions):
            if isinstance(item, dict) and item.get("id") == session.id:
                sessions[i] = updated
                found = True
                break
        if not found:
            sessions.append(updated)

        if is_active:
            index["active_session_id"] = session.id

        self._atomic_write(self._index_path, index)

    def _build_runtime_state(
        self,
        workspace_root: str,
        model_profile: str,
        authorization_level: str,
    ) -> Dict[str, Any]:
        return {
            "role": "system",
            "name": RUNTIME_STATE_NAME,
            "content": (
                "Kairo runtime state:\n"
                f"- Current workspace root: {workspace_root}\n"
                f"- Active model profile: {model_profile}\n"
                f"- Authorization level: {authorization_level}\n"
                "This message is maintained by Kairo and supersedes older workspace references."
            ),
        }

    def load_all(
        self,
        system_instruction: str,
        *,
        workspace_root: str = "",
        model_profile: str = "",
        authorization_level: str = "manual",
    ) -> Tuple[List[ConversationSession], str, List[str]]:
        """Load all sessions from disk.

        Returns:
            A tuple of (sessions, active_session_id, warnings).
        """
        self._warnings = []
        self._ensure_storage()

        index = self._load_index()

        sessions: List[ConversationSession] = []
        active_session_id = ""
        indexed_ids: set = set()

        if index is not None:
            active_session_id = str(index.get("active_session_id", ""))
            for item in index.get("sessions", []):
                if isinstance(item, dict) and item.get("id"):
                    indexed_ids.add(item["id"])

        # Scan for orphan session files when index is missing or empty.
        if not indexed_ids and self._storage_dir.exists():
            for path in sorted(self._storage_dir.glob("*.json")):
                if path.name == "index.json":
                    continue
                session_id = path.stem
                indexed_ids.add(session_id)
                self._warnings.append(f"Recovered session file without index entry: {path.name}")

        for session_id in indexed_ids:
            path = self._session_path(session_id)
            if not path.exists():
                self._warnings.append(f"Session file missing for {session_id}; skipping")
                continue
            try:
                session = self._load_session_file(
                    path,
                    system_instruction,
                    workspace_root=workspace_root,
                    model_profile=model_profile,
                    authorization_level=authorization_level,
                )
                if session is not None:
                    sessions.append(session)
            except Exception as exc:
                self._warnings.append(f"Failed to load session {session_id}: {exc}")

        if not sessions:
            # No valid sessions on disk; create a default one.
            session = ConversationSession(
                id=uuid.uuid4().hex,
                name="Conversation 1",
                history=[{"role": "system", "content": system_instruction}],
                token_tracker=TokenTracker(context_window=128000),
            )
            session.history.insert(
                1,
                self._build_runtime_state(workspace_root, model_profile, authorization_level),
            )
            self.save_session(session, is_active=True)
            return [session], session.id, self._warnings

        if active_session_id and not any(s.id == active_session_id for s in sessions):
            self._warnings.append(
                f"Active session {active_session_id} not found on disk; using first available"
            )
            active_session_id = sessions[0].id
        elif not active_session_id:
            active_session_id = sessions[0].id

        return sessions, active_session_id, self._warnings

    def _load_session_file(
        self,
        path: Path,
        system_instruction: str,
        *,
        workspace_root: str,
        model_profile: str,
        authorization_level: str,
    ) -> Optional[ConversationSession]:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)

        if not isinstance(data, dict):
            raise ValueError("session file is not a JSON object")

        history = data.get("history", [])
        if not history or history[0].get("role") != "system":
            raise ValueError("session history does not start with system message")

        # Replace system instruction if it has changed, preserving rest of history.
        current_system = system_instruction
        if history[0].get("content") != current_system:
            history[0] = {"role": "system", "content": current_system}

        # Ensure runtime state message exists at index 1.
        if len(history) < 2 or not (
            history[1].get("role") == "system" and history[1].get("name") == RUNTIME_STATE_NAME
        ):
            runtime_state = self._build_runtime_state(
                data.get("workspace_root", workspace_root),
                data.get("model_profile", model_profile),
                authorization_level,
            )
            history.insert(1, runtime_state)

        # 0.2.6-beta: warn (do not mutate) if an old session has a system
        # message after the first user message. The message packer will fold
        # it into the leading system slot at request time, but the persisted
        # history is left untouched for backward compatibility.
        first_user_idx = -1
        for i, msg in enumerate(history):
            if msg.get("role") == "user":
                first_user_idx = i
                break
        if first_user_idx != -1:
            for i in range(first_user_idx, len(history)):
                if history[i].get("role") == "system":
                    self._warnings.append(
                        f"Session '{data.get('name', path.stem)}' has a system message after the first "
                        "user message; it will be folded at request time."
                    )
                    break

        token_usage = data.get("token_usage", {})
        context_window = int(token_usage.get("context_window", 128000))
        token_tracker = TokenTracker(context_window=context_window)
        token_tracker.session_input_tokens = int(token_usage.get("session_input_tokens", 0))
        token_tracker.session_output_tokens = int(token_usage.get("session_output_tokens", 0))
        token_tracker.context_used_tokens = int(token_usage.get("context_used_tokens", 0))

        created_at = _parse_timestamp(data["created_at"]) if "created_at" in data else utc_now()
        updated_at = _parse_timestamp(data["updated_at"]) if "updated_at" in data else utc_now()

        session = ConversationSession(
            id=data.get("id", path.stem),
            name=data.get("name", "Conversation"),
            history=history,
            token_tracker=token_tracker,
            created_at=created_at,
            updated_at=updated_at,
            compression_count=int(data.get("compression_count", 0)),
        )
        return session

    def delete_session(self, session_id: str) -> bool:
        """Remove a session file from disk and clean the index entry."""
        path = self._session_path(session_id)
        try:
            path.unlink(missing_ok=True)
        except Exception as exc:
            self._warnings.append(f"Failed to delete session file {path}: {exc}")
            return False

        index = self._load_index()
        if index is not None:
            sessions = index.get("sessions", [])
            index["sessions"] = [s for s in sessions if isinstance(s, dict) and s.get("id") != session_id]
            if index.get("active_session_id") == session_id:
                index["active_session_id"] = ""
            try:
                self._atomic_write(self._index_path, index)
            except Exception as exc:
                self._warnings.append(f"Failed to update index after deletion: {exc}")
                return False
        return True

    # ---- Session management extensions (0.2.3) ---------------------------------

    def rename_session(self, session_id: str, new_name: str) -> bool:
        """Rename a session's on-disk file and its index entry consistently."""
        clean = (new_name or "").strip()
        if not clean:
            return False
        path = self._session_path(session_id)
        if not path.exists():
            return False
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            data["name"] = clean
            data["updated_at"] = _format_timestamp(datetime.now(timezone.utc))
            self._atomic_write(path, data)
        except Exception as exc:
            self._warnings.append(f"Failed to rename session file {path}: {exc}")
            return False

        index = self._load_index()
        if index is not None:
            for item in index.get("sessions", []):
                if isinstance(item, dict) and item.get("id") == session_id:
                    item["name"] = clean
                    item["updated_at"] = data["updated_at"]
                    break
            try:
                self._atomic_write(self._index_path, index)
            except Exception as exc:
                self._warnings.append(f"Failed to update index after rename: {exc}")
                return False
        return True

    def SESSION_EXPORT_VERSION(self) -> int:
        return SESSION_VERSION

    def export_session(
        self,
        session_id: str,
        *,
        fmt: str = "markdown",
        dest: Optional[Path] = None,
    ) -> Optional[Path]:
        """Write a Markdown or JSON copy of the session to disk.

        Returns the destination path on success. The original session file is
        never modified.
        """
        path = self._session_path(session_id)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception as exc:
            self._warnings.append(f"Failed to read session for export: {exc}")
            return None

        if dest is None:
            export_dir = self._storage_dir / "exports"
            export_dir.mkdir(parents=True, exist_ok=True)
            safe_name = _safe_filename(data.get("name", session_id))
            suffix = "md" if fmt.lower().startswith("m") else "json"
            dest = export_dir / f"{safe_name}.{suffix}"

        if fmt.lower().startswith("m"):
            content = self._render_session_markdown(data)
        elif fmt.lower().startswith("j"):
            content = json.dumps(data, ensure_ascii=False, indent=2)
        else:
            return None

        try:
            tmp_dest = dest.with_suffix(dest.suffix + ".tmp")
            with open(tmp_dest, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_dest, dest)
        except Exception as exc:
            self._warnings.append(f"Failed to write export: {exc}")
            try:
                tmp_dest.unlink(missing_ok=True)
            except Exception:
                pass
            return None
        return dest

    def _render_session_markdown(self, data: Dict[str, Any]) -> str:
        lines: List[str] = []
        lines.append(f"# {data.get('name', 'Session')}")
        lines.append("")
        lines.append(f"- Session id: `{data.get('id', '')}`")
        lines.append(f"- Updated at: {data.get('updated_at', '')}")
        lines.append("")
        for message in data.get("history", []):
            role = message.get("role", "system")
            content = (message.get("content", "") or "").strip()
            if role == "system" and message.get("name") == RUNTIME_STATE_NAME:
                # Skip internal runtime state; users do not need it in exports.
                continue
            if role == "system" and content.startswith("[Conversation Summary]"):
                lines.append("> _Summary_\n")
                lines.append(content + "\n")
                continue
            header = {"user": "## User", "assistant": "## Assistant", "tool": "## Tool", "system": "## System"}.get(role, "## " + role.title())
            tool_note = ""
            if role == "tool":
                tool_note = f" ({message.get('name', '')})"
            lines.append(header + tool_note)
            lines.append("")
            lines.append(content or "_no content_")
            lines.append("")
        return "\n".join(lines)

    def reveal_session_path(self, session_id: str) -> Optional[Path]:
        path = self._session_path(session_id)
        return path if path.exists() else None

    def session_metadata(self, session_id: str) -> Optional[tuple]:
        """Return ``(name, path)`` for the given session, or None if missing."""
        path = self._session_path(session_id)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return (data.get("name", session_id), path)
        except Exception:
            return None


def _safe_filename(name: str) -> str:
    keep = "-_.()"
    safe = []
    for char in (name or "").strip():
        if char.isalnum() or char in keep:
            safe.append(char)
        else:
            safe.append("_")
    return "".join(safe) or "session"
