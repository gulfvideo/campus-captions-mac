"""relay/preflight.py: the verdicts and the words. Pure evaluation, no relay,
no network - probe results are handed in."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from relay import preflight  # noqa: E402


def snapshot(**over) -> dict:
    """A healthy setup: Apple speech open and producing, good audio, a quiet
    computer, a wall and a phone connected, event live."""
    base = {
        "source": {"connected": True, "backend": "apple_speech",
                   "device": "Scarlett 2i2", "device_busy": False, "error": None},
        "audio": {"state": "live"},
        "asr": {"stream_open": True, "state": "producing"},
        "latency": {"final": {"samples": 40, "median": 1.3, "p95": 2.1}},
        "host": {"available": True, "cpu_percent": 22.0, "mem_available_mb": 6000,
                 "busiest": [{"name": "ProPresenter", "cpu_percent": 12.0}]},
        "viewers": {"wall": 1, "phone": 2},
        "translation": {"provider": "none"},
        "youtube": {"configured": False},
        "event": {"state": "live"},
    }
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = {**base[key], **value}
        else:
            base[key] = value
    return base


# Transcription and translation run on the Mac, so a healthy setup probes
# nothing unless YouTube captions are configured.
GOOD_PROBE: dict = {}
YOUTUBE = {"youtube": {"configured": True}}


def status_of(report: dict, cid: str) -> str | None:
    return next((c["status"] for c in report["checks"] if c["id"] == cid), None)


class VerdictTests(unittest.TestCase):
    def test_healthy_setup_is_ready(self):
        report = preflight.evaluate(snapshot(), GOOD_PROBE)
        self.assertEqual(report["verdict"], "ready", report)
        self.assertEqual(report["counts"]["fail"], 0)
        self.assertEqual(report["counts"]["warn"], 0)

    def test_no_capture_client_is_not_ready(self):
        report = preflight.evaluate(snapshot(source={"connected": False}), {})
        self.assertEqual(report["verdict"], "not_ready")
        self.assertEqual(status_of(report, "capture"), "fail")
        self.assertIn("launcher", report["checks"][0]["action"])

    def test_busy_computer_recommends_another_one(self):
        report = preflight.evaluate(snapshot(host={"cpu_percent": 95.0}), GOOD_PROBE)
        self.assertEqual(report["verdict"], "not_ready")
        self.assertIn("another computer", report["headline"].lower())
        self.assertEqual(status_of(report, "host"), "fail")

    def test_moderately_busy_computer_is_a_warning_that_names_the_culprit(self):
        report = preflight.evaluate(
            snapshot(host={"cpu_percent": 75.0, "busiest": [{"name": "OBS", "cpu_percent": 40.0}]}),
            GOOD_PROBE)
        self.assertEqual(report["verdict"], "warnings")
        host = next(c for c in report["checks"] if c["id"] == "host")
        self.assertIn("OBS 40%", host["detail"])

    def test_low_memory_fails(self):
        report = preflight.evaluate(snapshot(host={"mem_available_mb": 200}), GOOD_PROBE)
        self.assertEqual(status_of(report, "host"), "fail")

    def test_missing_psutil_is_skipped_not_faked(self):
        report = preflight.evaluate(snapshot(host={"available": False, "cpu_percent": None}), GOOD_PROBE)
        self.assertEqual(status_of(report, "host"), "skip")
        self.assertEqual(report["verdict"], "ready")

    def test_device_held_by_another_program_fails_with_the_fix(self):
        report = preflight.evaluate(snapshot(source={
            "device_busy": True,
            "error": "cannot open Scarlett: -9999 - another program appears to have this input "
                     "open in exclusive mode. Windows: Settings > System > Sound ..."}), GOOD_PROBE)
        self.assertEqual(status_of(report, "audio-device"), "fail")
        check = next(c for c in report["checks"] if c["id"] == "audio-device")
        self.assertIn("exclusive", check["action"])

    def test_no_audio_device_at_all_fails(self):
        report = preflight.evaluate(snapshot(source={"device": "no audio device [none] @ 16000 Hz"}), GOOD_PROBE)
        self.assertEqual(status_of(report, "audio-device"), "fail")
        self.assertEqual(report["verdict"], "not_ready")

    def test_silence_and_clipping_are_warnings(self):
        self.assertEqual(status_of(preflight.evaluate(snapshot(audio={"state": "silent"}), GOOD_PROBE),
                                   "audio-level"), "warn")
        self.assertEqual(status_of(preflight.evaluate(snapshot(audio={"state": "clipping"}), GOOD_PROBE),
                                   "audio-level"), "warn")

    def test_mock_backend_is_a_warning(self):
        report = preflight.evaluate(snapshot(source={"backend": "mock"}), {})
        self.assertEqual(status_of(report, "transcription"), "warn")

    def test_on_device_backend_needs_no_network(self):
        report = preflight.evaluate(snapshot(source={"backend": "whisper"}), {})
        self.assertEqual(status_of(report, "net-transcription"), "ok")

    def test_latency_thresholds(self):
        warn = preflight.evaluate(snapshot(latency={"final": {"samples": 5, "median": 2.5, "p95": 4.0}}), GOOD_PROBE)
        self.assertEqual(status_of(warn, "latency"), "warn")
        fail = preflight.evaluate(snapshot(latency={"final": {"samples": 5, "median": 5.0, "p95": 8.0}}), GOOD_PROBE)
        self.assertEqual(status_of(fail, "latency"), "fail")

    def test_unreachable_youtube_fails_and_slow_one_warns(self):
        down = {"upload.youtube.com": {"ok": 0, "failed": 3, "median_ms": None, "worst_ms": None,
                                       "error": "TimeoutError: timed out"}}
        self.assertEqual(status_of(preflight.evaluate(snapshot(**YOUTUBE), down), "net-youtube"), "fail")
        slow = {"upload.youtube.com": {"ok": 3, "failed": 0, "median_ms": 900, "worst_ms": 1400, "error": None}}
        self.assertEqual(status_of(preflight.evaluate(snapshot(**YOUTUBE), slow), "net-youtube"), "warn")

    def test_no_outputs_are_warnings_with_what_to_open(self):
        report = preflight.evaluate(snapshot(viewers={"wall": 0, "phone": 0}), GOOD_PROBE)
        self.assertEqual(report["verdict"], "warnings")
        wall = next(c for c in report["checks"] if c["id"] == "wall")
        self.assertIn("ProPresenter", wall["action"])

    def test_propresenter_disconnected_is_a_warning(self):
        report = preflight.evaluate(snapshot(), GOOD_PROBE,
                                    propresenter={"enabled": True, "state": "disconnected",
                                                  "error": "Connection refused"})
        self.assertEqual(status_of(report, "propresenter"), "warn")

    def test_failures_sort_first(self):
        report = preflight.evaluate(snapshot(source={"connected": False}, viewers={"wall": 0}), {})
        self.assertEqual(report["checks"][0]["status"], "fail")


if __name__ == "__main__":
    unittest.main()
