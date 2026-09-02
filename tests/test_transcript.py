"""Export formats of relay/transcript.py. Standard library only:

    python -m unittest discover -s tests

The smoke test (tools/smoke_test.py) proves the routes against a running
relay; this checks the format rules themselves - timestamp dialects, that
every format renders the same cue list, that the readable transcript is not
split for display, and CSV quoting.
"""

from __future__ import annotations

import csv
import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from relay import transcript  # noqa: E402


def final(text: str, started: float, committed: float, lang: str = "en",
          utterance: str = "") -> dict:
    return {"type": "final", "text": text, "lang": lang,
            "t0": started, "t": committed, "id": f"s:{int(committed)}",
            "utterance": utterance or f"s:{int(committed)}"}


LONG = ("We expect to break ground on the new science building next spring, "
        "with classes meeting there by the fall of the following year.")


def recorded() -> transcript.TranscriptRecorder:
    """Three English lines (one long enough to split) and one Spanish
    translation sharing the second line's utterance; video start at t=100."""
    rec = transcript.TranscriptRecorder()
    rec.mark_start(100.0)
    rec.observe(final("Good morning.", 101.0, 103.0))
    rec.observe(final(LONG, 103.5, 110.0))
    rec.observe(final("Bienvenidos.", 103.5, 110.4, lang="es", utterance="s:110"))
    rec.observe(final("Please stand.", 111.0, 111.4))
    return rec


class TimestampTests(unittest.TestCase):
    def test_srt_dialect_uses_comma(self):
        self.assertEqual(transcript.format_timestamp(3661.5), "01:01:01,500")

    def test_vtt_dialect_uses_full_stop(self):
        self.assertEqual(transcript.format_timestamp(3661.5, "."), "01:01:01.500")

    def test_negative_clamps_to_zero(self):
        self.assertEqual(transcript.format_timestamp(-2.0), "00:00:00,000")


class CueTests(unittest.TestCase):
    def test_utterance_id_survives_the_display_split(self):
        cues = recorded().cues("en")
        long_cues = [c for c in cues if c.utterance == "s:110"]
        self.assertGreater(len(long_cues), 1, "the long line should split into several cues")
        self.assertEqual(recorded().cues("es")[0].utterance, "s:110")


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.rec = recorded()

    def test_every_format_renders_and_agrees_on_cue_count(self):
        n = len(self.rec.cues("en"))
        self.assertEqual(self.rec.to_srt("en").count("-->"), n)
        self.assertEqual(self.rec.to_vtt("en").count("-->"), n)
        self.assertEqual(json.loads(self.rec.to_json("en"))["cue_count"], n)
        self.assertEqual(len(list(csv.reader(io.StringIO(self.rec.to_csv("en"))))) - 1, n)
        for fmt in transcript.FORMATS:
            self.assertTrue(self.rec.export("en", fmt), fmt)

    def test_offset_reaches_every_format(self):
        for fmt in transcript.FORMATS:
            self.assertNotEqual(self.rec.export("en", fmt), self.rec.export("en", fmt, offset=0.5), fmt)

    def test_unknown_format_is_rejected(self):
        with self.assertRaises(ValueError):
            self.rec.export("en", "docx")

    def test_unrecorded_language_exports_empty(self):
        for fmt in transcript.FORMATS:
            self.assertEqual(self.rec.export("fr", fmt), "")

    def test_srt_blocks(self):
        body = self.rec.to_srt("en")
        self.assertTrue(body.startswith("1\n00:00:01,000 --> "))
        self.assertNotIn("WEBVTT", body)
        first_timing = body.splitlines()[1]
        self.assertIn(",", first_timing)
        self.assertNotIn(".", first_timing)

    def test_vtt_header_and_dialect(self):
        body = self.rec.to_vtt("en")
        self.assertTrue(body.startswith("WEBVTT\n\n1\n00:00:01.000 --> "))
        self.assertNotIn(",", body.splitlines()[3])

    def test_txt_is_one_recorded_line_per_line_not_display_split(self):
        stamped = self.rec.to_txt("en")
        lines = stamped.splitlines()
        self.assertEqual(len(lines), 3)               # 3 recorded lines, however many cues
        self.assertEqual(lines[0], "[00:00:01] Good morning.")
        self.assertIn(LONG, lines[1])
        bare = self.rec.to_txt("en", timestamps=False)
        self.assertEqual(bare.splitlines(), ["Good morning.", LONG, "Please stand."])

    def test_json_shape(self):
        payload = json.loads(self.rec.to_json("es"))
        self.assertEqual(payload["language"], "es")
        self.assertTrue(payload["zero_marked"])
        self.assertEqual(payload["zero_time"], 100.0)
        self.assertEqual(payload["cue_count"], 1)
        cue = payload["cues"][0]
        self.assertEqual(set(cue), {"index", "start", "end", "text", "utterance"})
        self.assertEqual(cue["utterance"], "s:110")

    def test_csv_round_trips_text_with_commas_quotes_and_line_breaks(self):
        rec = transcript.TranscriptRecorder()
        rec.mark_start(0.0)
        rec.observe(final('He said, "come in", and we did.', 1.0, 3.0))
        rec.observe(final(LONG, 4.0, 10.0))
        rows = list(csv.reader(io.StringIO(rec.to_csv("en"))))
        self.assertEqual(rows[0][0], "index")
        self.assertEqual(rows[1][-1], 'He said, "come in", and we did.')
        self.assertEqual(rows[1][1], "00:00:01.000")
        self.assertEqual(rows[1][3], "1.000")
        for row in rows[1:]:
            self.assertNotIn("\n", row[-1])      # a cue's display break becomes a space


if __name__ == "__main__":
    unittest.main()
