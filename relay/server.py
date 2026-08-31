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
import io
import re
import secrets
import socket
import time
from pathlib import Path

import segno
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response

from . import config as config_module
from . import transcript as transcript_module
from . import translate as translate_module
from . import youtube_live as youtube_module
from .hub import CONTROL_ACTIONS, RELAY_ACTIONS, Client, Hub

HEARTBEAT_SECONDS = 5.0
STATUS_PUSH_SECONDS = 0.4

PAGES = {"wall": "wall.html", "phone": "phone.html",
         "status": "status.html", "tune": "tune.html", "qr": "qr.html"}

# Reachable without the admin token when the relay is public. Everything else -
# the status page, the tuner, the control API - is operator-only.
PUBLIC_PAGES = {"phone", "qr", "wall"}

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
    hub = Hub(source_language=cfg.source_language)

    targets = [lang for lang in cfg.offer_languages
               if lang and lang != cfg.source_language]
    translator = translate_module.create(cfg.translate_provider, targets)
    worker: translate_module.TranslationWorker | None = None

    def available_languages() -> list[str]:
        """Only languages that can actually be delivered are advertised, so the
        phone picker never offers a choice that does nothing."""
        if worker is None:
            return [cfg.source_language]
        return [cfg.source_language] + list(worker.targets)

    if translator is not None:
        def emit_translation(lang: str, text: str, utterance_id: str) -> None:
            hub.broadcast({"type": "final", "text": text, "lang": lang,
                           "utterance": utterance_id})

        worker = translate_module.TranslationWorker(
            translator, cfg.source_language, targets, emit_translation)
        hub.on_source_final = lambda msg: worker.submit(
            msg.get("text", ""), msg.get("id", ""))

    # One definition of the translation block, used by both the websocket feed
    # and /api/status. Adding it to only one is exactly the bug this avoids.
    recorder = transcript_module.TranscriptRecorder()
    live = youtube_module.LiveCaptions(
        youtube_module.urls_from_env(cfg.source_language, list(targets)))

    def on_broadcast(msg: dict) -> None:
        recorder.observe(msg)
        if msg.get("type") == "final" and live.active:
            live.submit(msg.get("lang") or cfg.source_language,
                        msg.get("text", ""),
                        float(msg.get("t0") or msg.get("t") or time.time()))

    hub.on_event = on_broadcast

    hub.snapshot_extra = lambda: {
        "translation": (worker.stats() if worker is not None
                        else {"provider": "none", "targets": []}),
        "languages": available_languages(),
        "transcript": recorder.stats(),
        "youtube_live": live.stats(),
    }

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
        if worker is not None:
            worker.start()
        if live.active:
            live.start()
        ip = local_ip()
        print(f"[relay] session {hub.session_id}")
        print(f"[relay] wall    http://{ip}:{cfg.port}/wall")
        print(f"[relay] phone   http://{ip}:{cfg.port}/phone")
        print(f"[relay] status  http://{ip}:{cfg.port}/status")
        print(f"[relay] tune    http://{ip}:{cfg.port}/tune")
        print(f"[relay] qr      http://{ip}:{cfg.port}/qr")
        if cfg.public_url:
            print(f"[relay] public  {cfg.public_url}")
        if cfg.is_public:
            print(f"[relay] PUBLIC MODE - operator pages need ?admin={cfg.admin_token}")
        if worker is not None:
            print(f"[relay] translating {cfg.source_language} -> "
                  f"{', '.join(worker.targets)} via {translator.name}")
        elif targets:
            print("[relay] translation NOT active - source language only")
        if live.active:
            print(f"[relay] youtube live captions -> "
                  f"{', '.join(sorted(live.senders))}")
        if cfg.token_required:
            print("[relay] source token required")
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if worker is not None:
                await worker.stop()
            await live.stop()

    app = FastAPI(title="Campus Captions relay", lifespan=lifespan)
    app.state.hub = hub
    app.state.config = cfg

    def admin_ok(request: Request) -> bool:
        """Operator routes are open on a LAN and token-gated when public."""
        if not cfg.is_public:
            return True
        supplied = (request.query_params.get("admin")
                    or request.headers.get("x-admin-token", ""))
        return bool(supplied) and secrets.compare_digest(supplied, cfg.admin_token)

    def require_admin(request: Request) -> None:
        if not admin_ok(request):
            # 404, not 403: a public visitor should not learn these exist.
            raise HTTPException(status_code=404, detail="not found")

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
    async def status_page(request: Request) -> FileResponse:
        require_admin(request)
        return page("status")

    @app.get("/tune")
    async def tune_page(request: Request) -> FileResponse:
        require_admin(request)
        return page("tune")

    @app.get("/qr")
    async def qr_page() -> FileResponse:
        return page("qr")

    @app.get("/robots.txt")
    async def robots() -> PlainTextResponse:
        # A live transcript of a room should never turn up in a search engine.
        return PlainTextResponse("User-agent: *\nDisallow: /\n",
                                 headers=NO_STORE)

    @app.get("/api/qr.svg")
    async def qr_svg(request: Request) -> Response:
        """QR for the phone view, as SVG so it stays sharp on an LED wall."""
        target = request.query_params.get("url") or (
            cfg.base_url(local_ip()) + "/phone")
        try:
            code = segno.make(target, error="m")
        except Exception:
            raise HTTPException(status_code=400, detail="cannot encode that url")
        buf = io.BytesIO()
        code.save(buf, kind="svg", scale=10, border=2,
                  dark=request.query_params.get("dark", "#000000"),
                  light=request.query_params.get("light", "#ffffff"))
        return Response(content=buf.getvalue(), media_type="image/svg+xml",
                        headers=NO_STORE)

    @app.get("/api/site")
    async def api_site() -> JSONResponse:
        """Public metadata: what address to advertise, what languages exist.

        Deliberately separate from /api/status, which is operator-only. A phone
        or a QR screen needs the base URL and the language list and nothing
        else - no audio levels, no viewer counts, no stream state.
        """
        ip = local_ip()
        base = cfg.base_url(ip)
        return JSONResponse({
            "base_url": base,
            "phone_url": base + "/phone",
            "source": cfg.source_language,
            "available": available_languages(),
            "offered": list(cfg.offer_languages),
        }, headers=NO_STORE)

    @app.get("/api/transcript.srt")
    async def transcript_srt(request: Request, lang: str = "") -> Response:
        """The event as a SubRip file, ready to upload to the video."""
        require_admin(request)
        lang = lang or cfg.source_language
        if not transcript_module.SAFE_LANG.match(lang):
            raise HTTPException(status_code=400, detail="bad language code")
        body = recorder.to_srt(lang)
        if not body:
            raise HTTPException(status_code=404,
                                detail=f"nothing recorded for {lang!r}")
        stamp = time.strftime("%Y%m%d-%H%M", time.localtime(recorder.started_at))
        filename = f"captions-{stamp}-{lang}.srt"
        return Response(
            content=body, media_type="application/x-subrip; charset=utf-8",
            headers={**NO_STORE,
                     "Content-Disposition": f'attachment; filename="{filename}"'})

    @app.get("/api/languages")
    async def api_languages() -> JSONResponse:
        """What the phone picker offers.

        `available` is what actually has a translator behind it. Until the
        translation stage lands that is the source language only, and the picker
        hides itself rather than offering choices that do nothing.
        """
        return JSONResponse({
            "source": cfg.source_language,
            "available": available_languages(),
            "offered": list(cfg.offer_languages),
        }, headers=NO_STORE)

    # Any other viewer/*.html, addressed by name. Registered last, so every
    # explicit route above still wins.
    #
    # This exists because HTML is read from disk per request while routes are
    # registered once at startup: adding a page used to mean editing this file
    # AND restarting, and the page would appear in a link while 404ing. Now a
    # new file in viewer/ is reachable immediately.
    @app.get("/{page}")
    async def named_page(page: str, request: Request) -> FileResponse:
        if not SAFE_PAGE_NAME.match(page):
            raise HTTPException(status_code=404, detail="not found")
        name = page if page.endswith(".html") else f"{page}.html"
        # Without this, /status.html walks straight past the gate that /status
        # enforces. Default-deny: anything not explicitly public is operator-
        # only, so a page added later is protected before anyone remembers to.
        if name[:-5] not in PUBLIC_PAGES:
            require_admin(request)
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
    async def api_status(request: Request) -> JSONResponse:
        require_admin(request)
        snap = hub.snapshot()
        ip = local_ip()
        snap["relay"] = {
            "host": cfg.host,
            "port": cfg.port,
            "ip": ip,
            "token_required": cfg.token_required,
            "config": str(cfg.config_path) if cfg.config_path else None,
            "mode": cfg.mode,
            "public_url": cfg.public_url,
            "base_url": cfg.base_url(ip),
        }
        return JSONResponse(snap, headers=NO_STORE)

    @app.post("/api/control")
    async def api_control(request: Request) -> JSONResponse:
        require_admin(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        action = (body or {}).get("action")
        if action in RELAY_ACTIONS:
            if action == "mark_video_start":
                when = recorder.mark_start()
                print("[relay] video start marked")
                return JSONResponse({"ok": True, "action": action,
                                     "zero_time": when}, headers=NO_STORE)
            recorder.clear()
            print("[relay] transcript reset")
            return JSONResponse({"ok": True, "action": action},
                                headers=NO_STORE)
        if action not in CONTROL_ACTIONS:
            raise HTTPException(
                status_code=400,
                detail=f"action must be one of "
                       f"{sorted(CONTROL_ACTIONS | RELAY_ACTIONS)}",
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
    async def ws_viewer(ws: WebSocket, role: str = "wall",
                        lang: str = "") -> None:
        await ws.accept()
        client = hub.add_viewer(role, peer_of(ws), lang=lang)
        print(f"[relay] + {client} ({len(hub.viewers)} viewers)")
        await serve(ws, client, read=False)
        print(f"[relay] - {client} ({len(hub.viewers)} viewers)")

    @app.websocket("/ws/status")
    async def ws_status(ws: WebSocket, admin: str = "") -> None:
        # The status feed carries audio levels, viewer counts and the last line
        # spoken. Gating the page but not its data would be theatre.
        if cfg.is_public and not (admin and secrets.compare_digest(
                admin, cfg.admin_token)):
            await ws.close(code=4401)
            return
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
