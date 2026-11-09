"""Opening a page as an app window rather than a browser tab.

Campus Captions is a web app that wants to look like a program. Chromium-based
browsers - Chrome, Edge, Brave, Chromium itself - can do that with `--app=URL`:
one window, no address bar, no tabs, its own taskbar or Dock entry. That is the
whole trick; there is no Electron here and nothing extra to download, which
matters when the alternative is adding a hundred megabytes to an installer to
get a window frame.

Not every machine has one. Firefox has no equivalent flag and Safari has none
either, so `open_link` falls back to a normal browser tab and says so rather
than failing. A tab is a worse experience, not a broken one.

The menu bar app and the source launcher both use it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

# How a link should open. Stored in event.toml as [ui] window_mode, so the
# choice survives a restart; see relay/config.py.
APP, BROWSER = "app", "browser"
MODES = (APP, BROWSER)
DEFAULT_MODE = APP

# Chromium-family browsers that understand --app=, most preferred first.
# Chrome first because a machine that has it usually prefers it.
_MACOS_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)
_UNIX_COMMANDS = (
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
    "microsoft-edge", "brave-browser",
)


def find_browser() -> str | None:
    """A Chromium-family browser that can open an app window, or None."""
    if sys.platform == "darwin":
        for candidate in _MACOS_CANDIDATES:
            if Path(candidate).is_file():
                return candidate
    # A Homebrew or user-local install, or Linux.
    for name in _UNIX_COMMANDS:
        found = shutil.which(name)
        if found:
            return found
    return None


def profile_dir(data_dir) -> Path:
    """Where the app window keeps its browser profile.

    Its own, not the operator's: an app window sharing the default profile
    inherits their extensions, their "restore tabs?" prompt and whatever
    else, and on some versions attaches to the already-running browser
    instead of opening a window at all. A dedicated directory makes it
    behave like a program every time. The operator token in the link signs
    this profile in on first open, and the cookie then lives here.
    """
    return Path(data_dir) / "appwindow"


def open_app_window(url: str, data_dir) -> bool:
    """Open `url` as a chromeless window. True if a browser was launched.

    Never raises: a window that will not open must not stop the relay, which
    is already running and serving the same page to anything that asks.
    """
    browser = find_browser()
    if not browser:
        return False
    profile = profile_dir(data_dir)
    try:
        profile.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(
            [browser, f"--app={url}", f"--user-data-dir={profile}",
             "--no-first-run", "--no-default-browser-check"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    except (OSError, ValueError) as exc:
        print(f"[appwindow] could not open an app window ({exc}); using a browser tab")
        return False


def open_link(url: str, mode: str, data_dir) -> str:
    """Open `url` however `mode` asks. Returns what actually happened -
    "app" or "browser" - which is not always what was asked for, since a
    machine with only Firefox has no app window to open."""
    if mode == APP and open_app_window(url, data_dir):
        return APP
    try:
        webbrowser.open(url)
    except Exception:
        pass          # no browser at all is not a reason to fail to start
    return BROWSER


def describe() -> dict:
    """What the Settings page needs to explain the choice: whether this
    machine can do app windows at all, and which browser would be used."""
    browser = find_browser()
    return {
        "available": browser is not None,
        "browser": Path(browser).stem if browser else "",
    }
