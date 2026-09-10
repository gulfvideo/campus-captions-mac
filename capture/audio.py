"""Live audio input.

Device selection is by index *or* name substring, because the show machine's
device list does not match a laptop's. Windows lists the same physical input
several times - once per host API (MME, DirectSound, WASAPI) - so an index that
is right on one machine is wrong on another, and they renumber when hardware is
plugged in. A name match is stable; when a name matches several entries the
best host API for the platform wins.

macOS has one host API (Core Audio) so there is nothing to disambiguate, but it
has a hazard Windows does not: without Microphone permission the process is
handed a stream of digital silence rather than an error. That looks exactly like
a muted mic, so it is detected and reported separately.

Audio is captured as mono 16-bit PCM. No resampling: whatever rate the device
opens at is reported to the backend, which tells the ASR service. That avoids a
resampling stage and the quality and latency that come with it.
"""

from __future__ import annotations

import asyncio
import math
import queue
import sys
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

# Best host API first, per platform. On macOS there is only one.
HOSTAPI_PREFERENCE = {
    "darwin": ("Core Audio",),
    "win32": ("Windows WASAPI", "Windows DirectSound", "MME"),
}.get(sys.platform, ("ALSA", "PulseAudio", "JACK"))

# A transient glitch should disappear from the status page on its own.
ERROR_TTL = 6.0

# Real hardware always has a noise floor. Sustained, perfectly zero samples mean
# the OS is handing us silence instead of the device - but *why* has several
# answers, and naming the wrong one sends an operator to a settings pane that
# was never the problem. In the order these actually happen on an AV desk: a
# virtual device (Loopback, NDI, Teams) with nothing routed into it yet, a
# docked MacBook whose built-in mic is inert while the lid is shut, and only
# then a missing permission. So describe the symptom and list the causes.
DIGITAL_SILENCE_AFTER = 5.0

# PortAudio's `active` flag is not proof of life. A driver can leave a stream
# marked active while it has stopped calling us back at all - reported on
# Windows, and the same shape of failure as a USB interface that is unplugged
# and replugged mid-event. Nothing downstream notices: the level meter simply
# holds its last value and the ASR backend keeps decoding a buffer nobody is
# refilling. Counting callbacks is the reliable signal, because a working
# device delivers one every BLOCK_MS whether or not anyone is talking.
CALLBACK_STALL_AFTER = 3.0

# Clipping: samples pinned at (or within a hair of) full scale. A single
# sample can be a click; a run of them in one 100 ms block is a level that is
# too hot for the interface, which ASR handles badly. The flag stays raised a
# few seconds after the last hit so the status page can be read, not caught.
CLIP_THRESHOLD = 32200               # of 32767 - allows for interfaces that limit
CLIP_MIN_SAMPLES = 8                 # per block, before a block counts as clipped
CLIP_HOLD_SECONDS = 3.0

NO_SIGNAL_HINT = (
    "no signal at all from this device - every sample is silent. Check that "
    "something is actually feeding it (a virtual input like Loopback or NDI "
    "needs audio routed in), that nothing else has it muted or open, and that "
    "microphone access is granted - on macOS in System Settings > Privacy & "
    "Security > Microphone."
)
# Appended when the silent input is the machine's own built-in mic, which is
# the one case with a fix that is not a settings pane.
LID_CLOSED_HINT = (
    " A MacBook's built-in mic also goes silent while the lid is closed - "
    "pick another input when docked."
)
BUILT_IN_MIC_MARKERS = ("macbook", "built-in", "imac ")

# PortAudio's ways of saying "the device exists but you cannot have it right
# now". On Windows that is almost always another program holding the input in
# WASAPI exclusive mode (OBS or vMix with "exclusive" ticked, a DAW); on macOS
# it is rarer but a virtual device's owner can do it. The preflight (N-02,
# N-10) turns this into "another program has the input" with the fix, rather
# than a PortAudio error code.
DEVICE_BUSY_MARKERS = (
    "unanticipated host error", "-9999", "device unavailable", "-9985",
    "in use", "exclusive", "access denied", "-9996",
)


def device_busy_hint(exc: BaseException | str) -> str:
    """A plain sentence when an open failure looks like the device being held
    by another program, else ""."""
    text = str(exc).lower()
    if not any(marker in text for marker in DEVICE_BUSY_MARKERS):
        return ""
    if sys.platform == "win32":
        return ("another program appears to have this input open in exclusive mode. "
                "Windows: Settings > System > Sound > the device > Advanced, untick "
                "\"Allow applications to take exclusive control\", or untick exclusive "
                "mode in OBS/vMix - or pick a different input.")
    return ("another program appears to have this input open. Quit the program "
            "that is using it, or pick a different input.")


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
    try:
        default_index = sd.default.device[0]
    except Exception:
        default_index = None
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
            "is_default": index == default_index,
        })
    return out


def print_devices() -> None:
    devices = list_input_devices()
    if not devices:
        print("no input devices found"
              + (f" ({AUDIO_IMPORT_ERROR})" if not AUDIO_AVAILABLE else ""))
        return
    print(f"{'idx':>4}  {'name':<44} {'host api':<22} {'in':>3}  {'rate':>6}")
    print("-" * 86)
    for dev in devices:
        mark = " *" if dev["is_default"] else "  "
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
        self.peak = 0.0                      # loudest sample in the last block, 0..1
        self.clipped_blocks = 0
        self._last_clip_at = 0.0
        self.blocks_dropped = 0
        self.blocks_read = 0
        self.overflows = 0
        self.nonzero_blocks = 0
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=32)
        self._stream = None
        self._running = False
        self._error: str | None = None
        self._error_at = 0.0
        self._started_at: float | None = None
        self._last_callback_at = 0.0

    def _note_error(self, message: str) -> None:
        self._error = message
        self._error_at = time.monotonic()

    def _callback(self, indata, frames, time_info, status) -> None:
        # First, before anything that can fail: the driver called us, and that
        # is the fact the watchdog needs. A callback that arrives carrying an
        # error status is still a live stream.
        self._last_callback_at = time.monotonic()
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
            # Any sample at all being non-zero proves the OS is really giving us
            # this device, as opposed to silence in place of it.
            if mono.any():
                self.nonzero_blocks += 1
            # RMS of int16 normalised to 0..1, for the status page meter.
            samples = mono.astype(np.float32) / 32768.0
            self.level = float(math.sqrt(float(np.mean(samples * samples)) + 1e-12))
            # Peak and clipping, from the raw int16 so full scale is exact.
            magnitude = np.abs(mono.astype(np.int32))
            self.peak = float(magnitude.max()) / 32768.0 if magnitude.size else 0.0
            if int(np.count_nonzero(magnitude >= CLIP_THRESHOLD)) >= CLIP_MIN_SAMPLES:
                self.clipped_blocks += 1
                self._last_clip_at = time.monotonic()
            self._queue.put_nowait(mono.tobytes())
            self.blocks_read += 1
        except queue.Full:
            self.blocks_dropped += 1
        except Exception as exc:
            self._note_error(f"{exc.__class__.__name__}: {exc}")

    def start(self) -> None:
        self._started_at = time.monotonic()
        self._last_callback_at = self._started_at
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
        self._running = True

    def stop(self) -> None:
        # Set this before closing PortAudio so blocks() exits even when the
        # stopped device has no queued frame left to yield. Runtime device
        # switching relies on that iterator ending so pump_audio can attach to
        # the replacement input.
        self._running = False
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
        while self._running:
            block = await loop.run_in_executor(None, self._get_block)
            if not self._running:
                break
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
    def callbacks_stalled(self) -> bool:
        """The device stopped calling us back while the stream is still open.

        Distinct from digital silence, which is a stream that is running and
        handing over zeroes. This is a stream that has stopped running without
        saying so, and unlike silence it never fixes itself - the fix is to
        open the device again.
        """
        if not self._running or not self._last_callback_at:
            return False
        return (time.monotonic() - self._last_callback_at) > CALLBACK_STALL_AFTER

    def receiving_digital_silence(self) -> bool:
        """Every sample exactly zero since the stream opened.

        Real hardware has a noise floor, so this never happens to a working
        input - it means the OS handed us silence instead of the device.
        """
        if self.nonzero_blocks or self._started_at is None:
            return False
        if self.blocks_read < 5:
            return False                       # too early to conclude anything
        return (time.monotonic() - self._started_at) > DIGITAL_SILENCE_AFTER

    @property
    def clipping(self) -> bool:
        """The input hit full scale within the last few seconds."""
        return bool(self._last_clip_at) and \
            (time.monotonic() - self._last_clip_at) < CLIP_HOLD_SECONDS

    @property
    def error(self) -> str | None:
        if self._error and (time.monotonic() - self._error_at) > ERROR_TTL:
            self._error = None
        # Re-evaluated every read, so it clears itself the moment real audio
        # arrives - no restart needed once the real cause is dealt with.
        if self.receiving_digital_silence():
            name = (self.device.name or "").lower()
            if any(marker in name for marker in BUILT_IN_MIC_MARKERS):
                return NO_SIGNAL_HINT + LID_CLOSED_HINT
            return NO_SIGNAL_HINT
        return self._error


class SilentInput:
    """Stand-in when there is no audio hardware.

    Lets the whole system - relay, viewers, status page, mock backend - run on a
    machine with no microphone at all, which is most development.
    """

    def __init__(self, sample_rate: int = PREFERRED_RATE):
        self.device = DeviceChoice(-1, "no audio device", "none", sample_rate, 1)
        self.level = 0.0
        self.peak = 0.0
        self.clipping = False
        self.blocks_dropped = 0
        self.blocks_read = 0
        self.error = None
        self.callbacks_stalled = False   # nothing to stall; keeps the watchdog
        self._running = True             # duck-typed against both inputs

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    async def blocks(self):
        while self._running:
            await asyncio.sleep(0.1)
            yield b""
