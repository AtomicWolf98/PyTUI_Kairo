import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from agent.token_tracker import TokenTracker


SUMMARY_PREFIX = "[Conversation Summary]"


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
    def __init__(self, system_instruction: str, context_window: int):
        self.system_instruction = system_instruction
        self.context_window = context_window
        self.estimator = ContextEstimator()
        self._last_tools: Optional[Sequence[Dict[str, Any]]] = None
        self.sessions: List[ConversationSession] = []
        self.active_session_id = ""
        self.create_session()

    @property
    def active(self) -> ConversationSession:
        for session in self.sessions:
            if session.id == self.active_session_id:
                return session
        raise RuntimeError("No active conversation session")

    def create_session(self, name: Optional[str] = None) -> ConversationSession:
        session_name = (name or "").strip() or f"Conversation {len(self.sessions) + 1}"
        session = ConversationSession(
            id=uuid.uuid4().hex,
            name=session_name,
            history=[{"role": "system", "content": self.system_instruction}],
            token_tracker=TokenTracker(context_window=self.context_window),
        )
        self.sessions.append(session)
        self.active_session_id = session.id
        self.refresh_context()
        return session

    def switch_session(self, session_id: str) -> bool:
        if not any(session.id == session_id for session in self.sessions):
            return False
        self.active_session_id = session_id
        self.refresh_context()
        return True

    def clear_active(self):
        self.active.history = [{"role": "system", "content": self.system_instruction}]
        self.active.token_tracker.reset()
        self.active.compression_count = 0
        self.active.touch()
        self.refresh_context()

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

    def compression_parts(
        self,
        preserve_recent_turns: int,
    ) -> Optional[Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]]:
        prefix, turns = split_complete_turns(self.active.history)
        preserve_count = max(0, int(preserve_recent_turns))
        if len(turns) <= preserve_count:
            return None

        base_system = prefix[:1]
        prior_summaries = prefix[1:]
        old_turns = turns[:-preserve_count] if preserve_count else turns
        recent_turns = turns[-preserve_count:] if preserve_count else []
        source = prior_summaries + [message for turn in old_turns for message in turn]
        retained = base_system + [message for turn in recent_turns for message in turn]
        if not source:
            return None
        return source, retained

    def apply_summary(self, summary: str, retained: List[Dict[str, Any]]):
        base_system = self.active.history[:1]
        summary_message = {
            "role": "system",
            "content": f"{SUMMARY_PREFIX}\n{summary.strip()}",
        }
        retained_without_base = retained[1:] if retained[:1] == base_system else retained
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

            if len(prefix) > 1:
                prefix = [prefix[0]]
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
