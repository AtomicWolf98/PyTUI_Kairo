"""FastAPI WebUI adapter for Kairo."""
from __future__ import annotations

import asyncio
import secrets
import socket
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from agent.config import Config
from agent.runtime import DoctorService, KairoRuntime, event_to_dict


def create_web_app(runtime: KairoRuntime, *, token: Optional[str] = None) -> FastAPI:
    """Create the local-only Kairo WebUI application."""
    app = FastAPI(title="Kairo WebUI", version="0.3.1-preview")
    auth_token = token if token is not None else secrets.token_urlsafe(24)
    app.state.kairo_runtime = runtime
    app.state.kairo_token = auth_token

    @app.middleware("http")
    async def local_token_middleware(request: Request, call_next):
        if request.url.path.startswith("/assets/") or request.url.path in ("/favicon.ico",):
            return await call_next(request)
        local_error = _local_request_error(request)
        if local_error:
            return JSONResponse({"detail": local_error}, status_code=403)
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

    @app.post("/api/config/export")
    async def api_config_export(request: Request):
        payload: Dict[str, Any] = {}
        try:
            payload = await request.json()
        except Exception:
            pass
        result = runtime.config_service.export_config(
            with_keys=bool(payload.get("with_keys", False)),
            confirm=str(payload.get("confirm", "")),
        )
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "Config export failed."))
        return result

    @app.post("/api/config/import")
    async def api_config_import(request: Request):
        payload = await request.json()
        result = runtime.config_service.import_config(str(payload.get("path", "")))
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "Config import failed."))
        return result

    @app.patch("/api/config/{section}")
    async def api_config_update(section: str, request: Request):
        payload = await request.json()
        result = runtime.config_service.update(section, payload)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "Config update failed."))
        return result

    @app.post("/api/config/profile/{profile_id}/switch")
    def api_switch_profile(profile_id: str):
        result = runtime.config_service.switch_profile(profile_id)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("message", "Profile not found."))
        return result

    @app.get("/api/workspace/snapshot")
    def api_workspace_snapshot(selected_file: str = ""):
        return runtime.workspace.snapshot(selected_file)

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
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "Bookmark save failed."))
        return result

    @app.delete("/api/workspace/bookmarks/{name}")
    def api_workspace_bookmark_delete(name: str):
        result = runtime.workspace.remove_bookmark(name)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error", "Bookmark not found."))
        return result

    @app.post("/api/workspace/move")
    async def api_workspace_move(request: Request):
        payload = await request.json()
        result = runtime.workspace.move(str(payload.get("target", "")))
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("message", "Workspace move failed."))
        return result

    @app.get("/api/sessions")
    def api_sessions():
        return runtime.sessions.list()

    @app.post("/api/sessions")
    async def api_session_create(request: Request):
        payload = await request.json()
        return runtime.sessions.create(payload.get("name"))

    @app.post("/api/sessions/{session_id}/switch")
    def api_session_switch(session_id: str):
        result = runtime.sessions.switch(session_id)
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail="Session not found.")
        return result

    @app.patch("/api/sessions/{session_id}")
    async def api_session_rename(session_id: str, request: Request):
        payload = await request.json()
        result = runtime.sessions.rename(session_id, str(payload.get("name", "")))
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "Rename failed."))
        return result

    @app.delete("/api/sessions/{session_id}")
    def api_session_delete(session_id: str):
        result = runtime.sessions.delete(session_id)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "Delete failed."))
        return result

    @app.get("/api/sessions/search")
    def api_session_search(q: str):
        return runtime.sessions.search(q)

    @app.post("/api/sessions/{session_id}/export")
    async def api_session_export(session_id: str, request: Request):
        payload = await request.json()
        result = runtime.sessions.export(session_id, fmt=str(payload.get("format", "markdown")))
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error", "Export failed."))
        return result

    @app.get("/api/chat/history")
    def api_chat_history():
        return runtime.chat.history()

    @app.post("/api/chat")
    async def api_chat(request: Request):
        payload = await request.json()
        result = runtime.submit_message(str(payload.get("message", "")))
        if not result.get("ok"):
            raise HTTPException(status_code=409, detail=result.get("error", "Chat submit failed."))
        return result

    @app.post("/api/chat/stop")
    def api_chat_stop():
        return runtime.stop_current_task()

    @app.post("/api/tools/approval")
    async def api_tool_approval(request: Request):
        payload = await request.json()
        result = runtime.resolve_approval(str(payload.get("id", "")), int(payload.get("choice", 0)))
        if not result.get("ok"):
            raise HTTPException(status_code=404, detail=result.get("error", "Approval not found."))
        return result

    @app.get("/api/skills")
    def api_skills():
        return runtime.skills.list()

    @app.post("/api/skills/reload")
    def api_skills_reload():
        return runtime.skills.reload()

    @app.post("/api/doctor")
    async def api_doctor(request: Request):
        payload: Dict[str, Any] = {}
        try:
            payload = await request.json()
        except Exception:
            pass
        return DoctorService(runtime).run(local_only=bool(payload.get("local_only", True)))

    @app.websocket("/api/events")
    async def api_events(websocket: WebSocket):
        if _local_websocket_error(websocket):
            await websocket.close(code=4403)
            return
        if runtime.config.web.get("local_auth_token", True):
            if websocket.query_params.get("token") != auth_token:
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


def run_web(config: Config, *, host: Optional[str] = None, port: Optional[int] = None, open_browser: Optional[bool] = None):
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
    root = Path(__file__).resolve().parents[2]
    dist = root / "web" / "dist"
    public = root / "web" / "public"
    static_root = dist if dist.exists() else public
    assets = static_root / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{path:path}")
    def spa(path: str = ""):
        index = static_root / "index.html"
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
