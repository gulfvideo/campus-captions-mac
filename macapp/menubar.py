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
import subprocess
import sys
import threading
import time
import types
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import rumps
import uvicorn

from capture import config as capture_config
from capture.__main__ import CaptureApp
from relay import config as relay_config
from relay.server import create_app

RELAY_READY_TIMEOUT = 25.0
STATUS_POLL_INTERVAL = 3.0

# Set in main() from the relay config, before anything below runs. /api/status
# is operator-only, so every poll from this process carries the token, and
# the status page is opened with it once so the browser gets its cookie.
OPERATOR_TOKEN = ""


def status_request(port: int) -> urllib.request.Request:
    return urllib.request.Request(
        f"http://127.0.0.1:{port}/api/status",
        headers={"Authorization": f"Bearer {OPERATOR_TOKEN}"})


def status_link(port: int) -> str:
    return f"http://127.0.0.1:{port}/status?token={OPERATOR_TOKEN}"


def settings_link(port: int) -> str:
    return f"http://127.0.0.1:{port}/settings?token={OPERATOR_TOKEN}"


def logs_dir() -> Path:
    """Mirrors relay/config.py's own _log_dir(): the packaged app's logs
    live in the standard per-user location, not next to the (nonexistent,
    once frozen) source tree."""
    if getattr(sys, "frozen", False):
        return Path.home() / "Library" / "Logs" / "Campus Captions"
    return relay_config.ROOT / "logs"


def reveal_in_finder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)  # nothing to show yet on a fresh install
    subprocess.run(["open", str(path)], check=False)


def _icons_dir() -> Path:
    """Where the menu bar PNGs live - inside the frozen bundle's Resources
    when packaged, next to this file when run from source."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "macapp" / "icons"
    return Path(__file__).resolve().parent / "icons"


_ICONS = _icons_dir()
ICON_IDLE = str(_ICONS / "menubar_idle.png")
ICON_STREAMING = str(_ICONS / "menubar_streaming.png")
ICON_INACTIVE = str(_ICONS / "menubar_inactive.png")

FALLBACK_VERSION = "0.2.5"  # kept in sync with CampusCaptions.spec's CFBundleShortVersionString


def app_version() -> str:
    """`NSBundle.mainBundle()` only reflects *this* app once PyInstaller has
    actually built a real .app around it; running from source it reports the
    Python.framework's own bundle instead, so the fallback covers dev runs."""
    if getattr(sys, "frozen", False):
        try:
            from Foundation import NSBundle
            version = NSBundle.mainBundle().infoDictionary().get("CFBundleShortVersionString")
            if version:
                return str(version)
        except Exception:
            pass
    return FALLBACK_VERSION


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
        with urllib.request.urlopen(status_request(port), timeout=2) as r:
            data = json.load(r)
        return data if "session_id" in data and "relay" in data else None
    except Exception:
        return None


def wait_ready(port: int, deadline: float) -> bool:
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(status_request(port), timeout=1.5) as r:
                json.load(r)
                return True
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(0.4)
    return False


def run_relay(app: "CampusCaptionsApp") -> None:
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
            app.set_icon_state("inactive")
            return
        app.port = cfg.port
        fastapi_app = create_app(cfg)
        server = _SilentServer(uvicorn.Config(
            fastapi_app, host=cfg.host, port=cfg.port,
            log_level="warning", ws_ping_interval=20, ws_ping_timeout=20,
            ws_max_size=64 * 1024, timeout_graceful_shutdown=5,
        ))
        fastapi_app.state.server = server
        server.run()
        if not getattr(fastapi_app.state, "restart_requested", False):
            app.set_icon_state("inactive")
            return
        print(f"[relay] restarting on port {fastapi_app.state.restart_port}")


def run_capture(relay_port: int) -> None:
    cfg = capture_config.load()
    # The relay may have fallen back to another port (see run_relay); a
    # local relay_url follows whatever the relay actually got. Later port
    # changes reach the capture client over its own link (`relay_moving`).
    cfg.relay_url = capture_config.relay_url_with_port(cfg.relay_url, relay_port, local_only=True)
    args = types.SimpleNamespace(no_audio=False, open=False)
    asyncio.run(CaptureApp(cfg, args).run())


def poll_status(app: "CampusCaptionsApp") -> None:
    """Keeps the menu bar icon honest about what's actually happening:
    a plain graduation cap while idle, filled-in while captions are
    flowing, and the warning glyph the moment the source reports trouble
    or the relay stops answering. Reads app.port every time, since a
    Settings-page port change moves the relay while this keeps running."""
    while True:
        try:
            with urllib.request.urlopen(status_request(app.port), timeout=2) as r:
                data = json.load(r)
            source = data.get("source", {})
            asr = data.get("asr", {})
            if source.get("error"):
                app.set_icon_state("inactive")
            elif asr.get("stream_open"):
                app.set_icon_state("streaming")
            else:
                app.set_icon_state("idle")
        except Exception:
            app.set_icon_state("inactive")
        time.sleep(STATUS_POLL_INTERVAL)


class CampusCaptionsApp(rumps.App):
    def __init__(self, port: int):
        super().__init__("Campus Captions", icon=ICON_IDLE, template=True, quit_button=None)
        self.port = port
        self._icon_state = "idle"
        self.menu = [
            rumps.MenuItem("About Campus Captions", callback=self.show_about),
            rumps.separator,
            rumps.MenuItem("Open Status Page", callback=self.open_status),
            # Cmd-, is the conventional Settings/Preferences shortcut, and now
            # that API keys, integrations, and the welcome-screen config have
            # their own page (see viewer/settings.html), this is the one it
            # actually belongs on - not the operate dashboard.
            rumps.MenuItem("Open Settings", callback=self.open_settings, key=","),
            rumps.MenuItem("Open Wall View", callback=self.open_wall),
            rumps.MenuItem("Open Phone View", callback=self.open_phone),
            rumps.separator,
            rumps.MenuItem("Show Config Folder in Finder", callback=self.show_config_folder),
            rumps.MenuItem("Show Logs in Finder", callback=self.show_logs_folder),
            rumps.separator,
            rumps.MenuItem("Quit Campus Captions", callback=self.quit_app, key="q"),
        ]

    def set_icon_state(self, state: str) -> None:
        """idle / streaming / inactive - a no-op if already showing that
        icon, so the menu bar doesn't flicker on every poll."""
        if state == self._icon_state:
            return
        self._icon_state = state
        self.icon = {
            "idle": ICON_IDLE,
            "streaming": ICON_STREAMING,
            "inactive": ICON_INACTIVE,
        }[state]

    def show_about(self, _sender) -> None:
        rumps.alert(
            "Campus Captions",
            f"Version {app_version()}\n\n"
            "Live captioning for campus events. Runs the relay and capture "
            "client in this one menu bar app - see Open Status Page to run "
            "an event, Open Settings for API keys and setup.\n\n"
            "github.com/koakley3/campus-captions",
        )

    def open_status(self, _sender) -> None:
        webbrowser.open(status_link(self.port))

    def open_settings(self, _sender) -> None:
        webbrowser.open(settings_link(self.port))

    def show_config_folder(self, _sender) -> None:
        reveal_in_finder(relay_config.DATA_DIR)

    def show_logs_folder(self, _sender) -> None:
        reveal_in_finder(logs_dir())

    def open_wall(self, _sender) -> None:
        webbrowser.open(f"http://127.0.0.1:{self.port}/wall")

    def open_phone(self, _sender) -> None:
        webbrowser.open(f"http://127.0.0.1:{self.port}/phone")

    def quit_app(self, _sender) -> None:
        rumps.quit_application()


def main() -> None:
    global OPERATOR_TOKEN
    rcfg = relay_config.load()
    OPERATOR_TOKEN = rcfg.operator_token

    port = rcfg.port
    if port_in_use(port):
        session = existing_session(port)
        if session is not None:
            webbrowser.open(status_link(port))
            rumps.alert("Campus Captions",
                        "Already running - opened its status page.\n\n"
                        "Quit the other copy first if you want a fresh start.")
            return
        # Something else has the port. Start on the next free one and say
        # so, rather than sending a volunteer to edit a config file. The
        # relay thread makes the same choice on its own; this is only so the
        # alert and the first status link agree with it.
        try:
            port = relay_config.choose_port(rcfg.host, port + 1)
        except OSError:
            rumps.alert("Campus Captions",
                        f"Port {rcfg.port} and the twenty above it are all in use "
                        f"on this Mac. Close whatever is using them and try again.")
            return
        rumps.alert(
            "Campus Captions",
            f"Port {rcfg.port} is already used by another program on this Mac, "
            f"so Campus Captions is starting on port {port} for now.\n\n"
            f"The wall and phone URLs have {port} in them. A ProPresenter prop or "
            f"printed QR code made for port {rcfg.port} will not connect until that "
            f"program is closed and Campus Captions is started again - or make the "
            f"change permanent under Settings > Network and update those URLs once.")

    app = CampusCaptionsApp(port)
    threading.Thread(target=run_relay, args=(app,), daemon=True).start()

    def wait_then_start_capture() -> None:
        if wait_ready(port, time.time() + RELAY_READY_TIMEOUT):
            threading.Thread(target=run_capture, args=(app.port,), daemon=True).start()
            threading.Thread(target=poll_status, args=(app,), daemon=True).start()
            webbrowser.open(status_link(port))
        else:
            app.set_icon_state("inactive")  # rumps allows icon updates off the main thread

    threading.Thread(target=wait_then_start_capture, daemon=True).start()
    app.run()


if __name__ == "__main__":
    main()
