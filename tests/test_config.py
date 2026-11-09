"""relay/config.py: the generic in-place TOML section writer and the overlay
key format. Standard library only; writes to a temporary directory."""

from __future__ import annotations

import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from relay import config  # noqa: E402


class SaveSectionTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "event.toml"

    def tearDown(self):
        self.dir.cleanup()

    def read(self) -> dict:
        with self.path.open("rb") as fh:
            return tomllib.load(fh)

    def test_creates_section_and_file(self):
        config.save_section(self.path, "[propresenter]",
                            {"host": "10.0.0.5", "port": 1025, "interims": False})
        self.assertEqual(self.read()["propresenter"],
                         {"host": "10.0.0.5", "port": 1025, "interims": False})

    def test_updates_in_place_keeping_comments_and_other_sections(self):
        self.path.write_text(
            "# top comment\n[relay]\nport = 8000   # keep me\n\n"
            "[propresenter]\n# how to reach it\nhost = \"127.0.0.1\"   # this Mac\nport = 1025\n\n"
            "[youtube]\nurl = \"\"\n", encoding="utf-8")
        config.save_section(self.path, "[propresenter]",
                            {"host": "10.0.0.9", "port": 2000, "surface": "message"})
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("# top comment", text)
        self.assertIn("# how to reach it", text)
        self.assertIn("port = 8000   # keep me", text)
        # The inline comment survives *in its column*: the value got two
        # characters shorter, so the gap grows by two to hold "# this Mac"
        # where it was. A file whose comments line up still does afterwards.
        self.assertIn('host = "10.0.0.9"    # this Mac', text)
        before = '[relay]\nport = 8000   # keep me'
        self.assertIn(before, text)                      # untouched key unchanged
        col = lambda ln: ln.index("#")
        self.assertEqual(col('host = "10.0.0.9"    # this Mac'),
                         col('host = "127.0.0.1"   # this Mac'))
        data = self.read()
        self.assertEqual(data["propresenter"],
                         {"host": "10.0.0.9", "port": 2000, "surface": "message"})
        self.assertEqual(data["youtube"]["url"], "")

    def test_numbers_stay_numbers(self):
        """A float used to fall through _toml_literal's string branch and be
        written quoted, so `silence_commit = 0.45` came back as the string
        "0.45" and broke the first comparison the backend made against it.
        bool is checked here too, since bool is an int in Python and would
        otherwise be written as 1."""
        config.save_section(self.path, "[whisper]", {
            "silence_commit": 0.45,
            "interim_interval": 1.0,
            "lines": 2,
            "vad": False,
            "verbose": True,
            "model": "small.en",
        })
        got = self.read()["whisper"]
        self.assertIsInstance(got["silence_commit"], float)
        self.assertEqual(got["silence_commit"], 0.45)
        self.assertIsInstance(got["interim_interval"], float)
        self.assertIsInstance(got["lines"], int)
        self.assertIs(got["vad"], False)
        self.assertIs(got["verbose"], True)
        self.assertEqual(got["model"], "small.en")

    def test_non_finite_numbers_are_refused(self):
        # inf and nan are not TOML, and writing one would produce a config
        # file the relay could no longer parse at all.
        for bad in (float("inf"), float("-inf"), float("nan")):
            with self.assertRaises(ValueError):
                config._toml_literal(bad)

    def test_lists_and_quoting(self):
        config.save_section(self.path, "[overlay]", {"keys": ["obs:ab\"c", "lobby:xyz"]})
        self.assertEqual(self.read()["overlay"]["keys"], ["obs:ab\"c", "lobby:xyz"])


class SetupCompleteTests(unittest.TestCase):
    """[setup] completed - what decides whether /status shows the dashboard
    or sends the operator to the first-run wizard."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "event.toml"

    def tearDown(self):
        self.dir.cleanup()

    def read(self) -> dict:
        with self.path.open("rb") as fh:
            return tomllib.load(fh)

    def test_writes_into_a_file_that_does_not_exist_yet(self):
        config.save_setup_complete(self.path)
        self.assertIs(self.read()["setup"]["completed"], True)

    def test_round_trips_through_load(self):
        config.save_setup_complete(self.path)
        self.assertTrue(config.load(self.path).setup_completed)
        config.save_setup_complete(self.path, False)
        self.assertFalse(config.load(self.path).setup_completed)

    def test_a_config_without_the_section_is_treated_as_a_first_run(self):
        # Every event.toml written before the wizard existed, and the shipped
        # example - a machine nobody has been through setup on.
        self.path.write_text("[relay]\nport = 8000\n", encoding="utf-8")
        self.assertFalse(config.load(self.path).setup_completed)

    def test_no_config_file_at_all_is_not_treated_as_a_first_run(self):
        # A deleted or not-yet-written event.toml is not a fresh install, and
        # putting a working booth into a wizard mid-event would be worse than
        # showing the wizard one time too few.
        missing = Path(self.dir.name) / "gone.toml"
        self.assertTrue(config.load(missing).setup_completed)

    def test_leaves_the_rest_of_the_file_alone(self):
        self.path.write_text(
            '# a comment\n[relay]\nport = 8123   # keep me\n', encoding="utf-8")
        config.save_setup_complete(self.path)
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("# a comment", text)
        self.assertIn("port = 8123   # keep me", text)
        self.assertEqual(self.read()["relay"]["port"], 8123)


class WindowModeTests(unittest.TestCase):
    """[ui] window_mode - whether the app opens its own window or a tab."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "event.toml"

    def tearDown(self):
        self.dir.cleanup()

    def test_defaults_to_an_app_window(self):
        self.assertEqual(config.load(self.path).window_mode, "app")

    def test_round_trip(self):
        config.save_window_mode(self.path, "browser")
        self.assertEqual(config.load(self.path).window_mode, "browser")
        config.save_window_mode(self.path, "app")
        self.assertEqual(config.load(self.path).window_mode, "app")

    def test_an_unknown_mode_is_refused_at_the_write(self):
        # The launcher only knows two modes; anything else would silently
        # become "browser" at read time and confuse whoever set it.
        with self.assertRaises(ValueError):
            config.save_window_mode(self.path, "hologram")

    def test_an_unreadable_value_falls_back_rather_than_failing(self):
        self.path.write_text('[ui]\nwindow_mode = "nonsense"\n', encoding="utf-8")
        self.assertEqual(config.load(self.path).window_mode, "app")


class OverlayKeyTests(unittest.TestCase):
    def test_round_trip(self):
        keys = {"obs": "abc123", "lobby": "def456"}
        formatted = config.format_overlay_keys(keys)
        self.assertEqual(formatted, ["obs:abc123", "lobby:def456"])
        self.assertEqual(config.parse_overlay_keys(formatted), keys)

    def test_malformed_entries_are_skipped(self):
        parsed = config.parse_overlay_keys(["good:key", "nocolon", ":nokey", "noname:", 42, None])
        self.assertEqual(parsed, {"good": "key"})


if __name__ == "__main__":
    unittest.main()


class SaverRoundTripTests(unittest.TestCase):
    """The six named savers are thin wrappers over save_section. One check
    that each still writes the key it claims to, with the right TOML type,
    into the right section, without eating the file's comments."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "event.toml"
        self.path.write_text(
            "# hand-edited, keep me\n"
            "[relay]\nport = 8000            # the usual\n\n"
            "[capture]\ndevice = \"\"              # blank = system default\n\n"
            "[youtube]\nurl = \"\"\nlang = \"en-US\"\n",
            encoding="utf-8")

    def tearDown(self):
        self.dir.cleanup()

    def read(self) -> dict:
        with self.path.open("rb") as fh:
            return tomllib.load(fh)

    def test_every_saver_round_trips(self):
        config.save_youtube_url(self.path, "https://example.test/c", "es")
        config.save_audio_device(self.path, 'Scarlett 2i2 "Mic #2"')
        config.save_translate_settings(self.path, ["es", "zh-Hans"])
        config.save_tunnel_settings(self.path, "captions.example.test", True)
        config.save_relay_port(self.path, 8123)
        config.save_event_settings(self.path, {"state": "live", "title": "Convocation"})

        data = self.read()
        self.assertEqual(data["youtube"], {"url": "https://example.test/c", "lang": "es"})
        # A '#' inside the quoted value must not be read as a comment.
        self.assertEqual(data["capture"]["device"], 'Scarlett 2i2 "Mic #2"')
        self.assertEqual(data["languages"]["offer"], ["es", "zh-Hans"])
        self.assertEqual(data["relay"]["public_hostname"], "captions.example.test")
        self.assertIs(data["tunnel"]["start_enabled"], True)      # bool, not "true"
        self.assertEqual(data["relay"]["port"], 8123)             # int, not "8123"
        self.assertEqual(data["event"], {"state": "live", "title": "Convocation"})

        text = self.path.read_text(encoding="utf-8")
        self.assertIn("# hand-edited, keep me", text)
        self.assertIn("# the usual", text)                        # inline comment kept
        self.assertIn("# blank = system default", text)

    def test_event_fields_stay_strings(self):
        """[event] is all text. A bool slipping through would be written as a
        TOML boolean and read back as one, which the welcome screen renders
        as the word True."""
        config.save_event_settings(self.path, {"title": "True"})
        self.assertEqual(self.read()["event"]["title"], "True")


class BundledCertsTests(unittest.TestCase):
    """The packaged app's OpenSSL is compiled with a CA path from the build
    machine's Python. On a Mac without it, every HTTPS connection fails
    'unable to get local issuer certificate' - which the updater reported as
    'could not reach github.com'."""

    def test_the_bundled_store_is_used_when_nothing_else_is_set(self):
        import certifi
        from unittest import mock
        with mock.patch.dict("os.environ", {}, clear=False) as env:
            env.pop("SSL_CERT_FILE", None)
            config.use_bundled_certs()
            self.assertEqual(certifi.where(), env["SSL_CERT_FILE"])
            self.assertTrue(Path(env["SSL_CERT_FILE"]).is_file())

    def test_an_operator_override_is_left_alone(self):
        from unittest import mock
        with mock.patch.dict("os.environ", {"SSL_CERT_FILE": "/corp/ca.pem"}):
            config.use_bundled_certs()
            import os
            self.assertEqual("/corp/ca.pem", os.environ["SSL_CERT_FILE"])
