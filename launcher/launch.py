"""Start everything. Run by "Start Captions.bat"; no terminal knowledge needed.

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
VENV_PY = VENV / "Scripts" / "python.exe" if os.name == "nt" else VENV / "bin" / "python"
REQUIREMENTS = ROOT / "requirements.txt"
STAMP = VENV / ".requirements-stamp"

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
    """Connect, don't bind.

    A bind probe is wrong on Windows: with SO_REUSEADDR it succeeds even when
    another process is already listening, so the port looks free when it is not.
    If a connection is accepted, something is there.
    """
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.8)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def existing_relay(port: int) -> dict | None:
    """Is the thing on this port one of ours, or someone else's server?"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status",
                                    timeout=2) as response:
            data = json.load(response)
        return data if "session_id" in data and "relay" in data else None
    except Exception:
        return None


def port_owner(port: int) -> str:
    """Best effort name of whatever holds the port, for the error message."""
    try:
        if os.name == "nt":
            out = subprocess.run(["netstat", "-ano"], capture_output=True,
                                 text=True, timeout=5).stdout
            for line in out.splitlines():
                if f":{port} " in line and "LISTENING" in line:
                    pid = line.split()[-1]
                    task = subprocess.run(
                        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                        capture_output=True, text=True, timeout=5).stdout.strip()
                    name = task.split(",")[0].strip('"') if task else "unknown"
                    return f"{name} (PID {pid})"
            return ""

        # macOS and Linux: lsof prints COMMAND and PID in the first two columns.
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


def wait_for_relay(port: int, proc: subprocess.Popen) -> bool:
    """Wait for OUR relay. If it exits, stop waiting - do not adopt another."""
    url = f"http://127.0.0.1:{port}/api/status"
    deadline = time.time() + RELAY_READY_TIMEOUT
    while time.time() < deadline:
        if proc.poll() is not None:
            return False                      # our relay died; whatever is on
        try:                                  # that port is not ours
            with urllib.request.urlopen(url, timeout=1.5) as response:
                json.load(response)
                return True
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(0.4)
    return False


def relay_ip(port: int) -> str:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status",
                                    timeout=2) as response:
            return (json.load(response).get("relay") or {}).get("ip", "127.0.0.1")
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
    procs: dict[str, subprocess.Popen] = {}
    restarts = {"relay": 0, "capture": 0}

    # Preflight. Starting the relay against an occupied port used to look like
    # a working system: the launcher found *something* answering on the port,
    # declared itself ready, and pointed the capture client at a relay that was
    # not its own while its own relay died over and over.
    if port_in_use(port):
        running = existing_relay(port)
        if running is not None:
            rule("already running")
            say(f"Campus Captions is already running on port {port}"
                f" (session {running.get('session_id')}).")
            say("Use that one - opening its status page now.")
            say("\nIf you want a fresh start, close the other Campus Captions")
            say("window first, then run this again.")
            try:
                webbrowser.open(f"http://127.0.0.1:{port}/status")
            except Exception:
                pass
            input("\npress Enter to close this window ")
            return 0

        owner = port_owner(port)
        rule("cannot start")
        say(f"Port {port} is already in use"
            + (f" by {owner}." if owner else " by another program."))
        say("\nClose that program, or change the port in config/event.toml:")
        say("\n    [relay]")
        say(f"    port = {port + 1}")
        say("\nIf you change it, the wall and phone URLs change too.")
        input("\npress Enter to close ")
        return 1

    rule("starting")
    procs["relay"] = spawn(python, ["-m", "relay"], "relay server")

    if not wait_for_relay(port, procs["relay"]):
        say("\nthe relay did not start. its error is above.")
        if procs["relay"].poll() is None:
            procs["relay"].terminate()
        input("\npress Enter to close ")
        return 1

    procs["capture"] = spawn(python, ["-m", "capture"], "capture client")

    ip = relay_ip(port)
    rule("ready")
    say(f"  status page   http://{ip}:{port}/status      <- open this")
    say(f"  wall view     http://{ip}:{port}/wall        <- ProPresenter")
    say(f"  phone view    http://{ip}:{port}/phone       <- audience")
    say()
    say("  The ASR stream starts CLOSED. Open it from the status page")
    say("  when the event is about to begin.")
    say()
    say("  Closing this window stops everything.")
    say("=" * 68)

    try:
        webbrowser.open(f"http://127.0.0.1:{port}/status")
    except Exception:
        pass

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
                module = "relay" if name == "relay" else "capture"
                procs[name] = spawn(python, ["-m", module], name)
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
