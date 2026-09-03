r"""End-to-end check of the relay against a real uvicorn process.

    .venv/bin/python tools/smoke_test.py "$(pwd)"          # macOS / Linux
    .venv\Scripts\python.exe tools\smoke_test.py %cd%       # Windows

Starts the relay on port 8765 with fixed test tokens and a temporary copy of
config/event.example.toml, then checks operator sign-in and the cookie
exchange, public-hostname 404s, CSRF cases, every input-validation rejection,
websocket accept/reject paths, the event state and welcome/logo images, the
operator caption action, transcript export rules, security headers and the
hashed Content-Security-Policy, body and frame size limits, and the QR
guards. Needs only the standard library plus `websockets` (in
requirements.txt). Nothing it writes survives: the temp config is deleted and
the relay is stopped; it never touches the checkout's .env or event.toml
because both tokens are passed in the environment.

Exit status is 0 only when every check passes.
"""
import asyncio, json, os, shutil, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request
import websockets

ROOT = sys.argv[1]
PY = sys.executable
PORT = 8765
OPTOK = "op-token-for-test-1234567890"
SRCTOK = "src-token-for-test-0987654321"
PUBLIC = "captions.example.com"
BASE = f"http://127.0.0.1:{PORT}"
failures = []

def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  -- {detail}"))
    if not cond:
        failures.append(name)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None
opener = urllib.request.build_opener(NoRedirect)

def http(method, path, body=None, headers=None, host=None, base=None):
    h = dict(headers or {})
    if host:
        h["Host"] = host
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request((base or BASE) + path, data=data, method=method, headers=h)
    try:
        with opener.open(req, timeout=5) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()

tmp = tempfile.mkdtemp()
cfg_path = os.path.join(tmp, "event.toml")
shutil.copy(os.path.join(ROOT, "config", "event.example.toml"), cfg_path)
env = {**os.environ, "CAPTIONS_OPERATOR_TOKEN": OPTOK, "CAPTIONS_SOURCE_TOKEN": SRCTOK,
       "CAPTIONS_PUBLIC_HOSTNAME": PUBLIC, "PYTHONUTF8": "1"}
proc = subprocess.Popen([PY, "-u", "-m", "relay", "--port", str(PORT), "--config", cfg_path],
                        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
try:
    deadline = time.time() + 20
    while time.time() < deadline:
        with socket.socket() as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", PORT)) == 0:
                break
        if proc.poll() is not None:
            print(proc.stdout.read()); sys.exit("relay exited early")
        time.sleep(0.3)
    time.sleep(0.5)

    # --- pages -----------------------------------------------------------
    st, h, b = http("GET", "/status")
    check("GET /status without token -> 401 login page", st == 401 and b"operator" in b, f"{st}")
    st, h, b = http("GET", "/status?token=wrong")
    check("GET /status?token=wrong -> 401", st == 401, f"{st}")
    st, h, b = http("GET", f"/status?token={OPTOK}&x=1")
    cookie_hdr = h.get("set-cookie", "")
    check("GET /status?token=good -> 303 to /status?x=1", st == 303 and h.get("location") == "/status?x=1", f"{st} {h.get('Location')}")
    check("  sets HttpOnly SameSite=strict cookie", "captions_operator=" in cookie_hdr and "HttpOnly" in cookie_hdr and "samesite=strict" in cookie_hdr.lower(), cookie_hdr)
    cookie = {"Cookie": f"captions_operator={OPTOK}"}
    st, h, b = http("GET", "/status", headers=cookie)
    check("GET /status with cookie -> 200 dashboard", st == 200 and b"Campus Captions" in b, f"{st}")
    st, h, b = http("GET", "/tune", headers=cookie)
    check("GET /tune with cookie -> 200", st == 200, f"{st}")
    st, h, b = http("GET", "/status.html")
    check("GET /status.html without token -> 401", st == 401, f"{st}")
    st, h, b = http("GET", "/phone")
    check("GET /phone stays public -> 200", st == 200, f"{st}")
    st, h, b = http("GET", "/")
    check("GET / local -> 303 /status", st == 303 and h.get("location") == "/status", f"{st}")
    st, h, b = http("GET", "/docs")
    check("GET /docs is not the API explorer", st in (401, 404) and b"swagger" not in b.lower(), f"{st}")
    st, h, b = http("GET", "/docs", headers={"Authorization": f"Bearer {OPTOK}"})
    check("GET /docs with token -> 404, no API explorer", st == 404 and b"swagger" not in b.lower(), f"{st}")
    st, h, b = http("GET", "/openapi.json", headers={"Authorization": f"Bearer {OPTOK}"})
    check("GET /openapi.json with token -> 404", st == 404 and b"paths" not in b, f"{st}")

    # --- security headers, CSP, size limits, QR guards --------------------------
    st, h, b = http("GET", "/phone")
    csp = h.get("content-security-policy", "")
    check("phone page carries a hashed script-src CSP", "script-src 'sha256-" in csp and "frame-ancestors 'self'" in csp and "'unsafe-inline'" not in csp.split("style-src")[0], csp[:160])
    import hashlib, base64, re as _re
    inline = _re.findall(rb"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", b, _re.S)
    digest = "'sha256-" + base64.b64encode(hashlib.sha256(inline[0]).digest()).decode() + "'"
    check("  CSP hash matches the page's inline script", digest in csp, digest)
    check("  nosniff / referrer / frame headers present", h.get("x-content-type-options") == "nosniff" and h.get("referrer-policy") == "no-referrer" and h.get("x-frame-options") == "SAMEORIGIN", str({k: h.get(k) for k in ("x-content-type-options", "referrer-policy", "x-frame-options")}))
    st, h, b = http("GET", "/api/site")
    check("JSON responses carry the strict CSP", h.get("content-security-policy", "").startswith("default-src 'none'"), h.get("content-security-policy", ""))
    big = json.dumps({"action": "caption", "text": "x" * 20000}).encode()
    st, h, b = http("POST", "/api/control", body=big, headers={"Authorization": f"Bearer {OPTOK}"})
    check("POST body over 16 KiB -> 413", st == 413, f"{st}")
    st, h, b = http("GET", "/api/qr.svg?url=https://evil.example/phish")
    check("qr.svg with a foreign url -> 400", st == 400, f"{st}")
    st, h, b = http("GET", "/api/qr.svg?dark=red")
    check("qr.svg with a bad colour -> 400", st == 400, f"{st}")
    st, h, b = http("GET", "/api/qr.svg")
    check("qr.svg default -> 200 svg", st == 200 and b"<svg" in b, f"{st}")

    # --- public hostname stays 404 even with a token -----------------------
    bearer = {"Authorization": f"Bearer {OPTOK}"}
    st, h, b = http("GET", "/status", headers={**bearer, **cookie}, host=PUBLIC)
    check("GET /status via public hostname with token -> 404", st == 404, f"{st}")
    st, h, b = http("GET", "/api/status", headers=bearer, host=PUBLIC)
    check("GET /api/status via public hostname -> 404", st == 404, f"{st}")
    st, h, b = http("GET", "/", host=PUBLIC)
    check("GET / via public hostname -> phone page", st == 200 and b"<html" in b.lower(), f"{st}")

    # --- API auth ------------------------------------------------------------
    st, h, b = http("GET", "/api/status")
    check("GET /api/status no auth -> 401", st == 401, f"{st}")
    st, h, b = http("GET", "/api/status", headers=bearer)
    snap = json.loads(b) if st == 200 else {}
    check("GET /api/status bearer -> 200", st == 200 and "session_id" in snap, f"{st}")
    check("  youtube status carries url_hint, never url", "url" not in snap.get("youtube", {}) and "url_hint" in snap.get("youtube", {}), str(snap.get("youtube")))
    st, h, b = http("GET", "/api/transcript.srt")
    check("GET /api/transcript.srt no auth -> 401", st == 401, f"{st}")

    # --- CSRF ---------------------------------------------------------------
    body = json.dumps({"action": "clear"}).encode()
    st, h, b = http("POST", "/api/control", body=body, headers={"Content-Type": "text/plain"})
    check("POST text/plain without cookie -> 401", st == 401, f"{st}")
    st, h, b = http("POST", "/api/control", body=body, headers={**cookie, "Content-Type": "text/plain", "Sec-Fetch-Site": "cross-site"})
    check("POST with cookie but Sec-Fetch-Site: cross-site -> 401", st == 401, f"{st}")
    st, h, b = http("POST", "/api/control", body={"action": "clear"}, headers={**cookie, "Sec-Fetch-Site": "same-origin"})
    check("POST with cookie same-origin -> 200", st == 200, f"{st} {b[:80]}")
    st, h, b = http("POST", "/api/control", body={"action": "clear"}, headers=bearer)
    check("POST with bearer -> 200", st == 200, f"{st}")

    # --- input validation -----------------------------------------------------
    st, h, b = http("POST", "/api/translation", body={"key": "abc\nAZURE_TRANSLATOR_ENDPOINT=http://evil"}, headers=bearer)
    check("translation key with newline -> 400", st == 400 and b"control" in b, f"{st} {b[:100]}")
    st, h, b = http("POST", "/api/control", body={"action": "set_deepgram_key", "key": "k\r\nX=1"}, headers=bearer)
    check("deepgram key with CRLF -> 400", st == 400, f"{st}")
    st, h, b = http("POST", "/api/tunnel", body={"token": "t\nTUNNEL_URL=x"}, headers=bearer)
    check("tunnel token with newline -> 400", st == 400, f"{st}")
    st, h, b = http("POST", "/api/tunnel", body={"hostname": "bad host!"}, headers=bearer)
    check("tunnel hostname with junk -> 400", st == 400, f"{st}")
    st, h, b = http("POST", "/api/youtube-captions", body={"url": "http://evil.example/collect"}, headers=bearer)
    check("youtube url on foreign host -> 400", st == 400, f"{st} {b[:100]}")
    st, h, b = http("POST", "/api/youtube-captions", body={"url": "http://127.0.0.1:8000/api/control"}, headers=bearer)
    check("youtube url to loopback -> 400", st == 400, f"{st}")
    st, h, b = http("POST", "/api/youtube-captions", body={"url": "http://upload.youtube.com/closedcaption?cid=abcd1234wxyz", "lang": "en\nUS"}, headers=bearer)
    check("youtube lang with newline -> 400", st == 400, f"{st}")
    st, h, b = http("POST", "/api/youtube-captions", body={"url": "http://upload.youtube.com/closedcaption?cid=abcd1234wxyz"}, headers=bearer)
    out = json.loads(b) if st == 200 else {}
    check("youtube url on upload.youtube.com -> 200 with masked hint", st == 200 and out.get("url_hint", "").endswith("wxyz") and "abcd1234" not in out.get("url_hint", "") and "url" not in out, f"{st} {out}")
    with open(cfg_path, encoding="utf-8") as fh:
        saved = fh.read()
    check("  url saved into the temp event.toml, not the tracked example", "abcd1234wxyz" in saved, "")
    with open(os.path.join(ROOT, "config", "event.example.toml"), encoding="utf-8") as fh:
        check("  tracked example config untouched", "abcd1234wxyz" not in fh.read(), "")

    # --- event state, welcome image, operator captions -----------------------------
    st, h, b = http("GET", "/api/site")
    site = json.loads(b)
    check("GET /api/site carries event state (starting)", st == 200 and site.get("event", {}).get("state") == "starting", str(site.get("event")))
    st, h, b = http("POST", "/api/event", body={"state": "bogus"}, headers=bearer)
    check("POST /api/event bad state -> 400", st == 400, f"{st}")
    st, h, b = http("POST", "/api/event", body={"title": "Fall Convocation", "notice": "Captions are automatic.", "message": "x\ny"}, headers=bearer)
    check("POST /api/event message with newline -> 400", st == 400, f"{st}")
    st, h, b = http("POST", "/api/event", body={"title": "Fall Convocation", "notice": "Captions are automatic."}, headers=bearer)
    out = json.loads(b) if st == 200 else {}
    check("POST /api/event title+notice -> 200, still starting", st == 200 and out.get("title") == "Fall Convocation" and out.get("state") == "starting", f"{st} {out}")
    st, h, b = http("POST", "/api/event", body={"state": "live"}, headers=bearer)
    check("POST /api/event state live -> 200", st == 200 and json.loads(b).get("state") == "live", f"{st}")
    st, h, b = http("POST", "/api/event", body={"state": "live"})
    check("POST /api/event without token -> 401", st == 401, f"{st}")
    with open(cfg_path, encoding="utf-8") as fh:
        saved = fh.read()
    check("  event title and state persisted to event.toml", 'title = "Fall Convocation"' in saved and 'state = "live"' in saved, saved[-300:])

    import struct, zlib
    def png_1x1():
        def chunk(tag, data):
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
        ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        idat = zlib.compress(b"\x00\x00\x00\x00")
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    st, h, b = http("GET", "/api/event/image")
    check("GET /api/event/image with none set -> 404", st == 404, f"{st}")
    st, h, b = http("PUT", "/api/event/image", body=b"GIF89a not really", headers={**bearer, "Content-Type": "image/gif"})
    check("PUT non-JPEG/PNG/WebP image -> 400", st == 400, f"{st} {b[:80]}")
    st, h, b = http("PUT", "/api/event/image", body=b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", headers={**bearer, "Content-Type": "image/png"})
    check("PUT SVG disguised as PNG -> 400 (sniffed)", st == 400, f"{st}")
    st, h, b = http("PUT", "/api/event/image", body=png_1x1(), headers={**bearer, "Content-Type": "image/png"})
    out = json.loads(b) if st == 200 else {}
    check("PUT real PNG -> 200 image true", st == 200 and out.get("image") is True and out.get("image_v", 0) > 0, f"{st} {out}")
    st, h, b = http("GET", "/api/event/image")
    check("GET /api/event/image public -> 200 image/png", st == 200 and h.get("content-type", "").startswith("image/png") and b[:4] == b"\x89PNG", f"{st} {h.get('content-type')}")
    st, h, b = http("PUT", "/api/event/image", body=png_1x1(), headers={"Content-Type": "image/png"})
    check("PUT image without token -> 401", st == 401, f"{st}")
    st, h, b = http("DELETE", "/api/event/image", headers=bearer)
    check("DELETE image -> 200 image false", st == 200 and json.loads(b).get("image") is False, f"{st}")
    check("  welcome image file removed from repo config dir", not any(os.path.exists(os.path.join(ROOT, "config", f"welcome.{e}")) for e in ("jpg", "png", "webp")), "")

    st, h, b = http("PUT", "/api/event/logo", body=png_1x1(), headers={**bearer, "Content-Type": "image/png"})
    out = json.loads(b) if st == 200 else {}
    check("PUT logo -> 200 logo true", st == 200 and out.get("logo") is True and out.get("logo_v", 0) > 0, f"{st} {out}")
    st, h, b = http("POST", "/api/event", body={"logo_alt": "Faulkner University"}, headers=bearer)
    check("POST logo_alt -> 200", st == 200 and json.loads(b).get("logo_alt") == "Faulkner University", f"{st}")
    st, h, b = http("GET", "/api/event/logo")
    check("GET /api/event/logo public -> 200 image/png", st == 200 and h.get("content-type", "").startswith("image/png"), f"{st}")
    st, h, b = http("GET", "/api/site")
    check("  /api/site event carries logo and logo_alt", json.loads(b).get("event", {}).get("logo") is True and json.loads(b)["event"].get("logo_alt") == "Faulkner University", b[:200])
    st, h, b = http("DELETE", "/api/event/logo", headers=bearer)
    check("DELETE logo -> 200 logo false", st == 200 and json.loads(b).get("logo") is False, f"{st}")
    check("  logo file removed from repo config dir", not any(os.path.exists(os.path.join(ROOT, "config", f"logo.{e}")) for e in ("jpg", "png", "webp")), "")
    st, h, b = http("GET", "/api/event/logo")
    check("GET /api/event/logo after delete -> 404", st == 404, f"{st}")

    st, h, b = http("POST", "/api/control", body={"action": "caption", "text": ""}, headers=bearer)
    check("caption with empty text -> 400", st == 400, f"{st}")
    st, h, b = http("POST", "/api/control", body={"action": "caption", "text": "[Applause]"}, headers=bearer)
    check("caption [Applause] -> 200", st == 200, f"{st} {b[:80]}")
    st, h, b = http("GET", "/api/status", headers=bearer)
    snap = json.loads(b)
    check("  operator caption became the last final", snap.get("asr", {}).get("last_final_text") == "[Applause]", str(snap.get("asr", {}).get("last_final_text")))
    check("  status snapshot carries event and health", snap.get("event", {}).get("state") == "live" and snap.get("health", {}).get("fault") == "no_source", f"{snap.get('event')} {snap.get('health')}")

    # --- transcript export follows caption presentation rules ------------------
    http("POST", "/api/control", body={"action": "mark_video_start"}, headers=bearer)
    time.sleep(1.0)   # lines committed before the video start (plus offset) are rightly excluded
    http("POST", "/api/control", body={"action": "caption", "text": "We expect to break ground on the new science building next spring, with classes meeting there by the fall of the following year if the schedule holds."}, headers=bearer)
    st, h, b = http("GET", "/api/transcript.srt?lang=en&offset=0.5", headers=bearer)
    srt = b.decode("utf-8", "replace")
    cue_lines = [l for l in srt.splitlines() if l and not l.isdigit() and "-->" not in l]
    check("GET /api/transcript.srt -> 200 with cues", st == 200 and "-->" in srt, f"{st}")
    check("  every caption line is at most 42 characters", all(len(l) <= 42 for l in cue_lines), str([l for l in cue_lines if len(l) > 42]))
    check("  the long caption was split across cues (4 wrapped lines -> 2 cues)", srt.count("-->") == 2, str(srt.count("-->")))
    st, h, b = http("GET", "/api/transcript.srt?lang=en&offset=99", headers=bearer)
    check("  offset out of range -> 400", st == 400, f"{st}")

    # --- websockets -------------------------------------------------------------
    async def ws_checks():
        async def try_ws(path, **kw):
            try:
                async with websockets.connect(f"ws://127.0.0.1:{PORT}{path}", open_timeout=5, **kw) as ws:
                    try:
                        first = await asyncio.wait_for(ws.recv(), timeout=2)
                    except asyncio.TimeoutError:
                        first = None
                    return "open", first
            except websockets.exceptions.InvalidStatus as e:
                return "rejected", e.response.status_code
            except Exception as e:
                return "error", repr(e)
        r = await try_ws("/ws/source")
        check("WS /ws/source without token rejected", r[0] == "rejected", str(r))
        r = await try_ws("/ws/source?token=wrong")
        check("WS /ws/source wrong token rejected", r[0] == "rejected", str(r))
        r = await try_ws("/ws/source", additional_headers={"Authorization": f"Bearer {SRCTOK}"}, origin="http://evil.example")
        check("WS /ws/source good token but foreign Origin rejected", r[0] == "rejected", str(r))
        r = await try_ws("/ws/source", additional_headers={"Authorization": f"Bearer {SRCTOK}"})
        check("WS /ws/source with bearer token accepted", r[0] == "open", str(r))
        r = await try_ws(f"/ws/source?token={SRCTOK}")
        check("WS /ws/source with ?token= still accepted", r[0] == "open", str(r))
        r = await try_ws("/ws/status")
        check("WS /ws/status without cookie rejected", r[0] == "rejected", str(r))
        r = await try_ws("/ws/status", additional_headers={"Cookie": f"captions_operator={OPTOK}"}, origin="http://evil.example")
        check("WS /ws/status with cookie but foreign Origin rejected", r[0] == "rejected", str(r))
        r = await try_ws("/ws/status", additional_headers={"Cookie": f"captions_operator={OPTOK}"}, origin=f"http://127.0.0.1:{PORT}")
        check("WS /ws/status with cookie and own Origin -> snapshot", r[0] == "open" and r[1] and "session_id" in r[1], str(r)[:120])
        # oversized frames and chatty viewers are closed, not parsed
        async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws/source", additional_headers={"Authorization": f"Bearer {SRCTOK}"}, max_size=None) as ws:
            await ws.send(json.dumps({"type": "final", "text": "y" * 70000}))
            try:
                await asyncio.wait_for(ws.recv(), timeout=3)
                closed = False
            except websockets.exceptions.ConnectionClosed:
                closed = True
            except asyncio.TimeoutError:
                closed = False
        check("WS source frame over 64 KiB is refused (connection closed)", closed, "still open")
        async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws/viewer?role=phone") as ws:
            await ws.recv()   # hello
            await ws.send("hello relay")
            try:
                while True:
                    await asyncio.wait_for(ws.recv(), timeout=3)
                closed = False
            except websockets.exceptions.ConnectionClosed as e:
                closed = e.rcvd is not None and e.rcvd.code == 1008
            except asyncio.TimeoutError:
                closed = False
        check("WS viewer that sends data is closed with 1008", closed, "not closed")
        r = await try_ws("/ws/viewer?role=phone")
        check("WS /ws/viewer stays public", r[0] == "open", str(r)[:120])
        hello = json.loads(r[1]) if r[0] == "open" and r[1] else {}
        check("  hello carries event state", hello.get("event", {}).get("state") == "live", str(hello.get("event")))
        # a viewer sees the event change push and the replayed operator caption
        async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws/viewer?role=phone") as ws:
            frames = []
            deadline = time.time() + 2
            while time.time() < deadline:
                try:
                    frames.append(json.loads(await asyncio.wait_for(ws.recv(), timeout=0.5)))
                except asyncio.TimeoutError:
                    break
            replayed = [f for f in frames if f.get("type") == "final" and f.get("replay")]
            check("  replay includes the operator caption with kind=note", any(f.get("text") == "[Applause]" and f.get("kind") == "note" for f in replayed), str(replayed)[:200])
            http("POST", "/api/event", body={"state": "paused", "pause_message": "Intermission"}, headers=bearer)
            got = None
            deadline = time.time() + 3
            while time.time() < deadline and got is None:
                try:
                    f = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
                    if f.get("type") == "event":
                        got = f
                except asyncio.TimeoutError:
                    break
            check("  viewer receives event frame on pause", got is not None and got.get("state") == "paused" and got.get("pause_message") == "Intermission", str(got))
            hb = None
            deadline = time.time() + 7
            while time.time() < deadline and hb is None:
                try:
                    f = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
                    if f.get("type") == "heartbeat":
                        hb = f
                except asyncio.TimeoutError:
                    pass
            check("  heartbeat carries event and health.fault=no_source", hb is not None and hb.get("event", {}).get("state") == "paused" and hb.get("health", {}).get("fault") == "no_source", str(hb)[:200])
        http("POST", "/api/event", body={"state": "live"}, headers=bearer)
        # a pushed caption from an authenticated source shows up in status
        async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws/source", additional_headers={"Authorization": f"Bearer {SRCTOK}"}) as ws:
            await ws.send(json.dumps({"type": "final", "text": "hello from the test"}))
            await asyncio.sleep(0.3)
        st, h, b = http("GET", "/api/status", headers=bearer)
        snap = json.loads(b)
        check("caption from authenticated source reached the relay", snap.get("asr", {}).get("last_final_text") == "hello from the test", str(snap.get("asr")))
    asyncio.run(ws_checks())

    # --- changing the port from the Settings page ------------------------------
    # Last, because the relay restarts on another port and we move it back.
    st, h, b = http("POST", "/api/relay", body={"port": 80}, headers=bearer)
    check("POST /api/relay port 80 -> 400", st == 400, f"{st}")
    st, h, b = http("POST", "/api/relay", body={"port": PORT}, headers=bearer)
    check("POST /api/relay same port -> 200, changed false", st == 200 and json.loads(b).get("changed") is False, f"{st} {b[:80]}")
    holder = socket.socket(); holder.bind(("127.0.0.1", PORT + 2)); holder.listen(1)
    st, h, b = http("POST", "/api/relay", body={"port": PORT + 2}, headers=bearer)
    check("POST /api/relay to a port something else holds -> 409", st == 409, f"{st}")
    holder.close()
    st, h, b = http("POST", "/api/relay", body={"port": PORT + 1})
    check("POST /api/relay without auth -> 401", st == 401, f"{st}")

    async def move_port():
        # A capture client connected during the move must be told where to go.
        async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws/source", additional_headers={"Authorization": f"Bearer {SRCTOK}"}) as ws:
            st, h, b = http("POST", "/api/relay", body={"port": PORT + 1}, headers=bearer)
            out = json.loads(b) if st == 200 else {}
            check("POST /api/relay new port -> 200, changed, capture client told", st == 200 and out.get("changed") is True and out.get("port") == PORT + 1 and out.get("capture_clients_told") == 1, f"{st} {out}")
            told = None
            try:
                while True:
                    m = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    if m.get("type") == "control" and m.get("action") == "relay_moving":
                        told = m; break
            except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
                pass
            check("  capture client received relay_moving with the new port", told is not None and told.get("port") == PORT + 1, str(told))
    asyncio.run(move_port())

    new_base = f"http://127.0.0.1:{PORT + 1}"
    came_up = False
    for _ in range(40):
        time.sleep(0.5)
        try:
            with urllib.request.urlopen(new_base + "/api/site", timeout=1) as r:
                came_up = r.status == 200
                break
        except Exception:
            continue
    check("  relay came back on the new port", came_up, "no answer on the new port within 20 s")
    with open(cfg_path, encoding="utf-8") as fh:
        saved = fh.read()
    check("  new port was saved to event.toml", f"port = {PORT + 1}" in saved, saved[:200])
    with socket.socket() as s:
        s.settimeout(0.5)
        check("  old port is no longer answering", s.connect_ex(("127.0.0.1", PORT)) != 0, "still open")
    st, h, b = http("GET", "/api/status", headers=bearer, base=new_base)
    check("  operator token still works on the new port", st == 200 and json.loads(b).get("relay", {}).get("port") == PORT + 1, f"{st}")
    # Move back so the relay's own output at teardown is on the expected port.
    http("POST", "/api/relay", body={"port": PORT}, headers=bearer, base=new_base)
    for _ in range(40):
        time.sleep(0.5)
        try:
            with urllib.request.urlopen(BASE + "/api/site", timeout=1) as r:
                if r.status == 200:
                    break
        except Exception:
            continue
    st, h, b = http("GET", "/api/site")
    check("  and back again on the original port", st == 200, f"{st}")
finally:
    proc.terminate()
    try:
        out = proc.communicate(timeout=5)[0]
    except subprocess.TimeoutExpired:
        proc.kill(); out = proc.communicate()[0]
    print("---- relay output ----")
    print("\n".join(l for l in out.splitlines() if l.startswith("[relay]"))[:3000])
    shutil.rmtree(tmp, ignore_errors=True)

print()
print(f"{len(failures)} failure(s)" if failures else "ALL CHECKS PASSED")
sys.exit(1 if failures else 0)
