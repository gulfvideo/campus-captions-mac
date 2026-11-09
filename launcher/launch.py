"""Start everything. Run by "Start Captions.command"; no terminal knowledge needed.

Creates the virtual environment on first run, installs pinned dependencies,
starts the relay, waits for it to answer, starts the capture client, opens the
status page in a browser, and supervises both until this window is closed.

If either process dies it is restarted. Nothing here needs the operator to type
a command, activate anything, or know what a port is.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
VENV_PY = VENV / "bin" / "python"
REQUIREMENTS = ROOT / "requirements.txt"
STAMP = VENV / ".requirements-stamp"
ENV_FILE = ROOT / ".env"
OPERATOR_TOKEN_KEY = "CAPTIONS_OPERATOR_TOKEN"

RELAY_READY_TIMEOUT = 25.0
RESTART_LIMIT = 5


def say(msg: str = "") -> None:
    print(msg, flush=True)


def rule(title: str) -> None:
    say()
    say(f"--- {title} " + "-" * max(0, 62 - len(title)))


# --------------------------------------------------------------------------
# environment
# --------------------------------------------------------------------------

def ensure_venv() -> Path:
    if not VENV_PY.exists():
        rule("first run: creating the virtual environment")
        say("this happens once and takes a minute")
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV)])
    return VENV_PY


def requirements_changed() -> bool:
    if not REQUIREMENTS.exists():
        return False
    current = str(REQUIREMENTS.stat().st_mtime_ns)
    if STAMP.exists() and STAMP.read_text(encoding="utf-8").strip() == current:
        return False
    return True


def ensure_dependencies(python: Path) -> None:
    if not requirements_changed():
        return
    rule("installing dependencies")
    subprocess.check_call([str(python), "-m", "pip", "install", "--disable-pip-version-check",
                           "-q", "-r", str(REQUIREMENTS)])
    STAMP.write_text(str(REQUIREMENTS.stat().st_mtime_ns), encoding="utf-8")
    say("dependencies ready")


def read_env_var(key: str) -> str:
    """The environment first, then .env - the same order the relay uses."""
    value = os.environ.get(key, "").strip()
    if value or not ENV_FILE.exists():
        return value
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def ensure_operator_token() -> str:
    """The secret that signs the status page in. The relay generates one
    itself if none exists, but this launcher needs it *before* the relay is
    up - to recognise an already-running relay, and to build the link it
    opens - so it settles the value first and the relay simply finds it."""
    token = read_env_var(OPERATOR_TOKEN_KEY)
    if not token:
        import secrets
        token = secrets.token_urlsafe(24)
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
        lines.append(f"{OPERATOR_TOKEN_KEY}={token}")
        ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        say("generated an operator token (saved in .env)")
    os.environ[OPERATOR_TOKEN_KEY] = token
    return token


def status_json(port: int, token: str, timeout: float) -> dict:
    """One /api/status snapshot. Operator-only, hence the bearer header."""
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/status",
        headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def read_window_mode() -> str:
    """"app" or "browser" - how [ui] window_mode says pages should open.
    Read here the same way the port is, because this file runs before the
    virtual environment exists and cannot import relay.config."""
    cfg = ROOT / "config" / "event.toml"
    if not cfg.exists():
        cfg = ROOT / "config" / "event.example.toml"
    try:
        import tomllib
        with cfg.open("rb") as fh:
            mode = str((tomllib.load(fh).get("ui") or {}).get("window_mode", "app"))
        return mode if mode in ("app", "browser") else "app"
    except Exception:
        return "app"


def open_page(url: str) -> None:
    """Open a page as an app window when asked for and possible, else a tab.

    launcher/appwindow.py is pure standard library, so it imports fine here -
    before the venv exists and before any dependency is installed.
    """
    try:
        from launcher import appwindow
        opened = appwindow.open_link(url, read_window_mode(), ROOT)
        if read_window_mode() == "app" and opened != "app":
            say("no browser that can open an app window; opened a tab instead")
    except Exception:
        try:
            webbrowser.open(url)
        except Exception:
            pass


def read_port() -> int:
    port = os.environ.get("CAPTIONS_PORT")
    if port and port.isdigit():
        return int(port)
    cfg = ROOT / "config" / "event.toml"
    if not cfg.exists():
        cfg = ROOT / "config" / "event.example.toml"
    try:
        import tomllib
        with cfg.open("rb") as fh:
            return int((tomllib.load(fh).get("relay") or {}).get("port", 8000))
    except Exception:
        return 8000


# --------------------------------------------------------------------------
# processes
# --------------------------------------------------------------------------

def spawn(python: Path, args: list[str], title: str) -> subprocess.Popen:
    say(f"starting {title}")
    # -u: without it Python buffers stdout when it is not a console, and this
    # window would sit blank while the relay and capture client print nothing.
    return subprocess.Popen([str(python), "-u", *args], cwd=str(ROOT))


def port_in_use(port: int) -> bool:
    """Connect, don't bind: if a connection is accepted, something is there."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.8)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def existing_relay(port: int, token: str) -> dict | None:
    """Is the thing on this port one of ours, or someone else's server?"""
    try:
        data = status_json(port, token, timeout=2)
        return data if "session_id" in data and "relay" in data else None
    except Exception:
        return None


def find_free_port(start: int, tries: int = 20) -> int:
    """The first port at or above `start` nothing answers on. Same idea as
    relay/config.py's choose_port, with the connect probe this file already
    uses (it cannot import the relay before the venv exists)."""
    for candidate in range(start, min(start + tries, 65536)):
        if not port_in_use(candidate):
            return candidate
    return start


def find_running_relay(configured: int, token: str, tries: int = 20) -> int | None:
    """Where our relay is listening, if it is: the configured port, or one of
    the ports it may have fallen back to. Used to point a restarted capture
    client at the right place."""
    for candidate in range(configured, min(configured + tries, 65536)):
        if port_in_use(candidate) and existing_relay(candidate, token) is not None:
            return candidate
    return None


def relay_args(port: int, fallback: bool) -> list[str]:
    # Only a fallback port is pinned on the command line; otherwise the relay
    # reads [relay] port itself, so a port saved from the Settings page wins
    # the next time it starts.
    return ["-m", "relay"] + (["--port", str(port)] if fallback else [])


def capture_args(port: int) -> list[str]:
    return ["-m", "capture", "--relay", f"ws://127.0.0.1:{port}/ws/source"]


def port_owner(port: int) -> str:
    """Best effort name of whatever holds the port, for the error message."""
    try:
        # lsof prints COMMAND and PID in the first two columns.
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                return f"{parts[0]} (PID {parts[1]})"
    except Exception:
        pass
    return ""


def wait_for_relay(port: int, proc: subprocess.Popen, token: str) -> bool:
    """Wait for OUR relay. If it exits, stop waiting - do not adopt another."""
    deadline = time.time() + RELAY_READY_TIMEOUT
    while time.time() < deadline:
        if proc.poll() is not None:
            return False                      # our relay died; whatever is on
        try:                                  # that port is not ours
            status_json(port, token, timeout=1.5)
            return True
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(0.4)
    return False


def relay_ip(port: int, token: str) -> str:
    try:
        return (status_json(port, token, timeout=2).get("relay") or {}).get("ip", "127.0.0.1")
    except Exception:
        return "127.0.0.1"


def main() -> int:
    os.chdir(ROOT)
    say("Campus Captions")
    say("=" * 68)

    try:
        python = ensure_venv()
        ensure_dependencies(python)
    except subprocess.CalledProcessError as exc:
        say(f"\nsetup failed: {exc}")
        say("check that this machine has Python 3.11 or newer installed.")
        if sys.platform == "darwin":
            say("on macOS, install it from python.org or run "
                "'brew install python@3.12'.")
            say("the Python that ships with macOS is too old.")
        input("\npress Enter to close ")
        return 1

    port = read_port()
    token = ensure_operator_token()
    fallback = False
    procs: dict[str, subprocess.Popen] = {}
    restarts = {"relay": 0, "capture": 0}

    # Preflight. Starting the relay against an occupied port used to look like
    # a working system: the launcher found *something* answering on the port,
    # declared itself ready, and pointed the capture client at a relay that was
    # not its own while its own relay died over and over.
    if port_in_use(port):
        running = existing_relay(port, token)
        if running is not None:
            rule("already running")
            say(f"Campus Captions is already running on port {port}"
                f" (session {running.get('session_id')}).")
            say("Use that one - opening its status page now.")
            say("\nIf you want a fresh start, close the other Campus Captions")
            say("window first, then run this again.")
            try:
                open_page(f"http://127.0.0.1:{port}/status?token={token}")
            except Exception:
                pass
            input("\npress Enter to close this window ")
            return 0

        # Something else has the port. Start anyway, on the next free one,
        # and say so in words a volunteer can act on. Stopping here with a
        # request to edit a config file cost a service its captions once.
        owner = port_owner(port)
        configured = port
        port = find_free_port(port + 1)
        fallback = True
        rule("port in use")
        say(f"Port {configured} is already in use"
            + (f" by {owner}." if owner else " by another program."))
        say(f"Starting on port {port} for now instead.")
        say()
        say(f"  The wall and phone URLs below have {port} in them. A ProPresenter")
        say(f"  prop, OBS scene or printed QR code made for port {configured} will not")
        say("  connect until that other program is closed and Campus Captions is")
        say("  started again - or you make the change permanent on the Settings")
        say("  page (Network - relay port) and update those URLs once.")

    status_link = f"http://127.0.0.1:{port}/status?token={token}"
    rule("starting")
    procs["relay"] = spawn(python, relay_args(port, fallback), "relay server")

    if not wait_for_relay(port, procs["relay"], token):
        say("\nthe relay did not start. its error is above.")
        if procs["relay"].poll() is None:
            procs["relay"].terminate()
        input("\npress Enter to close ")
        return 1

    procs["capture"] = spawn(python, capture_args(port), "capture client")

    ip = relay_ip(port, token)
    rule("ready")
    say(f"  status page   http://{ip}:{port}/status?token={token}")
    say("                ^ open this; the link signs your browser in for a month")
    say(f"  wall view     http://{ip}:{port}/wall        <- ProPresenter")
    say(f"  phone view    http://{ip}:{port}/phone       <- audience")
    say()
    say("  The ASR stream starts CLOSED. Open it from the status page")
    say("  when the event is about to begin.")
    say()
    say("  Closing this window stops everything.")
    say("=" * 68)

    open_page(status_link)

    try:
        while True:
            time.sleep(1.5)
            for name, proc in list(procs.items()):
                if proc.poll() is None:
                    continue
                if restarts[name] >= RESTART_LIMIT:
                    say(f"\n{name} keeps failing; giving up.")
                    raise KeyboardInterrupt
                restarts[name] += 1
                say(f"\n{name} stopped unexpectedly - restarting "
                    f"({restarts[name]}/{RESTART_LIMIT})")
                if name == "relay":
                    # The port may have changed on the Settings page since we
                    # started (the relay normally restarts itself for that,
                    # but not if it died first), and whatever squatted on it
                    # may have gone or arrived. Decide again, the same way.
                    port = read_port()
                    fallback = False
                    if port_in_use(port) and existing_relay(port, token) is None:
                        port = find_free_port(port + 1)
                        fallback = True
                        say(f"port {read_port()} is in use; starting the relay on {port}")
                    procs[name] = spawn(python, relay_args(port, fallback), name)
                else:
                    running = find_running_relay(read_port(), token)
                    if running is not None:
                        port = running
                    procs[name] = spawn(python, capture_args(port), name)
    except KeyboardInterrupt:
        pass
    finally:
        rule("shutting down")
        for name, proc in procs.items():
            if proc.poll() is None:
                proc.terminate()
        for proc in procs.values():
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        say("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
