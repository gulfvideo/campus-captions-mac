"""Running the relay and the capture client inside one process.

launch.py starts them as two subprocesses (`python -m relay`, `python -m
capture`), each with its own asyncio loop. A frozen app has no interpreter to
shell back out to, so the packaged app runs both here instead, each on its own
thread with its own event loop. They only ever talk to each other over a
localhost websocket, so nothing about that relationship changes.

macapp/menubar.py is the front end: it draws the menu bar item and the
alerts, and hands a status callback to Supervisor. Everything else - starting
the relay, restarting it when the operator changes the port, pointing the
capture client at whichever port it got - lives here.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import threading
import time
import types
import urllib.error
import urllib.request
from collections.abc import Callable

import uvicorn

from launcher import appwindow
from capture import config as capture_config
from capture.__main__ import CaptureApp
from relay import config as relay_config
from relay.server import create_app

RELAY_READY_TIMEOUT = 25.0
STATUS_POLL_INTERVAL = 3.0

# What poll_status() reports through the front end's callback.
IDLE, STREAMING, INACTIVE = "idle", "streaming", "inactive"


class _SilentServer(uvicorn.Server):
    """uvicorn.Server.run() installs SIGINT/SIGTERM handlers, which only the
    process's main thread may do. This runs on a worker thread instead; the
    front end's Quit item is how the app actually stops."""

    def install_signal_handlers(self) -> None:
        pass


def port_in_use(port: int) -> bool:
    """Connect, don't bind: if a connection is accepted, something is there."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.8)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def status_request(port: int, token: str) -> urllib.request.Request:
    """/api/status is operator-only, so every poll carries the token."""
    return urllib.request.Request(
        f"http://127.0.0.1:{port}/api/status",
        headers={"Authorization": f"Bearer {token}"})


def existing_session(port: int, token: str) -> dict | None:
    """Is the thing on this port one of ours, or someone else's server?"""
    try:
        with urllib.request.urlopen(status_request(port, token), timeout=2) as r:
            data = json.load(r)
        return data if "session_id" in data and "relay" in data else None
    except Exception:
        return None


def wait_ready(port: int, token: str, deadline: float) -> bool:
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(status_request(port, token), timeout=1.5) as r:
                json.load(r)
                return True
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(0.4)
    return False


class Supervisor:
    """The relay and the capture client, on two threads of this process.

    `on_state` is called with IDLE / STREAMING / INACTIVE whenever the picture
    changes; the front end turns that into whatever its platform shows. It is
    a plain attribute so a front end that needs the supervisor in order to
    build its menu can construct this first and assign the callback after,
    and it is called from worker threads - a UI toolkit that is main-thread
    only has to marshal there itself.
    """

    def __init__(self, cfg: relay_config.RelayConfig,
                 on_state: Callable[[str], None] | None = None):
        self.cfg = cfg
        self.port = cfg.port
        self.token = cfg.operator_token
        self.on_state = on_state or (lambda _state: None)

    # -- links the front end puts in its menu ----------------------------

    def link(self, path: str, signed_in: bool = False) -> str:
        """`path` on this relay. `signed_in` appends the operator token, which
        the page trades for a month-long cookie - only for operator pages, and
        never for the wall or phone links an operator might read out loud."""
        base = f"http://127.0.0.1:{self.port}{path}"
        return f"{base}?token={self.token}" if signed_in else base

    def open(self, path: str, signed_in: bool = False) -> str:
        """Open one of this relay's pages the way the operator has asked for
        - an app window or a browser tab. Returns which it managed."""
        return appwindow.open_link(
            self.link(path, signed_in=signed_in),
            self.cfg.window_mode, relay_config.DATA_DIR)

    def status_link(self) -> str:
        # /status redirects to /setup until the first-run wizard is done, so
        # this one link is right on a fresh install and on a booth machine
        # that has run a hundred events.
        return self.link("/status", signed_in=True)

    def settings_link(self) -> str:
        return self.link("/settings", signed_in=True)

    # -- the threads ------------------------------------------------------

    def run_relay(self) -> None:
        """Runs the relay for as long as the app lives. Each pass reads the
        config afresh and takes the configured port, or the next free one if
        something else holds it. When the operator changes the port on the
        Settings page the server exits with `restart_requested` set and this
        loop brings it straight back on the new port; any other exit ends it."""
        while True:
            cfg = relay_config.load()
            try:
                cfg.port = relay_config.choose_port(cfg.host, cfg.port)
            except OSError as exc:
                print(f"[relay] {exc}")
                self.on_state(INACTIVE)
                return
            self.port = cfg.port
            fastapi_app = create_app(cfg)
            server = _SilentServer(uvicorn.Config(
                fastapi_app, host=cfg.host, port=cfg.port,
                log_level="warning", ws_ping_interval=20, ws_ping_timeout=20,
                ws_max_size=64 * 1024, timeout_graceful_shutdown=5,
            ))
            fastapi_app.state.server = server
            server.run()
            if not getattr(fastapi_app.state, "restart_requested", False):
                self.on_state(INACTIVE)
                return
            print(f"[relay] restarting on port {fastapi_app.state.restart_port}")

    def run_capture(self, relay_port: int) -> None:
        cfg = capture_config.load()
        # The relay may have fallen back to another port (see run_relay); a
        # local relay_url follows whatever the relay actually got. Later port
        # changes reach the capture client over its own link (`relay_moving`).
        cfg.relay_url = capture_config.relay_url_with_port(
            cfg.relay_url, relay_port, local_only=True)
        args = types.SimpleNamespace(no_audio=False, open=False)
        asyncio.run(CaptureApp(cfg, args).run())

    def poll_status(self) -> None:
        """Keeps the front end's icon honest about what is actually
        happening: idle while nothing is being transcribed, streaming while
        captions are flowing, and inactive the moment the source reports
        trouble or the relay stops answering. Reads self.port every time,
        since a Settings-page port change moves the relay while this keeps
        running."""
        while True:
            try:
                with urllib.request.urlopen(
                        status_request(self.port, self.token), timeout=2) as r:
                    data = json.load(r)
                source = data.get("source", {})
                asr = data.get("asr", {})
                if source.get("error"):
                    self.on_state(INACTIVE)
                elif asr.get("stream_open"):
                    self.on_state(STREAMING)
                else:
                    self.on_state(IDLE)
            except Exception:
                self.on_state(INACTIVE)
            time.sleep(STATUS_POLL_INTERVAL)

    def start(self, open_browser: bool = True) -> None:
        """Start the relay, then the capture client once the relay answers.

        Returns immediately; everything runs on daemon threads so the front
        end's own Quit takes the whole process down with it.
        """
        threading.Thread(target=self.run_relay, daemon=True).start()
        # Keep the Mac (and its display, for a mirrored wall) awake for as
        # long as the app runs; caffeinate exits with us. ponytail: whole
        # app lifetime, not just while streaming - switch to start/stop on
        # STREAMING if idle operators complain about a Mac that never sleeps.
        subprocess.Popen(["/usr/bin/caffeinate", "-dis", "-w", str(os.getpid())],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def then_capture() -> None:
            if not wait_ready(self.port, self.token,
                              time.time() + RELAY_READY_TIMEOUT):
                self.on_state(INACTIVE)
                return
            threading.Thread(target=self.run_capture, args=(self.port,),
                             daemon=True).start()
            threading.Thread(target=self.poll_status, daemon=True).start()
            if open_browser:
                opened = appwindow.open_link(
                    self.status_link(), self.cfg.window_mode,
                    relay_config.DATA_DIR)
                if opened != self.cfg.window_mode:
                    # Asked for an app window and got a tab: no Chromium-family
                    # browser here. Worth one line in the log, since the
                    # Settings page says the same thing and an operator who
                    # ticked "app window" deserves to know why it did not.
                    print("[relay] no browser that can open an app window; "
                          "opened a browser tab instead")

        threading.Thread(target=then_capture, daemon=True).start()
