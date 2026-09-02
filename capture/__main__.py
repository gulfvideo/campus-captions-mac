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
import os
from pathlib import Path

from . import audio as audio_mod
from . import config as config_mod
from .backends import BACKENDS, create as create_backend
from .relay_client import RelayClient

STATUS_HZ = 4.0
# How long a pump waits on the current backend before looking again at which
# backend that is. Short enough that a switch is imperceptible, long enough
# that an idle capture client is not spinning.
BACKEND_POLL_SECONDS = 0.25


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
        self.audio_devices: list[dict] = []
        self._pending: list[tuple[str, dict]] = []  # handled in the main loop

    # -- control ---------------------------------------------------------

    def on_control(self, action: str, payload: dict | None = None) -> None:
        print(f"[capture] control: {action}")
        self._pending.append((action, payload or {}))

    def refresh_audio_devices(self) -> None:
        self.audio_devices = audio_mod.list_input_devices()

    async def switch_audio_device(self, spec: str | int) -> None:
        """Change inputs without restarting the capture client.

        A backend connection is tied to the input's sample rate. If it is open,
        close it for the handoff and restore the operator's open intent after
        the replacement input is live. On failure, restart the old input.
        """
        try:
            device = audio_mod.resolve_device(spec)
        except Exception as exc:
            self.audio_error = f"cannot select audio input: {exc}"
            print(f"[capture] {self.audio_error}")
            self.refresh_audio_devices()
            return

        old_input = self.input
        if old_input and old_input.device.index == device.index:
            self.audio_error = None
            self.refresh_audio_devices()
            return

        was_open = self.backend.wants_open
        if was_open:
            print("[capture] pausing ASR stream for audio input change")
            await self.backend.close()

        if old_input:
            old_input.stop()

        replacement = audio_mod.AudioInput(device)
        try:
            replacement.start()
        except Exception as exc:
            message = f"cannot open {device.name}: {exc}"
            try:
                if old_input:
                    old_input.start()
            except Exception as restore_exc:
                old_input = audio_mod.SilentInput()
                message += f"; previous input could not be restored: {restore_exc}"
            self.input = old_input or audio_mod.SilentInput()
            self.sample_rate = self.input.device.sample_rate
            self.audio_error = message
            print(f"[capture] {message}")
            if was_open:
                await self.backend.open(self.sample_rate)
            self.refresh_audio_devices()
            return

        self.input = replacement
        self.sample_rate = device.sample_rate
        self.cfg.device = str(device.index)
        self.audio_error = None
        self.refresh_audio_devices()
        print(f"[capture] input changed: {device}")
        if was_open:
            await self.backend.open(self.sample_rate)
            print(f"[capture] {self.cfg.backend} stream reopened after input change")

    async def switch_backend(self, name: str) -> None:
        """Swap the ASR backend without restarting the capture client.

        Mirrors switch_audio_device: validate first, only close the running
        backend once the replacement is known to be creatable, and restore the
        operator's open intent on the new backend afterward.
        """
        name = str(name).strip()
        if not name or name == self.cfg.backend:
            return
        if name not in BACKENDS:
            print(f"[capture] unknown backend {name!r}; available: {', '.join(sorted(BACKENDS))}")
            return
        if name == "deepgram":
            if not os.environ.get("DEEPGRAM_API_KEY", "").strip():
                print("[capture] cannot switch to deepgram: DEEPGRAM_API_KEY is not set")
                return

        was_open = self.backend.wants_open
        if was_open:
            print(f"[capture] closing {self.cfg.backend} stream to switch backend")
            await self.backend.close()

        self.backend = create_backend(name, self.cfg.backend_config)
        self.cfg.backend = name
        print(f"[capture] backend switched to {name}")
        if was_open:
            await self.backend.open(self.sample_rate)
            if self.backend.error:
                print(f"[capture] open failed: {self.backend.error}")
            else:
                print(f"[capture] {name} stream reopened after backend switch")

    async def _apply_control(self) -> None:
        while self._pending:
            action, payload = self._pending.pop(0)
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
                    self.backend.reload_wordlist()
                    print("[capture] keyterms and blocklist reloaded")
                elif action == "set_profanity_filter":
                    enabled = bool(payload.get("enabled"))
                    self.backend.mask_profanity = enabled
                    print(f"[capture] profanity filter {'enabled' if enabled else 'disabled'}")
                elif action == "select_audio_device":
                    await self.switch_audio_device(payload.get("device", ""))
                elif action == "refresh_audio_devices":
                    self.refresh_audio_devices()
                    print("[capture] audio device list refreshed")
                elif action == "select_backend":
                    await self.switch_backend(payload.get("backend", ""))
                elif action == "set_deepgram_key":
                    key = str(payload.get("key", "")).strip()
                    if key and config_mod.has_control_chars(key):
                        print("[capture] ignored Deepgram key: contains control characters")
                    elif key:
                        # Save first: if the write is refused, the environment
                        # never learns a key that would not survive a restart.
                        config_mod.save_env_var("DEEPGRAM_API_KEY", key)
                        os.environ["DEEPGRAM_API_KEY"] = key
                        print("[capture] Deepgram API key updated")  # never the key itself
            except Exception as exc:
                print(f"[capture] control {action} failed: {exc!r}")

    # -- pumps -----------------------------------------------------------

    async def pump_audio(self) -> None:
        while True:
            current = self.input
            async for block in current.blocks():
                # A runtime device change replaces self.input. Stop consuming
                # the old iterator and pick up the new one on the next pass.
                if current is not self.input:
                    break
                if block and self.backend.stream_open:
                    await self.backend.send_audio(block)

    async def pump_events(self) -> None:
        # Re-read self.backend every pass. Switching backends builds a whole
        # new instance with a new queue, and an `async for` over the old
        # one's events() would stay parked on a queue nothing writes to
        # again - the new backend then transcribed into a queue nobody
        # drained, which looked like "stream OPEN, no text yet" until the
        # app was restarted. Same hazard pump_audio guards against above.
        while True:
            event = await self.backend.next_event(timeout=BACKEND_POLL_SECONDS)
            if event is None:
                continue
            self.relay.send({
                "type": "final" if event.is_final else "interim",
                "text": event.text,
                "t": round(event.t, 3),
            })

    async def pump_mock_clears(self) -> None:
        """The mock script can ask for a clear; forward it like an operator would.

        Also re-read per pass: the backend in use when this started may not
        be the one running now, and switching *to* mock has to start working
        without a restart.
        """
        while True:
            queue = getattr(self.backend, "clear_requests", None)
            if queue is None:
                await asyncio.sleep(BACKEND_POLL_SECONDS)
                continue
            try:
                await asyncio.wait_for(queue.get(), timeout=BACKEND_POLL_SECONDS)
            except asyncio.TimeoutError:
                continue
            self.relay.send({"type": "clear"})

    async def pump_status(self) -> None:
        while True:
            error = self.audio_error or self.backend.error or self.input.error
            self.relay.send({
                "type": "status",
                "audio_level": round(float(self.input.level), 4),
                "stream_open": self.backend.stream_open,
                "backend": self.cfg.backend,
                "available_backends": sorted(BACKENDS),
                "has_deepgram_key": bool(os.environ.get("DEEPGRAM_API_KEY", "").strip()),
                "device": str(self.input.device),
                "device_index": self.input.device.index,
                "audio_devices": self.audio_devices,
                "mask_profanity": self.backend.mask_profanity,
                "blocked_word_count": self.backend.blocked_word_count,
                "error": error,
            })
            await self._apply_control()
            await asyncio.sleep(1.0 / STATUS_HZ)

    # -- lifecycle -------------------------------------------------------

    def open_input(self) -> None:
        if self.args.no_audio:
            self.input = audio_mod.SilentInput()
            print("[capture] --no-audio: running without an input device")
            self.refresh_audio_devices()
            return
        # A remembered input can simply be gone - the interface it names was
        # left at the last venue, or the dock is a different one today.
        # Dropping straight to SilentInput would hand the operator a dead
        # meter for hardware they no longer have, so fall back to the system
        # default first, and say that is what happened.
        saved = str(self.cfg.device or "").strip()
        attempts = [self.cfg.device] + ([""] if saved else [])
        failure: Exception | None = None
        for spec in attempts:
            try:
                device = audio_mod.resolve_device(spec)
                self.input = audio_mod.AudioInput(device)
                self.input.start()
                self.sample_rate = device.sample_rate
                if spec == "" and saved:
                    self.audio_error = (
                        f"saved input {saved!r} is not on this machine - "
                        f"using the system default instead")
                    print(f"[capture] {self.audio_error}")
                else:
                    self.audio_error = None
                print(f"[capture] input: {device}")
                self.refresh_audio_devices()
                return
            except Exception as exc:
                failure = exc

        self.audio_error = f"{failure}"
        if self.cfg.backend == "deepgram":
            # Deepgram with no audio would bill for silence. Refuse.
            raise SystemExit(
                f"[capture] cannot open audio input: {failure}\n"
                f"          run 'python -m capture --list-devices' to see "
                f"this machine's inputs."
            )
        self.input = audio_mod.SilentInput()
        print(f"[capture] no audio input ({failure}); continuing for the "
              f"{self.cfg.backend} backend")
        self.refresh_audio_devices()

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
    parser.add_argument("--backend", default=None, help="mock | deepgram | apple_speech")
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
