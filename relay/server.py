"""FastAPI relay: transcript in from one capture client, out to every viewer.

Endpoints (see docs/MESSAGES.md):

    GET  /wall /phone /status      the three pages
    GET  /tune                     build a wall-view URL with sliders
    GET  /qr                       QR code for the phone URL
    GET  /<name>                   any other viewer/<name>.html
    WS   /ws/viewer?role=wall&lang=es   caption feed, optionally translated
    WS   /ws/source                capture client, bidirectional (bearer token)
    WS   /ws/status                status page feed (operator-only)
    GET  /api/status               one status snapshot as JSON (operator-only)
    GET  /api/site                 public metadata: base URL, languages
    GET  /api/qr.svg               the QR code as SVG
    GET  /api/transcript.srt       recorded captions as SubRip (operator-only)
    POST /api/control              {"action": "open_stream"} etc.
    POST /api/youtube-captions     {"enabled": true} - relay-local, not forwarded
    POST /api/tunnel               {"token": "...", "enabled": true} - relay-local

Everything operator-only (marked above, plus /status, /tune, and any other
viewer page not explicitly public) needs the operator token - see
require_operator() below. The status page gets it once from the link the
launcher opens (`/status?token=...`) and keeps it in a cookie; scripts send
`Authorization: Bearer ...`. On top of that, none of it is ever reachable
through `public_hostname` - see is_public()/require_local() - so the tunnel
stays read-only even for someone holding the token. Both gates are
orthogonal to `source_token`, which is about who may claim to be the capture
client, not who may view or control things.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import io
import os
import re
import socket
import time
import urllib.parse
from pathlib import Path

import segno
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, PlainTextResponse,
                               RedirectResponse, Response)

from . import config as config_module
from . import event as event_module
from . import transcript as transcript_module
from . import translate as translate_module
from . import tunnel as tunnel_module
from .hub import CONTROL_ACTIONS, RELAY_ACTIONS, Client, Hub
from .youtube import ALLOWED_HOSTS as YOUTUBE_HOSTS
from .youtube import YouTubeCaptionPusher, urls_from_env, valid_ingestion_url

HEARTBEAT_SECONDS = 5.0
STATUS_PUSH_SECONDS = 0.4

PAGES = {"wall": "wall.html", "phone": "phone.html",
         "status": "status.html", "tune": "tune.html", "qr": "qr.html"}

# Reachable through the public hostname. Everything else - the status page,
# the tuner, any other named page - is operator-only, see require_local().
PUBLIC_PAGES = {"phone", "qr", "wall"}

# Only a bare file name may be requested as a page.
SAFE_PAGE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# A DNS hostname and nothing else: what `public_hostname` is compared against
# on every request, so it must never be able to carry anything surprising into
# event.toml or the Host check.
SAFE_HOSTNAME = re.compile(
    r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")

# Operator authentication. The status page is opened once as
# /status?token=... (the launcher and the menu bar app build that link); the
# relay swaps the token for a cookie and redirects to the clean URL, so the
# secret stays out of the address bar and the browser history. SameSite=Strict
# keeps the cookie off any request another site starts, which is what makes
# the JSON POSTs below safe from cross-site request forgery.
OPERATOR_COOKIE = "captions_operator"
OPERATOR_COOKIE_MAX_AGE = 30 * 24 * 3600

OPERATOR_LOGIN_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Captions - operator link needed</title>
<style>
  body { margin: 0; background: #14161a; color: #eceff4; font: 16px/1.5 "Segoe UI", system-ui, sans-serif; }
  main { max-width: 34rem; margin: 12vh auto; padding: 0 1.5rem; }
  h1 { font-size: 1.4rem; margin: 0 0 .75rem; }
  p { margin: 0 0 .75rem; color: #c7cdd8; }
  code { background: #1e2127; padding: .1rem .35rem; border-radius: 4px; }
</style></head><body><main>
<h1>This page is for the operator</h1>
<p>Open it from the link the launcher window printed, or from the menu bar
app&rsquo;s <b>Open Status Page</b>. That link ends in <code>?token=&hellip;</code>
and signs this browser in for a month.</p>
<p>The token is <code>CAPTIONS_OPERATOR_TOKEN</code> in the <code>.env</code>
file next to the app. It was generated the first time the relay started.</p>
</main></body></html>
"""

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
    event = event_module.EventState.from_config(cfg, config_module.DEFAULT_CONFIG.parent)
    hub.event = event
    tunnel = tunnel_module.CloudflareTunnel(token=os.environ.get("CLOUDFLARE_TUNNEL_TOKEN", "").strip())

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
        """Half of the operator guard: never reachable through the public
        hostname, whatever credentials are presented. A 404, not 403 - the
        public hostname should not confirm these routes exist at all."""
        if is_public(headers):
            raise HTTPException(status_code=404, detail="not found")

    def token_matches(candidate: str | None, expected: str) -> bool:
        if not candidate or not expected:
            return False
        return hmac.compare_digest(candidate.encode(), expected.encode())

    def bearer(headers) -> str | None:
        auth = headers.get("authorization", "")
        if auth[:7].lower() == "bearer ":
            return auth[7:].strip()
        return None

    def operator_ok(headers, cookies, *, strict_site: bool = False) -> bool:
        """A bearer header (scripts) or the session cookie (the status page).

        `strict_site` is for state-changing requests: with the cookie, also
        insist the browser itself says this is a same-origin request. The
        SameSite=Strict cookie already never travels cross-site; this closes
        the gap for a browser old enough not to honour that but new enough
        to send Sec-Fetch-Site.
        """
        if token_matches(bearer(headers), cfg.operator_token):
            return True
        if token_matches(cookies.get(OPERATOR_COOKIE), cfg.operator_token):
            if not strict_site:
                return True
            return headers.get("sec-fetch-site", "") in ("", "same-origin", "none")
        return False

    def require_operator(request: Request) -> None:
        """The other half of the operator guard, for the API: the LAN is
        where the audience is, so being on it proves nothing. 401 with a
        detail, unlike the public-hostname 404 - someone here is expected to
        be the operator and should learn what is missing."""
        require_local(request.headers)
        if not operator_ok(request.headers, request.cookies,
                           strict_site=request.method in ("POST", "PUT", "DELETE")):
            raise HTTPException(status_code=401, detail="operator token required")

    def same_origin(headers) -> bool:
        """Browsers always send Origin on a WebSocket handshake and never
        apply same-origin policy to it, so a page on any site could open a
        socket here unless this checks. Native clients send no Origin."""
        origin = headers.get("origin")
        if origin is None:
            return True
        try:
            origin_host = urllib.parse.urlsplit(origin).netloc
        except ValueError:
            return False
        return bool(origin_host) and origin_host.lower() == headers.get("host", "").lower()

    def text_field(body: dict, name: str, max_len: int, *, allow_empty: bool = False) -> str:
        """A string from a JSON body, stripped, length-bounded, and free of
        control characters - everything here ends up in .env or event.toml,
        where a newline is a way to write a different setting."""
        value = body.get(name)
        if not isinstance(value, str):
            raise HTTPException(status_code=400, detail=f"{name} must be a string")
        value = value.strip()
        if config_module.has_control_chars(value):
            raise HTTPException(status_code=400,
                                detail=f"{name} must not contain control characters")
        if len(value) > max_len:
            raise HTTPException(status_code=400,
                                detail=f"{name} must be at most {max_len} characters")
        if not value and not allow_empty:
            raise HTTPException(status_code=400, detail=f"{name} must not be empty")
        return value

    # -- translation ---------------------------------------------------------
    # A plain (translator, worker) pair can't be swapped later without
    # `nonlocal` scattered across every closure that reads them, so they live
    # on this object instead: /api/translation below calls .apply() with a
    # fresh key or language list, live, mid-event, no restart.

    class TranslationState:
        def __init__(self, source_language: str, targets: list[str]):
            self.source_language = source_language
            self.targets = list(targets)
            self.translator: translate_module.Translator | None = None
            self.worker: translate_module.TranslationWorker | None = None

        def _emit(self, lang: str, text: str, utterance_id: str) -> None:
            hub.broadcast({"type": "final", "text": text, "lang": lang,
                           "utterance": utterance_id})

        async def apply(self, provider: str, targets: list[str]) -> None:
            if self.worker is not None:
                await self.worker.stop()
                self.worker = None
            self.targets = list(targets)
            self.translator = translate_module.create(provider, self.targets)
            if self.translator is not None:
                self.worker = translate_module.TranslationWorker(
                    self.translator, self.source_language, self.targets, self._emit)
                self.worker.start()
            hub.on_source_final = (
                (lambda msg: self.worker.submit(msg.get("text", ""), msg.get("id", "")))
                if self.worker is not None else None
            )

        def languages(self) -> list[str]:
            """Only languages that can actually be delivered are advertised,
            so the phone picker never offers a choice that does nothing."""
            if self.worker is None:
                return [self.source_language]
            return [self.source_language] + list(self.worker.targets)

        def stats(self) -> dict:
            if self.worker is not None:
                return self.worker.stats()
            return {"provider": "none", "targets": list(self.targets)}

    translation = TranslationState(
        cfg.source_language,
        [lang for lang in cfg.offer_languages if lang and lang != cfg.source_language],
    )

    def config_target() -> Path:
        """Where the status page's settings get written. Never the tracked
        example file: with no event.toml yet, config.load() reads the example
        as a fallback, and writing a YouTube ingestion URL or tunnel hostname
        into it would put a credential in the next git commit."""
        if cfg.config_path and cfg.config_path != config_module.EXAMPLE_CONFIG:
            return cfg.config_path
        return config_module.DEFAULT_CONFIG

    # -- transcript + YouTube (source language uses hub.youtube above; each
    # translated language gets its own broadcast caption track, always-on
    # once its URL is set, since there's no per-language GUI for those) ------

    recorder = transcript_module.TranscriptRecorder()
    additional_youtube = {
        lang: YouTubeCaptionPusher(url, lang, start_enabled=True)
        for lang, url in urls_from_env(translation.targets).items()
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
        "translation": translation.stats(),
        "languages": translation.languages(),
        "transcript": recorder.stats(),
        "youtube_live_extra": [p.status() for p in additional_youtube.values()],
        "tunnel": tunnel.status(),
        "public_hostname": cfg.public_hostname,
        "event": event.public(),
        "health": hub.health(),
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
        await translation.apply(cfg.translate_provider, translation.targets)
        if cfg.tunnel_start_enabled and tunnel.configured:
            # Without a hostname nothing would be treated as public, so a
            # tunnel would expose the operator surface to the internet.
            if cfg.public_hostname:
                await tunnel.start()
            else:
                print("[relay] tunnel start_enabled but no public_hostname is set - "
                      "not starting it; set the hostname on the status page first")
        ip = local_ip()
        login = f"?token={cfg.operator_token}"
        print(f"[relay] session {hub.session_id}")
        print(f"[relay] wall    http://{ip}:{cfg.port}/wall")
        print(f"[relay] phone   http://{ip}:{cfg.port}/phone")
        print(f"[relay] status  http://{ip}:{cfg.port}/status{login}")
        print(f"[relay] tune    http://{ip}:{cfg.port}/tune{login}")
        print(f"[relay] qr      http://{ip}:{cfg.port}/qr")
        print("[relay] the status and tune links sign a browser in; "
              "the token is CAPTIONS_OPERATOR_TOKEN in .env")
        print(f"[relay] event   {event.state}"
              + (" - phones show the welcome screen until Start event is pressed"
                 if event.state == "starting" else ""))
        if cfg.public_hostname:
            print(f"[relay] public  https://{cfg.public_hostname}"
                  " (status/tune/control stay operator-only there)")
        if cfg.token_required:
            print("[relay] source token required")
        else:
            print("[relay] WARNING: no source token - every capture client will be "
                  "refused (set CAPTIONS_SOURCE_TOKEN)")
        if hub.youtube.configured:
            print(f"[relay] YouTube captions configured"
                  f" ({'enabled' if hub.youtube.enabled else 'disabled'} at startup)")
        if translation.worker is not None:
            print(f"[relay] translating {cfg.source_language} -> "
                  f"{', '.join(translation.worker.targets)} via {translation.translator.name}")
        elif translation.targets:
            print("[relay] translation NOT active - source language only "
                  "(add an Azure Translator key on the status page)")
        if additional_youtube:
            print(f"[relay] youtube live captions, extra languages -> "
                  f"{', '.join(sorted(additional_youtube))}")
        if tunnel.configured:
            print(f"[relay] Cloudflare tunnel configured"
                  f" ({'enabled' if tunnel.enabled else 'disabled'} at startup)")
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if translation.worker is not None:
                await translation.worker.stop()
            if tunnel.running:
                await tunnel.stop()

    # No /docs, /redoc or /openapi.json: they would describe the whole
    # operator API to anyone, including through the public hostname.
    app = FastAPI(title="Campus Captions relay", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.hub = hub
    app.state.config = cfg

    # -- pages -----------------------------------------------------------

    def file_page(path: Path) -> FileResponse:
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"{path.name} not found")
        return FileResponse(path, media_type="text/html; charset=utf-8",
                            headers=NO_STORE)

    def page(name: str) -> FileResponse:
        return file_page(config_module.VIEWER_DIR / PAGES[name])

    def operator_page(request: Request, path: Path) -> Response:
        """An operator-only page. `?token=...` on the URL signs the browser
        in (cookie) and redirects to the same URL without it; from then on
        the cookie alone is enough. No token and no cookie gets a short
        page saying where the link is, not the dashboard."""
        require_local(request.headers)
        offered = request.query_params.get("token")
        if offered is not None:
            if not token_matches(offered, cfg.operator_token):
                return HTMLResponse(OPERATOR_LOGIN_HTML, status_code=401, headers=NO_STORE)
            rest = [(k, v) for k, v in request.query_params.multi_items() if k != "token"]
            target = request.url.path + ("?" + urllib.parse.urlencode(rest) if rest else "")
            response = RedirectResponse(url=target, status_code=303, headers=NO_STORE)
            response.set_cookie(OPERATOR_COOKIE, cfg.operator_token,
                                max_age=OPERATOR_COOKIE_MAX_AGE, path="/",
                                httponly=True, samesite="strict")
            return response
        if not operator_ok(request.headers, request.cookies):
            return HTMLResponse(OPERATOR_LOGIN_HTML, status_code=401, headers=NO_STORE)
        return file_page(path)

    @app.get("/")
    async def root(request: Request) -> Response:
        # A public visitor landing on the bare domain wants captions, not the
        # operator dashboard - and the dashboard is blocked for them below anyway.
        if is_public(request.headers):
            return page("phone")
        return RedirectResponse(url="/status", status_code=303, headers=NO_STORE)

    @app.get("/wall")
    async def wall() -> FileResponse:
        return page("wall")

    @app.get("/phone")
    async def phone() -> FileResponse:
        return page("phone")

    @app.get("/status")
    async def status_page(request: Request) -> Response:
        return operator_page(request, config_module.VIEWER_DIR / PAGES["status"])

    @app.get("/tune")
    async def tune_page(request: Request) -> Response:
        return operator_page(request, config_module.VIEWER_DIR / PAGES["tune"])

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
            "available": translation.languages(),
            "offered": list(cfg.offer_languages),
            "event": event.public(),
        }, headers=NO_STORE)

    @app.get("/api/event/image")
    async def api_event_image() -> Response:
        """The welcome screen's background, public like the phone page it
        decorates. Cached by URL: viewers append ?v=<image_v> so a replaced
        image gets a new address."""
        path = event.image_path
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="no welcome image")
        return FileResponse(path, media_type=event.image_media_type,
                            headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/api/transcript.srt")
    async def transcript_srt(request: Request, lang: str = "") -> Response:
        """The event as a SubRip file, ready to upload to the video."""
        require_operator(request)
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
    async def named_page(page: str, request: Request) -> Response:
        if not SAFE_PAGE_NAME.match(page):
            raise HTTPException(status_code=404, detail="not found")
        name = page if page.endswith(".html") else f"{page}.html"
        path = config_module.VIEWER_DIR / name
        try:
            path.resolve().relative_to(config_module.VIEWER_DIR.resolve())
        except ValueError:
            raise HTTPException(status_code=404, detail="not found")
        # Without this, /status.html walks straight past the check /status
        # enforces. Default-deny: anything not explicitly public is
        # operator-only, so a page added later is protected before anyone
        # remembers to gate it by hand.
        if name[:-5] not in PUBLIC_PAGES:
            return operator_page(request, path)
        return file_page(path)

    # -- operator API ----------------------------------------------------

    @app.get("/api/status")
    async def api_status(request: Request) -> JSONResponse:
        require_operator(request)
        snap = hub.snapshot()
        snap["relay"] = {
            "host": cfg.host,
            "port": cfg.port,
            "ip": local_ip(),
            "token_required": cfg.token_required,
            "config": str(cfg.config_path) if cfg.config_path else None,
            "public_hostname": cfg.public_hostname,
        }
        return JSONResponse(snap, headers=NO_STORE)

    @app.post("/api/control")
    async def api_control(request: Request) -> JSONResponse:
        require_operator(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        action = (body or {}).get("action")

        if action in RELAY_ACTIONS:
            if action == "caption":
                text = text_field(body, "text", 200)
                hub.announce(text)
                print(f"[relay] operator caption: {text}")
                return JSONResponse({"ok": True, "action": action}, headers=NO_STORE)
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
            fields["key"] = text_field(body, "key", 200)
        delivered = hub.send_control(action, fields)
        print(f"[relay] control {action} -> {delivered} capture client(s)")
        return JSONResponse(
            {"ok": True, "action": action, "delivered_to": delivered},
            headers=NO_STORE,
        )

    @app.post("/api/event")
    async def api_event(request: Request) -> JSONResponse:
        """Relay-local. {"state": "live"} and/or the welcome-screen text:
        title, message, notice, pause_message - any subset. Every change is
        pushed to viewers at once and saved to event.toml."""
        require_operator(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        body = body or {}
        fields: dict[str, str] = {}
        if "state" in body:
            state = text_field(body, "state", 16).lower()
            if state not in event_module.STATES:
                raise HTTPException(
                    status_code=400,
                    detail=f"state must be one of {', '.join(event_module.STATES)}")
            fields["state"] = state
        for name, limit in (("title", 80), ("message", 300),
                            ("notice", 300), ("pause_message", 120)):
            if name in body:
                fields[name] = text_field(body, name, limit, allow_empty=True)
        if not fields:
            raise HTTPException(status_code=400,
                                detail="send state, title, message, notice, and/or pause_message")
        event.update(**fields)
        try:
            config_module.save_event_settings(config_target(), fields)
        except OSError as exc:
            print(f"[relay] could not save event settings to disk: {exc}")
        hub.broadcast_event()
        if "state" in fields:
            print(f"[relay] event -> {fields['state']}")
        return JSONResponse({"ok": True, **event.public()}, headers=NO_STORE)

    @app.put("/api/event/image")
    async def api_event_image_put(request: Request) -> JSONResponse:
        """The raw image bytes as the body (no multipart, no extra
        dependency). Type is sniffed from the bytes, never trusted from the
        header; size is capped before the body is read."""
        require_operator(request)
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > event_module.MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="image must be under 5 MB")
        data = await request.body()
        if len(data) > event_module.MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="image must be under 5 MB")
        try:
            event.set_image(data)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        hub.broadcast_event()
        print("[relay] welcome image updated")
        return JSONResponse({"ok": True, **event.public()}, headers=NO_STORE)

    @app.delete("/api/event/image")
    async def api_event_image_delete(request: Request) -> JSONResponse:
        require_operator(request)
        event.clear_image()
        hub.broadcast_event()
        print("[relay] welcome image removed")
        return JSONResponse({"ok": True, **event.public()}, headers=NO_STORE)

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
        require_operator(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        body = body or {}
        if "url" not in body and "enabled" not in body:
            raise HTTPException(status_code=400, detail="send url and/or enabled")

        if "url" in body:
            url = text_field(body, "url", 2000, allow_empty=True)
            if url and not valid_ingestion_url(url):
                raise HTTPException(
                    status_code=400,
                    detail="url must be a YouTube caption ingestion URL "
                           f"(host {' or '.join(sorted(YOUTUBE_HOSTS))})")
            lang = hub.youtube.lang
            if isinstance(body.get("lang"), str) and body["lang"].strip():
                lang = text_field(body, "lang", 16)
                if not transcript_module.SAFE_LANG.match(lang):
                    raise HTTPException(status_code=400, detail="lang must look like en-US")
            hub.youtube.configure(url, lang)
            try:
                config_module.save_youtube_url(config_target(), url, lang)
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

    @app.post("/api/tunnel")
    async def api_tunnel(request: Request) -> JSONResponse:
        """Relay-local, like /api/youtube-captions and /api/translation:
        manages a `cloudflared tunnel run` subprocess instead of forwarding
        to the capture client.

        {"token": "...", "hostname": "...", "enabled": true} - any subset.
        The token goes into .env (save_env_var, same mechanism as the
        Deepgram key); the hostname becomes `public_hostname` immediately
        (see is_public()/require_local() above) and is saved into
        event.toml alongside the operator's on/off intent
        (save_tunnel_settings). Create the tunnel and its Public Hostname
        once at one.dash.cloudflare.com first - see SETUP.md 3c for the
        walkthrough and where the token comes from.
        """
        require_operator(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        body = body or {}
        if not any(k in body for k in ("token", "hostname", "enabled")):
            raise HTTPException(status_code=400, detail="send token, hostname, and/or enabled")

        if "token" in body:
            token = text_field(body, "token", 4000)
            config_module.save_env_var("CLOUDFLARE_TUNNEL_TOKEN", token)
            os.environ["CLOUDFLARE_TUNNEL_TOKEN"] = token
            tunnel.configure(token=token)

        if "hostname" in body:
            hostname = text_field(body, "hostname", 253, allow_empty=True).lower()
            if hostname and not SAFE_HOSTNAME.match(hostname):
                raise HTTPException(status_code=400, detail="hostname must be a plain DNS name")
            # An empty hostname means "nothing is public" - with the tunnel
            # still up, that would open the operator surface to the internet.
            if not hostname and tunnel.enabled and body.get("enabled") is not False:
                raise HTTPException(status_code=400,
                                    detail="disable the tunnel before clearing its hostname")
            cfg.public_hostname = hostname

        if "enabled" in body:
            enabled = body.get("enabled")
            if not isinstance(enabled, bool):
                raise HTTPException(status_code=400, detail="enabled must be a boolean")
            if enabled:
                if not tunnel.configured:
                    raise HTTPException(status_code=400, detail="paste a tunnel token first")
                if not cfg.public_hostname:
                    raise HTTPException(status_code=400, detail="set the tunnel hostname first")
                await tunnel.start()
            else:
                await tunnel.stop()

        if "hostname" in body or "enabled" in body:
            try:
                config_module.save_tunnel_settings(
                    config_target(), cfg.public_hostname, tunnel.enabled)
            except OSError as exc:
                print(f"[relay] could not save tunnel settings to disk: {exc}")

        return JSONResponse(
            {"ok": True, **tunnel.status(), "public_hostname": cfg.public_hostname},
            headers=NO_STORE)

    @app.post("/api/translation")
    async def api_translation(request: Request) -> JSONResponse:
        """Relay-local, like /api/youtube-captions: the key and target
        languages never reach the capture client, they only matter here
        where finals get translated before fan-out.

        {"key": "...", "region": "...", "languages": "es, fr"} - any subset.
        The key/region go into .env (gitignored, never event.toml) via the
        same save_env_var used for the Deepgram key; languages go into
        event.toml so they survive a restart. Translation reconnects with
        the new settings immediately - no restart needed to pick up a key
        pasted mid-event.
        """
        require_operator(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        body = body or {}
        if not any(k in body for k in ("key", "region", "languages")):
            raise HTTPException(status_code=400, detail="send key, region, and/or languages")

        if "key" in body:
            key = text_field(body, "key", 200)
            config_module.save_env_var("AZURE_TRANSLATOR_KEY", key)
            os.environ["AZURE_TRANSLATOR_KEY"] = key

        if "region" in body:
            region = text_field(body, "region", 64, allow_empty=True)
            config_module.save_env_var("AZURE_TRANSLATOR_REGION", region)
            os.environ["AZURE_TRANSLATOR_REGION"] = region

        if "languages" in body:
            raw = body.get("languages")
            if not isinstance(raw, str) or len(raw) > 200:
                raise HTTPException(status_code=400, detail="languages must be a short string")
            parsed = [tok.strip() for tok in raw.replace(";", ",").split(",") if tok.strip()]
            if not all(transcript_module.SAFE_LANG.match(tok) for tok in parsed):
                raise HTTPException(status_code=400,
                                    detail="languages must look like: es, fr, zh-Hans")
            translation.targets = [lang for lang in parsed if lang != cfg.source_language]
            try:
                config_module.save_translate_settings(
                    config_target(), "azure", translation.targets)
            except OSError as exc:
                print(f"[relay] could not save translation languages to disk: {exc}")

        provider = "azure" if os.environ.get("AZURE_TRANSLATOR_KEY", "").strip() else "none"
        await translation.apply(provider, translation.targets)
        print(f"[relay] translation {'active' if translation.worker else 'inactive'}"
              f" -> {', '.join(translation.targets) or '(no target languages set)'}")
        return JSONResponse({"ok": True, **translation.stats()}, headers=NO_STORE)

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
        if not same_origin(ws.headers):
            await ws.close(code=4403)
            return
        if not operator_ok(ws.headers, ws.cookies):
            await ws.close(code=4401)
            return
        await ws.accept()
        client = hub.add_status(peer_of(ws))
        await serve(ws, client, read=False)

    @app.websocket("/ws/source")
    async def ws_source(ws: WebSocket, token: str | None = None) -> None:
        if is_public(ws.headers):
            await ws.close(code=4404)
            return
        if not same_origin(ws.headers):
            await ws.close(code=4403)
            return
        # `Authorization: Bearer` is what the capture client sends; `?token=`
        # is still accepted for hand-written clients. Always required: an
        # empty source token is refused rather than treated as "no token".
        if not token_matches(bearer(ws.headers) or token, cfg.source_token):
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
