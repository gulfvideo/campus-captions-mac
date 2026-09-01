"""Runs `cloudflared tunnel run` as a managed subprocess, so exposing this
relay on a domain the operator owns is a status-page toggle instead of a
CLI setup - the old path needed `cloudflared login` (interactive browser
OAuth), `tunnel create`, a hand-written `config.yml`, `tunnel route dns`,
and a LaunchAgent plist that (in practice) needed hand-patching to actually
pass `--config`.

Cloudflare's own current recommendation for exactly this - a lightweight
connector run against a tunnel configured entirely in their dashboard - is
a "remotely-managed" tunnel: create the tunnel and add a Public Hostname
once at one.dash.cloudflare.com (Zero Trust -> Networks -> Tunnels), copy
the token it shows you, and run:

    cloudflared tunnel run --token <TOKEN>

Everything else - which hostname routes to this machine - lives in
Cloudflare's dashboard and can be changed without restarting anything
here. See SETUP.md 3c for the walkthrough.

The token is passed via the TUNNEL_TOKEN environment variable rather than
`--token` on the command line, so it never shows up in `ps` output the way
an argv value would.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

# Homebrew's install location isn't on PATH for a GUI-launched app (it only
# ever gets a minimal system PATH), so shutil.which() alone would miss the
# most common way people actually install this on a Mac.
KNOWN_PATHS = (
    "/opt/homebrew/bin/cloudflared",   # Homebrew, Apple Silicon
    "/usr/local/bin/cloudflared",      # Homebrew, Intel
    "/usr/bin/cloudflared",
)

NOT_INSTALLED = (
    "cloudflared not found - install it (brew install cloudflared, or "
    "download it from github.com/cloudflare/cloudflared/releases), then "
    "try again"
)

_cached_binary: str | None = None


def find_binary() -> str | None:
    """Cached once found - cloudflared isn't going anywhere mid-event - but
    rechecked every time until then, so installing it while the app is
    already running and pressing Enable again just works."""
    global _cached_binary
    if _cached_binary and Path(_cached_binary).is_file():
        return _cached_binary
    found = shutil.which("cloudflared") or next(
        (p for p in KNOWN_PATHS if Path(p).is_file()), None)
    if found:
        _cached_binary = found
    return found


class CloudflareTunnel:
    """One instance per relay session. `enabled` is the operator's intent
    (persisted, survives a restart via [tunnel] start_enabled); `running`
    is whether a cloudflared process is actually alive right now; the two
    differ while cloudflared is missing, mid-reconnect, or freshly stopped.
    """

    def __init__(self, token: str = ""):
        self.token = (token or "").strip()
        self.configured = bool(self.token)
        self.enabled = False
        self.running = False
        self.connected = False
        self.error: str | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None

    def binary(self) -> str | None:
        return find_binary()

    def configure(self, token: str | None = None) -> None:
        if token is not None:
            token = token.strip()
            if token != self.token:
                self.error = None
            self.token = token
            self.configured = bool(self.token)
            if not self.configured:
                self.enabled = False

    async def start(self) -> None:
        if not self.configured:
            self.error = "paste a tunnel token first"
            self.enabled = False
            return
        binary = self.binary()
        if binary is None:
            self.error = NOT_INSTALLED
            self.enabled = False
            return
        if self.running:
            self.enabled = True
            return
        self.error = None
        try:
            env = {**os.environ, "TUNNEL_TOKEN": self.token}
            self._proc = await asyncio.create_subprocess_exec(
                binary, "tunnel", "run",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
        except OSError as exc:
            self.error = f"could not start cloudflared: {exc}"
            self.enabled = False
            return
        self.enabled = True
        self.running = True
        self._reader_task = asyncio.create_task(self._read_output())

    async def _read_output(self) -> None:
        """Mirrors cloudflared's own log lines into this process's stdout
        (already captured to a log file - see relay/config.py), and picks
        the two signals worth reflecting on the status page out of them."""
        proc = self._proc
        fatal = False
        try:
            async for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").rstrip()
                if not line:
                    continue
                print(f"[cloudflared] {line}")
                if "Registered tunnel connection" in line:
                    self.connected = True
                    self.error = None
                lowered = line.lower()
                if "failed to" in lowered or "connection error" in lowered or " err=" in lowered:
                    self.error = line
                # A bad token is permanent - a wrong password doesn't fix
                # itself on the next try, so retrying it forever every few
                # seconds would just look broken instead of resilient.
                if "not valid" in lowered or "unauthoriz" in lowered:
                    self.error = line
                    fatal = True
        except Exception:
            pass
        finally:
            code = proc.returncode
            self.running = False
            self.connected = False
            self._proc = None
            retry = self.enabled and not fatal   # False if stop() already ran
            if fatal:
                self.enabled = False
            elif retry:
                self.error = self.error or (
                    f"cloudflared exited unexpectedly (code {code}); reconnecting")
                await asyncio.sleep(3.0)
                if self.enabled:      # still wanted - stop() hasn't run since
                    await self.start()

    async def stop(self) -> None:
        self.enabled = False
        proc = self._proc
        if proc is not None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
        task = self._reader_task
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=6.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
        self._proc = None
        self._reader_task = None
        self.running = False
        self.connected = False

    def status(self) -> dict:
        return {
            "installed": self.binary() is not None,
            "configured": self.configured,
            "enabled": self.enabled,
            "running": self.running,
            "connected": self.connected,
            "error": self.error,
        }
