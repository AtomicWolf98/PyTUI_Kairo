import json
import os
import time
import urllib.request
import urllib.error
from typing import Any, Dict, Generator, List, Optional, Tuple

from agent.config import Config


class LLMClient:
    """OpenAI-compatible chat completions client with streaming, retries and proxy support."""

    def __init__(self, config: Config):
        self.config = config
        self._opener = self._build_opener()

    @staticmethod
    def _build_opener() -> urllib.request.OpenerDirector:
        """Build a URL opener honoring HTTP_PROXY/HTTPS_PROXY environment variables."""
        proxies = {}
        for scheme, env_var in (("http", "HTTP_PROXY"), ("https", "HTTPS_PROXY")):
            value = os.environ.get(env_var) or os.environ.get(env_var.lower())
            if value:
                proxies[scheme] = value
        if proxies:
            handler = urllib.request.ProxyHandler(proxies)
            return urllib.request.build_opener(handler)
        return urllib.request.build_opener()

    @staticmethod
    def _classify_http_error(code: int, message: str) -> str:
        """Classify an HTTP error into a high-level category."""
        normalized = str(message).lower()
        context_markers = (
            "context length",
            "context window",
            "maximum context",
            "too many tokens",
            "max context",
            "context_length_exceeded",
            "token limit",
        )
        if code in (400, 413) and any(marker in normalized for marker in context_markers):
            return "context_error"
        if code == 429:
            return "rate_limit"
        if code in (401, 403):
            return "auth_error"
        if code >= 500:
            return "server_error"
        return "client_error"

    def _post_with_retries(
        self,
        url: str,
        payload: Dict[str, Any],
        headers: Dict[str, str],
        *,
        max_retries: int = 3,
        base_delay: float = 1.0,
        timeout: float = 60.0,
    ) -> urllib.response.addinfourl:
        """Send a POST request with retries for transient failures."""
        data = json.dumps(payload).encode("utf-8")
        last_error = ""

        for attempt in range(max_retries + 1):
            req = urllib.request.Request(
                url=url,
                data=data,
                headers=headers,
                method="POST",
            )
            try:
                return self._opener.open(req, timeout=timeout)
            except urllib.error.HTTPError as exc:
                try:
                    fp = getattr(exc, "fp", None)
                    if fp is not None:
                        error_body = fp.read().decode("utf-8", errors="replace")
                    else:
                        error_body = ""
                    error_json = json.loads(error_body)
                    error_msg = error_json.get("error", {}).get("message", error_body)
                except Exception:
                    error_msg = str(exc)
                full_error = f"HTTP Error {exc.code}: {error_msg}"
                category = self._classify_http_error(exc.code, error_msg)

                # Non-retryable categories fail immediately.
                if category in ("context_error", "auth_error", "client_error"):
                    raise _CategorizedError(category, full_error)

                last_error = full_error
                if attempt >= max_retries:
                    raise _CategorizedError(category, last_error)

                delay = base_delay * (2 ** attempt)
                time.sleep(delay)
            except urllib.error.URLError as exc:
                last_error = f"Connection failed: {exc.reason}"
                if attempt >= max_retries:
                    raise _CategorizedError("connection_error", last_error)
                delay = base_delay * (2 ** attempt)
                time.sleep(delay)
            except Exception as exc:
                raise _CategorizedError("connection_error", f"Connection failed: {exc}")

        raise _CategorizedError("connection_error", last_error)

    def stream_response(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        max_tokens_override: Optional[int] = None,
        temperature_override: Optional[float] = None,
        profile_role: str = "chat",
        profile_id: Optional[str] = None,
        cancel_token=None,
    ) -> Generator[Tuple[str, Any], None, None]:
        """
        Sends chat completion request to the OpenAI-compatible endpoint.

        Yields:
            - ("thought", text): Reasoning/thinking content
            - ("content", text): Regular output content
            - ("tool_calls", list_of_tool_calls): Cumulative tool calls structure
            - ("usage", dict): Usage metadata
            - ("error", text): Non-recoverable error details
            - ("context_error", text): Context-length error that the caller may retry after compression
            - ("stopped", None): The stream was cancelled via *cancel_token*
        """
        from agent.profile_resolver import resolve_profile

        # 0.2.6-beta: defensive backstop. If strict packing is enabled and the
        # caller passed messages with a system message after index 0, refuse to
        # send the request rather than triggering a provider format error.
        if getattr(self.config, "strict_message_packing", True) and len(messages) > 1:
            for idx in range(1, len(messages)):
                if messages[idx].get("role") == "system":
                    yield (
                        "error",
                        "Internal error: system message present after the leading system slot; "
                        "message packing was bypassed. Refusing to send malformed payload.",
                    )
                    return

        profile = resolve_profile(self.config, profile_id=profile_id, role=profile_role)
        if profile is None:
            yield ("error", "No configured LLM profile available.")
            return

        url = str(profile.base_url).rstrip("/")
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"

        payload = {
            "model": profile.model,
            "messages": messages,
            "temperature": profile.temperature if temperature_override is None else temperature_override,
            "max_tokens": profile.max_tokens if max_tokens_override is None else max_tokens_override,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools

        headers = {"Content-Type": "application/json"}
        if profile.api_key:
            headers["Authorization"] = f"Bearer {profile.api_key}"

        if cancel_token is not None and cancel_token.cancelled:
            yield ("stopped", None)
            return

        try:
            response = self._post_with_retries(url, payload, headers)
        except _CategorizedError as exc:
            yield (exc.category if exc.category == "context_error" else "error", exc.message)
            return

        if cancel_token is not None:
            cancel_token.add_cancel_callback(response.close)
            if cancel_token.cancelled:
                yield ("stopped", None)
                return

        in_think_tag = False
        text_buffer = ""
        tool_calls_dict: Dict[int, Dict[str, Any]] = {}
        stopped = False

        try:
            for raw_line in response:
                # 0.2.6-beta: cooperative cancel before reading the next chunk.
                if cancel_token is not None and cancel_token.cancelled:
                    stopped = True
                    yield ("stopped", None)
                    return
                line_str = raw_line.decode("utf-8", errors="replace").strip()
                if cancel_token is not None and cancel_token.cancelled:
                    stopped = True
                    yield ("stopped", None)
                    return
                if not line_str:
                    continue

                if not line_str.startswith("data: "):
                    continue

                data_content = line_str[6:].strip()
                if data_content == "[DONE]":
                    break

                try:
                    chunk = json.loads(data_content)
                except json.JSONDecodeError:
                    continue

                if chunk.get("usage"):
                    yield ("usage", chunk["usage"])

                choices = chunk.get("choices")
                if not choices:
                    continue

                delta = choices[0].get("delta", {})

                # 1. API-native reasoning content (e.g. DeepSeek reasoning_content)
                reasoning_content = delta.get("reasoning_content")
                if reasoning_content:
                    yield ("thought", reasoning_content)
                    continue

                # 2. Tool call deltas
                tool_calls = delta.get("tool_calls")
                if tool_calls:
                    for tc in tool_calls:
                        idx = tc.get("index", 0)
                        if idx not in tool_calls_dict:
                            tool_calls_dict[idx] = {
                                "id": tc.get("id", ""),
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        if tc.get("id"):
                            tool_calls_dict[idx]["id"] = tc["id"]
                        fn = tc.get("function", {})
                        if fn.get("name"):
                            tool_calls_dict[idx]["function"]["name"] = fn["name"]
                        if fn.get("arguments"):
                            tool_calls_dict[idx]["function"]["arguments"] += fn["arguments"]
                    yield ("tool_calls", list(tool_calls_dict.values()))
                    continue

                # 3. Text content and inline <think> tags
                content = delta.get("content")
                if content:
                    text_buffer += content
                    while text_buffer:
                        if not in_think_tag:
                            think_start = text_buffer.find("<think>")
                            if think_start != -1:
                                if think_start > 0:
                                    yield ("content", text_buffer[:think_start])
                                in_think_tag = True
                                text_buffer = text_buffer[think_start + 7:]
                            else:
                                if _ends_with_partial_tag(text_buffer, "<think>"):
                                    break
                                yield ("content", text_buffer)
                                text_buffer = ""
                        else:
                            think_end = text_buffer.find("</think>")
                            if think_end != -1:
                                if think_end > 0:
                                    yield ("thought", text_buffer[:think_end])
                                in_think_tag = False
                                text_buffer = text_buffer[think_end + 8:]
                            else:
                                if _ends_with_partial_tag(text_buffer, "</think>"):
                                    break
                                yield ("thought", text_buffer)
                                text_buffer = ""
        except Exception as exc:
            if cancel_token is not None and cancel_token.cancelled:
                stopped = True
                yield ("stopped", None)
                return
            yield ("error", f"Error reading response stream: {exc}")
        finally:
            response.close()

        if stopped:
            return

        if cancel_token is not None and cancel_token.cancelled:
            yield ("stopped", None)
            return

        if text_buffer:
            if in_think_tag:
                yield ("thought", text_buffer)
            else:
                yield ("content", text_buffer)


class _CategorizedError(Exception):
    def __init__(self, category: str, message: str):
        self.category = category
        self.message = message
        super().__init__(message)


def _ends_with_partial_tag(text: str, tag: str) -> bool:
    """Return True if *text* ends with a prefix of *tag*."""
    return any(text.endswith(tag[:i]) for i in range(1, len(tag) + 1))
