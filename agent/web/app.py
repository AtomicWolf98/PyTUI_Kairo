"""FastAPI WebUI adapter for Kairo."""
from __future__ import annotations

import asyncio
import secrets
import socket
import webbrowser
from contextlib import suppress
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from agent._version import __version__
from agent.config import Config
from agent.runtime import DoctorService, KairoRuntime, event_to_dict
from agent.web.assets import static_root


_ERROR_STATUS = {
    "invalid_request": 400,
    "invalid_session_id": 400,
    "config_validation_failed": 400,
    "session_not_found": 404,
    "approval_not_found": 404,
    "runtime_busy": 409,
    "last_session": 409,
    "max_sessions": 409,
    "skill_manifest_changed": 409,
    "session_persistence_failed": 500,
    "mutation_failed": 500,
    "runtime_sync_failed": 500,
    "runtime_degraded": 503,
    "runtime_closing": 503,
}


def _api_result(result: dict[str, Any], *, default_status: int = 400):
    """Return stable WebUI errors while preserving successful service payloads."""
    if result.get("ok"):
        return result
    code = str(result.get("code", "") or "invalid_request")
    message = str(result.get("error") or result.get("message") or "Request failed.")
    payload = {
        **result,
        "ok": False,
        "code": code,
        "error": message,
        "detail": message,
        "retryable": bool(result.get("retryable", code == "runtime_busy")),
    }
    return JSONResponse(payload, status_code=_ERROR_STATUS.get(code, default_status))


def create_web_app(runtime: KairoRuntime, *, token: str | None = None) -> FastAPI:
    """Create the local-only Kairo WebUI application."""
    app = FastAPI(title="Kairo WebUI", version=__version__)
    auth_token = token if token is not None else secrets.token_urlsafe(24)
    app.state.kairo_runtime = runtime
    app.state.kairo_token = auth_token

    @app.middleware("http")
    async def local_token_middleware(request: Request, call_next):
        local_error = _local_request_error(request)
        if local_error:
            return JSONResponse({"detail": local_error}, status_code=403)
        if request.url.path.startswith("/assets/") or request.url.path in ("/", "/index.html", "/favicon.ico"):
            return await call_next(request)
        if runtime.config.web.get("local_auth_token", True):
            supplied = request.headers.get("x-kairo-token") or request.query_params.get("token")
            if supplied != auth_token:
                return JSONResponse({"detail": "Missing or invalid local auth token."}, status_code=401)
        return await call_next(request)

    @app.get("/api/status")
    def api_status():
        return {**runtime.status(), "web": {"token_required": runtime.config.web.get("local_auth_token", True)}}

    @app.get("/api/config")
    def api_config():
        return runtime.config_service.redacted()

    @app.get("/api/settings/view")
    def api_settings_view():
        return runtime.config_service.settings_view()

    @app.patch("/api/settings/{section}")
    async def api_settings_update(section: str, request: Request):
        payload = await request.json()
        result = runtime.config_service.update_settings(section, payload)
        return _api_result(result)

    @app.post("/api/settings/provider")
    async def api_settings_provider_create(request: Request):
        payload = await request.json()
        result = runtime.config_service.save_provider("", payload, create=True)
        return _api_result(result)

    @app.patch("/api/settings/provider/{provider_id}")
    async def api_settings_provider_update(provider_id: str, request: Request):
        payload = await request.json()
        result = runtime.config_service.save_provider(provider_id, payload)
        return _api_result(result)

    @app.delete("/api/settings/provider/{provider_id}")
    def api_settings_provider_delete(provider_id: str):
        result = runtime.config_service.delete_provider(provider_id)
        return _api_result(result)

    @app.post("/api/settings/provider/{provider_id}/test")
    def api_settings_provider_test(provider_id: str):
        result = runtime.config_service.test_provider(provider_id)
        if not result.get("ok"):
            return JSONResponse(result, status_code=200)
        return result

    @app.post("/api/settings/profile")
    async def api_settings_profile_create(request: Request):
        payload = await request.json()
        result = runtime.config_service.save_profile("", payload, create=True)
        return _api_result(result)

    @app.patch("/api/settings/profile/{profile_id:path}")
    async def api_settings_profile_update(profile_id: str, request: Request):
        payload = await request.json()
        result = runtime.config_service.save_profile(profile_id, payload)
        return _api_result(result)

    @app.delete("/api/settings/profile/{profile_id:path}")
    def api_settings_profile_delete(profile_id: str):
        result = runtime.config_service.delete_profile(profile_id)
        return _api_result(result)

    @app.post("/api/config/export")
    async def api_config_export(request: Request):
        payload: dict[str, Any] = {}
        with suppress(Exception):
            payload = await request.json()
        result = runtime.config_service.export_config(
            with_keys=bool(payload.get("with_keys", False)),
            confirm=str(payload.get("confirm", "")),
        )
        return _api_result(result)

    @app.post("/api/config/import")
    async def api_config_import(request: Request):
        payload = await request.json()
        result = runtime.config_service.import_config(str(payload.get("path", "")))
        return _api_result(result)

    @app.patch("/api/config/{section}")
    async def api_config_update(section: str, request: Request):
        payload = await request.json()
        result = runtime.config_service.update(section, payload)
        return _api_result(result)

    @app.post("/api/config/profile/{profile_id}/switch")
    def api_switch_profile(profile_id: str):
        result = runtime.config_service.switch_profile(profile_id)
        return _api_result(result, default_status=404)

    @app.get("/api/workspace/snapshot")
    def api_workspace_snapshot(selected_file: str = ""):
        result = runtime.workspace.snapshot(selected_file)
        status = runtime.status()
        result.setdefault("runtime_id", status.get("runtime_id", ""))
        result.setdefault("workspace_revision", status.get("workspace_revision", 0))
        return result

    @app.get("/api/workspace/file")
    def api_workspace_file(path: str = ""):
        result = runtime.workspace.file_preview(path)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "File preview failed."))
        return result

    @app.get("/api/workspace/bookmarks")
    def api_workspace_bookmarks():
        return runtime.workspace.bookmarks()

    @app.post("/api/workspace/bookmarks")
    async def api_workspace_bookmark_add(request: Request):
        payload = await request.json()
        result = runtime.workspace.add_bookmark(str(payload.get("name", "")), str(payload.get("path", "")))
        return _api_result(result)

    @app.delete("/api/workspace/bookmarks/{name}")
    def api_workspace_bookmark_delete(name: str):
        result = runtime.workspace.remove_bookmark(name)
        return _api_result(result, default_status=404)

    @app.post("/api/workspace/move")
    async def api_workspace_move(request: Request):
        payload = await request.json()
        result = runtime.workspace.move(str(payload.get("target", "")))
        return _api_result(result)

    @app.get("/api/sessions")
    def api_sessions():
        return runtime.sessions.list()

    @app.post("/api/sessions")
    async def api_session_create(request: Request):
        payload = await request.json()
        return _api_result(runtime.sessions.create(payload.get("name")))

    @app.post("/api/sessions/{session_id}/switch")
    def api_session_switch(session_id: str):
        result = runtime.sessions.switch(session_id)
        return _api_result(result, default_status=404)

    @app.patch("/api/sessions/{session_id}")
    async def api_session_rename(session_id: str, request: Request):
        payload = await request.json()
        result = runtime.sessions.rename(session_id, str(payload.get("name", "")))
        return _api_result(result)

    @app.delete("/api/sessions/{session_id}")
    def api_session_delete(session_id: str):
        result = runtime.sessions.delete(session_id)
        return _api_result(result)

    @app.get("/api/sessions/search")
    def api_session_search(q: str):
        return runtime.sessions.search(q)

    @app.post("/api/sessions/{session_id}/export")
    async def api_session_export(session_id: str, request: Request):
        payload = await request.json()
        result = runtime.sessions.export(session_id, fmt=str(payload.get("format", "markdown")))
        return _api_result(result)

    @app.get("/api/chat/history")
    def api_chat_history():
        return runtime.chat.history()

    @app.post("/api/chat")
    async def api_chat(request: Request):
        payload = await request.json()
        result = runtime.submit_message(str(payload.get("message", "")))
        return _api_result(result, default_status=409)

    @app.post("/api/chat/stop")
    def api_chat_stop():
        return runtime.stop_current_task()

    @app.post("/api/tools/approval")
    async def api_tool_approval(request: Request):
        payload = await request.json()
        result = runtime.resolve_approval(str(payload.get("id", "")), int(payload.get("choice", 0)))
        return _api_result(result, default_status=404)

    @app.get("/api/skills")
    def api_skills():
        return runtime.skills.list()

    @app.post("/api/skills/reload")
    def api_skills_reload():
        return _api_result(runtime.skills.reload())

    @app.post("/api/skills/trust")
    async def api_skills_trust(request: Request):
        payload = await request.json()
        if not hasattr(runtime.skills, "trust"):
            return _api_result({"ok": False, "code": "invalid_request", "error": "Skill trust is unavailable."})
        return _api_result(runtime.skills.trust(str(payload.get("manifest_digest", ""))))

    @app.delete("/api/skills/trust")
    def api_skills_revoke():
        if not hasattr(runtime.skills, "revoke"):
            return _api_result({"ok": False, "code": "invalid_request", "error": "Skill trust is unavailable."})
        return _api_result(runtime.skills.revoke())

    @app.post("/api/doctor")
    async def api_doctor(request: Request):
        payload: dict[str, Any] = {}
        with suppress(Exception):
            payload = await request.json()
        return DoctorService(runtime).run(local_only=bool(payload.get("local_only", True)))

    @app.websocket("/api/events")
    async def api_events(websocket: WebSocket):
        if _local_websocket_error(websocket):
            await websocket.close(code=4403)
            return
        if runtime.config.web.get("local_auth_token", True) and websocket.query_params.get("token") != auth_token:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()

        def enqueue(event):
            loop.call_soon_threadsafe(q.put_nowait, event)

        unsubscribe = runtime.events.subscribe(enqueue)
        try:
            await websocket.send_json({"kind": "status", "payload": runtime.status()})
            for event in runtime.events.snapshot():
                await websocket.send_json(event_to_dict(event))
            while True:
                event = await q.get()
                await websocket.send_json(event_to_dict(event))
        except WebSocketDisconnect:
            pass
        finally:
            unsubscribe()

    _mount_static(app, auth_token)
    return app


def run_web(config: Config, *, host: str | None = None, port: int | None = None, open_browser: bool | None = None):
    """Run the local WebUI server."""
    import uvicorn

    runtime = KairoRuntime(config)
    token = secrets.token_urlsafe(24) if config.web.get("local_auth_token", True) else ""
    host = host or config.web.get("host", "127.0.0.1")
    requested_port = config.web.get("port", 8765) if port is None else port
    port = _resolve_port(host, int(requested_port))
    app = create_web_app(runtime, token=token)
    url = f"http://{host}:{port}/" + (f"?token={token}" if token else "")
    should_open = config.web.get("open_browser", True) if open_browser is None else open_browser
    print(f"Kairo WebUI: {url}")
    if should_open:
        webbrowser.open(url)
    uvicorn.run(app, host=host, port=port, log_level="info")


def _mount_static(app: FastAPI, token: str) -> None:
    root = static_root()
    assets = root / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{path:path}")
    def spa(path: str = ""):
        index = root / "index.html"
        if index.exists():
            return FileResponse(index)
        return HTMLResponse("<!doctype html><title>Kairo</title><div id='root'>Kairo WebUI assets are missing.</div>")


def _resolve_port(host: str, port: int) -> int:
    if port != 0:
        return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _local_request_error(request: Request) -> str:
    client = request.client.host if request.client else ""
    if client not in ("127.0.0.1", "::1", "localhost", "testclient"):
        return "Kairo WebUI is local-only by default."
    return ""


def _local_websocket_error(websocket: WebSocket) -> str:
    client = websocket.client.host if websocket.client else ""
    if client not in ("127.0.0.1", "::1", "localhost", "testclient"):
        return "Kairo WebUI is local-only by default."
    return ""
