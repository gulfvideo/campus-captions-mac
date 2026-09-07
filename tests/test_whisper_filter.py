"""capture/backends/whisper.py: dropping text the model invented.

Whisper does not know how to output nothing. Fed a stretch of room tone it
produces fluent, confident speech - "Okay. Thank you. Bye bye. Good bye." and
the subtitle boilerplate its training data is full of. Reported from a real
room: two paragraphs of it during a quiet passage, on a wall in front of an
audience.

These tests cover the filter in front of that, not the decoder: a fake model
returns segments with the scores a real one would carry, and the assertions
are about which of them reach a caption.

faster-whisper is an optional dependency and is not needed here - the module
imports it lazily, and nothing in these tests reaches the import.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# whisper.py imports numpy at module scope for its level metering. Standing in
# for it keeps this test runnable in a checkout that has not installed the
# optional Whisper extras.
if "numpy" not in sys.modules:
    _np = types.ModuleType("numpy")
    _np.ndarray = object
    _np.float32 = float
    sys.modules["numpy"] = _np

from capture.backends import whisper as whisper_mod  # noqa: E402


class Segment:
    """What faster-whisper yields, with the two scores that matter."""

    def __init__(self, text, no_speech_prob=0.05, avg_logprob=-0.3):
        self.text = text
        self.no_speech_prob = no_speech_prob
        self.avg_logprob = avg_logprob


class FakeModel:
    def __init__(self, segments):
        self.segments = segments
        self.kwargs = None

    def transcribe(self, _audio, **kwargs):
        self.kwargs = kwargs
        return iter(self.segments), None


class Backend(whisper_mod.LocalWhisperBackend):
    """Just enough of the backend to call _transcribe."""

    def __init__(self, segments):
        self.model = FakeModel(segments)
        self._prompt = None
        self.language = "en"

    def _ensure_model(self):
        return self.model


class SegmentFilterTests(unittest.TestCase):
    def test_real_speech_passes(self):
        out = Backend([Segment("Please stand for the reading.")])._transcribe(None)
        self.assertEqual(out, "Please stand for the reading.")

    def test_dropped_when_the_model_doubts_it_was_speech(self):
        loud_doubt = Segment("Okay. Thank you. Bye bye.",
                             no_speech_prob=whisper_mod.NO_SPEECH_MAX + 0.2)
        self.assertEqual(Backend([loud_doubt])._transcribe(None), "")

    def test_dropped_when_the_decoder_was_guessing(self):
        low_confidence = Segment("Good bye. Good bye.",
                                 avg_logprob=whisper_mod.MIN_AVG_LOGPROB - 0.5)
        self.assertEqual(Backend([low_confidence])._transcribe(None), "")

    def test_the_two_checks_are_independent(self):
        # Either one alone is enough; a hallucination usually trips only one.
        only_no_speech = Segment("Bye bye.", no_speech_prob=0.95, avg_logprob=-0.2)
        only_logprob = Segment("Bye bye.", no_speech_prob=0.01, avg_logprob=-2.0)
        self.assertEqual(Backend([only_no_speech])._transcribe(None), "")
        self.assertEqual(Backend([only_logprob])._transcribe(None), "")

    def test_real_speech_survives_alongside_invented_text(self):
        out = Backend([
            Segment("Please stand for the reading."),
            Segment("Okay. Thank you. Bye bye.", no_speech_prob=0.92),
        ])._transcribe(None)
        self.assertEqual(out, "Please stand for the reading.")

    def test_borderline_scores_are_kept(self):
        # The thresholds are a floor, not a preference: text that only just
        # clears them is real speech someone said quietly, and dropping it
        # loses a caption a deaf reader needed.
        edge = Segment("Amen.",
                       no_speech_prob=whisper_mod.NO_SPEECH_MAX - 0.01,
                       avg_logprob=whisper_mod.MIN_AVG_LOGPROB + 0.01)
        self.assertEqual(Backend([edge])._transcribe(None), "Amen.")

    def test_missing_scores_do_not_drop_the_caption(self):
        # A future faster-whisper that stops reporting one of these must not
        # take captions down.
        bare = types.SimpleNamespace(text="Please be seated.")
        self.assertEqual(Backend([bare])._transcribe(None), "Please be seated.")


class TranscribeOptionsTests(unittest.TestCase):
    def test_vad_is_on(self):
        # The level gate cannot tell a fan from a voice; Silero can, and this
        # is the check that actually stops room tone reaching the decoder.
        backend = Backend([Segment("Hello.")])
        backend._transcribe(None)
        self.assertIs(backend.model.kwargs["vad_filter"], True)
        self.assertEqual(
            backend.model.kwargs["vad_parameters"]["min_silence_duration_ms"],
            whisper_mod.VAD_MIN_SILENCE_MS)

    def test_repetition_guard_is_still_on(self):
        backend = Backend([Segment("Hello.")])
        backend._transcribe(None)
        self.assertIs(backend.model.kwargs["condition_on_previous_text"], False)


if __name__ == "__main__":
    unittest.main()
