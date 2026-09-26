"""relay/state.py: the health words the status page shows, and the numbers
behind them. Standard library only."""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from relay import state as state_module  # noqa: E402
from relay.state import RelayState  # noqa: E402


def connected(**status) -> RelayState:
    s = RelayState(session_id="t")
    s.source_connected = True
    base = {"audio_level": 0.2, "stream_open": False}
    base.update(status)
    s.note_status(base, "peer")
    return s


class ClippingTests(unittest.TestCase):
    def test_clipping_wins_over_live(self):
        s = connected(audio_level=0.5, audio_peak=0.999, clipping=True)
        self.assertEqual(s.audio_status(time.time()), "clipping")
        snap = s.snapshot({"total": 0}, 0)
        self.assertTrue(snap["audio"]["clipping"])
        self.assertAlmostEqual(snap["audio"]["peak"], 0.999)

    def test_clipping_flag_clears_with_the_next_status(self):
        s = connected(audio_level=0.5, clipping=True)
        for _ in range(5):        # level well above the floor; EMA catches up
            s.note_status({"audio_level": 0.5, "clipping": False}, "peer")
        self.assertEqual(s.audio_status(time.time()), "live")

    def test_peak_is_clamped_and_ignores_junk(self):
        s = connected(audio_peak=7.0)
        self.assertEqual(s.audio_peak, 1.0)
        s.note_status({"audio_level": 0.2, "audio_peak": True}, "peer")
        self.assertEqual(s.audio_peak, 1.0)          # a bool is not a level; unchanged

    def test_source_drop_resets_peak_and_clipping(self):
        s = connected(audio_peak=0.9, clipping=True)
        s.source_dropped()
        self.assertEqual(s.audio_peak, 0.0)
        self.assertFalse(s.clipping)


class AudioRestartTests(unittest.TestCase):
    """The watchdog's restart count has to reach the status page, or a flaky
    interface leaves no trace anywhere but capture.log."""

    def test_the_count_reaches_the_snapshot(self):
        s = connected(audio_restarts=3)
        self.assertEqual(3, s.snapshot({"total": 0}, 0)["source"]["audio_restarts"])

    def test_a_client_that_never_restarted_reports_zero(self):
        s = connected()
        self.assertEqual(0, s.snapshot({"total": 0}, 0)["source"]["audio_restarts"])

    def test_junk_is_ignored_rather_than_shown(self):
        for junk in ("lots", None, True, -4, 1.5):
            with self.subTest(junk=junk):
                s = connected(audio_restarts=junk)
                self.assertEqual(
                    0, s.snapshot({"total": 0}, 0)["source"]["audio_restarts"])


class LatencyTests(unittest.TestCase):
    def test_no_samples_means_none_not_zero(self):
        snap = connected().snapshot({"total": 0}, 0)
        self.assertIsNone(snap["latency"]["final"])
        self.assertIsNone(snap["latency"]["interim"])

    def test_median_and_p95_from_speech_end(self):
        s = connected()
        now = time.time()
        for lag in (1.0, 1.2, 1.4, 1.6, 4.0):
            s.note_final("x", speech_end=now - lag)
        stats = s.snapshot({"total": 0}, 0)["latency"]["final"]
        self.assertEqual(stats["samples"], 5)
        self.assertAlmostEqual(stats["median"], 1.4, places=1)
        self.assertAlmostEqual(stats["p95"], 4.0, places=1)
        self.assertAlmostEqual(stats["last"], 4.0, places=1)

    def test_interims_are_tracked_separately(self):
        s = connected()
        now = time.time()
        s.note_interim(speech_end=now - 0.5)
        s.note_final("x", speech_end=now - 1.5)
        snap = s.snapshot({"total": 0}, 0)["latency"]
        self.assertAlmostEqual(snap["interim"]["median"], 0.5, places=1)
        self.assertAlmostEqual(snap["final"]["median"], 1.5, places=1)

    def test_clock_skew_and_stale_stamps_are_ignored(self):
        s = connected()
        now = time.time()
        s.note_final("future", speech_end=now + 5.0)          # capture clock ahead
        s.note_final("ancient", speech_end=now - 600.0)        # stale
        s.note_interim(speech_end=None)                        # backend cannot say
        snap = s.snapshot({"total": 0}, 0)
        self.assertIsNone(snap["latency"]["final"])
        self.assertIsNone(snap["latency"]["interim"])

    def test_window_is_bounded(self):
        s = connected()
        now = time.time()
        for _ in range(state_module.LATENCY_WINDOW + 50):
            s.note_final("x", speech_end=now - 1.0)
        self.assertEqual(len(s.final_latency), state_module.LATENCY_WINDOW)


if __name__ == "__main__":
    unittest.main()
