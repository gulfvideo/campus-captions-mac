"""The ASR interface: audio frames in, interim/final transcript events out.

This is the whole contract. The relay and the viewers never learn which backend
is running - they only ever see the messages in docs/MESSAGES.md. Adding a local
on-device backend later means writing one new file next to this one and adding a
line to registry.py; nothing else in the project changes.

Lifecycle is explicit on purpose:

    backend = SomeBackend(config)
    await backend.open(sample_rate=48000)     # a paid connection starts HERE
    await backend.send_audio(frames)          # repeatedly
    await backend.close()                     # and stops HERE

Nothing opens a stream implicitly. The capture client runs with the microphone
live and the stream closed all through setup and rehearsal, because Deepgram
bills connection time, not speech time.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TranscriptEvent:
    """One piece of transcript. `is_final` is the only thing that commits a line."""

    text: str
    is_final: bool
    t: float = field(default_factory=time.time)


class ASRBackend(ABC):
    """Base class. Subclasses push events with `self._emit(...)`."""

    name: str = "base"

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self._queue: asyncio.Queue[TranscriptEvent] = asyncio.Queue(maxsize=512)
        self._open = False
        self._error: str | None = None

    # -- lifecycle -------------------------------------------------------

    @abstractmethod
    async def open(self, sample_rate: int) -> None:
        """Start producing transcript. For paid backends, start billing."""

    @abstractmethod
    async def close(self) -> None:
        """Stop producing transcript and release the connection."""

    @abstractmethod
    async def send_audio(self, frames: bytes) -> None:
        """Hand over one block of mono 16-bit PCM at the opened sample rate."""

    async def reload_keyterms(self) -> None:
        """Re-read vocabulary. Backends without keyterms do nothing."""
        return None

    # -- output ----------------------------------------------------------

    def events(self) -> AsyncIterator[TranscriptEvent]:
        """Async iterator of transcript events. One consumer."""
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[TranscriptEvent]:
        while True:
            yield await self._queue.get()

    def _emit(self, text: str, is_final: bool) -> None:
        text = " ".join(str(text).split())
        if not text:
            return
        try:
            self._queue.put_nowait(TranscriptEvent(text=text, is_final=is_final))
        except asyncio.QueueFull:
            # Transcript is worthless if it is late. Drop the oldest interim
            # rather than block the audio path or grow without limit.
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(TranscriptEvent(text=text, is_final=is_final))
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    # -- reported to the status page -------------------------------------

    @property
    def stream_open(self) -> bool:
        return self._open

    @property
    def error(self) -> str | None:
        return self._error
