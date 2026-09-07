"""Notification-area launcher for the packaged Campus Captions on Windows.

The relay and the capture client run on background threads of this process -
see launcher/embedded.py, which macapp/menubar.py uses in exactly the same
way. Everything here is the Windows half: the tray icon, its menu, and the
message boxes.

Built with no console window (see CampusCaptions.spec), so this is the only
proof the app is running and the only place to open the pages or quit it. An
operator who has never opened a terminal never needs to.
"""

from __future__ import annotations

import ctypes
import io
import os
import subprocess
import sys
import threading
from pathlib import Path

# --------------------------------------------------------------------------
# Standard streams, before anything can print
# --------------------------------------------------------------------------
# A --noconsole PyInstaller build starts with sys.stdout and sys.stderr set to
# None, and the very first print() from an import below would be an
# AttributeError with no console to show it on. relay/config.py's setup_logging
# replaces both with a file-backed tee, but that only runs once config loads.
# This covers the gap.
#
# A real file on os.devnull rather than a hand-rolled object with write() and
# flush(): libraries reach for sys.stdout.encoding, .fileno() and .buffer, and
# a stand-in missing any of them fails in a build with no console to print the
# traceback to.

def _devnull():
    try:
        return open(os.devnull, "w", encoding="utf-8")
    except OSError:
        return io.StringIO()


if sys.stdout is None:
    sys.stdout = _devnull()
if sys.stderr is None:
    sys.stderr = _devnull()

import pystray                                                  # noqa: E402
from PIL import Image                                           # noqa: E402

from launcher import appwindow, embedded                                   # noqa: E402
from launcher.version import VERSION                            # noqa: E402
from relay import config as relay_config                        # noqa: E402

# MessageBoxW's uType flags. Spelled out rather than imported from a constants
# module that does not exist on the machines this is developed on.
MB_OK = 0x0
MB_ICONINFORMATION = 0x40
MB_ICONWARNING = 0x30


def alert(message: str, title: str = "Campus Captions",
          icon: int = MB_ICONINFORMATION) -> None:
    """A native message box. Used only before the tray icon exists - once it
    does, notify() on the icon is less intrusive and does not block."""
    try:
        ctypes.windll.user32.MessageBoxW(None, message, title, MB_OK | icon)
    except Exception:
        print(f"[tray] {title}: {message}")


def _icons_dir() -> Path:
    """Inside the frozen bundle when packaged, next to this file from source."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "winapp" / "icons"
    return Path(__file__).resolve().parent / "icons"


_ICONS = _icons_dir()
ICON_FILES = {
    embedded.IDLE: _ICONS / "tray_idle.ico",
    embedded.STREAMING: _ICONS / "tray_streaming.ico",
    embedded.INACTIVE: _ICONS / "tray_inactive.ico",
}


def load_icons() -> dict[str, Image.Image]:
    """Every state's image, loaded once. pystray wants PIL images, not paths,
    and re-reading a file on each status poll would hit the disk every three
    seconds for the life of the event."""
    images: dict[str, Image.Image] = {}
    for state, path in ICON_FILES.items():
        try:
            images[state] = Image.open(path)
        except OSError:
            # A missing icon must never be why captions do not run. pystray
            # needs *something*, so fall back to a plain square.
            images[state] = Image.new("RGBA", (32, 32), (47, 111, 208, 255))
    return images


def open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)  # nothing to show yet on a fresh install
    try:
        os.startfile(str(path))              # noqa: S606 - Windows Explorer
    except (AttributeError, OSError):
        subprocess.run(["explorer", str(path)], check=False)


class CampusCaptionsTray:
    def __init__(self, supervisor: embedded.Supervisor):
        self.supervisor = supervisor
        self.images = load_icons()
        self._state = embedded.IDLE
        self.icon = pystray.Icon(
            "campus-captions",
            icon=self.images[embedded.IDLE],
            title="Campus Captions",
            menu=pystray.Menu(
                pystray.MenuItem("Open Status Page", self.open_status, default=True),
                pystray.MenuItem("Open Settings", self.open_settings),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Open Wall View", self.open_wall),
                pystray.MenuItem("Open Phone View", self.open_phone),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Open Config Folder", self.open_config_folder),
                pystray.MenuItem("Open Logs Folder", self.open_logs_folder),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(f"About (version {VERSION})", self.show_about),
                pystray.MenuItem("Quit Campus Captions", self.quit),
            ),
        )

    # -- the supervisor's callback ---------------------------------------

    def set_state(self, state: str) -> None:
        """idle / streaming / inactive. A no-op when nothing changed, so the
        tray is not redrawn on every poll. Safe from the supervisor's worker
        threads: pystray marshals icon changes itself."""
        if state == self._state:
            return
        self._state = state
        self.icon.icon = self.images[state]
        self.icon.title = {
            embedded.IDLE: "Campus Captions - ready, captions not started",
            embedded.STREAMING: "Campus Captions - captions running",
            embedded.INACTIVE: "Campus Captions - not working, open the status page",
        }[state]

    # -- menu ------------------------------------------------------------

    def open_status(self, *_):
        self.supervisor.open("/status", signed_in=True)

    def open_settings(self, *_):
        self.supervisor.open("/settings", signed_in=True)

    def open_wall(self, *_):
        self.supervisor.open("/wall")

    def open_phone(self, *_):
        self.supervisor.open("/phone")

    def open_config_folder(self, *_):
        open_folder(relay_config.DATA_DIR)

    def open_logs_folder(self, *_):
        open_folder(relay_config._log_dir())

    def show_about(self, *_):
        # On its own thread: MessageBoxW blocks until dismissed, and blocking
        # here would freeze the tray menu with it.
        threading.Thread(target=alert, daemon=True, args=(
            f"Campus Captions {VERSION}\n\n"
            "Live captioning for campus events. The relay and the capture "
            "client both run inside this one program.\n\n"
            "Open Status Page starts and stops captions.\n"
            "Open Settings holds API keys and one-time setup.\n\n"
            "github.com/koakley3/campus-captions",
        )).start()

    def quit(self, *_):
        # Every worker is a daemon thread, so stopping the tray loop takes
        # the relay and the capture client down with the process.
        self.icon.stop()

    def run(self) -> None:
        self.icon.run()


def main() -> None:
    rcfg = relay_config.load()
    port = rcfg.port

    if embedded.port_in_use(port):
        if embedded.existing_session(port, rcfg.operator_token) is not None:
            appwindow.open_link(
                f"http://127.0.0.1:{port}/status?token={rcfg.operator_token}",
                rcfg.window_mode, relay_config.DATA_DIR)
            alert("Campus Captions is already running - its status page has been "
                  "opened.\n\nQuit the other copy from the notification area "
                  "first if you want a fresh start.")
            return
        # Something else has the port. Start on the next free one and say so,
        # rather than sending a volunteer to edit a config file. The relay
        # thread makes the same choice on its own; this is only so the message
        # and the first status link agree with it.
        try:
            port = relay_config.choose_port(rcfg.host, port + 1)
        except OSError:
            alert(f"Port {rcfg.port} and the twenty above it are all in use on "
                  f"this computer. Close whatever is using them and start "
                  f"Campus Captions again.", icon=MB_ICONWARNING)
            return
        alert(f"Port {rcfg.port} is already used by another program on this "
              f"computer, so Campus Captions is starting on port {port} for now."
              f"\n\nThe wall and phone addresses have {port} in them. A "
              f"ProPresenter prop, OBS scene or printed QR code made for port "
              f"{rcfg.port} will not connect until that program is closed and "
              f"Campus Captions is started again - or make the change permanent "
              f"under Settings > Network and update those addresses once.",
              icon=MB_ICONWARNING)

    rcfg.port = port
    supervisor = embedded.Supervisor(rcfg)
    tray = CampusCaptionsTray(supervisor)
    supervisor.on_state = tray.set_state
    supervisor.start()
    tray.run()


if __name__ == "__main__":
    main()
