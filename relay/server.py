"""FastAPI relay: transcript in from one capture client, out to every viewer.

Endpoints (see docs/MESSAGES.md):

    GET  /wall /phone /status      the three pages
    GET  /tune                     build a wall-view URL with sliders
    GET  /<name>                   any other viewer/<name>.html
    WS   /ws/viewer?role=wall      caption feed
    WS   /ws/source?token=...      capture client, bidirectional
    WS   /ws/status                status page feed
    GET  /api/status               one status snapshot as JSON
    POST /api/control              {"action": "open_stream"} etc.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import socket
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from . import config as config_module
from .hub import CONTROL_ACTIONS, Client, Hub

HEARTBEAT_SECONDS = 5.0
STATUS_PUSH_SECONDS = 0.4

PAGES = {"wall": "wall.html", "phone": "phone.html",
         "status": "status.html", "tune": "tune.html"}

# Only a bare file name may be requested as a page.
SAFE_PAGE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# Never cached: ProPresenter can hold a page for the length of an event, and an
# operator editing query params must always get the current file.
NO_STORE = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
}


def local_ip() -> str:
    """Best guess at the LAN address to hand phones and ProPresenter."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))     # no packet is actually sent
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def peer_of(ws: WebSocket) -> str:
    client = ws.client
    return f"{client.host}:{client.port}" if client else "?"


def create_app(cfg: config_module.RelayConfig | None = None) -> FastAPI:
    cfg = cfg or config_module.load()
    hub = Hub()

    async def heartbeat_loop() -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            hub.heartbeat()

    async def status_loop() -> None:
        while True:
            await asyncio.sleep(STATUS_PUSH_SECONDS)
            hub.push_status()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        tasks = [
            asyncio.create_task(heartbeat_loop()),
            asyncio.create_task(status_loop()),
        ]
        ip = local_ip()
        print(f"[relay] session {hub.session_id}")
        print(f"[relay] wall    http://{ip}:{cfg.port}/wall")
        print(f"[relay] phone   http://{ip}:{cfg.port}/phone")
        print(f"[relay] status  http://{ip}:{cfg.port}/status")
        print(f"[relay] tune    http://{ip}:{cfg.port}/tune")
        if cfg.token_required:
            print("[relay] source token required")
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title="Campus Captions relay", lifespan=lifespan)
    app.state.hub = hub
    app.state.config = cfg

    # -- pages -----------------------------------------------------------

    def page(name: str) -> FileResponse:
        path = config_module.VIEWER_DIR / PAGES[name]
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"{PAGES[name]} not found")
        return FileResponse(path, media_type="text/html; charset=utf-8",
                            headers=NO_STORE)

    @app.get("/")
    async def root() -> FileResponse:
        return page("status")

    @app.get("/wall")
    async def wall() -> FileResponse:
        return page("wall")

    @app.get("/phone")
    async def phone() -> FileResponse:
        return page("phone")

    @app.get("/status")
    async def status_page() -> FileResponse:
        return page("status")

    @app.get("/tune")
    async def tune_page() -> FileResponse:
        return page("tune")

    # Any other viewer/*.html, addressed by name. Registered last, so every
    # explicit route above still wins.
    #
    # This exists because HTML is read from disk per request while routes are
    # registered once at startup: adding a page used to mean editing this file
    # AND restarting, and the page would appear in a link while 404ing. Now a
    # new file in viewer/ is reachable immediately.
    @app.get("/{page}")
    async def named_page(page: str) -> FileResponse:
        if not SAFE_PAGE_NAME.match(page):
            raise HTTPException(status_code=404, detail="not found")
        name = page if page.endswith(".html") else f"{page}.html"
        path = config_module.VIEWER_DIR / name
        try:
            path.resolve().relative_to(config_module.VIEWER_DIR.resolve())
        except ValueError:
            raise HTTPException(status_code=404, detail="not found")
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"{name} not found")
        return FileResponse(path, media_type="text/html; charset=utf-8",
                            headers=NO_STORE)

    # -- operator API ----------------------------------------------------

    @app.get("/api/status")
    async def api_status() -> JSONResponse:
        snap = hub.snapshot()
        snap["relay"] = {
            "host": cfg.host,
            "port": cfg.port,
            "ip": local_ip(),
            "token_required": cfg.token_required,
            "config": str(cfg.config_path) if cfg.config_path else None,
        }
        return JSONResponse(snap, headers=NO_STORE)

    @app.post("/api/control")
    async def api_control(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            body = {}
        action = (body or {}).get("action")
        if action not in CONTROL_ACTIONS:
            raise HTTPException(
                status_code=400,
                detail=f"action must be one of {sorted(CONTROL_ACTIONS)}",
            )
        delivered = hub.send_control(action)
        print(f"[relay] control {action} -> {delivered} capture client(s)")
        return JSONResponse(
            {"ok": True, "action": action, "delivered_to": delivered},
            headers=NO_STORE,
        )

    # -- websockets ------------------------------------------------------

    async def pump(ws: WebSocket, client: Client) -> None:
        """The only writer for this socket."""
        while True:
            payload = await client.queue.get()
            await ws.send_text(payload)

    async def serve(ws: WebSocket, client: Client, read: bool) -> None:
        sender = asyncio.create_task(pump(ws, client))
        try:
            while True:
                raw = await ws.receive_text()
                if read:
                    hub.ingest(raw, client.peer)
        except WebSocketDisconnect:
            pass
        except Exception as exc:                     # never take the relay down
            print(f"[relay] {client} error: {exc!r}")
        finally:
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sender
            hub.remove(client)

    @app.websocket("/ws/viewer")
    async def ws_viewer(ws: WebSocket, role: str = "wall") -> None:
        await ws.accept()
        client = hub.add_viewer(role, peer_of(ws))
        print(f"[relay] + {client} ({len(hub.viewers)} viewers)")
        await serve(ws, client, read=False)
        print(f"[relay] - {client} ({len(hub.viewers)} viewers)")

    @app.websocket("/ws/status")
    async def ws_status(ws: WebSocket) -> None:
        await ws.accept()
        client = hub.add_status(peer_of(ws))
        await serve(ws, client, read=False)

    @app.websocket("/ws/source")
    async def ws_source(ws: WebSocket, token: str | None = None) -> None:
        if cfg.token_required and token != cfg.source_token:
            await ws.close(code=4401)
            print(f"[relay] rejected source {peer_of(ws)}: bad token")
            return
        await ws.accept()
        client = hub.add_source(peer_of(ws))
        print(f"[relay] + capture client {client.peer}")
        await serve(ws, client, read=True)
        print(f"[relay] - capture client {client.peer}")

    return app


app = create_app()
