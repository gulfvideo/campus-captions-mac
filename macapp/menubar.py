"""Menu bar launcher for the packaged Campus Captions.app.

The source tree runs the relay and the capture client as two separate
processes (`python -m relay`, `python -m capture`), each with its own asyncio
loop - see launcher/launch.py. A frozen .app has no interpreter to shell back
out to, so this runs both in the same process instead, each on its own thread
with its own event loop. They only ever talk to each other over a localhost
websocket, so nothing about that relationship changes.

A beta tester has no terminal window to watch, so this shows a menu bar icon
instead: proof it's running, and a place to open the pages or quit.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
import types
import urllib.error
import urllib.request
import webbrowser

import rumps
import uvicorn

from capture import config as capture_config
from capture.__main__ import CaptureApp
from relay import config as relay_config
from relay.server import create_app

RELAY_READY_TIMEOUT = 25.0
TITLE = "\U0001F393"          # graduation cap
TITLE_ERROR = "⚠️"  # warning sign


class _SilentServer(uvicorn.Server):
    """uvicorn.Server.run() installs SIGINT/SIGTERM handlers, which only the
    process's main thread may do. This runs on a worker thread instead; the
    menu bar's Quit item is how the app actually stops."""

    def install_signal_handlers(self) -> None:
        pass


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.8)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def existing_session(port: int) -> dict | None:
    """Is the thing on this port one of ours, or someone else's server?"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as r:
            data = json.load(r)
        return data if "session_id" in data and "relay" in data else None
    except Exception:
        return None


def wait_ready(port: int, deadline: float) -> bool:
    url = f"http://127.0.0.1:{port}/api/status"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as r:
                json.load(r)
                return True
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(0.4)
    return False


def run_relay(cfg) -> None:
    server = _SilentServer(uvicorn.Config(
        create_app(cfg), host=cfg.host, port=cfg.port,
        log_level="warning", ws_ping_interval=20, ws_ping_timeout=20,
    ))
    server.run()


def run_capture() -> None:
    cfg = capture_config.load()
    args = types.SimpleNamespace(no_audio=False, open=False)
    asyncio.run(CaptureApp(cfg, args).run())


class CampusCaptionsApp(rumps.App):
    def __init__(self, port: int):
        super().__init__("Campus Captions", title=TITLE, quit_button=None)
        self.port = port
        self.menu = [
            rumps.MenuItem("Open Status Page", callback=self.open_status),
            rumps.MenuItem("Open Wall View", callback=self.open_wall),
            rumps.MenuItem("Open Phone View", callback=self.open_phone),
            rumps.separator,
            rumps.MenuItem("Quit Campus Captions", callback=self.quit_app),
        ]

    def open_status(self, _sender) -> None:
        webbrowser.open(f"http://127.0.0.1:{self.port}/status")

    def open_wall(self, _sender) -> None:
        webbrowser.open(f"http://127.0.0.1:{self.port}/wall")

    def open_phone(self, _sender) -> None:
        webbrowser.open(f"http://127.0.0.1:{self.port}/phone")

    def quit_app(self, _sender) -> None:
        rumps.quit_application()


def main() -> None:
    rcfg = relay_config.load()

    if port_in_use(rcfg.port):
        session = existing_session(rcfg.port)
        if session is not None:
            webbrowser.open(f"http://127.0.0.1:{rcfg.port}/status")
            rumps.alert("Campus Captions",
                        "Already running - opened its status page.\n\n"
                        "Quit the other copy first if you want a fresh start.")
        else:
            rumps.alert("Campus Captions",
                        f"Port {rcfg.port} is already used by another program "
                        f"on this Mac. Close it, or change the port in this "
                        f"app's config/event.toml (right-click the app, Show "
                        f"Package Contents).")
        return

    app = CampusCaptionsApp(rcfg.port)
    threading.Thread(target=run_relay, args=(rcfg,), daemon=True).start()

    def wait_then_start_capture() -> None:
        if wait_ready(rcfg.port, time.time() + RELAY_READY_TIMEOUT):
            threading.Thread(target=run_capture, daemon=True).start()
            webbrowser.open(f"http://127.0.0.1:{rcfg.port}/status")
        else:
            app.title = TITLE_ERROR  # rumps allows title updates off the main thread

    threading.Thread(target=wait_then_start_capture, daemon=True).start()
    app.run()


if __name__ == "__main__":
    main()
