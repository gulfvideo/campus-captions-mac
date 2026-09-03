"""Direct caption output into a local ProPresenter over its network API.

ProPresenter 7.9 and later (and the year-numbered releases that followed,
18 through 21) expose an HTTP API on the LAN - Settings -> Network, tick
"Enable Network", note the port. Everything here speaks that API and nothing
else: no slides, playlists, props or macros are ever touched. Two caption
surfaces are supported, chosen by `surface`:

    stage_message   PUT /v1/stage/message with the caption text, DELETE to
                    clear. One text box, rendered by any stage layout that
                    includes a Stage Message element. Touches nothing on the
                    audience side unless the church routes a stage layout to
                    an audience screen, which is a ProPresenter-side choice.

    message         POST /v1/message/{id}/trigger with one token filled in;
                    GET /v1/message/{id}/clear to clear. A reusable Message
                    (we look it up by name) with its own theme, screens and
                    position - the church designs "Live Captions" once, and
                    the caption rides in its one text token.

Which of the two is the right N-05 mechanism is the N-04 spike's question,
answered on a real ProPresenter (docs/PROPRESENTER_SPIKE.md). This module
makes either a one-line change of config so the spike is about behaviour,
not plumbing.

Endpoint shapes follow the API v1 reference ProPresenter serves from its own
Network settings ("API Documentation"). tests/fake_propresenter.py imitates
exactly those shapes; if the real thing disagrees, fix both.

Design notes:

* Finals only by default. Interims can be sent (`interims = true`) but only
  the newest pending one is ever delivered - a slot, not a queue - because a
  presentation system redrawing a text box twenty times a second is exactly
  the "visual stability" problem N-04 asks about.
* Every call is timed. `status()` reports the median and p95 of the round
  trip, which is the N-06 "no more than 500 ms added at p95" figure, measured
  rather than asserted.
* Failure never propagates. Phones, translation, the recorder and YouTube
  carry on; this output degrades to `disconnected` and keeps probing.
* Nothing here is ever logged with caption text or a credential in it.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque

SURFACES = ("stage_message", "message")
DEFAULT_PORT = 1025
REQUEST_TIMEOUT = 2.0
PROBE_INTERVAL = 10.0            # seconds between health checks while connected
RETRY_INTERVAL = 4.0             # seconds between attempts while disconnected
DEGRADED_AFTER = 3               # consecutive push failures before "disconnected"
LATENCY_WINDOW = 200
MAX_TEXT_CHARS = 500             # a caption line, not a paragraph

# The API this module was written against. `GET /version` reports
# `api_version`; anything else is flagged, not refused - the spike decides.
SUPPORTED_API = "v1"

# ProPresenter serves its version at the root, not under the version prefix
# every other endpoint uses - /v1/version is a 404 while /v1/messages and
# /v1/stage/message are fine. Verified against ProPresenter 21.4, which
# answers /version with {"api_version": "v1", ...}. Kept as a constant so the
# probe and the message it reports on failure cannot drift apart.
VERSION_PATH = "/version"

STATES = ("off", "connecting", "connected", "degraded", "disconnected", "unsupported")


class ProPresenterOutput:
    def __init__(self, host: str = "127.0.0.1", port: int = DEFAULT_PORT,
                 surface: str = "stage_message", message: str = "Live Captions",
                 token: str = "Caption", language: str = "", interims: bool = False,
                 start_enabled: bool = False):
        self.host = (host or "").strip()
        self.port = int(port or DEFAULT_PORT)
        self.surface = surface if surface in SURFACES else "stage_message"
        self.message = (message or "").strip()
        self.token = (token or "").strip()
        self.language = (language or "").strip()      # "" = the source language
        self.interims = bool(interims)
        self.enabled = bool(start_enabled) and self.configured

        self.state = "off"
        self.version: dict | None = None
        self.message_id: str | None = None
        self.error: str | None = None
        self.pushed = 0
        self.failures = 0
        self.cleared = 0
        self._consecutive_failures = 0
        self._latencies: deque[float] = deque(maxlen=LATENCY_WINDOW)
        self._last_probe_at = 0.0

        self._finals: asyncio.Queue = asyncio.Queue(maxsize=32)
        self._pending_interim: str | None = None
        self._wake: asyncio.Event | None = None
        self._worker: asyncio.Task | None = None
        self._prober: asyncio.Task | None = None

    # -- configuration -----------------------------------------------------

    @property
    def configured(self) -> bool:
        return bool(self.host and self.port)

    def configure(self, **changes) -> None:
        """Apply any subset of host, port, surface, message, token, language,
        interims. A changed host/port/surface/message forgets the resolved
        message id and the version so the next probe re-checks them."""
        reconnect = False
        for key in ("host", "port", "surface", "message", "token", "language", "interims"):
            if key not in changes or changes[key] is None:
                continue
            value = changes[key]
            if key == "port":
                value = int(value)
            elif key == "interims":
                value = bool(value)
            elif key == "surface":
                if value not in SURFACES:
                    raise ValueError(f"surface must be one of {', '.join(SURFACES)}")
            else:
                value = str(value).strip()
            if getattr(self, key) != value:
                setattr(self, key, value)
                if key in ("host", "port", "surface", "message"):
                    reconnect = True
        if reconnect:
            self.version = None
            self.message_id = None
            self.error = None
            if self.enabled:
                self.state = "connecting"
                self._last_probe_at = 0.0       # probe again on the next tick
        if not self.configured:
            self.enabled = False
            self.state = "off"

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        if not self.configured:
            self.error = "set a host and port first"
            return
        self.enabled = True
        self.state = "connecting"
        self.error = None
        if self._wake is None:
            self._wake = asyncio.Event()
        if self._worker is None:
            self._worker = asyncio.create_task(self._run_worker())
        if self._prober is None:
            self._prober = asyncio.create_task(self._run_prober())
        await self.probe()

    async def stop(self, clear_surface: bool = True) -> None:
        """Operator turned it off. The caption surface is cleared on the way
        out so a stale last line does not sit on the screen (N-05: pausing
        or ending clears cleanly)."""
        self.enabled = False
        if clear_surface and self.state in ("connected", "degraded"):
            try:
                await self.clear()
            except Exception:
                pass
        for attr in ("_worker", "_prober"):
            task = getattr(self, attr)
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                setattr(self, attr, None)
        self._pending_interim = None
        while not self._finals.empty():
            self._finals.get_nowait()
        self.state = "off"

    # -- input -------------------------------------------------------------

    def submit(self, text: str, is_final: bool) -> None:
        """Called for every caption on the configured language. Cheap: it
        only queues. Interims replace each other; finals line up."""
        if not self.enabled:
            return
        text = " ".join(str(text).split())[:MAX_TEXT_CHARS]
        if not text:
            return
        if is_final:
            self._pending_interim = None       # the final supersedes it
            try:
                self._finals.put_nowait(text)
            except asyncio.QueueFull:
                # Way behind: showing the newest line matters more than
                # showing every line. Drop the oldest.
                try:
                    self._finals.get_nowait()
                    self._finals.put_nowait(text)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass
        elif self.interims:
            self._pending_interim = text
        else:
            return
        if self._wake is not None:
            self._wake.set()

    # -- HTTP (blocking; always run in an executor) ---------------------------

    def _url(self, path: str) -> str:
        return f"http://{self.host}:{self.port}/{path.lstrip('/')}"

    def _call(self, method: str, path: str, body=None) -> tuple[int, object]:
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self._url(path), data=data, method=method,
                                         headers=headers)
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                raw = response.read()
                code = response.status
        except urllib.error.HTTPError as exc:
            raw = b""
            try:
                raw = exc.read()
            except Exception:
                pass
            finally:
                exc.close()
            code = exc.code
        finally:
            self._latencies.append(time.perf_counter() - started)
        parsed: object = None
        if raw:
            try:
                parsed = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                parsed = raw.decode("utf-8", "replace")[:200]
        return code, parsed

    async def _request(self, method: str, path: str, body=None) -> tuple[int, object]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._call, method, path, body)

    # -- probing -----------------------------------------------------------

    async def probe(self) -> bool:
        """Is ProPresenter there, is its API the one we know, and (for the
        message surface) does the named Message exist? Sets state; never
        raises. Returns True when captions can be delivered right now."""
        self._last_probe_at = time.time()
        if not self.configured:
            self.state = "off"
            return False
        try:
            code, version = await self._request("GET", VERSION_PATH)
        except Exception as exc:
            return self._offline(f"{exc.__class__.__name__}: {exc}")
        if code != 200 or not isinstance(version, dict):
            return self._offline(f"GET {VERSION_PATH} answered {code}")
        self.version = {k: version.get(k) for k in
                        ("name", "platform", "os_version", "host_description", "api_version")
                        if k in version}
        api = str(version.get("api_version") or "")
        if api and not api.lower().startswith(SUPPORTED_API):
            self.state = "unsupported"
            self.error = (f"ProPresenter reports API {api}; this build knows "
                          f"{SUPPORTED_API} - captions may not render")
            return False

        if self.surface == "message":
            ok = await self._resolve_message()
            if not ok:
                return False

        if self.state != "degraded" or self._consecutive_failures == 0:
            self.state = "connected"
        self.error = None
        return True

    async def _resolve_message(self) -> bool:
        """Find the Message called `self.message` and remember its id. The
        church creates it once in ProPresenter; we never create or edit it."""
        if not self.message:
            self.state = "degraded"
            self.error = "name the ProPresenter Message that carries the caption token"
            return False
        try:
            code, listing = await self._request("GET", "/v1/messages")
        except Exception as exc:
            return self._offline(f"{exc.__class__.__name__}: {exc}")
        if code != 200 or not isinstance(listing, list):
            return self._offline(f"GET /v1/messages answered {code}")
        wanted = self.message.strip().lower()
        for item in listing:
            ident = (item or {}).get("id") or {}
            name = str(ident.get("name") or "")
            if name.strip().lower() == wanted:
                self.message_id = str(ident.get("uuid") or ident.get("index") or name)
                tokens = [str((t or {}).get("name") or "") for t in (item.get("tokens") or [])]
                if self.token and tokens and self.token not in tokens:
                    self.state = "degraded"
                    self.error = (f"Message '{name}' has no token called '{self.token}' "
                                  f"(it has: {', '.join(tokens) or 'none'})")
                    return False
                return True
        self.message_id = None
        self.state = "degraded"
        self.error = (f"no Message called '{self.message}' in ProPresenter - create one "
                      f"with a text token named '{self.token}'")
        return False

    def _offline(self, reason: str) -> bool:
        self.state = "disconnected"
        self.error = reason
        self.version = None
        self.message_id = None
        return False

    async def _run_prober(self) -> None:
        while True:
            interval = PROBE_INTERVAL if self.state in ("connected", "degraded") else RETRY_INTERVAL
            await asyncio.sleep(interval)
            if not self.enabled:
                continue
            if time.time() - self._last_probe_at >= interval - 0.5:
                await self.probe()

    # -- delivery ----------------------------------------------------------

    async def _run_worker(self) -> None:
        assert self._wake is not None
        while True:
            await self._wake.wait()
            self._wake.clear()
            # Finals first, in order; then whatever interim is newest.
            while not self._finals.empty():
                text = self._finals.get_nowait()
                await self._deliver(text)
            if self._pending_interim is not None:
                text, self._pending_interim = self._pending_interim, None
                await self._deliver(text)

    async def _deliver(self, text: str) -> None:
        if not self.enabled or self.state in ("off", "unsupported"):
            return
        if self.state == "disconnected":
            # Do not hammer a closed app with every caption; the prober
            # will notice when it is back.
            return
        try:
            if self.surface == "stage_message":
                code, _ = await self._request("PUT", "/v1/stage/message", text)
            else:
                if self.message_id is None and not await self._resolve_message():
                    return
                body = [{"name": self.token, "text": {"text": text}}]
                code, _ = await self._request(
                    "POST", f"/v1/message/{urllib.parse.quote(self.message_id)}/trigger", body)
        except Exception as exc:
            return self._push_failed(f"{exc.__class__.__name__}: {exc}")
        if 200 <= code < 300:
            self.pushed += 1
            self._consecutive_failures = 0
            if self.state == "degraded":
                self.state = "connected"
                self.error = None
            return
        self._push_failed(f"ProPresenter answered {code}")

    def _push_failed(self, reason: str) -> None:
        self.failures += 1
        self._consecutive_failures += 1
        self.error = reason
        if self._consecutive_failures >= DEGRADED_AFTER:
            self.state = "disconnected"
            self._last_probe_at = 0.0
        else:
            self.state = "degraded"

    async def clear(self) -> None:
        """Take the caption off the surface. Used on Clear, on pause/end,
        and on Disable. Best effort; never raises."""
        self._pending_interim = None
        if not self.configured or self.state in ("off", "disconnected", "unsupported"):
            return
        try:
            if self.surface == "stage_message":
                code, _ = await self._request("DELETE", "/v1/stage/message")
            else:
                if self.message_id is None:
                    return
                code, _ = await self._request(
                    "GET", f"/v1/message/{urllib.parse.quote(self.message_id)}/clear")
        except Exception as exc:
            self._push_failed(f"{exc.__class__.__name__}: {exc}")
            return
        if 200 <= code < 300:
            self.cleared += 1
        else:
            self._push_failed(f"clear answered {code}")

    # -- reporting ---------------------------------------------------------

    def latency(self) -> dict | None:
        if not self._latencies:
            return None
        values = sorted(self._latencies)
        p95 = values[min(len(values) - 1, int(round(0.95 * (len(values) - 1))))]
        return {"samples": len(values),
                "median_ms": round(statistics.median(values) * 1000),
                "p95_ms": round(p95 * 1000),
                "last_ms": round(self._latencies[-1] * 1000)}

    def status(self) -> dict:
        return {
            "configured": self.configured,
            "enabled": self.enabled,
            "state": self.state,
            "host": self.host,
            "port": self.port,
            "surface": self.surface,
            "message": self.message,
            "token": self.token,
            "message_id": self.message_id,
            "language": self.language,
            "interims": self.interims,
            "version": self.version,
            "pushed": self.pushed,
            "cleared": self.cleared,
            "failures": self.failures,
            "error": self.error,
            "latency": self.latency(),
        }
