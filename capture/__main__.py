"""python -m capture - read a microphone, run ASR, feed the relay.

    python -m capture --list-devices
    python -m capture                          # mock backend, no hardware needed
    python -m capture --backend deepgram --device "Scarlett"

The ASR stream starts closed. Audio levels flow to the status page immediately
so the booth can check the microphone; no transcript is produced, and for
Deepgram nothing is billed, until an operator presses "Open ASR stream".
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
import time
from pathlib import Path

from . import audio as audio_mod
from . import config as config_mod
from .backends import create as create_backend
from .relay_client import RelayClient

STATUS_HZ = 4.0


class CaptureApp:
    def __init__(self, cfg, args):
        self.cfg = cfg
        self.args = args
        self.backend = create_backend(cfg.backend, cfg.backend_config)
        self.relay = RelayClient(cfg.relay_url, cfg.source_token,
                                 on_control=self.on_control)
        self.input = None
        self.sample_rate = audio_mod.PREFERRED_RATE
        self.audio_error: str | None = None
        self._pending: list[str] = []       # control actions, handled in the loop

    # -- control ---------------------------------------------------------

    def on_control(self, action: str) -> None:
        print(f"[capture] control: {action}")
        self._pending.append(action)

    async def _apply_control(self) -> None:
        while self._pending:
            action = self._pending.pop(0)
            try:
                if action == "open_stream":
                    await self.backend.open(self.sample_rate)
                    if self.backend.error:
                        print(f"[capture] open failed: {self.backend.error}")
                    else:
                        print(f"[capture] {self.cfg.backend} stream OPEN"
                              + (" (billing)" if self.cfg.backend == "deepgram" else ""))
                elif action == "close_stream":
                    await self.backend.close()
                    print(f"[capture] {self.cfg.backend} stream closed")
                elif action == "clear":
                    self.relay.send({"type": "clear"})
                elif action == "reload_keyterms":
                    await self.backend.reload_keyterms()
                    print("[capture] keyterms reloaded")
            except Exception as exc:
                print(f"[capture] control {action} failed: {exc!r}")

    # -- pumps -----------------------------------------------------------

    async def pump_audio(self) -> None:
        async for block in self.input.blocks():
            if block and self.backend.stream_open:
                await self.backend.send_audio(block)

    async def pump_events(self) -> None:
        async for event in self.backend.events():
            self.relay.send({
                "type": "final" if event.is_final else "interim",
                "text": event.text,
                "t": round(event.t, 3),
            })

    async def pump_mock_clears(self) -> None:
        """The mock script can ask for a clear; forward it like an operator would."""
        queue = getattr(self.backend, "clear_requests", None)
        if queue is None:
            return
        while True:
            await queue.get()
            self.relay.send({"type": "clear"})

    async def pump_status(self) -> None:
        while True:
            error = self.backend.error or self.audio_error or self.input.error
            self.relay.send({
                "type": "status",
                "audio_level": round(float(self.input.level), 4),
                "stream_open": self.backend.stream_open,
                "backend": self.cfg.backend,
                "device": str(self.input.device),
                "error": error,
            })
            await self._apply_control()
            await asyncio.sleep(1.0 / STATUS_HZ)

    # -- lifecycle -------------------------------------------------------

    def open_input(self) -> None:
        if self.args.no_audio:
            self.input = audio_mod.SilentInput()
            print("[capture] --no-audio: running without an input device")
            return
        try:
            device = audio_mod.resolve_device(self.cfg.device)
            self.input = audio_mod.AudioInput(device)
            self.input.start()
            self.sample_rate = device.sample_rate
            print(f"[capture] input: {device}")
        except Exception as exc:
            self.audio_error = f"{exc}"
            if self.cfg.backend == "deepgram":
                # Deepgram with no audio would bill for silence. Refuse.
                raise SystemExit(
                    f"[capture] cannot open audio input: {exc}\n"
                    f"          run 'python -m capture --list-devices' to see "
                    f"this machine's inputs."
                )
            self.input = audio_mod.SilentInput()
            print(f"[capture] no audio input ({exc}); continuing for the "
                  f"{self.cfg.backend} backend")

    async def run(self) -> None:
        self.open_input()
        tasks = [
            asyncio.create_task(self.relay.run()),
            asyncio.create_task(self.pump_audio()),
            asyncio.create_task(self.pump_events()),
            asyncio.create_task(self.pump_status()),
            asyncio.create_task(self.pump_mock_clears()),
        ]
        if self.cfg.start_open or self.args.open:
            await asyncio.sleep(0.6)          # let the relay link come up first
            self.on_control("open_stream")
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            with contextlib.suppress(Exception):
                await self.backend.close()
            self.input.stop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Campus Captions capture client.")
    parser.add_argument("--list-devices", action="store_true",
                        help="print this machine's audio inputs and exit")
    parser.add_argument("--backend", default=None, help="mock | deepgram")
    parser.add_argument("--device", default=None,
                        help="input device index or part of its name")
    parser.add_argument("--relay", default=None, help="relay source websocket URL")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--open", action="store_true",
                        help="open the ASR stream at startup (for deepgram this "
                             "starts billing immediately)")
    parser.add_argument("--no-audio", action="store_true",
                        help="run with no input device at all")
    args = parser.parse_args()

    if args.list_devices:
        audio_mod.print_devices()
        return

    cfg = config_mod.load(args.config)
    if args.backend:
        cfg.backend = args.backend
    if args.device is not None:
        cfg.device = args.device
    if args.relay:
        cfg.relay_url = args.relay

    print(f"[capture] backend={cfg.backend} relay={cfg.relay_url}")
    if cfg.needs_api_key:
        import os
        if not os.environ.get("DEEPGRAM_API_KEY", "").strip():
            raise SystemExit(
                "[capture] DEEPGRAM_API_KEY is not set.\n"
                "          put it in .env next to this project, or set it in "
                "the environment.\n"
                "          see .env.example"
            )
        print("[capture] DEEPGRAM_API_KEY found")

    app = CaptureApp(cfg, args)
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        print("\n[capture] stopped")


if __name__ == "__main__":
    main()
