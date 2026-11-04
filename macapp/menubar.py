"""Menu bar launcher for the packaged Campus Captions.app.

The relay and the capture client run on background threads of this process -
see launcher/embedded.py, which winapp/tray.py uses in exactly the same way.
Everything here is the macOS half: the menu bar item, its icons, and the
alerts.

A beta tester has no terminal window to watch, so this shows a menu bar icon
instead: proof it's running, and a place to open the pages or quit.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import rumps

from launcher import appwindow, embedded
from launcher.version import VERSION
from relay import config as relay_config


def logs_dir() -> Path:
    return relay_config._log_dir()


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
ICONS = {
    embedded.IDLE: str(_ICONS / "menubar_idle.png"),
    embedded.STREAMING: str(_ICONS / "menubar_streaming.png"),
    embedded.INACTIVE: str(_ICONS / "menubar_inactive.png"),
}

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
    return VERSION


class CampusCaptionsApp(rumps.App):
    def __init__(self, supervisor: embedded.Supervisor):
        super().__init__("Campus Captions", icon=ICONS[embedded.IDLE],
                         template=True, quit_button=None)
        self.supervisor = supervisor
        self._icon_state = embedded.IDLE
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
        icon, so the menu bar doesn't flicker on every poll. rumps allows
        icon updates off the main thread, which is where embedded.Supervisor
        calls this from."""
        if state == self._icon_state:
            return
        self._icon_state = state
        self.icon = ICONS[state]

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
        self.supervisor.open("/status", signed_in=True)

    def open_settings(self, _sender) -> None:
        self.supervisor.open("/settings", signed_in=True)

    def show_config_folder(self, _sender) -> None:
        reveal_in_finder(relay_config.DATA_DIR)

    def show_logs_folder(self, _sender) -> None:
        reveal_in_finder(logs_dir())

    def open_wall(self, _sender) -> None:
        self.supervisor.open("/wall")

    def open_phone(self, _sender) -> None:
        self.supervisor.open("/phone")

    def quit_app(self, _sender) -> None:
        rumps.quit_application()


def main() -> None:
    rcfg = relay_config.load()
    port = rcfg.port
    if embedded.port_in_use(port):
        session = embedded.existing_session(port, rcfg.operator_token)
        if session is not None:
            appwindow.open_link(
                f"http://127.0.0.1:{port}/status?token={rcfg.operator_token}",
                rcfg.window_mode, relay_config.DATA_DIR)
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

    rcfg.port = port
    supervisor = embedded.Supervisor(rcfg)
    app = CampusCaptionsApp(supervisor)
    supervisor.on_state = app.set_icon_state
    supervisor.start()
    app.run()


if __name__ == "__main__":
    main()
