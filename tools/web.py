import urllib.request
import re

from tools.base import BaseTool
from tools.policy import NetworkPolicy, OperationScope, Permission, SecurityError

class WebFetchTool(BaseTool):
    name = "web_fetch"
    description = "Fetches the HTML content of a website URL and extracts the main text."
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The absolute URL to fetch (e.g. https://example.com)."
            }
        },
        "required": ["url"]
    }
    permission = Permission.NETWORK

    def classify_scope(self, arguments: str) -> OperationScope:
        """Network boundaries are enforced by NetworkPolicy; scope is internal."""
        return OperationScope.INTERNAL

    def __init__(self, config=None):
        self.network_policy: NetworkPolicy
        max_fetch_bytes = 1_048_576
        if config is not None:
            network_policy = config.policy.get("network", {})
            self.network_policy = NetworkPolicy(
                allow_hosts=network_policy.get("allow_hosts"),
                deny_hosts=network_policy.get("deny_hosts"),
                deny_private_loopback=network_policy.get("deny_private_loopback", True),
            )
            max_fetch_bytes = config.policy.get("resource_limits", {}).get("max_fetch_bytes", max_fetch_bytes)
        else:
            self.network_policy = NetworkPolicy()
        self.max_bytes = max(1024, int(max_fetch_bytes))

    def execute(self, url: str) -> str:
        try:
            self.network_policy.validate_url(url)
        except SecurityError as e:
            return f"Error: {e}"

        # Add simple user-agent to avoid getting blocked by simple checkers
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }

        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as response:
                content_length = response.headers.get("Content-Length")
                if content_length is not None and int(content_length) > self.max_bytes:
                    return (
                        f"Error: Response for '{url}' exceeds the configured fetch limit "
                        f"of {self.max_bytes:,} bytes."
                    )
                html_bytes = response.read(self.max_bytes + 1)
                if len(html_bytes) > self.max_bytes:
                    return (
                        f"Error: Response body for '{url}' exceeds the configured fetch limit "
                        f"of {self.max_bytes:,} bytes."
                    )
                charset = response.headers.get_content_charset() or "utf-8"
                html = html_bytes.decode(charset, errors="replace")

            # Clean HTML to extract text
            text = self.clean_html(html)
            return f"Successfully fetched content from {url}:\n\n{text[:8000]}"  # Limit display size
        except Exception as e:
            return f"Error fetching URL '{url}': {str(e)}"

    def clean_html(self, html: str) -> str:
        # Remove script and style elements
        html = re.sub(r'<(script|style)\b[^>]*>([\s\S]*?)<\/\1>', ' ', html, flags=re.IGNORECASE)
        # Remove comments
        html = re.sub(r'<!--[\s\S]*?-->', ' ', html)
        # Replace block tags with newlines
        html = re.sub(r'</?(p|div|h\d|li|tr|br|section|article|header|footer)\b[^>]*>', '\n', html, flags=re.IGNORECASE)
        # Strip all other HTML tags
        html = re.sub(r'<[^>]+>', ' ', html)
        # Unescape basic HTML entities
        html = html.replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"').replace("&apos;", "'")
        # Collapse multiple newlines and spaces
        lines = [line.strip() for line in html.splitlines()]
        cleaned_lines = []
        for line in lines:
            if line:
                # Collapse spaces
                line = re.sub(r'\s+', ' ', line)
                cleaned_lines.append(line)
        return "\n".join(cleaned_lines)
