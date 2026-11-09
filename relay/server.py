"""FastAPI relay: transcript in from one capture client, out to every viewer.

Endpoints (see docs/MESSAGES.md):

    GET  /wall /phone /status      the three pages
    GET  /settings                 API keys, integrations, keywords, welcome-screen config
    GET  /tune                     build a wall-view URL with sliders
    GET  /qr                       QR code for the phone URL
    GET  /fonts/<name>             bundled font files (self-hosted, no CDN)
    GET  /<name>                   any other viewer/<name>.html
    WS   /ws/viewer?role=wall&lang=es   caption feed, optionally translated
    WS   /ws/source                capture client, bidirectional (bearer token)
    WS   /ws/status                status page feed (operator-only)
    GET  /api/status               one status snapshot as JSON (operator-only)
    GET  /api/site                 public metadata: base URL, languages
    GET  /api/qr.svg               the QR code as SVG
    GET  /api/transcript.{fmt}     recorded captions as srt|vtt|txt|json|csv (operator-only)
    POST /api/control              {"action": "open_stream"} etc.
    POST /api/youtube-captions     {"enabled": true} - relay-local, not forwarded
    POST /api/tunnel               {"token": "...", "enabled": true} - relay-local

Everything operator-only (marked above, plus /status, /settings, /tune, and
any other viewer page not explicitly public) needs the operator token - see
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
import base64
import contextlib
import hashlib
import hmac
import io
import os
import re
import secrets
import socket
import tomllib
import time
import urllib.parse
from pathlib import Path

import segno
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, PlainTextResponse,
                               RedirectResponse, Response)

from . import config as config_module
from . import event as event_module
from . import preflight as preflight_module
from . import propresenter as propresenter_module
from .slides import SlideClient, SlidePreview
from . import transcript as transcript_module
from . import translate as translate_module
from . import tunnel as tunnel_module
from . import updater as updater_module
from .hub import CONTROL_ACTIONS, INTERNAL_CONTROL_ACTIONS, RELAY_ACTIONS, Client, Hub
from .youtube import ALLOWED_HOSTS as YOUTUBE_HOSTS
from .youtube import YouTubeCaptionPusher, urls_from_env, valid_ingestion_url

# The only import the relay makes from the capture package, and deliberately
# its leaf module: it names and platform-checks the ASR backends without
# importing any of them, so the relay stays clear of numpy, websockets and
# PortAudio (see capture/backends/support.py, and ci.yml on why that matters).
from capture.backends import support as backend_support
from launcher import appwindow

HEARTBEAT_SECONDS = 5.0
STATUS_PUSH_SECONDS = 0.4

PAGES = {"wall": "wall.html", "phone": "phone.html", "status": "status.html",
         "settings": "settings.html", "tune": "tune.html", "qr": "qr.html",
         "setup": "setup.html"}

# Bundled font files, served under /fonts/<name>. See serve_font() below.
FONT_FILES = {
    "AtkinsonHyperlegible-Regular.ttf": "font/ttf",
    "AtkinsonHyperlegible-Bold.ttf": "font/ttf",
}

# Reachable through the public hostname. Everything else - the status page,
# the tuner, any other named page - is operator-only, see require_local().
PUBLIC_PAGES = {"phone", "qr", "wall"}

# Only a bare file name may be requested as a page.
SAFE_PAGE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# A DNS hostname and nothing else: what `public_hostname` is compared against
# on every request, so it must never be able to carry anything surprising into
# event.toml or the Host check.
# Queued to a viewer's outbox to make pump() close its socket with 4403 -
# used when the wall output key it connected with is revoked.
CLOSE_SENTINEL = "__close_4403__"

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

# The largest JSON body any operator endpoint accepts, and the largest frame
# a websocket peer may send. Both are far above anything real; the point is
# that a hostile client cannot make the event loop parse megabytes.
MAX_JSON_BYTES = 16 * 1024
MAX_WS_FRAME_BYTES = 64 * 1024

# Sent on every response. The pages load nothing from anywhere else, so the
# policy can be strict: scripts only by hash of the page's own inline block
# (computed when the page is served, so editing a page never breaks it),
# no framing except by our own tune page, no forms, no plugins.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "SAMEORIGIN",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}
SCRIPT_RE = re.compile(rb"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)
# `<!-- include name.js -->` or `name.css` in a viewer page, replaced with
# that file's contents wrapped in a <script> or <style> before the page is
# hashed and served. The CSP names hashes of a page's own inline blocks and
# nothing else - there is deliberately no 'self' in script-src - so a shared
# module cannot be pulled in with src=. Stitching it in here keeps the strict
# policy and still lets several pages share one copy of a file instead of
# carrying a copy each, which for the stylesheet is the difference between
# one set of design tokens and seven sets that drift apart.
INCLUDE_RE = re.compile(rb"<!--\s*include\s+([A-Za-z0-9_][A-Za-z0-9_.-]*\.(?:js|css))\s*-->")
INCLUDE_TAG = {".js": (b"<script>", b"</script>"), ".css": (b"<style>", b"</style>")}
SAFE_HEX_COLOR = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _script_hash(script: bytes) -> str:
    """The CSP hash the browser will compute for an inline script.

    Browsers hash the script *as parsed*, and the HTML parser turns every
    CR LF and lone CR into LF before anything else sees the text. A checkout
    with CRLF line endings (git's autocrlf) therefore served a
    hash of the raw bytes that matched nothing, and every page's script was
    silently blocked. Normalising here makes the hash line-ending independent.
    """
    normalized = script.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return "'sha256-" + base64.b64encode(hashlib.sha256(normalized).digest()).decode() + "'"


def expand_includes(html: bytes,
                    viewer_dir: Path) -> tuple[bytes, tuple[tuple[Path, float], ...]]:
    """Stitch every `<!-- include name.js -->` and `<!-- include name.css -->`
    into the page, and report what was pulled in and when it was last written,
    so a caller caching the result can tell that a *shared* file has changed
    underneath it.

    A script arrives as an inline <script>, which page_csp then hashes; a
    stylesheet as an inline <style>, which the policy already allows with
    style-src 'unsafe-inline'.

    The name may not contain a path separator, and the file has to resolve
    inside the viewer directory: these pages are edited by whoever runs the
    booth, and a page is not a place from which to read the rest of the disk.
    """
    used: list[tuple[Path, float]] = []

    def one(match: re.Match[bytes]) -> bytes:
        name = match.group(1).decode()
        path = (viewer_dir / name).resolve()
        if path.parent != viewer_dir.resolve() or not path.is_file():
            raise HTTPException(
                status_code=500,
                detail=f"{name} is included by a page but is not in the viewer directory")
        used.append((path, path.stat().st_mtime))
        # A newline either side, so the file's first line cannot land inside
        # a comment that happened to precede the marker.
        open_tag, close_tag = INCLUDE_TAG[path.suffix]
        return open_tag + b"\n" + path.read_bytes() + b"\n" + close_tag

    return INCLUDE_RE.sub(one, html), tuple(used)


def includes_current(used: tuple[tuple[Path, float], ...]) -> bool:
    """True while every file stitched into a cached page is as it was."""
    for path, mtime in used:
        try:
            if path.stat().st_mtime != mtime:
                return False
        except OSError:
            return False
    return True


def page_csp(html: bytes) -> str:
    hashes = " ".join(_script_hash(m.group(1)) for m in SCRIPT_RE.finditer(html))
    script_src = hashes or "'none'"
    return (
        "default-src 'none'; "
        f"script-src {script_src}; "
        "style-src 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self' ws: wss:; "
        "frame-src 'self'; frame-ancestors 'self'; "
        "base-uri 'none'; form-action 'none'; object-src 'none'"
    )


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
    propresenter = propresenter_module.ProPresenterOutput(
        host=cfg.pp_host, port=cfg.pp_port, surface=cfg.pp_surface,
        message=cfg.pp_message, token=cfg.pp_token, language=cfg.pp_language,
        reveal=cfg.pp_reveal, lines=cfg.pp_lines,
        chars_per_line=cfg.pp_chars_per_line, start_enabled=cfg.pp_start_enabled)

    slide_client = None
    if cfg.slides_enabled:
        try:
            slide_client = SlideClient(cfg.slides_host, cfg.slides_port,
                                       cfg.slides_screen, cfg.slides_approved_hosts)
        except (ValueError, TypeError):
            pass  # Bad optional configuration must not interrupt captions.
    slides = SlidePreview(slide_client, cfg.slides_enabled)
    slides.set_live(event.state == "live")

    def slide_settings():
        return {"enabled": cfg.slides_enabled, "host": cfg.slides_host,
                "port": cfg.slides_port, "screen": cfg.slides_screen,
                "allow_lan": cfg.slides_host in cfg.slides_approved_hosts,
                "preview": slides.public()}

    last_preflight: dict = {"report": None}
    # Mutable so /api/setup can flip it without a relay restart: the wizard's
    # last step writes [setup] completed into event.toml, and the very next
    # request to /status must already go to the dashboard.
    setup_state = {"completed": cfg.setup_completed,
                   "tour": cfg.tour_completed}

    def overlay_key_list() -> list[dict]:
        base = cfg.base_url(local_ip())
        return [{"name": name, "key": key, "url": f"{base}/wall?key={key}"}
                for name, key in cfg.overlay_keys.items()]
    tunnel = tunnel_module.CloudflareTunnel(token=os.environ.get("CLOUDFLARE_TUNNEL_TOKEN", "").strip())
    # `is_live` is read at the moment an install is attempted rather than
    # captured at startup: the whole point of the guard is what the event is
    # doing right now, not what it was doing when the relay came up.
    updater = updater_module.Updater(
        manifest_url=os.environ.get("CAPTIONS_UPDATE_URL", ""),
        is_live=lambda: event.state == "live")

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

    async def read_json(request: Request) -> dict:
        """The request body as a dict, or {} if it is not JSON - bounded
        before it is read, so a large body is refused rather than parsed."""
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > MAX_JSON_BYTES:
            raise HTTPException(status_code=413, detail="request body too large")
        data = await request.body()
        if len(data) > MAX_JSON_BYTES:
            raise HTTPException(status_code=413, detail="request body too large")
        try:
            import json as _json
            body = _json.loads(data.decode("utf-8")) if data else {}
        except (ValueError, UnicodeDecodeError):
            return {}
        return body if isinstance(body, dict) else {}

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
            self.translator: translate_module.AzureTranslator | None = None
            self.worker: translate_module.TranslationWorker | None = None

        def _emit(self, lang: str, text: str, utterance_id: str,
                  t0: float | None = None, t: float | None = None) -> None:
            # The translated line inherits the source line's window, so its
            # .srt cue spans the time it was spoken rather than collapsing to
            # the minimum duration at the moment Azure answered.
            msg = {"type": "final", "text": text, "lang": lang,
                   "utterance": utterance_id}
            if isinstance(t0, (int, float)):
                msg["t0"] = round(float(t0), 3)
            if isinstance(t, (int, float)):
                msg["t"] = round(float(t), 3)
            hub.broadcast(msg)

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
                (lambda msg: self.worker.submit(msg.get("text", ""), msg.get("id", ""),
                                                msg.get("t0"), msg.get("t")))
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
        kind = msg.get("type")
        lang = msg.get("lang") or cfg.source_language

        # ProPresenter follows one language stream - the source by default.
        # It sees interims too (the module decides whether to send them) and
        # an operator Clear takes the caption off the ProPresenter surface.
        if propresenter.enabled:
            wanted = propresenter.language or cfg.source_language
            if kind in ("final", "interim") and lang == wanted:
                propresenter.submit(msg.get("text", ""), kind == "final")
            elif kind == "clear":
                asyncio.create_task(propresenter.clear())

        if kind != "final":
            return
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
        # Where this relay is, for the Settings page's port field, plus how it
        # opens its own pages, for the window-mode panel next to it. The full
        # block with token_required and the config path stays on /api/status.
        # This is the payload the Settings page actually renders from - it
        # reads the websocket, not /api/status - so anything that panel needs
        # has to be here as well.
        "relay": {"host": cfg.host, "port": cfg.port, "ip": local_ip(),
                  "window_mode": cfg.window_mode,
                  "app_window": appwindow.describe(),
                  # Whether anyone has been shown round here yet. The tour
                  # starts itself off this, so it has to arrive with the
                  # first frame rather than after a second request.
                  "tour_completed": setup_state["tour"]},
        "propresenter": propresenter.status(),
        "slide_settings": slide_settings(),
        "overlay_keys": overlay_key_list(),
        # The last preflight report, so a status page opened after the check
        # still shows it. A few KB.
        "preflight": last_preflight.get("report"),
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
        slides.start()
        await translation.apply(cfg.translate_provider, translation.targets)
        if cfg.tunnel_start_enabled and tunnel.configured:
            # Without a hostname nothing would be treated as public, so a
            # tunnel would expose the operator surface to the internet.
            if cfg.public_hostname:
                await tunnel.start()
            else:
                print("[relay] tunnel start_enabled but no public_hostname is set - "
                      "not starting it; set the hostname on the status page first")
        if cfg.pp_start_enabled and propresenter.configured:
            await propresenter.start()
            print(f"[relay] ProPresenter output {propresenter.state}: "
                  f"{propresenter.surface} at {propresenter.host}:{propresenter.port}")
        if cfg.overlay_keys:
            print(f"[relay] wall output keys in force: {', '.join(cfg.overlay_keys)}")
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
            await slides.stop()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if translation.worker is not None:
                await translation.worker.stop()
            if tunnel.running:
                await tunnel.stop()
            if propresenter.enabled:
                await propresenter.stop()

    # No /docs, /redoc or /openapi.json: they would describe the whole
    # operator API to anyone, including through the public hostname.
    app = FastAPI(title="Campus Captions relay", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.hub = hub
    app.state.slides = slides
    app.state.config = cfg
    # Set by /api/relay when the operator changes the port: whoever runs the
    # uvicorn server (relay/__main__.py, macapp/menubar.py) sets
    # app.state.server so the endpoint can ask it to exit, then reads these
    # to decide whether to come back on the new port or stay down.
    app.state.restart_requested = False
    app.state.restart_port = None

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        # Pages set their own hashed policy in file_page; everything else
        # (JSON, SVG, images, .srt) can carry the strictest one there is.
        response.headers.setdefault("Content-Security-Policy",
                                    "default-src 'none'; frame-ancestors 'self'")
        return response

    # -- pages -----------------------------------------------------------

    # path -> (page mtime, [(included path, its mtime)], html, csp)
    page_cache: dict[Path, tuple[float, tuple[tuple[Path, float], ...], bytes, str]] = {}

    def file_page(path: Path) -> Response:
        """A viewer page with a Content-Security-Policy that names the hash
        of its inline script. Read per request (the file may change during
        an event), hashed once per file version.

        A cached page is also invalidated when a shared script it includes
        changes, so editing tour.js updates every page carrying it and not
        just the ones nobody has opened yet.
        """
        try:
            mtime = path.stat().st_mtime
        except OSError:
            raise HTTPException(status_code=404, detail=f"{path.name} not found")
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"{path.name} not found")
        cached = page_cache.get(path)
        if cached is None or cached[0] != mtime or not includes_current(cached[1]):
            html, includes = expand_includes(path.read_bytes(), config_module.VIEWER_DIR)
            cached = (mtime, includes, html, page_csp(html))
            page_cache[path] = cached
        html, csp = cached[2], cached[3]
        return Response(content=html, media_type="text/html; charset=utf-8",
                        headers={**NO_STORE, "Content-Security-Policy": csp})

    def page(name: str) -> FileResponse:
        return file_page(config_module.VIEWER_DIR / PAGES[name])

    def operator_gate(request: Request) -> Response | None:
        """The sign-in half of an operator-only page: None once this request
        is authenticated, otherwise the response to send instead.

        `?token=...` on the URL signs the browser in (cookie) and redirects
        to the same URL without it; from then on the cookie alone is enough.
        No token and no cookie gets a short page saying where the link is,
        not the dashboard.

        Split out from operator_page so /status can decide *after* signing
        the browser in whether the dashboard or the setup wizard is what
        this operator should be looking at - the answer depends on config,
        which someone with no token has no business learning.
        """
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
        return None

    def operator_page(request: Request, path: Path) -> Response:
        """An operator-only page."""
        return operator_gate(request) or file_page(path)

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
        blocked = operator_gate(request)
        if blocked is not None:
            return blocked
        # First run goes to the wizard, not the dashboard. The dashboard is
        # for watching a configured system; on a machine where nobody has
        # picked a microphone or a transcription backend there is nothing
        # yet to watch. Checked here rather than with a separate link so the
        # status URL the launcher prints, the tray menu opens and every page
        # of the docs names keeps working unchanged - it just lands somewhere
        # more useful the first time.
        if not setup_state["completed"]:
            return RedirectResponse(url="/setup", status_code=303, headers=NO_STORE)
        return file_page(config_module.VIEWER_DIR / PAGES["status"])

    @app.get("/setup")
    async def setup_page(request: Request) -> Response:
        return operator_page(request, config_module.VIEWER_DIR / PAGES["setup"])

    @app.get("/tune")
    async def tune_page(request: Request) -> Response:
        return operator_page(request, config_module.VIEWER_DIR / PAGES["tune"])

    @app.get("/settings")
    async def settings_page(request: Request) -> Response:
        return operator_page(request, config_module.VIEWER_DIR / PAGES["settings"])

    @app.get("/qr")
    async def qr_page() -> FileResponse:
        return page("qr")

    @app.get("/robots.txt")
    async def robots() -> PlainTextResponse:
        # A live transcript of a room should never turn up in a search engine.
        return PlainTextResponse("User-agent: *\nDisallow: /\n", headers=NO_STORE)

    @app.get("/api/qr.svg")
    async def qr_svg(request: Request) -> Response:
        """QR for the phone view, as SVG so it stays sharp on an LED wall.

        `url` may only point at this relay: the page is public, and a QR
        served from the venue's own address that led somewhere else would
        be a phishing aid."""
        ip = local_ip()
        ours = (cfg.base_url(ip), f"http://{ip}:{cfg.port}",
                f"http://localhost:{cfg.port}", f"http://127.0.0.1:{cfg.port}")
        target = request.query_params.get("url") or (cfg.base_url(ip) + "/phone")
        if len(target) > 512 or not any(target == o or target.startswith(o + "/") for o in ours):
            raise HTTPException(status_code=400, detail="url must be this relay's own address")
        dark = request.query_params.get("dark", "#000000")
        light = request.query_params.get("light", "#ffffff")
        if not (SAFE_HEX_COLOR.match(dark) and SAFE_HEX_COLOR.match(light)):
            raise HTTPException(status_code=400, detail="colours must be #rgb or #rrggbb")
        try:
            code = segno.make(target, error="m")
            buf = io.BytesIO()
            code.save(buf, kind="svg", scale=10, border=2, dark=dark, light=light)
        except Exception:
            raise HTTPException(status_code=400, detail="cannot encode that url")
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
            "slides": slides.public(),
        }, headers=NO_STORE)

    @app.get("/api/slides")
    async def api_slides(request: Request) -> JSONResponse:
        if request.query_params:
            raise HTTPException(status_code=400, detail="no query parameters accepted")
        return JSONResponse(slides.public(), headers=NO_STORE)

    @app.get("/api/slides/image/{revision}")
    async def api_slide_image(revision: str, request: Request) -> Response:
        if request.query_params:
            raise HTTPException(status_code=400, detail="no query parameters accepted")
        image = slides.image(revision)
        if image is None:
            raise HTTPException(status_code=404, detail="no current slide", headers=NO_STORE)
        return Response(image, media_type="image/png", headers=NO_STORE)

    def serve_slot(slot: event_module.ImageSlot, what: str) -> Response:
        """A public image, cached by URL: viewers append ?v=<version> so a
        replaced file gets a new address."""
        path = slot.path
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail=f"no {what}")
        return FileResponse(path, media_type=slot.media_type,
                            headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/api/event/image")
    async def api_event_image() -> Response:
        """The welcome screen's background, public like the phone page it decorates."""
        return serve_slot(event.welcome, "welcome image")

    @app.get("/api/event/logo")
    async def api_event_logo() -> Response:
        """The logo shown above the captions while they are live."""
        return serve_slot(event.logo, "logo")

    @app.get("/fonts/{name}")
    async def serve_font(name: str) -> Response:
        """Self-hosted, not pulled from a CDN at render time - a font
        picker shouldn't add a new way for the public phone/wall pages to
        depend on the internet being up. Public, like the pages that use it;
        an explicit allowlist rather than SAFE_PAGE_NAME since this serves
        raw files, not viewer/*.html read fresh on every request."""
        media_type = FONT_FILES.get(name)
        if media_type is None:
            raise HTTPException(status_code=404, detail="not found")
        path = config_module.VIEWER_DIR / "fonts" / name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="not found")
        return FileResponse(path, media_type=media_type,
                             headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.get("/api/transcript.{fmt}")
    async def transcript_export(request: Request, fmt: str, lang: str = "",
                                offset: float = 0.0, timestamps: str = "1") -> Response:
        """The recorded event as a file: .srt, .vtt, .txt, .json or .csv -
        the extension picks the format (transcript_module.FORMATS).

        `offset` (seconds) shifts every cue earlier, to take the recognizer's
        own delay back out: a line is recorded when it was committed, which
        is roughly a second after it was said. 0.8 is a fair value for
        Deepgram; leave it at 0 if the timing looks right already. `.txt`
        also takes `?timestamps=0` for bare text with no time prefixes.
        """
        require_operator(request)
        if fmt not in transcript_module.FORMATS:
            raise HTTPException(
                status_code=404,
                detail=f"format must be one of {', '.join(sorted(transcript_module.FORMATS))}")
        lang = lang or cfg.source_language
        if not transcript_module.SAFE_LANG.match(lang):
            raise HTTPException(status_code=400, detail="bad language code")
        if not (0.0 <= offset <= 10.0):
            raise HTTPException(status_code=400, detail="offset must be 0-10 seconds")
        options = {}
        if fmt == "txt":
            options["timestamps"] = timestamps.strip().lower() not in ("0", "false", "no", "")
        body = recorder.export(lang, fmt, offset=offset, **options)
        if not body:
            raise HTTPException(status_code=404,
                                detail=f"nothing recorded for {lang!r}")
        media_type, _ = transcript_module.FORMATS[fmt]
        stamp = time.strftime("%Y%m%d-%H%M", time.localtime(recorder.started_at))
        filename = f"captions-{stamp}-{lang}.{fmt}"
        return Response(
            content=body, media_type=media_type,
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
            "window_mode": cfg.window_mode,
            "app_window": appwindow.describe(),
            "tour_completed": setup_state["tour"],
        }
        return JSONResponse(snap, headers=NO_STORE)

    def remember_audio_device(index: int) -> None:
        """Persist the operator's input choice so the next launch comes up on
        it, rather than making them re-pick before every event.

        Stored by name, resolved here from the device list the capture client
        already reports - the index is only meaningful until something is
        plugged in or removed. An index we cannot name is not saved at all:
        writing a stale number would be worse than remembering nothing.
        """
        name = next((d.get("name") for d in hub.state.audio_devices
                     if d.get("index") == index), None)
        if not name:
            return
        try:
            config_module.save_audio_device(config_target(), name)
            print(f"[relay] remembered audio input {name!r} for the next launch")
        except OSError as exc:
            print(f"[relay] could not save the audio device to disk: {exc}")

    @app.post("/api/control")
    async def api_control(request: Request) -> JSONResponse:
        require_operator(request)
        body = await read_json(request)
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

        if action not in CONTROL_ACTIONS or action in INTERNAL_CONTROL_ACTIONS:
            raise HTTPException(
                status_code=400,
                detail="action must be one of "
                       f"{sorted((CONTROL_ACTIONS - INTERNAL_CONTROL_ACTIONS) | RELAY_ACTIONS)}",
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
        elif action == "set_profanity_filter":
            enabled = body.get("enabled")
            if not isinstance(enabled, bool):
                raise HTTPException(status_code=400, detail="enabled must be a boolean")
            fields["enabled"] = enabled
        delivered = hub.send_control(action, fields)
        print(f"[relay] control {action} -> {delivered} capture client(s)")
        if action == "select_audio_device":
            remember_audio_device(fields["device"])
        return JSONResponse(
            {"ok": True, "action": action, "delivered_to": delivered},
            headers=NO_STORE,
        )

    @app.post("/api/event")
    async def api_event(request: Request) -> JSONResponse:
        """Relay-local. {"state": "live"} and/or the welcome-screen text:
        title, message, notice, pause_message, disclaimer - any subset.
        Every change is pushed to viewers at once and saved to event.toml."""
        require_operator(request)
        body = await read_json(request)
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
                            ("notice", 300), ("pause_message", 120), ("logo_alt", 80),
                            ("disclaimer", 200)):
            if name in body:
                fields[name] = text_field(body, name, limit, allow_empty=True)
        if not fields:
            raise HTTPException(status_code=400,
                                detail="send state, title, message, notice, pause_message, "
                                        "and/or disclaimer")
        event.update(**fields)
        slides.set_live(event.state == "live")
        try:
            config_module.save_event_settings(config_target(), fields)
        except OSError as exc:
            print(f"[relay] could not save event settings to disk: {exc}")
        hub.broadcast_event()
        if "state" in fields:
            print(f"[relay] event -> {fields['state']}")
            if fields["state"] in ("paused", "ended") and propresenter.enabled:
                # Pausing or ending clears the ProPresenter surface (N-05),
                # so a stale line never sits on a screen in front of the room.
                asyncio.create_task(propresenter.clear())
        return JSONResponse({"ok": True, **event.public()}, headers=NO_STORE)

    async def store_image(request: Request, setter, what: str) -> JSONResponse:
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
            setter(data)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        hub.broadcast_event()
        print(f"[relay] {what} updated")
        return JSONResponse({"ok": True, **event.public()}, headers=NO_STORE)

    def remove_image(request: Request, clearer, what: str) -> JSONResponse:
        require_operator(request)
        clearer()
        hub.broadcast_event()
        print(f"[relay] {what} removed")
        return JSONResponse({"ok": True, **event.public()}, headers=NO_STORE)

    @app.put("/api/event/image")
    async def api_event_image_put(request: Request) -> JSONResponse:
        return await store_image(request, event.set_image, "welcome image")

    @app.delete("/api/event/image")
    async def api_event_image_delete(request: Request) -> JSONResponse:
        return remove_image(request, event.clear_image, "welcome image")

    @app.put("/api/event/logo")
    async def api_event_logo_put(request: Request) -> JSONResponse:
        return await store_image(request, event.set_logo, "logo")

    @app.delete("/api/event/logo")
    async def api_event_logo_delete(request: Request) -> JSONResponse:
        return remove_image(request, event.clear_logo, "logo")

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
        body = await read_json(request)
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

    # Recognition settings that had no UI at all until now: changing how the
    # recognizer behaves meant a text editor and a restart. Declared as data
    # so the validator, the file writer and the control message cannot
    # disagree about which keys exist or what shape they are.
    #   toml section -> {field: (kind, low, high)}
    # `kind` is "bool", "number" or one of the string tuples below.
    WHISPER_COMPUTE = ("int8", "int8_float16", "float16", "float32")
    BACKEND_OPTIONS = {
        "deepgram": {
            "diarize": ("bool", None, None),
            "filler_words": ("bool", None, None),
            "inaudible_below": ("number", 0.0, 1.0),
        },
        "whisper": {
            "compute_type": (WHISPER_COMPUTE, None, None),
            "interim_interval": ("number", 0.2, 5.0),
            "silence_commit": ("number", 0.2, 5.0),
        },
        "apple_speech": {
            # A BCP-47 locale, e.g. en-US. Checked by shape, not against a
            # list: macOS gains locales, and refusing one this file has not
            # heard of would be worse than passing it to the helper, which
            # reports an unusable one itself.
            "locale": ("locale", None, None),
        },
    }
    LOCALE_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")

    def clean_backend_options(body: dict) -> tuple[dict, dict]:
        """(per-section values for event.toml, flat values for the capture
        client). Raises HTTPException on anything that does not fit."""
        sections: dict[str, dict] = {}
        flat: dict = {}
        for section, fields in BACKEND_OPTIONS.items():
            given = body.get(section)
            if given is None:
                continue
            if not isinstance(given, dict):
                raise HTTPException(status_code=400,
                                    detail=f"{section} must be an object")
            for field, (kind, low, high) in fields.items():
                if field not in given:
                    continue
                value = given[field]
                if kind == "bool":
                    if not isinstance(value, bool):
                        raise HTTPException(
                            status_code=400,
                            detail=f"{section}.{field} must be true or false")
                elif kind == "number":
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        raise HTTPException(
                            status_code=400,
                            detail=f"{section}.{field} must be a number")
                    if not (low <= float(value) <= high):
                        raise HTTPException(
                            status_code=400,
                            detail=f"{section}.{field} must be between {low} and {high}")
                    value = float(value)
                elif kind == "locale":
                    value = str(value).strip()
                    if not LOCALE_RE.match(value):
                        raise HTTPException(
                            status_code=400,
                            detail=f"{section}.{field} must look like en-US")
                else:                       # a tuple of allowed strings
                    if value not in kind:
                        raise HTTPException(
                            status_code=400,
                            detail=f"{section}.{field} must be one of {', '.join(kind)}")
                sections.setdefault(section, {})[field] = value
                # capture/config.py flattens every backend section into one
                # dict, prefixing only [whisper] - mirror that exactly, or the
                # backend reads a key nobody wrote.
                flat[f"whisper_{field}" if section == "whisper" else field] = value
        return sections, flat

    @app.get("/api/backend-options")
    async def api_backend_options(request: Request) -> JSONResponse:
        """What the Settings page renders as the recognition-tuning panel.
        Read from the config file rather than remembered, so it survives a
        relay restart and reflects a hand-edited file too."""
        require_operator(request)
        data: dict = {}
        path = config_target()
        if path.exists():
            try:
                with path.open("rb") as fh:
                    data = tomllib.load(fh)
            except (OSError, ValueError) as exc:
                print(f"[relay] could not read {path}: {exc}")
        out = {}
        for section, fields in BACKEND_OPTIONS.items():
            have = data.get(section) or {}
            out[section] = {f: have.get(f) for f in fields}
        out["whisper_compute_choices"] = list(WHISPER_COMPUTE)
        return JSONResponse(out, headers=NO_STORE)

    @app.post("/api/backend-options")
    async def api_backend_options_save(request: Request) -> JSONResponse:
        """Save recognition settings and apply them to the running capture
        client. Written to event.toml so they survive a restart, and pushed
        over the control channel so they take effect now."""
        require_operator(request)
        body = await read_json(request)
        sections, flat = clean_backend_options(body or {})
        if not sections:
            raise HTTPException(status_code=400,
                                detail="send at least one of "
                                       f"{', '.join(BACKEND_OPTIONS)}")
        try:
            for section, values in sections.items():
                config_module.save_section(config_target(), f"[{section}]", values)
        except OSError as exc:
            raise HTTPException(status_code=500,
                                detail=f"could not write the config file: {exc}") from None
        delivered = hub.send_control("set_backend_options", {"options": flat})
        print(f"[relay] recognition options saved ({', '.join(sorted(flat))}); "
              f"applied on {delivered} capture client(s)")
        return JSONResponse({"ok": True, "delivered_to": delivered, **sections},
                            headers=NO_STORE)

    @app.get("/api/wordlist/{name}")
    async def api_wordlist(name: str, request: Request) -> JSONResponse:
        """A hand-maintained word list, as text, for the Settings page to
        edit. Only the two names in config.WORDLISTS resolve to anything."""
        require_operator(request)
        try:
            path = config_module.wordlist_path(name)
        except ValueError:
            raise HTTPException(status_code=404, detail="unknown word list") from None
        try:
            text = path.read_text(encoding="utf-8") if path.exists() else ""
        except (OSError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=500,
                                detail=f"could not read {path.name}: {exc}") from None
        return JSONResponse({"name": name, "path": str(path), "text": text},
                            headers=NO_STORE)

    @app.post("/api/wordlist/{name}")
    async def api_wordlist_save(name: str, request: Request) -> JSONResponse:
        """Replace a word list and reload it into the running capture client.

        The body is the raw text, not JSON: a campus's worth of names runs
        past MAX_JSON_BYTES, and wrapping plain text in JSON to get it through
        a 16 KiB cap would be solving the wrong problem. Same shape as the
        welcome-screen image upload, which takes its bytes the same way.

        Saving reloads: an operator who has just typed a name in expects the
        next sentence to use it, and "now press Reload as well" is a step that
        exists only because the file used to be edited outside the app.
        """
        require_operator(request)
        try:
            path = config_module.wordlist_path(name)
        except ValueError:
            raise HTTPException(status_code=404, detail="unknown word list") from None

        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > config_module.MAX_WORDLIST_BYTES:
            raise HTTPException(status_code=413, detail="word list too large")
        raw = await request.body()
        if len(raw) > config_module.MAX_WORDLIST_BYTES:
            raise HTTPException(status_code=413, detail="word list too large")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="word list must be UTF-8") from None
        # Normalise line endings and drop other control characters: these
        # files are read a line at a time, and a stray CR or NUL would become
        # a term nothing ever matches.
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = "".join(ch for ch in text if ch == "\n" or ch >= " ")
        lines = [line.strip() for line in text.split("\n")]
        cleaned = "\n".join(lines).strip() + "\n" if any(lines) else ""

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(cleaned, encoding="utf-8", newline="\n")
        except OSError as exc:
            raise HTTPException(status_code=500,
                                detail=f"could not write {path.name}: {exc}") from None

        delivered = hub.send_control("reload_keyterms", {})
        terms = sum(1 for line in cleaned.split("\n")
                    if line and not line.startswith("#"))
        print(f"[relay] {name} saved ({terms} terms); "
              f"reloaded on {delivered} capture client(s)")
        return JSONResponse(
            {"ok": True, "name": name, "terms": terms, "delivered_to": delivered},
            headers=NO_STORE)

    @app.get("/api/setup")
    async def api_setup(request: Request) -> JSONResponse:
        """Everything the first-run wizard needs that it cannot get from
        /api/status: which backends this machine could run and what each one
        is, whether the keys they need are already saved, and where the
        finished system will be reachable.

        The live half - audio devices, input levels, whether the capture
        client is even connected - comes from /api/status, which the wizard
        polls like the status page does.
        """
        require_operator(request)
        ip = local_ip()
        return JSONResponse({
            "completed": setup_state["completed"],
            "tour_completed": setup_state["tour"],
            "backends": [backend_support.describe(name)
                         for name in backend_support.supported()],
            "auto_backend": backend_support.resolve_auto(),
            "has_deepgram_key": bool(os.environ.get("DEEPGRAM_API_KEY", "").strip()),
            "event": {"title": cfg.event_title, "notice": cfg.event_notice},
            # The LAN address, not localhost: these are the three links the
            # wizard's last step tells the operator to hand out, and a phone
            # cannot open 127.0.0.1.
            "urls": {
                "status": f"http://{ip}:{cfg.port}/status",
                "wall": f"http://{ip}:{cfg.port}/wall",
                "phone": f"{cfg.base_url(ip)}/phone",
            },
        }, headers=NO_STORE)

    @app.post("/api/ui")
    async def api_ui(request: Request) -> JSONResponse:
        """{"window_mode": "app"|"browser"} - how this machine opens the
        app's own pages from now on.

        Takes effect on the next launch and on the next Open from the tray
        or menu bar; the window already on screen is not moved, because
        closing an operator's dashboard out from under them mid-event to
        reopen it in a different frame would be worse than waiting.
        """
        require_operator(request)
        body = await read_json(request)
        mode = str((body or {}).get("window_mode", "")).strip().lower()
        if mode not in appwindow.MODES:
            raise HTTPException(
                status_code=400,
                detail=f"window_mode must be one of {', '.join(appwindow.MODES)}")
        try:
            config_module.save_window_mode(config_target(), mode)
        except OSError as exc:
            raise HTTPException(status_code=500,
                                detail=f"could not write the config file: {exc}") from None
        cfg.window_mode = mode
        print(f"[relay] pages will open as a {mode}")
        return JSONResponse({"ok": True, "window_mode": mode,
                             "app_window": appwindow.describe()},
                            headers=NO_STORE)

    @app.post("/api/setup")
    async def api_setup_save(request: Request) -> JSONResponse:
        """{"completed": true} - the wizard's Finish button, and its Skip.
        {"tour_completed": true} - the guided tour's Finish, and its Skip.

        Only these two flags: every actual setting either of them touches is
        saved through the endpoint that already owns it (/api/control for
        the microphone, backend and Deepgram key, /api/event for the title
        and notice), so there is one writer per setting and the wizard
        cannot drift from what the status and settings pages do. The tour
        writes no settings at all.
        """
        require_operator(request)
        body = await read_json(request)
        body = body or {}
        # Two independent flags, and a request may carry either. The tour
        # ends on a page the wizard never sees, so it reports on its own.
        for key in ("completed", "tour_completed"):
            if key in body and not isinstance(body[key], bool):
                raise HTTPException(status_code=400, detail=f"{key} must be a boolean")
        if "completed" not in body and "tour_completed" not in body:
            body = {"completed": True}

        try:
            if "completed" in body:
                config_module.save_setup_complete(config_target(), body["completed"])
                setup_state["completed"] = body["completed"]
                cfg.setup_completed = body["completed"]
                print("[relay] first-run setup marked "
                      f"{'complete' if body['completed'] else 'incomplete'}")
                if "tour_completed" not in body:
                    # A config file that does not mention the tour answers
                    # for it with setup.completed (see load()), so writing
                    # only completed=true here would tell the next launch
                    # the tour had been seen when it has not. Pin it.
                    body = dict(body, tour_completed=setup_state["tour"])
            if "tour_completed" in body:
                config_module.save_tour_complete(config_target(), body["tour_completed"])
                setup_state["tour"] = body["tour_completed"]
                cfg.tour_completed = body["tour_completed"]
                print("[relay] guided tour marked "
                      f"{'seen' if body['tour_completed'] else 'unseen'}")
        except OSError as exc:
            raise HTTPException(status_code=500,
                                detail=f"could not write the config file: {exc}") from None
        return JSONResponse({"ok": True, "completed": setup_state["completed"],
                             "tour_completed": setup_state["tour"]}, headers=NO_STORE)

    @app.post("/api/preflight")
    async def api_preflight(request: Request) -> JSONResponse:
        """Run the preflight check (relay/preflight.py) now and return the
        report: a verdict, a headline, and a list of findings with what to
        do about each. Takes a few seconds - it probes the network to the
        services this setup depends on. Changes nothing."""
        require_operator(request)
        snap = hub.snapshot()
        report = await preflight_module.run(
            snap, propresenter.status() if propresenter.configured else None)
        last_preflight["report"] = report
        counts = report.get("counts") or {}
        print(f"[relay] preflight: {report['verdict']} - {counts.get('fail', 0)} to fix, "
              f"{counts.get('warn', 0)} warnings, {counts.get('ok', 0)} ok "
              f"({report.get('duration_ms')} ms)")
        return JSONResponse({"ok": True, **report}, headers=NO_STORE)

    @app.post("/api/relay")
    async def api_relay(request: Request) -> JSONResponse:
        """Change the port the relay listens on, from the Settings page.

        {"port": 8001}. Saved into [relay] port in event.toml, then the relay
        tells its capture client to follow (`relay_moving`), answers this
        request, and asks its own server to exit; the process that runs the
        server (relay/__main__.py, the menu bar app) starts it again on the
        new port. The launcher's subprocess loops the same way. The operator
        cookie is not port-specific, so the Settings page just moves over.
        """
        require_operator(request)
        body = await read_json(request)
        body = body or {}
        if "port" not in body:
            raise HTTPException(status_code=400, detail="send port")
        port = body.get("port")
        if isinstance(port, str) and port.strip().isdigit():
            port = int(port)
        if isinstance(port, bool) or not isinstance(port, int) or not (1024 <= port <= 65535):
            raise HTTPException(status_code=400, detail="port must be a number from 1024 to 65535")
        if port == cfg.port:
            return JSONResponse({"ok": True, "port": port, "changed": False}, headers=NO_STORE)
        if not config_module.port_is_free(cfg.host, port):
            raise HTTPException(status_code=409,
                                detail=f"port {port} is already in use by another program")
        try:
            config_module.save_relay_port(config_target(), port)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"could not save the port: {exc}")

        told = hub.send_control("relay_moving", {"port": port})
        previous = cfg.port
        app.state.restart_requested = True
        app.state.restart_port = port
        print(f"[relay] port {previous} -> {port}: saved; {told} capture client(s) told; restarting")

        def request_exit() -> None:
            server = getattr(app.state, "server", None)
            if server is not None:
                server.should_exit = True
            else:
                print("[relay] no server handle - restart it by hand on the new port")

        # After this response has gone out, not before.
        asyncio.get_running_loop().call_later(0.4, request_exit)
        return JSONResponse({"ok": True, "port": port, "changed": True, "previous": previous,
                             "capture_clients_told": told}, headers=NO_STORE)

    @app.get("/api/update")
    async def api_update_status(request: Request) -> JSONResponse:
        """What the operator is running and whether anything newer exists."""
        require_operator(request)
        return JSONResponse(updater.status(), headers=NO_STORE)

    @app.post("/api/update")
    async def api_update(request: Request) -> JSONResponse:
        """{"action": "check"|"download"|"install"}

        Relay-local and operator-driven throughout - nothing here happens on a
        timer. See relay/updater.py.
        """
        require_operator(request)
        body = await read_json(request) or {}
        action = text_field(body, "action", 32)
        if action == "check":
            return JSONResponse(await updater.check(), headers=NO_STORE)
        if action == "download":
            return JSONResponse(await updater.fetch(), headers=NO_STORE)
        if action == "install":
            # Not a 4xx when the event is live: the operator asked a reasonable
            # question and the answer is "not now", which belongs in the status
            # the page already renders rather than as a failed request.
            return JSONResponse(updater.install(), headers=NO_STORE)
        raise HTTPException(
            status_code=400,
            detail="action must be check, download or install")

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
        body = await read_json(request)
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

    @app.get("/api/slide-settings")
    async def get_slide_settings(request: Request) -> JSONResponse:
        require_operator(request)
        return JSONResponse(slide_settings(), headers=NO_STORE)

    @app.post("/api/slide-settings")
    async def set_slide_settings(request: Request) -> JSONResponse:
        require_operator(request)
        body = await read_json(request)
        fields = {"enabled", "host", "port", "screen", "allow_lan"}
        if not body or set(body) - fields:
            raise HTTPException(status_code=400, detail="send slide settings only")
        values = {key: body.get(key, slide_settings()[key]) for key in fields}
        if type(values["enabled"]) is not bool or type(values["allow_lan"]) is not bool:
            raise HTTPException(status_code=400, detail="enabled and allow_lan must be true or false")
        host = values["host"]
        if not isinstance(host, str) or not host or len(host) > 45:
            raise HTTPException(status_code=400, detail="enter a literal IP address")
        host = host.strip()
        approved = [host] if values["allow_lan"] else []
        try:
            client = SlideClient(host, values["port"], values["screen"], approved)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            config_module.save_section(config_target(), "[slides]", {
                "enabled": values["enabled"], "host": host, "port": values["port"],
                "screen": values["screen"], "approved_hosts": approved})
        except OSError as exc:
            raise HTTPException(status_code=500, detail="could not save slide settings") from exc
        cfg.slides_enabled = values["enabled"]
        cfg.slides_host, cfg.slides_port = host, values["port"]
        cfg.slides_screen, cfg.slides_approved_hosts = values["screen"], tuple(approved)
        slides.configure(client, cfg.slides_enabled)
        return JSONResponse(slide_settings(), headers=NO_STORE)

    @app.post("/api/propresenter")
    async def api_propresenter(request: Request) -> JSONResponse:
        """Relay-local, like the tunnel and YouTube panels: configure and
        switch the direct ProPresenter output (relay/propresenter.py).

        Any subset of {"host", "port", "surface", "message", "token",
        "language", "reveal", "lines", "chars_per_line"} is applied and
        saved into event.toml's
        [propresenter] section; {"enabled": bool} starts or stops delivery;
        {"probe": true} re-checks the connection now; {"clear": true} takes
        the caption off the surface. Returns the output's status().
        """
        require_operator(request)
        body = await read_json(request)
        body = body or {}
        known = ("host", "port", "surface", "message", "token", "language",
                 "reveal", "lines", "chars_per_line", "interims",
                 "enabled", "probe", "clear")
        if not any(k in body for k in known):
            raise HTTPException(status_code=400, detail=f"send one of: {', '.join(known)}")

        changes = {}
        if "host" in body:
            host = text_field(body, "host", 253).lower()
            if not SAFE_HOSTNAME.match(host):
                raise HTTPException(status_code=400, detail="host must be a plain DNS name or address")
            changes["host"] = host
        if "port" in body:
            port = body.get("port")
            if isinstance(port, str) and port.strip().isdigit():
                port = int(port)
            if isinstance(port, bool) or not isinstance(port, int) or not (1 <= port <= 65535):
                raise HTTPException(status_code=400, detail="port must be 1-65535")
            changes["port"] = port
        if "surface" in body:
            surface = body.get("surface")
            if surface not in propresenter_module.SURFACES:
                raise HTTPException(
                    status_code=400,
                    detail=f"surface must be one of {', '.join(propresenter_module.SURFACES)}")
            changes["surface"] = surface
        for key in ("message", "token"):
            if key in body:
                changes[key] = text_field(body, key, 120, allow_empty=True)
        if "language" in body:
            language = text_field(body, "language", 16, allow_empty=True)
            if language and not transcript_module.SAFE_LANG.match(language):
                raise HTTPException(status_code=400, detail="language must look like: en, es, zh-Hans")
            changes["language"] = language
        if "reveal" in body:
            reveal = body.get("reveal")
            if reveal not in propresenter_module.REVEALS:
                raise HTTPException(
                    status_code=400,
                    detail=f"reveal must be one of {', '.join(propresenter_module.REVEALS)}")
            changes["reveal"] = reveal
        elif "interims" in body:
            # The name this setting shipped under, still accepted.
            if not isinstance(body.get("interims"), bool):
                raise HTTPException(status_code=400, detail="interims must be a boolean")
            changes["interims"] = body["interims"]
        if "lines" in body:
            count = body.get("lines")
            if isinstance(count, str) and count.strip().isdigit():
                count = int(count)
            if isinstance(count, bool) or not isinstance(count, int) \
                    or not (1 <= count <= propresenter_module.MAX_LINES):
                raise HTTPException(
                    status_code=400,
                    detail=f"lines must be 1-{propresenter_module.MAX_LINES}")
            changes["lines"] = count
        if "chars_per_line" in body:
            width = body.get("chars_per_line")
            if isinstance(width, str) and width.strip().isdigit():
                width = int(width)
            low, high = (propresenter_module.MIN_CHARS_PER_LINE,
                         propresenter_module.MAX_CHARS_PER_LINE)
            if isinstance(width, bool) or not isinstance(width, int) or not (low <= width <= high):
                raise HTTPException(status_code=400,
                                    detail=f"chars_per_line must be {low}-{high}")
            changes["chars_per_line"] = width
        if changes:
            propresenter.configure(**changes)
            try:
                config_module.save_section(config_target(), "[propresenter]", {
                    "host": propresenter.host, "port": propresenter.port,
                    "surface": propresenter.surface, "message": propresenter.message,
                    "token": propresenter.token, "language": propresenter.language,
                    "reveal": propresenter.reveal, "lines": propresenter.lines,
                    "chars_per_line": propresenter.chars_per_line,
                })
            except OSError as exc:
                print(f"[relay] could not save ProPresenter settings to disk: {exc}")

        if "enabled" in body:
            enabled = body.get("enabled")
            if not isinstance(enabled, bool):
                raise HTTPException(status_code=400, detail="enabled must be a boolean")
            if enabled:
                if not propresenter.configured:
                    raise HTTPException(status_code=400, detail="set a host and port first")
                await propresenter.start()
            else:
                await propresenter.stop()
            try:
                config_module.save_section(config_target(), "[propresenter]",
                                           {"start_enabled": enabled})
            except OSError as exc:
                print(f"[relay] could not save ProPresenter settings to disk: {exc}")
            print(f"[relay] ProPresenter output {'enabled' if enabled else 'disabled'}")
        elif body.get("probe") is True:
            await propresenter.probe()
        if body.get("clear") is True:
            await propresenter.clear()

        return JSONResponse({"ok": True, **propresenter.status()}, headers=NO_STORE)

    @app.post("/api/overlay-keys")
    async def api_overlay_keys(request: Request) -> JSONResponse:
        """Wall output keys (N-09): one per browser-source output - "obs",
        "propresenter", "lobby" - so a forgotten scene or an old prop can be
        switched off by revoking its key instead of changing every URL.

        {"create": "obs"} makes (or replaces) a key for that name;
        {"revoke": "obs"} removes it and closes the wall sockets that used it.
        While any key exists, a wall socket without a valid ?key= is refused
        (close 4403). With none, the wall needs no key, as before. Keys are
        saved to event.toml's [overlay] section.
        """
        require_operator(request)
        body = await read_json(request)
        body = body or {}
        if "create" not in body and "revoke" not in body:
            raise HTTPException(status_code=400, detail="send create or revoke with a name")
        field = "create" if "create" in body else "revoke"
        name = text_field(body, field, 32)
        if not re.match(r"^[A-Za-z0-9 _.-]{1,32}$", name):
            raise HTTPException(status_code=400,
                                detail="name must be 1-32 letters, digits, spaces, . _ or -")
        if field == "create":
            cfg.overlay_keys[name] = secrets.token_urlsafe(9)
            print(f"[relay] wall output key created: {name}")
        else:
            if name not in cfg.overlay_keys:
                raise HTTPException(status_code=404, detail=f"no key called {name!r}")
            del cfg.overlay_keys[name]
            print(f"[relay] wall output key revoked: {name}")
            # Anyone holding the revoked key is dropped now, not at their
            # next reconnect. Their page retries and is refused with 4403.
            for client in list(hub.viewers.values()):
                if (client.role == "wall" and client.key is not None
                        and client.key not in cfg.overlay_keys.values()):
                    client.send(CLOSE_SENTINEL)
        try:
            config_module.save_section(config_target(), "[overlay]",
                                       {"keys": config_module.format_overlay_keys(cfg.overlay_keys)})
        except OSError as exc:
            print(f"[relay] could not save overlay keys to disk: {exc}")
        return JSONResponse({"ok": True, "keys": overlay_key_list()}, headers=NO_STORE)

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
        body = await read_json(request)
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
        """The only writer for this socket.

        A viewer that closes the tab or walks out of wifi range usually
        disappears here, mid-send, before the reader side notices. That is
        the ordinary end of this task, not an error: stop writing and let
        serve() unwind. Letting it escape instead used to strand the client
        in hub.viewers - see the finally below.
        """
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(client.queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    if client.dropped:
                        # The hub gave up on this viewer because its queue was
                        # full. Close the socket so the page reconnects and
                        # replays at once, instead of sitting silent for the
                        # twenty seconds its stale-link timer takes to notice.
                        await ws.close(code=1013)
                        return
                    continue
                if payload == CLOSE_SENTINEL:
                    # Queued by an overlay-key revoke: end this socket with
                    # the same code a refused key gets, so the page knows why.
                    await ws.close(code=4403, reason="output key revoked")
                    return
                await ws.send_text(payload)
        except Exception:
            # Writing to a socket the client already dropped. Starlette and
            # uvicorn each raise their own type for it; neither is worth
            # distinguishing when the only response is to stop writing.
            return

    async def serve(ws: WebSocket, client: Client, read: bool) -> None:
        sender = asyncio.create_task(pump(ws, client))
        try:
            while True:
                raw = await ws.receive_text()
                if not read:
                    # Viewers never send. One that does is not our page.
                    await ws.close(code=1008)
                    break
                if len(raw) > MAX_WS_FRAME_BYTES:
                    await ws.close(code=1009)
                    break
                hub.ingest(raw, client.peer)
        except WebSocketDisconnect:
            pass
        except Exception as exc:                     # never take the relay down
            print(f"[relay] {client} error: {exc!r}")
        finally:
            sender.cancel()
            # CancelledError is the normal path; the Exception is the one that
            # bit us. A sender that already died of its own accord stores its
            # exception, cancel() is then a no-op, and this await re-raises it
            # - which used to skip hub.remove() entirely and leave a dead
            # viewer collecting every caption until its queue filled 256 deep.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await sender
            hub.remove(client)

    @app.websocket("/ws/viewer")
    async def ws_viewer(ws: WebSocket, role: str = "wall", lang: str = "",
                        key: str = "") -> None:
        await ws.accept()
        # Output keys gate the wall only. Phones stay anonymous and public;
        # a wall is an output somebody set up, and the key is how it is
        # switched off again without touching the URL in ProPresenter or OBS.
        # Accepted first so the 4403 reaches the page: a refused handshake is
        # indistinguishable from the relay being down.
        if role == "wall" and cfg.overlay_keys and key not in cfg.overlay_keys.values():
            await ws.close(code=4403, reason="output key required")
            print(f"[relay] refused wall {peer_of(ws)}: "
                  f"{'no' if not key else 'unknown'} output key")
            return
        client = hub.add_viewer(role, peer_of(ws), lang=lang)
        client.key = key or None
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
