"""Everything the status page needs to know, in one place.

The relay deliberately knows nothing about which ASR backend is running. It
records whatever the capture client reports - a backend name, a device name, a
level, an open/closed flag - and never interprets it. Swapping Deepgram for a
local backend later changes nothing in this file.
"""

from __future__ import annotations

import statistics
import time
from collections import deque
from dataclasses import dataclass, field

# How long a signal stays meaningful before the status page should stop
# trusting it.
STATUS_STALE_AFTER = 6.0        # no status frame from capture in this long
AUDIO_SILENT_BELOW = 0.008      # RMS under this is silence, not speech
AUDIO_QUIET_AFTER = 3.0         # no audio above the floor in this long
ASR_IDLE_AFTER = 30.0           # stream open but no text in this long

# Caption latency: seconds from the words being spoken (the backend's
# `speech_end`) to the text reaching the relay. Kept as a rolling window so
# the figure describes the last few minutes, not the whole event; anything
# outside the sane range is a clock problem, not a latency, and is ignored.
LATENCY_WINDOW = 200
LATENCY_MAX_SANE = 60.0

# The level is smoothed before it decides anything. One stray sample of room
# tone crossing the floor must not make the status page claim audio is live -
# an operator glancing at this needs it to mean "speech is arriving".
AUDIO_EMA_ALPHA = 0.25


def _clean_host(raw: dict) -> dict:
    """The capture client's host block, with only the fields and types the
    preflight reads - anything else a client sends is dropped, not stored."""
    def num(value):
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    busiest = []
    for item in (raw.get("busiest") or [])[:6]:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            pct = num(item.get("cpu_percent"))
            if pct is not None:
                busiest.append({"name": item["name"][:48], "cpu_percent": round(pct, 1)})
    avail = num(raw.get("mem_available_mb"))
    return {
        "available": bool(raw.get("available")),
        "cpu_percent": num(raw.get("cpu_percent")),
        "cpu_count": int(raw["cpu_count"]) if isinstance(raw.get("cpu_count"), int) else None,
        "mem_percent": num(raw.get("mem_percent")),
        "mem_available_mb": int(avail) if avail is not None else None,
        "own_cpu_percent": num(raw.get("own_cpu_percent")),
        "busiest": busiest,
        "age": num(raw.get("age")),
    }


@dataclass
class RelayState:
    session_id: str
    started_at: float = field(default_factory=time.time)

    # reported by the capture client
    source_connected: bool = False
    source_peer: str | None = None
    backend: str | None = None
    available_backends: list[str] = field(default_factory=list)
    # The capture client's Whisper model state - is faster-whisper importable,
    # has the model been downloaded, is a download running. Passed straight
    # through: the relay makes no judgement about it, it only carries it to
    # the setup wizard and the settings page, which offer the download.
    whisper: dict = field(default_factory=dict)
    has_deepgram_key: bool = False
    mask_profanity: bool = False
    blocked_word_count: int = 0
    device_busy: bool = False               # another program holds the audio input
    audio_restarts: int = 0                 # input reopened after it stopped responding
    host: dict = field(default_factory=dict)  # capture machine's CPU/memory, see capture/host.py
    device: str | None = None
    device_index: int | None = None
    audio_devices: list[dict] = field(default_factory=list)
    stream_open: bool = False
    audio_level: float = 0.0
    audio_ema: float = 0.0
    audio_peak: float = 0.0
    clipping: bool = False
    error: str | None = None

    # observed by the relay
    last_status_at: float | None = None
    last_audio_at: float | None = None      # last frame above the silence floor
    last_interim_at: float | None = None
    last_final_at: float | None = None
    last_final_text: str = ""
    finals: int = 0
    interims: int = 0
    # speech-to-relay latency samples, seconds; finals are what a viewer reads,
    # interims are how soon the first words appear
    final_latency: deque = field(default_factory=lambda: deque(maxlen=LATENCY_WINDOW))
    interim_latency: deque = field(default_factory=lambda: deque(maxlen=LATENCY_WINDOW))

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
        peak = msg.get("audio_peak")
        if isinstance(peak, (int, float)) and not isinstance(peak, bool):
            self.audio_peak = max(0.0, min(1.0, float(peak)))
        self.clipping = bool(msg.get("clipping"))
        self.stream_open = bool(msg.get("stream_open"))
        self.has_deepgram_key = bool(msg.get("has_deepgram_key"))
        self.mask_profanity = bool(msg.get("mask_profanity"))
        count = msg.get("blocked_word_count")
        if isinstance(count, int) and not isinstance(count, bool):
            self.blocked_word_count = max(0, count)
        self.device_busy = bool(msg.get("device_busy"))
        # Cumulative for the session. A rising count during an event is the
        # clearest evidence there is that an interface is flaky, and it is the
        # only trace of a restart that survives outside the log file.
        restarts = msg.get("audio_restarts")
        if isinstance(restarts, int) and not isinstance(restarts, bool):
            self.audio_restarts = max(0, restarts)
        host = msg.get("host")
        if isinstance(host, dict):
            self.host = _clean_host(host)
        for key in ("backend", "device"):
            val = msg.get(key)
            if isinstance(val, str):
                setattr(self, key, val[:128])
        whisper = msg.get("whisper")
        if isinstance(whisper, dict):
            self.whisper = {
                "installed": bool(whisper.get("installed")),
                "packaged": bool(whisper.get("packaged")),
                "installable": bool(whisper.get("installable")),
                "present": bool(whisper.get("present")),
                "state": str(whisper.get("state", ""))[:32],
                "message": str(whisper.get("message", ""))[:200],
                "model": str(whisper.get("model", ""))[:64],
                "path": str(whisper.get("path", ""))[:512],
            }

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

    def note_interim(self, speech_end: float | None = None) -> None:
        self.interims += 1
        self.last_interim_at = time.time()
        self._note_latency(self.interim_latency, speech_end, self.last_interim_at)

    def note_final(self, text: str, speech_end: float | None = None) -> None:
        self.finals += 1
        self.last_final_at = time.time()
        self.last_final_text = text
        self._note_latency(self.final_latency, speech_end, self.last_final_at)

    @staticmethod
    def _note_latency(bucket: deque, speech_end: float | None, arrived: float) -> None:
        if speech_end is None:
            return
        latency = arrived - speech_end
        # Negative means the two clocks disagree (capture and relay on
        # different machines); huge means a stale timestamp. Neither is a
        # caption latency, so neither goes in the window.
        if 0.0 <= latency <= LATENCY_MAX_SANE:
            bucket.append(latency)

    @staticmethod
    def _latency_stats(bucket: deque) -> dict | None:
        if not bucket:
            return None
        values = sorted(bucket)
        p95_index = min(len(values) - 1, int(round(0.95 * (len(values) - 1))))
        return {
            "samples": len(values),
            "last": round(bucket[-1], 2),
            "median": round(statistics.median(values), 2),
            "p95": round(values[p95_index], 2),
        }

    def source_dropped(self) -> None:
        self.source_connected = False
        self.source_peer = None
        self.stream_open = False
        self.audio_level = 0.0
        self.audio_ema = 0.0
        self.audio_peak = 0.0
        self.clipping = False

    # -- derived health -------------------------------------------------
    # One place decides what "green" means, so the status page only renders.

    def _age(self, stamp: float | None, now: float) -> float | None:
        return None if stamp is None else round(now - stamp, 2)

    def audio_status(self, now: float) -> str:
        if not self.source_connected:
            return "no_source"
        if self.last_status_at is None or (now - self.last_status_at) > STATUS_STALE_AFTER:
            return "stale"
        if self.clipping:
            return "clipping"           # audio is arriving, and too hot to use well
        if self.last_audio_at is None or (now - self.last_audio_at) > AUDIO_QUIET_AFTER:
            return "silent"
        return "live"

    def has_recent_text(self, now: float, within: float = STATUS_STALE_AFTER) -> bool:
        """Transcript messages arrive over the same connection as status
        heartbeats but are tracked independently (note_interim/note_final,
        not note_status). A slow event loop can delay the heartbeat while
        captions keep flowing fine - this is how the audience-facing fault
        check tells that apart from an actual dead pipeline."""
        last_text = max(
            [t for t in (self.last_interim_at, self.last_final_at) if t is not None],
            default=None,
        )
        return last_text is not None and (now - last_text) <= within

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
                "whisper": self.whisper,
                "has_deepgram_key": self.has_deepgram_key,
                "mask_profanity": self.mask_profanity,
                "blocked_word_count": self.blocked_word_count,
                "device": self.device,
                "device_index": self.device_index,
                "device_busy": self.device_busy,
                "audio_restarts": self.audio_restarts,
                "audio_devices": self.audio_devices,
                "status_age": self._age(self.last_status_at, now),
                "error": self.error,
            },
            "host": dict(self.host),
            "audio": {
                "level": round(self.audio_level, 4),
                "smoothed": round(self.audio_ema, 4),
                "peak": round(self.audio_peak, 4),
                "clipping": self.clipping,
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
            "latency": {
                "final": self._latency_stats(self.final_latency),
                "interim": self._latency_stats(self.interim_latency),
            },
            "viewers": viewers,
        }
