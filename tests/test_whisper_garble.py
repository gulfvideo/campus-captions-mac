"""capture/backends/whisper.py: rejecting a decode that is shaped like noise.

Sits alongside the per-segment confidence gate already in _transcribe, which
uses Whisper's own no_speech_prob and avg_logprob. These two shapes score
confidently and survive that gate, and are not word repetition either, so
collapse_repeats leaves them alone as well."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capture.backends.whisper import (  # noqa: E402
    collapse_repeats, is_garbled,
)


class GarbleTests(unittest.TestCase):
    def test_ordinary_speech_survives(self):
        for line in (
            "Good morning and welcome to chapel.",
            "Turn with me to John chapter three.",
            "- Yes?",
            "Let's pray.",
            "",
        ):
            with self.subTest(line=line):
                self.assertFalse(is_garbled(line))

    def test_a_long_character_run_is_rejected(self):
        self.assertTrue(is_garbled("aaaaaaaaaaaaaaaaaa"))

    def test_a_short_run_is_left_alone(self):
        """People do stretch words out, and a reader is entitled to see it."""
        self.assertFalse(is_garbled("Sooooo good"))

    def test_a_run_split_across_words_still_counts(self):
        """Whitespace is removed before the run check, which is why the gate
        runs on the assembled line rather than per segment."""
        self.assertTrue(is_garbled("aaaaa aaaaa"))

    def test_a_long_line_of_mostly_punctuation_is_rejected(self):
        noise = "♪ ~ " * 22 + "♪"
        self.assertGreaterEqual(len(noise), 80)
        self.assertTrue(is_garbled(noise))

    def test_a_short_punctuated_line_is_never_judged_on_ratio(self):
        self.assertFalse(is_garbled("♪ ~ ♪"))

    def test_heavily_punctuated_real_speech_survives(self):
        line = ("Well - and I mean this - it's not about what we've done, "
                "it's about what He's done; that's the whole point, isn't it?")
        self.assertGreaterEqual(len(line), 80)
        self.assertFalse(is_garbled(line))

    def test_a_stammer_is_trimmed_not_rejected(self):
        """The division of labour: collapse_repeats trims what is still speech,
        is_garbled drops what is not."""
        self.assertFalse(is_garbled("I mean I mean I mean I mean it"))
        self.assertEqual("I mean I mean it",
                         collapse_repeats("I mean I mean I mean I mean it"))


if __name__ == "__main__":
    unittest.main()
