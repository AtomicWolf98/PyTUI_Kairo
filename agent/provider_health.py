"""Provider health check (Feature 2 of the 0.2.3 release).

Performs a minimal, non-streaming OpenAI-compatible chat-completions request
to validate that a provider is reachable, the API key works, and the model
name is accepted.

The test request obeys these rules (required by the planning document):

- Tools are disabled.
- Streaming is disabled; the full small response is read once.
- A short timeout (default 10s) is enforced.
- Nothing is appended to the session history.
- Context compression is never triggered from this path.

Error classification is exposed via :class:`ProviderTestResult` so both the
plain and Textual UIs can render consistent result messaging.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional

STATUS_SUCCESS = "success"
STATUS_AUTH_ERROR = "auth_error"
STATUS_MODEL_ERROR = "model_error"
STATUS_URL_ERROR = "url_error"
STATUS_RATE_LIMIT = "rate_limit"
STATUS_UNKNOWN = "unknown"


@dataclass
class ProviderTestResult:
    status: str
    http_status: int = 0
    provider_message: str = ""
    elapsed_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status == STATUS_SUCCESS

    def summary(self) -> str:
        if self.ok:
            return f"[OK] Provider reachable (HTTP {self.http_status}, {self.elapsed_ms}ms)."
        label = {
            STATUS_AUTH_ERROR: "Auth Error",
            STATUS_MODEL_ERROR: "Model Error",
            STATUS_URL_ERROR: "URL Error",
            STATUS_RATE_LIMIT: "Rate Limit",
            STATUS_UNKNOWN: "Unknown",
        }.get(self.status, self.status)
        detail = self.provider_message.strip() or "no additional detail"
        return f"[{label}] {detail} (HTTP {self.http_status or '-'}, {self.elapsed_ms}ms)"


_MODEL_NOT_FOUND_MARKERS = (
    "model not found",
    "does not exist",
    "model_not_found",
    "invalid model",
    "unknown model",
)


def _classify(http_status: int, error_message: str) -> str:
    """Map an HTTP status + provider error body onto a high-level category."""
    message = (error_message or "").lower()
    if http_status in (401, 403):
        return STATUS_AUTH_ERROR
    if http_status == 404 or any(marker in message for marker in _MODEL_NOT_FOUND_MARKERS):
        return STATUS_MODEL_ERROR
    if http_status == 429:
        return STATUS_RATE_LIMIT
    if http_status >= 400 and http_status < 500:
        # 400 with model markers also falls through to MODEL_ERROR above;
        # remaining 4xx become auth/client classification.
        return STATUS_AUTH_ERROR if http_status in (401, 403) else STATUS_UNKNOWN
    return STATUS_UNKNOWN


def _build_opener() -> urllib.request.OpenerDirector:
    proxies: Dict[str, str] = {}
    for scheme, env_var in (("http", "HTTP_PROXY"), ("https", "HTTPS_PROXY")):
        value = os.environ.get(env_var) or os.environ.get(env_var.lower())
        if value:
            proxies[scheme] = value
    if proxies:
        return urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
    return urllib.request.build_opener()


def test_connection(
    *,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float = 10.0,
    opener: Optional[urllib.request.OpenerDirector] = None,
) -> ProviderTestResult:
    """Send a minimal chat-completions probe and classify the response.

    ``opener`` may be injected for unit tests. In production use the default
    opener that respects HTTP(S)_PROXY environment variables.
    """
    url = (base_url or "").rstrip("/")
    if not (url.startswith("http://") or url.startswith("https://")):
        return ProviderTestResult(status=STATUS_URL_ERROR, provider_message=f"Invalid base_url: {base_url!r}")
    if not url.endswith("/chat/completions"):
        url = f"{url}/chat/completions"

    payload: Dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "temperature": 0,
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url=url, data=data, headers=headers, method="POST")
    opener_to_use = opener or _build_opener()

    start = time.monotonic()
    try:
        response = opener_to_use.open(request, timeout=timeout)
        try:
            # Drain the body; status 200 (or 2xx) means the model accepted.
            _ = response.read()
        finally:
            response.close()
        elapsed = int((time.monotonic() - start) * 1000)
        return ProviderTestResult(status=STATUS_SUCCESS, http_status=response.status, elapsed_ms=elapsed)
    except urllib.error.HTTPError as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        try:
            parsed = json.loads(body)
            msg = parsed.get("error", {}).get("message", body) if isinstance(parsed, dict) else body
        except (TypeError, ValueError, AttributeError):
            msg = body or str(exc)
        status = _classify(exc.code, str(msg))
        return ProviderTestResult(status=status, http_status=exc.code, provider_message=str(msg), elapsed_ms=elapsed)
    except urllib.error.URLError as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        reason = getattr(exc, "reason", str(exc))
        return ProviderTestResult(
            status=STATUS_URL_ERROR,
            http_status=0,
            provider_message=f"Cannot reach provider: {reason}",
            elapsed_ms=elapsed,
        )
    except Exception as exc:  # pragma: no cover - defensive
        elapsed = int((time.monotonic() - start) * 1000)
        return ProviderTestResult(
            status=STATUS_UNKNOWN,
            http_status=0,
            provider_message=str(exc),
            elapsed_ms=elapsed,
        )