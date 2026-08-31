"""Live audio input.

Device selection is by index *or* name substring, because the show machine's
device list does not match a laptop's. Windows lists the same physical input
several times - once per host API (MME, DirectSound, WASAPI) - so an index that
is right on one machine is wrong on another. A name match is stable across
machines; when a name matches several entries, WASAPI wins.

Audio is captured as mono 16-bit PCM. No resampling: whatever rate the device
opens at is reported to the backend, which tells the ASR service. That avoids a
resampling stage and the quality and latency that come with it.
"""

from __future__ import annotations

import asyncio
import math
import queue
import time
from dataclasses import dataclass

try:
    import numpy as np
    import sounddevice as sd
    AUDIO_AVAILABLE = True
    AUDIO_IMPORT_ERROR = ""
except Exception as exc:                       # no PortAudio, headless box, ...
    AUDIO_AVAILABLE = False
    AUDIO_IMPORT_ERROR = f"{exc.__class__.__name__}: {exc}"

# 16 kHz is plenty for speech and is what ASR services want; fall back to the
# device's own rate when it cannot do 16k.
PREFERRED_RATE = 16000
BLOCK_MS = 100                                  # 100 ms blocks
HOSTAPI_PREFERENCE = ("Windows WASAPI", "Windows DirectSound", "MME")
# A transient glitch should disappear from the status page on its own.
ERROR_TTL = 6.0


@dataclass
class DeviceChoice:
    index: int
    name: str
    hostapi: str
    sample_rate: int
    channels: int

    def __str__(self) -> str:
        return f"{self.name} [{self.hostapi}] @ {self.sample_rate} Hz"


def list_input_devices() -> list[dict]:
    if not AUDIO_AVAILABLE:
        return []
    out = []
    for index, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] < 1:
            continue
        out.append({
            "index": index,
            "name": dev["name"],
            "hostapi": sd.query_hostapis(dev["hostapi"])["name"],
            "channels": dev["max_input_channels"],
            "default_samplerate": int(dev["default_samplerate"]),
        })
    return out


def print_devices() -> None:
    devices = list_input_devices()
    if not devices:
        print("no input devices found"
              + (f" ({AUDIO_IMPORT_ERROR})" if not AUDIO_AVAILABLE else ""))
        return
    try:
        default_index = sd.default.device[0]
    except Exception:
        default_index = None
    print(f"{'idx':>4}  {'name':<44} {'host api':<22} {'in':>3}  {'rate':>6}")
    print("-" * 86)
    for dev in devices:
        mark = " *" if dev["index"] == default_index else "  "
        print(f"{dev['index']:>4}{mark}{dev['name'][:44]:<44} "
              f"{dev['hostapi'][:22]:<22} {dev['channels']:>3}  "
              f"{dev['default_samplerate']:>6}")
    print("\n  * = system default input")
    print("  select with --device <index> or --device \"<part of the name>\"")
    print("  a name is safer than an index: indices differ between machines")


def resolve_device(spec: str | int | None) -> DeviceChoice:
    """Turn '3', 'yeti' or None into a concrete device."""
    if not AUDIO_AVAILABLE:
        raise RuntimeError(f"audio unavailable: {AUDIO_IMPORT_ERROR}")

    devices = list_input_devices()
    if not devices:
        raise RuntimeError("no input devices found")

    chosen: dict | None = None
    spec_str = "" if spec is None else str(spec).strip()

    if spec_str == "":
        try:
            default_index = sd.default.device[0]
        except Exception:
            default_index = None
        chosen = next((d for d in devices if d["index"] == default_index), devices[0])
    elif spec_str.lstrip("-").isdigit():
        index = int(spec_str)
        chosen = next((d for d in devices if d["index"] == index), None)
        if chosen is None:
            raise RuntimeError(
                f"device index {index} is not an input device. "
                f"run with --list-devices to see what this machine has."
            )
    else:
        needle = spec_str.lower()
        matches = [d for d in devices if needle in d["name"].lower()]
        if not matches:
            raise RuntimeError(
                f"no input device matching {spec_str!r}. "
                f"run with --list-devices to see what this machine has."
            )
        # Same physical device under several host APIs: prefer the modern one.
        def rank(dev: dict) -> int:
            try:
                return HOSTAPI_PREFERENCE.index(dev["hostapi"])
            except ValueError:
                return len(HOSTAPI_PREFERENCE)
        matches.sort(key=rank)
        chosen = matches[0]
        if len(matches) > 1:
            others = ", ".join(f"{m['index']}:{m['hostapi']}" for m in matches[1:])
            print(f"[audio] {spec_str!r} matched {len(matches)} devices, "
                  f"using {chosen['index']} [{chosen['hostapi']}] (also {others})")

    rate = _pick_rate(chosen)
    return DeviceChoice(
        index=chosen["index"],
        name=chosen["name"],
        hostapi=chosen["hostapi"],
        sample_rate=rate,
        channels=1,
    )


def _pick_rate(dev: dict) -> int:
    for rate in (PREFERRED_RATE, dev["default_samplerate"], 48000, 44100):
        try:
            sd.check_input_settings(device=dev["index"], channels=1,
                                    dtype="int16", samplerate=rate)
            return int(rate)
        except Exception:
            continue
    return int(dev["default_samplerate"])


class AudioInput:
    """Reads a device and hands out 16-bit PCM blocks plus a live level.

    The PortAudio callback runs on its own thread, so blocks cross into asyncio
    through a bounded queue. If the consumer stalls, blocks are dropped rather
    than piling up - late audio is worthless to a live caption.
    """

    def __init__(self, device: DeviceChoice, block_ms: int = BLOCK_MS):
        self.device = device
        self.block_frames = max(160, int(device.sample_rate * block_ms / 1000))
        self.level = 0.0
        self.blocks_dropped = 0
        self.blocks_read = 0
        self.overflows = 0
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=32)
        self._stream = None
        self._error: str | None = None
        self._error_at = 0.0

    def _note_error(self, message: str) -> None:
        self._error = message
        self._error_at = time.monotonic()

    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            # PortAudio glitches are usually a single dropped buffer at startup
            # or when another app grabs the device. Count them, report them, but
            # never latch - a stale error on the status page during an event
            # sends the operator hunting for a problem that already passed.
            if getattr(status, "input_overflow", False):
                self.overflows += 1
            self._note_error(f"audio: {status}")
        try:
            mono = indata[:, 0] if indata.ndim > 1 else indata
            # RMS of int16 normalised to 0..1, for the status page meter.
            samples = mono.astype(np.float32) / 32768.0
            self.level = float(math.sqrt(float(np.mean(samples * samples)) + 1e-12))
            self._queue.put_nowait(mono.tobytes())
            self.blocks_read += 1
        except queue.Full:
            self.blocks_dropped += 1
        except Exception as exc:
            self._note_error(f"{exc.__class__.__name__}: {exc}")

    def start(self) -> None:
        self._stream = sd.InputStream(
            device=self.device.index,
            channels=1,
            samplerate=self.device.sample_rate,
            dtype="int16",
            blocksize=self.block_frames,
            # Ask PortAudio for generous buffers. Caption latency is dominated
            # by the ASR service, not by this, and the older Windows host APIs
            # (MME especially) overflow at low latency settings.
            latency="high",
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    async def blocks(self):
        """Async iterator of PCM blocks."""
        loop = asyncio.get_running_loop()
        while True:
            block = await loop.run_in_executor(None, self._get_block)
            if block is None:
                await asyncio.sleep(0.05)
                continue
            yield block

    def _get_block(self) -> bytes | None:
        try:
            return self._queue.get(timeout=0.5)
        except queue.Empty:
            return None

    @property
    def error(self) -> str | None:
        if self._error and (time.monotonic() - self._error_at) > ERROR_TTL:
            self._error = None
        return self._error


class SilentInput:
    """Stand-in when there is no audio hardware.

    Lets the whole system - relay, viewers, status page, mock backend - run on a
    machine with no microphone at all, which is most development.
    """

    def __init__(self, sample_rate: int = PREFERRED_RATE):
        self.device = DeviceChoice(-1, "no audio device", "none", sample_rate, 1)
        self.level = 0.0
        self.blocks_dropped = 0
        self.blocks_read = 0
        self.error = None

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    async def blocks(self):
        while True:
            await asyncio.sleep(0.1)
            yield b""
