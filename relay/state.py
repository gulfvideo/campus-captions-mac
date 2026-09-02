"""Everything the status page needs to know, in one place.

The relay deliberately knows nothing about which ASR backend is running. It
records whatever the capture client reports - a backend name, a device name, a
level, an open/closed flag - and never interprets it. Swapping Deepgram for a
local backend later changes nothing in this file.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

# How long a signal stays meaningful before the status page should stop
# trusting it.
STATUS_STALE_AFTER = 4.0        # no status frame from capture in this long
AUDIO_SILENT_BELOW = 0.008      # RMS under this is silence, not speech
AUDIO_QUIET_AFTER = 3.0         # no audio above the floor in this long
ASR_IDLE_AFTER = 30.0           # stream open but no text in this long

# The level is smoothed before it decides anything. One stray sample of room
# tone crossing the floor must not make the status page claim audio is live -
# an operator glancing at this needs it to mean "speech is arriving".
AUDIO_EMA_ALPHA = 0.25


@dataclass
class RelayState:
    session_id: str
    started_at: float = field(default_factory=time.time)

    # reported by the capture client
    source_connected: bool = False
    source_peer: str | None = None
    backend: str | None = None
    available_backends: list[str] = field(default_factory=list)
    has_deepgram_key: bool = False
    device: str | None = None
    device_index: int | None = None
    audio_devices: list[dict] = field(default_factory=list)
    stream_open: bool = False
    audio_level: float = 0.0
    audio_ema: float = 0.0
    error: str | None = None

    # observed by the relay
    last_status_at: float | None = None
    last_audio_at: float | None = None      # last frame above the silence floor
    last_interim_at: float | None = None
    last_final_at: float | None = None
    last_final_text: str = ""
    finals: int = 0
    interims: int = 0

    def note_status(self, msg: dict, peer: str) -> None:
        now = time.time()
        self.source_connected = True
        self.source_peer = peer
        self.last_status_at = now
        level = msg.get("audio_level")
        if isinstance(level, (int, float)):
            self.audio_level = max(0.0, min(1.0, float(level)))
            self.audio_ema = (
                (1.0 - AUDIO_EMA_ALPHA) * self.audio_ema
                + AUDIO_EMA_ALPHA * self.audio_level
            )
            if self.audio_ema >= AUDIO_SILENT_BELOW:
                self.last_audio_at = now
        self.stream_open = bool(msg.get("stream_open"))
        self.has_deepgram_key = bool(msg.get("has_deepgram_key"))
        for key in ("backend", "device"):
            val = msg.get(key)
            if isinstance(val, str):
                setattr(self, key, val[:128])
        backends = msg.get("available_backends")
        if isinstance(backends, list):
            self.available_backends = [str(b)[:32] for b in backends[:16] if isinstance(b, str)]
        device_index = msg.get("device_index")
        if isinstance(device_index, int) and not isinstance(device_index, bool):
            self.device_index = device_index
        devices = msg.get("audio_devices")
        if isinstance(devices, list):
            cleaned = []
            def safe_int(value: object) -> int:
                try:
                    return int(value)
                except (TypeError, ValueError, OverflowError):
                    return 0
            for item in devices[:64]:
                if not isinstance(item, dict):
                    continue
                index = item.get("index")
                if isinstance(index, bool) or not isinstance(index, int):
                    continue
                cleaned.append({
                    "index": index,
                    "name": str(item.get("name", ""))[:160],
                    "hostapi": str(item.get("hostapi", ""))[:80],
                    "channels": safe_int(item.get("channels", 0)),
                    "default_samplerate": safe_int(item.get("default_samplerate", 0)),
                    "is_default": bool(item.get("is_default", False)),
                })
            self.audio_devices = cleaned
        err = msg.get("error")
        self.error = err[:512] if isinstance(err, str) and err else None

    def note_interim(self) -> None:
        self.interims += 1
        self.last_interim_at = time.time()

    def note_final(self, text: str) -> None:
        self.finals += 1
        self.last_final_at = time.time()
        self.last_final_text = text

    def source_dropped(self) -> None:
        self.source_connected = False
        self.source_peer = None
        self.stream_open = False
        self.audio_level = 0.0
        self.audio_ema = 0.0

    # -- derived health -------------------------------------------------
    # One place decides what "green" means, so the status page only renders.

    def _age(self, stamp: float | None, now: float) -> float | None:
        return None if stamp is None else round(now - stamp, 2)

    def audio_status(self, now: float) -> str:
        if not self.source_connected:
            return "no_source"
        if self.last_status_at is None or (now - self.last_status_at) > STATUS_STALE_AFTER:
            return "stale"
        if self.last_audio_at is None or (now - self.last_audio_at) > AUDIO_QUIET_AFTER:
            return "silent"
        return "live"

    def asr_status(self, now: float) -> str:
        if not self.source_connected:
            return "no_source"
        if not self.stream_open:
            return "closed"
        last_text = max(
            [t for t in (self.last_interim_at, self.last_final_at) if t is not None],
            default=None,
        )
        if last_text is None or (now - last_text) > ASR_IDLE_AFTER:
            return "open_no_text"
        return "producing"

    def snapshot(self, viewers: dict[str, int], seq: int) -> dict:
        now = time.time()
        return {
            "type": "status",
            "t": round(now, 3),
            "session_id": self.session_id,
            "uptime": round(now - self.started_at, 1),
            "seq": seq,
            "source": {
                "connected": self.source_connected,
                "peer": self.source_peer,
                "backend": self.backend,
                "available_backends": self.available_backends,
                "has_deepgram_key": self.has_deepgram_key,
                "device": self.device,
                "device_index": self.device_index,
                "audio_devices": self.audio_devices,
                "status_age": self._age(self.last_status_at, now),
                "error": self.error,
            },
            "audio": {
                "level": round(self.audio_level, 4),
                "smoothed": round(self.audio_ema, 4),
                "state": self.audio_status(now),
                "age": self._age(self.last_audio_at, now),
            },
            "asr": {
                "stream_open": self.stream_open,
                "state": self.asr_status(now),
                "finals": self.finals,
                "interims": self.interims,
                "last_final_age": self._age(self.last_final_at, now),
                "last_final_text": self.last_final_text,
            },
            "viewers": viewers,
        }
