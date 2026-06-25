import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from agent.token_tracker import TokenTracker


SUMMARY_PREFIX = "[Conversation Summary]"
RUNTIME_STATE_NAME = "kairo_runtime_state"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def split_complete_turns(messages: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[List[Dict[str, Any]]]]:
    """Split a chat history into its system prefix and complete user-led turns."""
    prefix: List[Dict[str, Any]] = []
    turns: List[List[Dict[str, Any]]] = []
    current: Optional[List[Dict[str, Any]]] = None

    for message in messages:
        if message.get("role") == "user":
            current = [message]
            turns.append(current)
        elif current is None:
            prefix.append(message)
        else:
            current.append(message)
    return prefix, turns


class ContextEstimator:
    """Provider-neutral token estimator for OpenAI-compatible request payloads."""

    @staticmethod
    def estimate_text(text: Any) -> int:
        if not text:
            return 0
        value = str(text)
        cjk_count = sum(1 for char in value if ord(char) > 255)
        ascii_len = len(value) - cjk_count
        ascii_tokens = max(1, (ascii_len + 3) // 4) if ascii_len else 0
        return cjk_count + ascii_tokens

    def estimate_messages(
        self,
        messages: Sequence[Dict[str, Any]],
        tools: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> int:
        total = 3
        for message in messages:
            serialized = json.dumps(message, ensure_ascii=False, separators=(",", ":"), default=str)
            total += 4 + self.estimate_text(serialized)
        if tools:
            serialized_tools = json.dumps(tools, ensure_ascii=False, separators=(",", ":"), default=str)
            total += self.estimate_text(serialized_tools)
        return max(0, total)


@dataclass
class ConversationSession:
    id: str
    name: str
    history: List[Dict[str, Any]]
    token_tracker: TokenTracker
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    compression_count: int = 0

    def touch(self):
        self.updated_at = utc_now()


class ConversationManager:
    def __init__(
        self,
        system_instruction: str,
        context_window: int,
        session_store: Optional[Any] = None,
        *,
        workspace_root: str = "",
        model_profile: str = "",
        authorization_level: str = "manual",
    ):
        self.system_instruction = system_instruction
        self.context_window = context_window
        self.estimator = ContextEstimator()
        self._last_tools: Optional[Sequence[Dict[str, Any]]] = None
        self.session_store = session_store
        self.sessions: List[ConversationSession] = []
        self.active_session_id = ""
        self._runtime_state = {
            "workspace_root": workspace_root,
            "model_profile": model_profile,
            "authorization_level": authorization_level,
        }
        # 0.2.4: dirty tracking + autosave
        self._dirty = False
        self._dirty_reason = ""
        self._autosave = True  # default; updated by caller when config is known
        self._max_sessions = 0  # 0 = unlimited; updated by caller
        self._save_interval_seconds = 0.0  # 0 = no throttling
        self._last_save_time: Optional[float] = None
        self._load_or_create_session()

    def _build_runtime_state_message(self) -> Dict[str, Any]:
        return {
            "role": "system",
            "name": RUNTIME_STATE_NAME,
            "content": (
                "Kairo runtime state:\n"
                f"- Current workspace root: {self._runtime_state['workspace_root']}\n"
                f"- Active model profile: {self._runtime_state['model_profile']}\n"
                f"- Authorization level: {self._runtime_state['authorization_level']}\n"
                "This message is maintained by Kairo and supersedes older workspace references."
            ),
        }

    def _make_default_history(self) -> List[Dict[str, Any]]:
        return [
            {"role": "system", "content": self.system_instruction},
            self._build_runtime_state_message(),
        ]

    def _load_or_create_session(self):
        if self.session_store is not None:
            try:
                sessions, active_id, warnings = self.session_store.load_all(
                    self.system_instruction,
                    workspace_root=self._runtime_state["workspace_root"],
                    model_profile=self._runtime_state["model_profile"],
                    authorization_level=self._runtime_state["authorization_level"],
                )
                for warning in warnings:
                    print(f"[Warning] {warning}")
                if sessions:
                    self.sessions = sessions
                    self.active_session_id = active_id
                    return
            except Exception as exc:
                print(f"[Warning] Failed to load sessions: {exc}")
        self.create_session()

    def load_sessions(self):
        """Reload sessions from disk if a session store is configured."""
        if self.session_store is None:
            return
        sessions, active_id, warnings = self.session_store.load_all(
            self.system_instruction,
            workspace_root=self._runtime_state["workspace_root"],
            model_profile=self._runtime_state["model_profile"],
            authorization_level=self._runtime_state["authorization_level"],
        )
        for warning in warnings:
            print(f"[Warning] {warning}")
        if sessions:
            self.sessions = sessions
            self.active_session_id = active_id

    @property
    def active(self) -> ConversationSession:
        for session in self.sessions:
            if session.id == self.active_session_id:
                return session
        raise RuntimeError("No active conversation session")

    def create_session(self, name: Optional[str] = None) -> ConversationSession:
        # 0.2.4: enforce max_sessions limit when configured
        if self._max_sessions and len(self.sessions) >= self._max_sessions:
            raise RuntimeError(
                f"Cannot create session: max_sessions ({self._max_sessions}) reached. "
                "Delete an existing session first."
            )
        session_name = (name or "").strip() or f"Conversation {len(self.sessions) + 1}"
        session = ConversationSession(
            id=uuid.uuid4().hex,
            name=session_name,
            history=self._make_default_history(),
            token_tracker=TokenTracker(context_window=self.context_window),
        )
        self.sessions.append(session)
        self.active_session_id = session.id
        self.refresh_context()
        if self.session_store is not None:
            try:
                self.session_store.save_session(session, is_active=True, reason="create")
            except Exception as exc:
                print(f"[Warning] Failed to save new session: {exc}")
        return session

    def switch_session(self, session_id: str) -> bool:
        if not any(session.id == session_id for session in self.sessions):
            return False
        previous_id = self.active_session_id
        self.active_session_id = session_id
        self.refresh_context()
        if self.session_store is not None:
            try:
                # Save the session we are leaving before switching.
                previous = next((s for s in self.sessions if s.id == previous_id), None)
                if previous is not None:
                    self.session_store.save_session(previous, is_active=False, reason="switch_from")
                self.session_store.save_session(self.active, is_active=True, reason="switch_to")
            except Exception as exc:
                print(f"[Warning] Failed to save session switch: {exc}")
        return True

    def clear_active(self):
        self.active.history = self._make_default_history()
        self.active.token_tracker.reset()
        self.active.compression_count = 0
        self.active.touch()
        self.refresh_context()
        self.save_active(reason="clear")

    def set_context_window(self, context_window: int):
        self.context_window = max(1, int(context_window))
        for session in self.sessions:
            session.token_tracker.context_window = self.context_window
        self.refresh_context()

    def refresh_context(self, tools: Optional[Sequence[Dict[str, Any]]] = None) -> int:
        if tools is not None:
            self._last_tools = tools
        effective_tools = self._last_tools
        used = self.estimator.estimate_messages(self.active.history, effective_tools)
        self.active.token_tracker.set_context_used(used)
        self.active.touch()
        return used

    def append_message(self, message: Dict[str, Any]):
        """Append a message to the active session and refresh context."""
        self.active.history.append(message)
        self.refresh_context()

    def replace_active_history(
        self,
        history: List[Dict[str, Any]],
        *,
        reason: str = "",
        save: bool = False,
    ):
        """Replace the active session history in full.

        Args:
            history: The new history list.
            reason: A short label for logging / audit.
            save: If True, persist immediately via ``save_active``.
        """
        self.active.history = history
        self.refresh_context()
        if save:
            self.save_active(reason=reason or "replace_history")

    def update_runtime_state(
        self,
        workspace_root: Optional[str] = None,
        model_profile: Optional[str] = None,
        authorization_level: Optional[str] = None,
    ):
        """Update or insert the runtime state system message in all sessions."""
        if workspace_root is not None:
            self._runtime_state["workspace_root"] = workspace_root
        if model_profile is not None:
            self._runtime_state["model_profile"] = model_profile
        if authorization_level is not None:
            self._runtime_state["authorization_level"] = authorization_level

        runtime_message = self._build_runtime_state_message()
        for session in self.sessions:
            updated = False
            for i, message in enumerate(session.history):
                if i == 0:
                    continue
                if message.get("role") == "system" and message.get("name") == RUNTIME_STATE_NAME:
                    session.history[i] = runtime_message
                    updated = True
                    break
            if not updated:
                session.history.insert(1, runtime_message)
            session.touch()
        self.refresh_context()

    def save_active(self, reason: str = ""):
        """Persist the active session if a store is configured."""
        if self.session_store is None:
            return
        try:
            self.session_store.save_session(self.active, is_active=True, reason=reason)
            self._dirty = False
            self._dirty_reason = ""
            import time
            self._last_save_time = time.monotonic()
        except Exception as exc:
            print(f"[Warning] Failed to save active session ({reason}): {exc}")

    def save_all(self, reason: str = ""):
        """Persist all sessions if a store is configured."""
        if self.session_store is None:
            return
        for session in self.sessions:
            try:
                self.session_store.save_session(
                    session,
                    is_active=(session.id == self.active_session_id),
                    reason=reason,
                )
            except Exception as exc:
                print(f"[Warning] Failed to save session {session.name} ({reason}): {exc}")

    def mark_dirty(self, reason: str = ""):
        """Mark the active session as dirty and auto-save when autosave is enabled.

        Autosave behaviour is controlled by the ``sessions.autosave`` flag that
        was read at construction time (or updated later).  When autosave is on,
        every ``mark_dirty`` call persists immediately (subject to
        ``save_interval_seconds`` throttling).  When it is off the dirty flag
        is merely recorded so that ``save_active`` on shutdown or explicit save
        will still write.
        """
        self._dirty = True
        self._dirty_reason = reason or self._dirty_reason
        if self._autosave:
            # Respect save_interval_seconds throttle when configured
            if self._save_interval_seconds and self._last_save_time is not None:
                import time
                elapsed = time.monotonic() - self._last_save_time
                if elapsed < self._save_interval_seconds:
                    return  # throttled; dirty flag is set, will save later
            self.save_active(reason=reason or "dirty")

    # ---- history invariant validator (0.2.4) -----------------------------------

    def validate_history_invariants(
        self,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> List[str]:
        """Return a list of invariant violations found in *history*.

        Checks performed:

        * history is not empty
        * first message has ``role == "system"``
        * runtime state message exists before the first user message
        * no ``role == "system"`` message appears after the first user message
        * no orphan ``tool`` result (must be preceded by an assistant tool_call)

        Returns an empty list when all invariants hold.
        """
        messages = history if history is not None else self.active.history
        errors: List[str] = []

        if not messages:
            errors.append("history is empty")
            return errors

        if messages[0].get("role") != "system":
            errors.append("first message is not role=system")

        # runtime state before first user
        first_user_idx = -1
        runtime_state_found = False
        for i, msg in enumerate(messages):
            role = msg.get("role")
            if role == "user" and first_user_idx == -1:
                first_user_idx = i
            if msg.get("name") == RUNTIME_STATE_NAME and role == "system":
                runtime_state_found = True
                if first_user_idx != -1 and i > first_user_idx:
                    errors.append("runtime state message appears after first user message")
        if not runtime_state_found:
            errors.append("runtime state message missing")

        # no system after first user
        if first_user_idx != -1:
            for i in range(first_user_idx, len(messages)):
                if messages[i].get("role") == "system":
                    errors.append(
                        f"system message at index {i} after first user message at index {first_user_idx}"
                    )

        # orphan tool results
        pending_tool_call_ids: set = set()
        for msg in messages:
            role = msg.get("role")
            if role == "assistant":
                for tc in msg.get("tool_calls", []):
                    tc_id = tc.get("id")
                    if tc_id:
                        pending_tool_call_ids.add(tc_id)
            elif role == "tool":
                tc_id = msg.get("tool_call_id")
                if tc_id in pending_tool_call_ids:
                    pending_tool_call_ids.discard(tc_id)
                else:
                    errors.append(f"orphan tool result for tool_call_id={tc_id!r}")

        return errors

    def compression_parts(
        self,
        preserve_recent_turns: int,
    ) -> Optional[Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]]:
        prefix, turns = split_complete_turns(self.active.history)
        preserve_count = max(0, int(preserve_recent_turns))
        if len(turns) <= preserve_count:
            return None

        # Preserve the main system prompt and the runtime state message.
        base_system = prefix[:2] if len(prefix) >= 2 else prefix[:1]
        prior_summaries = prefix[len(base_system):]
        old_turns = turns[:-preserve_count] if preserve_count else turns
        recent_turns = turns[-preserve_count:] if preserve_count else []
        source = prior_summaries + [message for turn in old_turns for message in turn]
        retained = base_system + [message for turn in recent_turns for message in turn]
        if not source:
            return None
        return source, retained

    def apply_summary(self, summary: str, retained: List[Dict[str, Any]]):
        base_system = self.active.history[:2] if len(self.active.history) >= 2 else self.active.history[:1]
        summary_message = {
            "role": "system",
            "content": f"{SUMMARY_PREFIX}\n{summary.strip()}",
        }
        retained_without_base = retained[len(base_system):] if retained[:len(base_system)] == base_system else retained
        self.active.history = base_system + [summary_message] + retained_without_base
        self.active.compression_count += 1
        self.active.touch()
        self.refresh_context()

    def trim_oldest_to_budget(
        self,
        budget: int,
        tools: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> Tuple[int, bool]:
        """Drop complete oldest turns, preserving the system prompt and newest user turn."""
        removed = 0
        while self.estimator.estimate_messages(self.active.history, tools) > budget:
            prefix, turns = split_complete_turns(self.active.history)
            if len(turns) > 1:
                turns.pop(0)
                removed += 1
                self.active.history = prefix + [message for turn in turns for message in turn]
                continue

            # Keep the main system prompt and runtime state message.
            min_prefix = 2 if len(prefix) >= 2 and prefix[1].get("name") == RUNTIME_STATE_NAME else 1
            if len(prefix) > min_prefix:
                prefix = prefix[:min_prefix]
                self.active.history = prefix + [message for turn in turns for message in turn]
                removed += 1
                continue
            break

        self.active.touch()
        used = self.refresh_context(tools)
        return removed, used <= budget

    def session_menu_options(self) -> List[str]:
        options = []
        for session in self.sessions:
            marker = "*" if session.id == self.active_session_id else " "
            options.append(
                f"{marker} {session.name} | {len(session.history)} messages | "
                f"~{session.token_tracker.context_used_tokens:,}/{session.token_tracker.context_window:,}"
            )
        return options

    # ---- provider payload validation (0.2.6-beta) -----------------------------

    def validate_provider_payload(self, messages: Sequence[Dict[str, Any]]) -> List[str]:
        """Return strict-OpenAI payload violations for *messages*.

        Thin wrapper over :func:`agent.message_packer.validate_provider_payload`
        so callers that already hold a ConversationManager can validate a
        packed payload without importing the packer directly.
        """
        from agent.message_packer import validate_provider_payload

        return validate_provider_payload(list(messages))
