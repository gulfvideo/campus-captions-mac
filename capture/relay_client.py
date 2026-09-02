"""The capture client's link to the relay.

Sends transcript and status up, receives control down, and reconnects forever if
the relay restarts mid-event. Interim text produced while the link is down is
dropped - it is rewritten several times a second and would be stale on arrival.
Committed lines are kept and sent the moment the link is back, flagged `late`:
a deaf reader has no other channel, so a sentence spoken during a ten-second
reconnect must still reach their scrollback and the transcript, even if it
arrives after the fact.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.parse
from collections import deque
from collections.abc import Callable

import websockets

RECONNECT_CAP = 10.0
# Finals held while the relay is unreachable. Two hundred lines is roughly
# ten minutes of dense speech; beyond that the oldest go, bounded on purpose.
PENDING_FINALS_MAX = 200


class RelayClient:
    def __init__(self, url: str, token: str = "",
                 on_control: Callable[[str, dict], None] | None = None):
        self.url = url
        # The token rides in a header, not the URL: a query string ends up in
        # access logs, proxy logs and this client's own connection message.
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.on_control = on_control
        self._ws = None
        self._connected = False
        self._sent = 0
        self._last_error: str | None = None
        self._pending: deque[str] = deque(maxlen=PENDING_FINALS_MAX)

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def display_url(self) -> str:
        """The relay address with any query string removed, for log lines."""
        parts = urllib.parse.urlsplit(self.url)
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def send(self, msg: dict) -> None:
        """Fire and forget. Never blocks the audio path, never raises."""
        msg.setdefault("t", round(time.time(), 3))
        ws = self._ws
        if ws is None or not self._connected:
            if msg.get("type") == "final":
                msg["late"] = True
                self._pending.append(json.dumps(msg, ensure_ascii=False))
            return
        try:
            asyncio.create_task(self._send(ws, json.dumps(msg, ensure_ascii=False)))
        except RuntimeError:
            pass

    async def _flush_pending(self, ws) -> None:
        """Committed lines from the outage, oldest first, before anything new."""
        if not self._pending:
            return
        count = len(self._pending)
        while self._pending:
            await ws.send(self._pending.popleft())
            self._sent += 1
        print(f"[capture] sent {count} caption line(s) held during the outage")

    async def _send(self, ws, payload: str) -> None:
        try:
            await ws.send(payload)
            self._sent += 1
        except Exception:
            self._connected = False

    async def run(self) -> None:
        backoff = 0.5
        while True:
            try:
                async with websockets.connect(self.url, additional_headers=self._headers,
                                              ping_interval=20, ping_timeout=20) as ws:
                    self._ws = ws
                    self._connected = True
                    self._last_error = None
                    print(f"[capture] connected to relay {self.display_url}")
                    backoff = 0.5
                    await self._flush_pending(ws)
                    async for raw in ws:
                        self._handle(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_error = f"{exc.__class__.__name__}: {exc}"
            finally:
                if self._connected:
                    print("[capture] relay link lost, reconnecting")
                self._ws = None
                self._connected = False
            await asyncio.sleep(backoff)
            backoff = min(RECONNECT_CAP, backoff * 2)

    def _handle(self, raw) -> None:
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        if not isinstance(msg, dict) or msg.get("type") != "control":
            return
        action = msg.get("action")
        if isinstance(action, str) and self.on_control:
            self.on_control(action, msg)
