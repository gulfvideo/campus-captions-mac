"""Apple on-device speech backend: SpeechAnalyzer via a Swift subprocess helper.

macOS 26+ only. Fully on-device and free - no API key, no per-minute cost, and
(after the one-time model download) no network at all. Apple's SpeechAnalyzer/
SpeechTranscriber APIs are Swift-only with no Python or Objective-C bridge, so
the actual recognition runs in a small compiled helper (native/speech_helper.swift)
that this class starts as a subprocess: raw PCM16 audio goes in on its stdin,
one JSON object per line comes out on its stdout.

The helper is compiled and ad-hoc signed on first use (native/build.sh). The
signing matters: SpeechAnalyzer is gated by the normal Speech Recognition TCC
permission, which only applies to a codesigned binary carrying an embedded
Info.plist with NSSpeechRecognitionUsageDescription. See native/build.sh and
native/Info.plist for exactly how that's done, and native/Speech.entitlements
for why it must NOT declare com.apple.developer.speech-recognition - that is a
restricted entitlement reserved for Apple-provisioned binaries, and claiming it
without a matching provisioning profile gets the process SIGKILLed by AMFI at
launch, not merely denied.
"""

from __future__ import annotations

import asyncio
import json
import platform
import subprocess
import sys
from pathlib import Path

from .base import ASRBackend

FROZEN = getattr(sys, "frozen", False)
# In a PyInstaller bundle, NATIVE_DIR's datas land next to this file's frozen
# location; from source, it's the real directory beside this module.
NATIVE_DIR = Path(sys._MEIPASS, "capture", "backends", "native") if FROZEN else \
    Path(__file__).resolve().parent / "native"
BUILD_SCRIPT = NATIVE_DIR / "build.sh"
SOURCE = NATIVE_DIR / "speech_helper.swift"
BINARY = NATIVE_DIR / "build" / "speech_helper"

# Long enough for the helper's own finalize-on-EOF to emit its last result.
DRAIN_SECONDS = 3.0


def _unsupported_reason() -> str | None:
    """None if this Mac can run SpeechAnalyzer, else why it can't."""
    if sys.platform != "darwin":
        return "apple_speech only runs on macOS"
    try:
        major = int(platform.mac_ver()[0].split(".")[0])
    except (ValueError, IndexError):
        return None  # unknown version string; let the helper itself decide
    if major < 26:
        return f"apple_speech needs macOS 26 or newer (this Mac has {platform.mac_ver()[0]})"
    return None


def _ensure_built() -> str | None:
    """Compiles+signs the helper if missing or stale. Returns an error, or None.

    A frozen app ships a prebuilt binary and has no writable source tree to
    compile from (and no guarantee the Xcode command line tools even exist on
    the machine it's installed on) - just check the binary is there.
    """
    if FROZEN:
        return None if BINARY.exists() else "speech_helper is missing from this app bundle"
    if BINARY.exists() and BINARY.stat().st_mtime >= SOURCE.stat().st_mtime:
        return None
    try:
        result = subprocess.run(
            [str(BUILD_SCRIPT)], capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        return "Xcode command line tools not found (swiftc/codesign unavailable)"
    except subprocess.TimeoutExpired:
        return "building speech_helper timed out"
    if result.returncode != 0:
        return f"failed to build speech_helper: {result.stderr.strip()[-500:]}"
    return None


class AppleSpeechBackend(ASRBackend):
    name = "apple_speech"

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        cfg = self.config
        self.locale = str(cfg.get("locale", "en-US"))
        self.sample_rate = 16000

        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._want_open = False

    # -- lifecycle ---------------------------------------------------------

    async def open(self, sample_rate: int) -> None:
        if self._want_open:
            return
        reason = _unsupported_reason()
        if reason:
            self._error = reason
            return

        self._error = "building speech_helper..."
        build_error = await asyncio.get_running_loop().run_in_executor(None, _ensure_built)
        if build_error:
            self._error = build_error
            return

        self.sample_rate = int(sample_rate)
        self._want_open = True
        self._error = None
        try:
            self._proc = await asyncio.create_subprocess_exec(
                str(BINARY), "--sample-rate", str(self.sample_rate), "--locale", self.locale,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except Exception as exc:
            self._error = f"could not start speech_helper: {exc}"
            self._want_open = False
            return
        self._reader_task = asyncio.create_task(self._read_output())

    async def close(self) -> None:
        self._want_open = False
        self._open = False
        proc, self._proc = self._proc, None
        if proc is not None:
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=DRAIN_SECONDS)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        task, self._reader_task = self._reader_task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def send_audio(self, frames: bytes) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or not self._open:
            return  # closed, or the helper hasn't reported "ready" yet: drop
        try:
            proc.stdin.write(frames)
            await proc.stdin.drain()
        except Exception as exc:
            self._error = f"send failed: {exc.__class__.__name__}"

    # -- helper output -------------------------------------------------------

    async def _read_output(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            async for raw in proc.stdout:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if "text" in msg:
                    self._emit(msg.get("text", ""), is_final=bool(msg.get("final")))
                elif "status" in msg:
                    if msg["status"] == "ready":
                        self._open = True
                        self._error = None
                    elif msg["status"] == "downloading-model":
                        self._error = ("downloading the on-device speech model "
                                        "(first use only - needs internet once)")
                elif "error" in msg:
                    self._error = str(msg["error"])
        except Exception as exc:
            self._error = f"speech_helper reader failed: {exc.__class__.__name__}"
        finally:
            self._open = False

    # -- reporting -------------------------------------------------------

    @property
    def wants_open(self) -> bool:
        return self._want_open
