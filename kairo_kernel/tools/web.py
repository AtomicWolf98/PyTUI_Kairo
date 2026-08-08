"""HTTP fetch tool with redirect-by-redirect SSRF validation."""

from __future__ import annotations

import asyncio
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message
from typing import cast

from kairo_kernel.contracts.content import ContentBlock, TextBlock
from kairo_kernel.contracts.enums import OperationScope
from kairo_kernel.contracts.json import JsonObject, freeze_json
from kairo_kernel.contracts.tools import ToolDescriptor, ToolExecutionContext, ToolInvocation
from kairo_kernel.runtime.workspace import WorkspaceLeaseManager
from kairo_kernel.tools.base import BuiltinTool, string_argument
from kairo_kernel.tools.policy import NetworkPolicy, PolicyViolation


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


def _schema() -> JsonObject:
    frozen = freeze_json(
        {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}
    )
    if not isinstance(frozen, JsonObject):
        raise TypeError("Tool schema must be an object.")
    return frozen


class WebFetchTool(BuiltinTool):
    def __init__(
        self,
        workspace: WorkspaceLeaseManager,
        *,
        network_policy: NetworkPolicy | None = None,
        max_fetch_bytes: int = 1_048_576,
        max_redirects: int = 5,
        timeout_seconds: float = 30.0,
        max_output_chars: int = 200_000,
    ) -> None:
        super().__init__(
            ToolDescriptor("web_fetch", "Fetch a public HTTP(S) text resource.", _schema(), ("network",)),
            workspace,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )
        self.network_policy = network_policy or NetworkPolicy()
        self.max_fetch_bytes = max(1, max_fetch_bytes)
        self.max_redirects = max(0, max_redirects)

    async def _classify(self, invocation: ToolInvocation) -> OperationScope:
        self.network_policy.validate(string_argument(invocation.arguments, "url"))
        return OperationScope.EXTERNAL

    async def _run(self, invocation: ToolInvocation, context: ToolExecutionContext) -> tuple[ContentBlock, ...]:
        del context
        url = string_argument(invocation.arguments, "url")
        async with await self.workspace.read():
            text = await asyncio.to_thread(self._fetch, url)
        return (TextBlock(text),)

    def _fetch(self, initial_url: str) -> str:
        opener = urllib.request.build_opener(_NoRedirect())
        url = initial_url
        for redirect_count in range(self.max_redirects + 1):
            self.network_policy.validate(url)
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "Kairo-Kernel/1.0", "Accept": "text/*,application/json,application/xml"},
                method="GET",
            )
            try:
                response = opener.open(request, timeout=self.timeout_seconds)
            except urllib.error.HTTPError as exc:
                if exc.code not in (301, 302, 303, 307, 308):
                    raise RuntimeError(f"HTTP {exc.code}: {exc.reason}") from exc
                location = exc.headers.get("Location", "")
                if not location:
                    raise PolicyViolation("Redirect response omitted Location.") from exc
                if redirect_count >= self.max_redirects:
                    raise PolicyViolation("Redirect limit exceeded.") from exc
                url = urllib.parse.urljoin(url, location)
                continue
            with response:
                content_type = response.headers.get_content_type()
                if not (
                    content_type.startswith("text/")
                    or content_type in ("application/json", "application/xml", "application/xhtml+xml")
                ):
                    raise PolicyViolation(f"Unsupported response content type: {content_type}")
                raw = cast(bytes, response.read(self.max_fetch_bytes + 1))
                if len(raw) > self.max_fetch_bytes:
                    raise PolicyViolation(f"Response exceeds {self.max_fetch_bytes} byte limit.")
                charset = str(response.headers.get_content_charset() or "utf-8")
                return raw.decode(charset, errors="replace")
        raise PolicyViolation("Redirect limit exceeded.")
