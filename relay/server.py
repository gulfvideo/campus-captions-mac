"""FastAPI relay: transcript in from one capture client, out to every viewer.

Endpoints (see docs/MESSAGES.md):

    GET  /wall /phone /status      the three pages
    GET  /tune                     build a wall-view URL with sliders
    GET  /qr                       QR code for the phone URL
    GET  /<name>                   any other viewer/<name>.html
    WS   /ws/viewer?role=wall&lang=es   caption feed, optionally translated
    WS   /ws/source?token=...      capture client, bidirectional
    WS   /ws/status                status page feed
    GET  /api/status               one status snapshot as JSON (operator-only)
    GET  /api/site                 public metadata: base URL, languages
    GET  /api/qr.svg               the QR code as SVG
    GET  /api/transcript.srt       recorded captions as SubRip (operator-only)
    POST /api/control              {"action": "open_stream"} etc.
    POST /api/youtube-captions     {"enabled": true} - relay-local, not forwarded

Everything operator-only (marked above, plus /status, /tune, and any other
viewer page not explicitly public) is walled off from `public_hostname` - see
is_public()/require_local() below - regardless of how the relay was reached.
That gate is orthogonal to `source_token`, which is about who may claim to be
the capture client, not who may view or control things.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import re
import socket
import time
from pathlib import Path

import segno
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response

from . import config as config_module
from . import transcript as transcript_module
from . import translate as translate_module
from .hub import CONTROL_ACTIONS, RELAY_ACTIONS, Client, Hub
from .youtube import YouTubeCaptionPusher, urls_from_env

HEARTBEAT_SECONDS = 5.0
STATUS_PUSH_SECONDS = 0.4

PAGES = {"wall": "wall.html", "phone": "phone.html",
         "status": "status.html", "tune": "tune.html", "qr": "qr.html"}

# Reachable through the public hostname. Everything else - the status page,
# the tuner, any other named page - is operator-only, see require_local().
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
    hub.youtube = YouTubeCaptionPusher(cfg.youtube_url, cfg.youtube_lang, cfg.youtube_start_enabled)

    def is_public(headers) -> bool:
        """True when this request arrived addressed to `public_hostname`
        (e.g. a Cloudflare Tunnel domain) rather than localhost or the booth
        LAN - i.e. it could be anyone on the internet, not just the booth.
        `public_hostname` empty (the default) means there is no tunnel and
        nothing is ever treated as public."""
        if not cfg.public_hostname:
            return False
        return headers.get("host", "").split(":")[0].lower() == cfg.public_hostname

    def require_local(headers) -> None:
        """Guard for the operator control surface: never reachable through
        the public hostname, only from the booth LAN. A 404, not 403 -
        the public hostname should not confirm these routes exist at all."""
        if is_public(headers):
            raise HTTPException(status_code=404, detail="not found")

    # -- translation -------------------------------------------------------

    targets = [lang for lang in cfg.offer_languages
               if lang and lang != cfg.source_language]
    translator = translate_module.create(cfg.translate_provider, targets)
    worker: translate_module.TranslationWorker | None = None

    def available_languages() -> list[str]:
        """Only languages that can actually be delivered are advertised, so
        the phone picker never offers a choice that does nothing."""
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

    # -- transcript + YouTube (source language uses hub.youtube above; each
    # translated language gets its own broadcast caption track, always-on
    # once its URL is set, since there's no per-language GUI for those) ------

    recorder = transcript_module.TranscriptRecorder()
    additional_youtube = {
        lang: YouTubeCaptionPusher(url, lang, start_enabled=True)
        for lang, url in urls_from_env(targets).items()
    }

    def on_broadcast(msg: dict) -> None:
        recorder.observe(msg)
        if msg.get("type") != "final":
            return
        lang = msg.get("lang") or cfg.source_language
        if lang == cfg.source_language:
            return  # already pushed to YouTube by ingest(), via hub.youtube
        pusher = additional_youtube.get(lang)
        if pusher is not None:
            asyncio.create_task(pusher.push(msg.get("text", "")))

    hub.on_event = on_broadcast

    hub.snapshot_extra = lambda: {
        "translation": (worker.stats() if worker is not None
                        else {"provider": "none", "targets": []}),
        "languages": available_languages(),
        "transcript": recorder.stats(),
        "youtube_live_extra": [p.status() for p in additional_youtube.values()],
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
        ip = local_ip()
        print(f"[relay] session {hub.session_id}")
        print(f"[relay] wall    http://{ip}:{cfg.port}/wall")
        print(f"[relay] phone   http://{ip}:{cfg.port}/phone")
        print(f"[relay] status  http://{ip}:{cfg.port}/status")
        print(f"[relay] tune    http://{ip}:{cfg.port}/tune")
        print(f"[relay] qr      http://{ip}:{cfg.port}/qr")
        if cfg.public_hostname:
            print(f"[relay] public  https://{cfg.public_hostname}"
                  " (status/tune/control stay operator-only there)")
        if cfg.token_required:
            print("[relay] source token required")
        if hub.youtube.configured:
            print(f"[relay] YouTube captions configured"
                  f" ({'enabled' if hub.youtube.enabled else 'disabled'} at startup)")
        if worker is not None:
            print(f"[relay] translating {cfg.source_language} -> "
                  f"{', '.join(worker.targets)} via {translator.name}")
        elif targets:
            print("[relay] translation NOT active - source language only")
        if additional_youtube:
            print(f"[relay] youtube live captions, extra languages -> "
                  f"{', '.join(sorted(additional_youtube))}")
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if worker is not None:
                await worker.stop()

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
    async def root(request: Request) -> FileResponse:
        # A public visitor landing on the bare domain wants captions, not the
        # operator dashboard - and the dashboard is blocked for them below anyway.
        return page("phone") if is_public(request.headers) else page("status")

    @app.get("/wall")
    async def wall() -> FileResponse:
        return page("wall")

    @app.get("/phone")
    async def phone() -> FileResponse:
        return page("phone")

    @app.get("/status")
    async def status_page(request: Request) -> FileResponse:
        require_local(request.headers)
        return page("status")

    @app.get("/tune")
    async def tune_page(request: Request) -> FileResponse:
        require_local(request.headers)
        return page("tune")

    @app.get("/qr")
    async def qr_page() -> FileResponse:
        return page("qr")

    @app.get("/robots.txt")
    async def robots() -> PlainTextResponse:
        # A live transcript of a room should never turn up in a search engine.
        return PlainTextResponse("User-agent: *\nDisallow: /\n", headers=NO_STORE)

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

        Deliberately separate from /api/status, which is operator-only. A
        phone or a QR screen needs the base URL and the language list and
        nothing else - no audio levels, no viewer counts, no stream state.
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
        require_local(request.headers)
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
        # Without this, /status.html walks straight past the check /status
        # enforces. Default-deny: anything not explicitly public is
        # operator-only, so a page added later is protected before anyone
        # remembers to gate it by hand.
        if name[:-5] not in PUBLIC_PAGES:
            require_local(request.headers)
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
        require_local(request.headers)
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
        require_local(request.headers)
        try:
            body = await request.json()
        except Exception:
            body = {}
        action = (body or {}).get("action")

        if action in RELAY_ACTIONS:
            if action == "mark_video_start":
                when = recorder.mark_start()
                print("[relay] video start marked")
                return JSONResponse({"ok": True, "action": action, "zero_time": when},
                                    headers=NO_STORE)
            recorder.clear()
            print("[relay] transcript reset")
            return JSONResponse({"ok": True, "action": action}, headers=NO_STORE)

        if action not in CONTROL_ACTIONS:
            raise HTTPException(
                status_code=400,
                detail=f"action must be one of {sorted(CONTROL_ACTIONS | RELAY_ACTIONS)}",
            )
        fields = {}
        if action == "select_audio_device":
            device = body.get("device")
            if isinstance(device, bool) or not isinstance(device, int):
                raise HTTPException(status_code=400, detail="device must be an integer")
            if device < 0 or device > 4096:
                raise HTTPException(status_code=400, detail="device is out of range")
            fields["device"] = device
        elif action == "select_backend":
            backend = body.get("backend")
            if not isinstance(backend, str) or not (1 <= len(backend) <= 64):
                raise HTTPException(status_code=400, detail="backend must be a short string")
            fields["backend"] = backend
        elif action == "set_deepgram_key":
            key = body.get("key")
            if not isinstance(key, str) or not (1 <= len(key.strip()) <= 200):
                raise HTTPException(status_code=400, detail="key must be a short string")
            fields["key"] = key.strip()
        delivered = hub.send_control(action, fields)
        print(f"[relay] control {action} -> {delivered} capture client(s)")
        return JSONResponse(
            {"ok": True, "action": action, "delivered_to": delivered},
            headers=NO_STORE,
        )

    @app.post("/api/youtube-captions")
    async def api_youtube_captions(request: Request) -> JSONResponse:
        """Relay-local - unlike /api/control, this never reaches the capture
        client. YouTube pushing happens entirely in this process.

        {"url": "..."} sets (or clears, with "") the ingestion URL and saves
        it into config/event.toml, so pasting it here is the only step - no
        file to edit, and it survives a relay restart. {"enabled": true} is
        the separate operator toggle for whether pushing is actually live.
        Either or both may be sent in one request. This only ever controls the
        source-language track - see relay/youtube.py for additional languages.
        """
        require_local(request.headers)
        try:
            body = await request.json()
        except Exception:
            body = {}
        body = body or {}
        if "url" not in body and "enabled" not in body:
            raise HTTPException(status_code=400, detail="send url and/or enabled")

        if "url" in body:
            url = body.get("url")
            if not isinstance(url, str) or len(url) > 2000:
                raise HTTPException(status_code=400, detail="url must be a string")
            url = url.strip()
            if url and not (url.startswith("http://") or url.startswith("https://")):
                raise HTTPException(status_code=400, detail="url must start with http:// or https://")
            lang = body.get("lang")
            lang = lang.strip() if isinstance(lang, str) and lang.strip() else hub.youtube.lang
            hub.youtube.configure(url, lang)
            try:
                config_module.save_youtube_url(cfg.config_path or config_module.DEFAULT_CONFIG, url, lang)
            except OSError as exc:
                print(f"[relay] could not save YouTube URL to disk: {exc}")
            print(f"[relay] YouTube ingestion URL {'set' if url else 'cleared'}")

        if "enabled" in body:
            enabled = body.get("enabled")
            if not isinstance(enabled, bool):
                raise HTTPException(status_code=400, detail="enabled must be a boolean")
            if enabled and not hub.youtube.configured:
                raise HTTPException(status_code=400, detail="paste a YouTube ingestion URL first")
            hub.youtube.enabled = enabled
            print(f"[relay] YouTube captions {'enabled' if enabled else 'disabled'}")

        return JSONResponse({"ok": True, **hub.youtube.status()}, headers=NO_STORE)

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
    async def ws_viewer(ws: WebSocket, role: str = "wall", lang: str = "") -> None:
        await ws.accept()
        client = hub.add_viewer(role, peer_of(ws), lang=lang)
        print(f"[relay] + {client} ({len(hub.viewers)} viewers)")
        await serve(ws, client, read=False)
        print(f"[relay] - {client} ({len(hub.viewers)} viewers)")

    @app.websocket("/ws/status")
    async def ws_status(ws: WebSocket) -> None:
        if is_public(ws.headers):
            await ws.close(code=4404)
            return
        await ws.accept()
        client = hub.add_status(peer_of(ws))
        await serve(ws, client, read=False)

    @app.websocket("/ws/source")
    async def ws_source(ws: WebSocket, token: str | None = None) -> None:
        if is_public(ws.headers):
            await ws.close(code=4404)
            return
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
