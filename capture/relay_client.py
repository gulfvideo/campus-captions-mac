"""The capture client's link to the relay.

Sends transcript and status up, receives control down, and reconnects forever if
the relay restarts mid-event. Transcript produced while the link is down is
dropped rather than queued: a caption that arrives late is worse than one that
never arrives.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable

import websockets

RECONNECT_CAP = 10.0


class RelayClient:
    def __init__(self, url: str, token: str = "",
                 on_control: Callable[[str, dict], None] | None = None):
        if token:
            url += ("&" if "?" in url else "?") + "token=" + token
        self.url = url
        self.on_control = on_control
        self._ws = None
        self._connected = False
        self._sent = 0
        self._last_error: str | None = None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def send(self, msg: dict) -> None:
        """Fire and forget. Never blocks the audio path, never raises."""
        ws = self._ws
        if ws is None or not self._connected:
            return
        msg.setdefault("t", round(time.time(), 3))
        try:
            asyncio.create_task(self._send(ws, json.dumps(msg, ensure_ascii=False)))
        except RuntimeError:
            pass

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
                async with websockets.connect(self.url, ping_interval=20,
                                              ping_timeout=20) as ws:
                    self._ws = ws
                    self._connected = True
                    self._last_error = None
                    print(f"[capture] connected to relay {self.url}")
                    backoff = 0.5
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
