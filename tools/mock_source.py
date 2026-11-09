"""A capture client stand-in for testing the relay.

Speaks the source half of docs/MESSAGES.md: connects to /ws/source, reports
status four times a second, obeys control messages, and plays a transcript
script as interims committed into finals.

It exists so the relay, the viewers and the status page can be exercised end to
end - including the open/close stream lifecycle - before the real capture client
lands in stage 3. Like the real backends, it starts with the ASR stream
CLOSED: audio levels flow, no transcript does, until an operator presses "Open
ASR stream" on the status page.

    python tools/mock_source.py
    python tools/mock_source.py --open --interim-ms 120 --gap-ms 600

Requires the venv (websockets). The relay must be running.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from capture.backends.mock import load_script  # noqa: E402 - same script format

DEFAULT_SCRIPT = ROOT / "config" / "mock_script.txt"

STATUS_HZ = 4.0


class MockSource:
    def __init__(self, args):
        self.args = args
        self.script = load_script(Path(args.script))
        self.stream_open = bool(args.open)
        self.speaking = False
        self.idx = 0
        self.pending_clear = False

    # -- outbound --------------------------------------------------------

    def audio_level(self) -> float:
        """Plausible RMS: speech when a line is being spoken, room tone otherwise."""
        if self.speaking:
            return min(1.0, max(0.0, random.gauss(0.27, 0.09)))
        return max(0.0, random.gauss(0.0022, 0.0007))     # room tone

    async def send_status(self, ws) -> None:
        while True:
            await ws.send(json.dumps({
                "type": "status",
                "t": round(time.time(), 3),
                "audio_level": round(self.audio_level(), 4),
                "stream_open": self.stream_open,
                "backend": "mock",
                "device": self.args.device,
                "error": None,
            }))
            await asyncio.sleep(1.0 / STATUS_HZ)

    async def play(self, ws) -> None:
        while True:
            if not self.stream_open:
                self.speaking = False
                await asyncio.sleep(0.2)
                continue

            if self.idx >= len(self.script):
                if not self.args.loop:
                    self.speaking = False
                    await asyncio.sleep(3600)
                    continue
                self.idx = 0

            line = self.script[self.idx]
            self.idx += 1

            if not line:
                self.speaking = False
                await asyncio.sleep(self.args.gap_ms / 1000.0 * 3)
                continue
            if line.lower() == "[clear]":
                self.speaking = False
                await ws.send(json.dumps({"type": "clear"}))
                await asyncio.sleep(self.args.gap_ms / 1000.0)
                continue

            self.speaking = True
            built = ""
            for word in line.split():
                if not self.stream_open:        # operator closed mid-line
                    break
                built = f"{built} {word}".strip()
                await ws.send(json.dumps({"type": "interim", "text": built}))
                jitter = 1.0 + random.uniform(-self.args.jitter, self.args.jitter)
                await asyncio.sleep(max(0.01, self.args.interim_ms / 1000.0 * jitter))
            if self.stream_open and built:
                await ws.send(json.dumps({"type": "final", "text": line}))
            self.speaking = False
            await asyncio.sleep(self.args.gap_ms / 1000.0)

    # -- inbound ---------------------------------------------------------

    async def listen(self, ws) -> None:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if msg.get("type") != "control":
                continue
            action = msg.get("action")
            print(f"   control: {action}")
            if action == "open_stream":
                # The real backends start recognising here.
                self.stream_open = True
            elif action == "close_stream":
                self.stream_open = False
            elif action == "clear":
                self.idx = self.idx            # nothing to reset in the mock
            elif action == "reload_keyterms":
                print("   (mock backend has no keyterms to reload)")

    # -- lifecycle -------------------------------------------------------

    async def run(self) -> None:
        url = self.args.url
        # Header, not query string - same as the real capture client, so the
        # token never lands in a log line or a proxy's access log.
        headers = {"Authorization": f"Bearer {self.args.token}"} if self.args.token else {}
        backoff = 0.5
        while True:
            try:
                async with websockets.connect(url, additional_headers=headers,
                                              ping_interval=20) as ws:
                    print(f"   connected to {url}")
                    print(f"   stream is {'OPEN' if self.stream_open else 'CLOSED'}"
                          f" - control it from the status page")
                    backoff = 0.5
                    await asyncio.gather(
                        self.send_status(ws),
                        self.play(ws),
                        self.listen(ws),
                    )
            except (OSError, websockets.WebSocketException) as exc:
                print(f"   relay unavailable ({exc.__class__.__name__}), "
                      f"retrying in {backoff:.1f}s")
                await asyncio.sleep(backoff)
                backoff = min(10.0, backoff * 2)


def main() -> None:
    p = argparse.ArgumentParser(description="Mock capture client for the relay.")
    p.add_argument("--url", default="ws://127.0.0.1:8000/ws/source")
    p.add_argument("--token", default="")
    p.add_argument("--script", default=str(DEFAULT_SCRIPT))
    p.add_argument("--device", default="Mock input (no hardware)")
    p.add_argument("--interim-ms", type=float, default=180)
    p.add_argument("--gap-ms", type=float, default=900)
    p.add_argument("--jitter", type=float, default=0.35)
    p.add_argument("--open", action="store_true",
                   help="start with the ASR stream already open")
    p.add_argument("--loop", action="store_true", default=True)
    p.add_argument("--once", dest="loop", action="store_false")
    args = p.parse_args()

    print("mock capture client")
    try:
        asyncio.run(MockSource(args).run())
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
