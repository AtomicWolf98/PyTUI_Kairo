import json
import os
import sys


def send(message):
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:
        continue
    if method == "server/discover":
        result = {
            "supportedVersions": ["2026-07-28"],
            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
        }
    elif method == "initialize":
        result = {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            "serverInfo": {"name": "fixture", "version": "1"},
        }
    elif method == "tools/list":
        result = {"tools": [{"name": "echo", "inputSchema": {}}]}
    elif method in {"resources/list", "prompts/list"}:
        result = {method.split("/", 1)[0]: []}
    elif method == "tools/call":
        send({"jsonrpc": "2.0", "id": "server-sampling", "method": "sampling/createMessage", "params": {}})
        rejected = json.loads(sys.stdin.readline())
        result = {
            "content": [{"type": "text", "text": os.environ.get("SAFE_VALUE", "missing")}],
            "rejectionCode": rejected["error"]["code"],
        }
    else:
        result = {}
    send({"jsonrpc": "2.0", "id": request_id, "result": result})
