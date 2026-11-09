"""capture/audio.py + capture/__main__.py: detecting an input that has stopped
delivering callbacks, and reopening it. No audio hardware is touched - the
stall is produced by moving the heartbeat backwards, and AudioInput is
replaced with a fake for the restart tests."""

from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capture import audio as audio_mod  # noqa: E402


class FakeInput:
    """Stands in for AudioInput so these tests need no PortAudio - CI has
    none. `fails` makes start() raise the way a device in use does."""

    fails = False

    def __init__(self, device):
        self.device = device
        self.started = False

    def start(self):
        if FakeInput.fails:
            raise OSError("Device unavailable")
        self.started = True

    def stop(self):
        self.started = False


class StallDetectionTests(unittest.TestCase):
    def make_input(self) -> audio_mod.AudioInput:
        device = audio_mod.DeviceChoice(0, "Test Interface", "Core Audio", 16000, 1)
        return audio_mod.AudioInput(device)

    def test_a_stream_that_was_never_started_is_not_stalled(self):
        self.assertFalse(self.make_input().callbacks_stalled)

    def test_a_freshly_started_stream_is_not_stalled(self):
        audio = self.make_input()
        audio._running = True
        audio._last_callback_at = time.monotonic()
        self.assertFalse(audio.callbacks_stalled)

    def test_silence_from_a_live_device_is_not_a_stall(self):
        """The distinction the whole feature rests on: a device delivering
        zeroes is working, and must not be torn down and reopened."""
        audio = self.make_input()
        audio._running = True
        audio._started_at = time.monotonic() - (
            audio_mod.DIGITAL_SILENCE_AFTER + 1)
        audio._last_callback_at = time.monotonic()   # callbacks still arriving
        audio.blocks_read = 60                       # ...carrying only zeroes
        audio.nonzero_blocks = 0
        self.assertTrue(audio.receiving_digital_silence())
        self.assertFalse(audio.callbacks_stalled)

    def test_no_callbacks_past_the_limit_is_a_stall(self):
        audio = self.make_input()
        audio._running = True
        audio._last_callback_at = time.monotonic() - (
            audio_mod.CALLBACK_STALL_AFTER + 0.5)
        self.assertTrue(audio.callbacks_stalled)

    def test_a_stopped_stream_is_not_reported_as_stalled(self):
        """stop() is deliberate. Only a stream nobody closed counts."""
        audio = self.make_input()
        audio._running = False
        audio._last_callback_at = time.monotonic() - 60
        self.assertFalse(audio.callbacks_stalled)

    def test_a_callback_carrying_an_error_still_counts_as_alive(self):
        import numpy as np

        audio = self.make_input()
        audio._running = True
        audio._last_callback_at = time.monotonic() - 60
        audio._callback(np.zeros(160, dtype="int16"), 160, None, "input overflow")
        self.assertFalse(audio.callbacks_stalled)

    def test_silent_input_never_stalls(self):
        """The fallback has no callbacks to miss; the watchdog reads the same
        attribute on both, so it has to answer."""
        self.assertFalse(audio_mod.SilentInput().callbacks_stalled)


class FakeBackend:
    def __init__(self, wants_open: bool = False):
        self.wants_open = wants_open
        self.opened_at_rate: int | None = None
        self.closed = 0

    async def open(self, sample_rate: int) -> None:
        self.opened_at_rate = sample_rate

    async def close(self) -> None:
        self.closed += 1


class RestartTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from capture.__main__ import CaptureApp

        self.real_input = audio_mod.AudioInput
        audio_mod.AudioInput = FakeInput
        FakeInput.fails = False

        self.app = CaptureApp.__new__(CaptureApp)          # no relay, no config
        self.app.backend = FakeBackend()
        self.app.input = audio_mod.SilentInput()
        self.app.sample_rate = 16000
        self.app.audio_error = "something earlier"
        self.app.device_busy = False
        self.app.audio_restarts = 0
        self.app._input_lost = None
        self.app.cfg = type("Cfg", (), {"backend": "whisper"})()
        self.device = audio_mod.DeviceChoice(3, "WING", "Core Audio", 48000, 1)

    def tearDown(self):
        audio_mod.AudioInput = self.real_input
        FakeInput.fails = False

    async def test_successful_restart_installs_the_new_input(self):
        ok = await self.app.restart_input(self.device)
        self.assertTrue(ok)
        self.assertEqual(48000, self.app.sample_rate)
        self.assertEqual(3, self.app.input.device.index)
        self.assertEqual(1, self.app.audio_restarts)
        self.assertIsNone(self.app.audio_error)
        self.assertIsNone(self.app._input_lost)

    async def test_an_open_stream_is_closed_and_reopened_at_the_new_rate(self):
        """A backend connection is bound to the sample rate, so it cannot ride
        through the swap - but the operator's open intent must survive it."""
        self.app.backend = FakeBackend(wants_open=True)
        await self.app.restart_input(self.device)
        self.assertEqual(1, self.app.backend.closed)
        self.assertEqual(48000, self.app.backend.opened_at_rate)

    async def test_a_closed_stream_is_left_closed(self):
        await self.app.restart_input(self.device)
        self.assertIsNone(self.app.backend.opened_at_rate)

    async def test_a_failed_restart_falls_back_and_stays_queued(self):
        """Leaving a stopped AudioInput in place would spin pump_audio on an
        iterator that ends immediately, so the fallback has to be SilentInput -
        and the device has to stay remembered or nothing ever retries it."""
        FakeInput.fails = True
        ok = await self.app.restart_input(self.device)
        self.assertFalse(ok)
        self.assertIsInstance(self.app.input, audio_mod.SilentInput)
        self.assertEqual(self.device, self.app._input_lost)
        self.assertEqual(0, self.app.audio_restarts)
        self.assertIn("WING", self.app.audio_error)

    async def test_a_later_attempt_recovers_the_device(self):
        FakeInput.fails = True
        await self.app.restart_input(self.device)
        FakeInput.fails = False
        self.assertTrue(await self.app.restart_input(self.app._input_lost))
        self.assertIsNone(self.app._input_lost)


class GainTests(unittest.TestCase):
    """The boost for a quiet mixer feed: doubles amplitude at +6 dB, clips at
    full scale instead of wrapping, and +0 leaves the samples untouched."""

    def test_six_db_doubles_and_full_scale_clips(self):
        import numpy as np
        quiet = np.array([1000, -1000, 20000, -20000], dtype=np.int16)
        out = audio_mod.apply_gain(quiet, 6)
        self.assertEqual(np.int16, out.dtype)
        self.assertAlmostEqual(2.0, out[0] / 1000, places=1)
        self.assertEqual(32767, out[2])        # clipped, not wrapped negative
        self.assertEqual(-32768, out[3])

    def test_zero_db_is_the_same_array(self):
        import numpy as np
        samples = np.array([1, 2, 3], dtype=np.int16)
        self.assertIs(samples, audio_mod.apply_gain(samples, 0))


class RescanTests(unittest.IsolatedAsyncioTestCase):
    """The Refresh button. PortAudio reads the device list once, at init, so a
    refresh that only re-reads it shows a device that is gone and misses one
    that is new - which is how "cannot open Teams Rec: PaErrorCode -9986"
    survived four refreshes."""

    setUp = RestartTests.setUp
    tearDown = RestartTests.tearDown

    def patches(self, calls: list):
        resolved = audio_mod.DeviceChoice(5, "WING", "Core Audio", 48000, 1)
        return (
            mock.patch.object(audio_mod, "rescan_devices",
                              lambda: calls.append("rescan")),
            mock.patch.object(audio_mod, "resolve_device",
                              lambda spec: calls.append(spec) or resolved),
            mock.patch.object(audio_mod, "list_input_devices", lambda: []),
        )

    async def test_a_live_input_is_reopened_by_name_after_the_rescan(self):
        """Indices renumber when a device goes, so the old one is not reused."""
        calls: list = []
        self.app.input = FakeInput(self.device)       # index 3, named WING
        self.app.input.started = True
        a, b, c = self.patches(calls)
        with a, b, c:
            await self.app.rescan_audio_devices()
        self.assertEqual(["rescan", "WING"], calls)
        self.assertEqual(5, self.app.input.device.index)
        self.assertTrue(self.app.input.started)

    async def test_with_no_live_input_it_only_rescans(self):
        calls: list = []
        a, b, c = self.patches(calls)
        with a, b, c:
            await self.app.rescan_audio_devices()
        self.assertEqual(["rescan"], calls)


if __name__ == "__main__":
    unittest.main()
