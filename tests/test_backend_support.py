"""capture/backends/support.py: which ASR backend a machine gets.

The regression this file exists for: config/event.example.toml shipped
`backend = "apple_speech"`, and a fresh clone has no config/event.toml, so
both the relay and the capture client fell back to the example - and every
install on an older Mac came up on a backend that could only ever report
an error at the first "Open ASR stream".

Standard library only, and deliberately no import of registry.py: the point
of support.py is that it answers these questions without pulling in numpy,
websockets or PortAudio, and a test that imported the backends would not
notice if that stopped being true.
"""

from __future__ import annotations

import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from capture.backends import support  # noqa: E402


class PlatformSupportTests(unittest.TestCase):
    def test_apple_speech_is_refused_off_macos(self):
        reason = support.apple_speech_unsupported()
        if sys.platform == "darwin":
            # Either it runs here or the reason names the macOS version.
            self.assertTrue(reason is None or "macOS" in reason, reason)
        else:
            self.assertEqual(reason, "apple_speech only runs on macOS")

    def test_supported_excludes_apple_speech_off_macos(self):
        if sys.platform == "darwin":
            self.skipTest("apple_speech may legitimately be available here")
        self.assertNotIn("apple_speech", support.supported())

    def test_the_always_available_backends_are_offered_everywhere(self):
        # Whisper is a pip install, not bound to an operating system, so it
        # must always be offerable. mock has to be, or resolve_auto() has
        # nothing to fall back to.
        for name in ("mock", "whisper"):
            self.assertIn(name, support.supported())

    def test_unknown_backend_is_reported_not_raised(self):
        # The status page's picker and the setup wizard both call this; a
        # typo in event.toml must not 500 the relay.
        self.assertIn("unknown", support.unsupported_reason("nope") or "")

    def test_every_catalogue_entry_is_described(self):
        for info in support.CATALOGUE:
            self.assertTrue(info.label, info.name)
            self.assertTrue(info.summary, info.name)


class ResolveTests(unittest.TestCase):
    def test_auto_never_picks_something_this_machine_cannot_run(self):
        self.assertIsNone(support.unsupported_reason(support.resolve_auto()))

    def test_auto_never_picks_a_backend_needing_an_optional_install(self):
        self.assertNotEqual(support.resolve_auto(), "whisper")

    def test_apple_speech_is_substituted_off_macos(self):
        if sys.platform == "darwin":
            self.skipTest("apple_speech may legitimately be available here")
        self.assertNotEqual(support.resolve("apple_speech"), "apple_speech")
        self.assertIsNone(support.unsupported_reason(support.resolve("apple_speech")))

    def test_a_runnable_backend_is_left_alone(self):
        self.assertEqual(support.resolve("whisper"), "whisper")
        self.assertEqual(support.resolve("mock"), "mock")

    def test_empty_and_auto_both_mean_auto(self):
        self.assertEqual(support.resolve(""), support.resolve_auto())
        self.assertEqual(support.resolve("auto"), support.resolve_auto())

    def test_an_unknown_name_is_passed_through(self):
        # create() owns that error and says what the valid names are; silently
        # swapping a typo for a working backend would hide the mistake.
        self.assertEqual(support.resolve("nonsense"), "nonsense")

    def test_describe_shape(self):
        described = support.describe("whisper")
        self.assertEqual(described["name"], "whisper")
        self.assertTrue(described["label"])
        self.assertTrue(described["supported"])
        self.assertEqual(described["unsupported_reason"], "")


class ShippedConfigTests(unittest.TestCase):
    """The example config is what a fresh clone and a fresh install both
    read, so its [capture] backend is the actual default for everyone."""

    def setUp(self):
        with (ROOT / "config" / "event.example.toml").open("rb") as fh:
            self.data = tomllib.load(fh)

    def test_the_shipped_default_is_not_platform_bound(self):
        backend = self.data["capture"]["backend"]
        self.assertEqual(
            backend, "auto",
            "config/event.example.toml must not name a specific backend: it is "
            "read on every platform, and naming one puts the machines that "
            "cannot run it on a backend that can only report an error.")

    def test_the_shipped_default_resolves_here(self):
        resolved = support.resolve(self.data["capture"]["backend"])
        self.assertIsNone(support.unsupported_reason(resolved))


if __name__ == "__main__":
    unittest.main()
