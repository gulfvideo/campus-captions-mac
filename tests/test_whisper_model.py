"""The Whisper model default: old installs move off base.en, and the
download row asks about the model actually configured."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from capture import config as capture_config  # noqa: E402
from capture.backends import support  # noqa: E402


class WhisperModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def load(self, model):
        path = self.tmp / "event.toml"
        path.write_text(f'[whisper]\nmodel = "{model}"\n', encoding="utf-8")
        return capture_config.load(path).backend_config["whisper_model"]

    def test_old_default_moves_to_new_default(self):
        self.assertEqual(self.load("base.en"), support.WHISPER_DEFAULT_MODEL)
        self.assertEqual(support.WHISPER_DEFAULT_MODEL, "small.en")

    def test_a_chosen_model_is_kept(self):
        self.assertEqual(self.load("medium.en"), "medium.en")
        self.assertEqual(self.load("tiny.en"), "tiny.en")

    def test_present_is_per_model(self):
        base = self.tmp / "models--Systran--faster-whisper-base.en" / "snapshots" / "x"
        base.mkdir(parents=True)
        (base / "model.bin").write_bytes(b"")
        self.assertTrue(support.whisper_model_present(self.tmp, "base.en"))
        self.assertFalse(support.whisper_model_present(self.tmp, "small.en"))
        # "medium" must not be satisfied by medium.en, nor the reverse.
        med = self.tmp / "models--Systran--faster-whisper-medium.en" / "snapshots" / "x"
        med.mkdir(parents=True)
        (med / "model.bin").write_bytes(b"")
        self.assertFalse(support.whisper_model_present(self.tmp, "medium"))

    def test_half_download_is_absent(self):
        part = self.tmp / "models--Systran--faster-whisper-small.en" / "blobs"
        part.mkdir(parents=True)
        (part / "abc.incomplete").write_bytes(b"")
        self.assertFalse(support.whisper_model_present(self.tmp, "small.en"))


if __name__ == "__main__":
    unittest.main()
