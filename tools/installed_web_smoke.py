"""Exercise the installed Kairo WebUI over a real loopback HTTP socket."""
from __future__ import annotations

import argparse
import json
import re
import socket
import threading
import time
import urllib.request
from importlib.metadata import version
from typing import Any, cast

import uvicorn

from agent.web.app import create_web_app


class _Config:
    web = {"local_auth_token": False}


class _Events:
    def subscribe(self, _callback):
        return lambda: None

    def snapshot(self):
        return []


class _Runtime:
    config = _Config()
    events = _Events()

    def status(self):
        return {"version": version("kairo-agent")}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _get(url: str) -> tuple[int, bytes, str]:
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.status, response.read(), response.headers.get_content_type()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-version", default="0.3.3")
    args = parser.parse_args()
    installed_version = version("kairo-agent")
    if installed_version != args.expected_version:
        raise SystemExit(f"installed version is {installed_version}, expected {args.expected_version}")

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            create_web_app(cast(Any, _Runtime()), token=""),
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
    )
    thread = threading.Thread(target=server.run, name="kairo-wheel-smoke", daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not server.started:
            raise RuntimeError("installed WebUI server did not start")

        status, html_bytes, content_type = _get(f"{base}/")
        html = html_bytes.decode("utf-8")
        if status != 200 or content_type != "text/html":
            raise RuntimeError(f"WebUI entry point returned {status} ({content_type})")
        if "Kairo WebUI assets are missing" in html:
            raise RuntimeError("installed WebUI served the missing-assets placeholder")

        assets = sorted(set(re.findall(r'(?:src|href)=["\'](/assets/[^"\']+)["\']', html)))
        if not assets:
            raise RuntimeError("installed WebUI entry point references no assets")
        results = {}
        for asset in assets:
            asset_status, body, _ = _get(f"{base}{asset}")
            if asset_status != 200 or not body:
                raise RuntimeError(f"asset {asset} returned {asset_status} or an empty body")
            results[asset] = len(body)
        print(json.dumps({"version": installed_version, "entry_status": status, "assets": results}, sort_keys=True))
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
