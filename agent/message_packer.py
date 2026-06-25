"""Strict OpenAI-compatible message packing for Kairo 0.2.6-beta.

The internal conversation history may carry several system-class messages
(main system prompt, ``kairo_runtime_state`` and ``[Conversation Summary]``)
which are valuable for persistence but break strict OpenAI-compatible providers
that only accept a single leading ``system`` message.

This module folds every system message into the first ``system`` slot and
returns a provider-safe ``messages`` list plus a list of human-readable
warnings. It never mutates the input history.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Tuple

# Provider-safe fields kept on each message. Internal/debug fields (anything
# starting with "_") and hidden reasoning are dropped before sending.
_PROVIDER_FIELDS = {"role", "content", "name", "tool_calls", "tool_call_id"}

# Recognized system-class message names that are safe to fold into the leading
# system prompt instead of being treated as anomalous.
RUNTIME_STATE_NAME = "kairo_runtime_state"
SUMMARY_PREFIX = "[Conversation Summary]"


def _is_system(message: Dict[str, Any]) -> bool:
    return message.get("role") == "system"


def _is_runtime_state(message: Dict[str, Any]) -> bool:
    return _is_system(message) and message.get("name") == RUNTIME_STATE_NAME


def _is_summary(message: Dict[str, Any]) -> bool:
    if not _is_system(message):
        return False
    content = str(message.get("content", ""))
    return content.startswith(SUMMARY_PREFIX)


def _fold_system_content(parts: List[str]) -> str:
    """Join non-empty system content fragments with blank-line separators."""
    cleaned = [str(part).strip() for part in parts if str(part).strip()]
    return "\n\n".join(cleaned)


def _strip_internal_fields(message: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of *message* with only provider-safe fields kept."""
    return {key: copy.deepcopy(value) for key, value in message.items() if key in _PROVIDER_FIELDS}


def _validate_tool_pairing(messages: List[Dict[str, Any]]) -> List[str]:
    """Return warnings for orphan tool results or missing tool results."""
    warnings: List[str] = []
    pending_calls: Dict[str, str] = {}
    for index, message in enumerate(messages):
        role = message.get("role")
        if role == "assistant":
            for call in message.get("tool_calls", []) or []:
                call_id = call.get("id") if isinstance(call, dict) else None
                if call_id:
                    pending_calls[call_id] = call.get("function", {}).get("name", "")
        elif role == "tool":
            call_id = message.get("tool_call_id")
            if call_id in pending_calls:
                del pending_calls[call_id]
            else:
                warnings.append(f"orphan tool result at index {index} has no matching assistant tool_call")
    for call_id, name in pending_calls.items():
        warnings.append(f"assistant tool_call '{name}' (id={call_id}) has no matching tool result")
    return warnings


def pack_messages_for_provider(
    history: List[Dict[str, Any]],
    *,
    strict_openai: bool = True,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Fold internal history into a provider-safe ``messages`` list.

    Rules:
      1. All ``system`` messages are merged into a single leading ``system``
         message (main prompt + runtime state + summary).
      2. No ``system`` message appears after index 0 in the output.
      3. System messages that appeared *after* the first non-system message
         generate a warning (history invariant drift) but are still folded to
         keep the payload valid.
      4. Only provider-safe fields are kept; internal/debug fields are dropped.
      5. Tool call / tool result pairing is validated and warnings are emitted.

    Returns ``(messages, warnings)``. When ``strict_openai`` is False the input
    is still stripped of internal fields and tool pairing is validated, but
    system messages are not folded (kept in place) for permissive providers.
    """
    warnings: List[str] = []
    if not history:
        return [], warnings

    system_fragments: List[str] = []
    non_system: List[Dict[str, Any]] = []
    first_non_system_seen = False

    for message in history:
        if _is_system(message):
            if first_non_system_seen:
                # System after the first user/assistant/tool: invariant drift.
                label = "runtime state" if _is_runtime_state(message) else (
                    "conversation summary" if _is_summary(message) else "system message"
                )
                warnings.append(
                    f"{label} appeared after the first non-system message; folded into the leading system prompt."
                )
            system_fragments.append(str(message.get("content", "")))
        else:
            first_non_system_seen = True
            non_system.append(_strip_internal_fields(message))

    warnings.extend(_validate_tool_pairing(non_system))

    if not strict_openai:
        # Permissive mode: keep system messages in their original positions but
        # still strip internal fields and validate tool pairing.
        output = [_strip_internal_fields(message) for message in history]
        return output, warnings

    if not system_fragments:
        # No system message at all: strict providers still require one leading
        # system slot, so synthesize a minimal one.
        warnings.append("history had no system message; inserted an empty leading system message.")
        packed_system: Dict[str, Any] = {"role": "system", "content": ""}
        return [packed_system] + non_system, warnings

    packed_system = {"role": "system", "content": _fold_system_content(system_fragments)}
    return [packed_system] + non_system, warnings


def validate_provider_payload(messages: List[Dict[str, Any]]) -> List[str]:
    """Return a list of strict-OpenAI payload violations for *messages*.

    Checks:
      - first message is ``role == "system"``
      - no ``system`` message after index 0
      - no orphan tool result
      - every assistant tool_call has a matching tool result
    """
    errors: List[str] = []
    if not messages:
        errors.append("payload is empty")
        return errors
    if messages[0].get("role") != "system":
        errors.append("first message is not role=system")
    for index in range(1, len(messages)):
        if messages[index].get("role") == "system":
            errors.append(f"system message at index {index} after leading system message")
    errors.extend(_validate_tool_pairing(messages))
    return errors
