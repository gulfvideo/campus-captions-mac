"""relay/hub.py: what the relay does with a backend's speech_end. Standard
library only - the hub never touches a socket, so a viewer here is just a
Client whose queue we read back."""

from __future__ import annotations

import asyncio
import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from relay.hub import Hub  # noqa: E402


def drain(client) -> list[dict]:
    out = []
    while True:
        try:
            out.append(json.loads(client.queue.get_nowait()))
        except asyncio.QueueEmpty:
            return out


class SpeechEndTests(unittest.TestCase):
    def setUp(self):
        # Client queues are asyncio.Queues; they need a loop to exist even
        # though nothing here awaits.
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.hub = Hub(session_id="abc12345", source_language="en")
        self.hub.add_source("src")

    def tearDown(self):
        self.loop.close()
        asyncio.set_event_loop(None)

    def test_speech_end_feeds_latency_and_never_reaches_viewers(self):
        viewer = self.hub.add_viewer("phone", "p")
        drain(viewer)
        self.hub.ingest(json.dumps({"type": "interim", "text": "Hi",
                                    "speech_end": time.time() - 0.4}), "src")
        self.hub.ingest(json.dumps({"type": "final", "text": "Hi.",
                                    "speech_end": time.time() - 1.5}), "src")
        self.assertEqual(len(self.hub.state.interim_latency), 1)
        self.assertEqual(len(self.hub.state.final_latency), 1)
        self.assertAlmostEqual(self.hub.state.final_latency[0], 1.5, places=1)
        for msg in drain(viewer):
            self.assertNotIn("speech_end", msg)

    def test_missing_or_junk_speech_end_is_simply_unmeasured(self):
        self.hub.ingest(json.dumps({"type": "final", "text": "No stamp."}), "src")
        self.hub.ingest(json.dumps({"type": "final", "text": "Bool.", "speech_end": True}), "src")
        self.hub.ingest(json.dumps({"type": "final", "text": "Str.", "speech_end": "now"}), "src")
        self.assertEqual(self.hub.state.finals, 3)
        self.assertEqual(len(self.hub.state.final_latency), 0)
        self.assertIsNone(self.hub.snapshot()["latency"]["final"])


if __name__ == "__main__":
    unittest.main()
