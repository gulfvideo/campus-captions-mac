"""Mock backend: replays a scripted transcript, ignores audio entirely.

A first-class backend, not a stub. Most development and every soak test runs
against this one: no audio hardware, no API key, no spend, and repeatable
output. It obeys the same open/close lifecycle as the real backends so the
operator flow can be rehearsed exactly as it will run on the night.

Script format (config/mock_script.txt):

    # comment
    A line that will be spoken word by word, then committed.
                        <- a blank line is a pause
    [clear]             <- wipes the display, as an operator would
"""

from __future__ import annotations

import asyncio
import random
import time
from pathlib import Path

from .base import ASRBackend


def load_script(path: Path) -> list[str]:
    if not path.exists():
        return [f"Mock script not found at {path}."]
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        lines.append(line)
    while lines and not lines[0]:
        lines.pop(0)
    return lines or ["Mock script is empty."]


class MockBackend(ASRBackend):
    name = "mock"

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        cfg = self.config
        # An empty value in event.toml must fall back, not resolve to "."
        self.script_path = Path(
            str(cfg.get("mock_script") or "config/mock_script.txt"))
        self.interim_ms = float(cfg.get("mock_interim_ms", 180))
        self.gap_ms = float(cfg.get("mock_gap_ms", 900))
        self.jitter = float(cfg.get("mock_jitter", 0.35))
        self.loop_script = bool(cfg.get("mock_loop", True))
        self.script: list[str] = []
        self._task: asyncio.Task | None = None
        self._idx = 0
        # Set when the script asks for a clear; the capture client drains this.
        self.clear_requests: asyncio.Queue[bool] = asyncio.Queue()

    async def open(self, sample_rate: int) -> None:
        if self._open:
            return
        self.script = load_script(self.script_path)
        self._open = True
        self._error = None
        self._task = asyncio.create_task(self._play())

    async def close(self) -> None:
        self._open = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def send_audio(self, frames: bytes) -> None:
        """Audio is intentionally ignored - that is the point of this backend."""
        return None

    async def reload_keyterms(self) -> None:
        # Nothing to reload, but reloading the script is the useful analogue:
        # edit the transcript and press the button to pick it up.
        self.script = load_script(self.script_path)
        self._idx = 0

    async def _play(self) -> None:
        try:
            while self._open:
                if self._idx >= len(self.script):
                    if not self.loop_script:
                        await asyncio.sleep(3600)
                        continue
                    self._idx = 0
                line = self.script[self._idx]
                self._idx += 1

                if not line:
                    await asyncio.sleep(self.gap_ms / 1000.0 * 3)
                    continue
                if line.lower() == "[clear]":
                    await self.clear_requests.put(True)
                    await asyncio.sleep(self.gap_ms / 1000.0)
                    continue

                built = ""
                for word in line.split():
                    built = f"{built} {word}".strip()
                    # The mock "speaks" each word the instant it emits it, so
                    # the latency it reports is the pipeline's own overhead
                    # and nothing else - what a soak test wants to see.
                    self._emit(built, is_final=False, speech_end=time.time())
                    wobble = 1.0 + random.uniform(-self.jitter, self.jitter)
                    await asyncio.sleep(max(0.01, self.interim_ms / 1000.0 * wobble))
                self._emit(line, is_final=True, speech_end=time.time())
                await asyncio.sleep(self.gap_ms / 1000.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                      # never kill the capture app
            self._error = f"mock backend: {exc}"
