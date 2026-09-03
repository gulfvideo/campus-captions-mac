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
        self.assertIn('host = "10.0.0.9"   # this Mac', text)   # inline comment survives
        data = self.read()
        self.assertEqual(data["propresenter"],
                         {"host": "10.0.0.9", "port": 2000, "surface": "message"})
        self.assertEqual(data["youtube"]["url"], "")

    def test_lists_and_quoting(self):
        config.save_section(self.path, "[overlay]", {"keys": ["obs:ab\"c", "lobby:xyz"]})
        self.assertEqual(self.read()["overlay"]["keys"], ["obs:ab\"c", "lobby:xyz"])


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
