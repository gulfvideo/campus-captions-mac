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
from . import host as host_mod
from .backends import create as create_backend
from .backends import support as backend_support
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
        self.device_busy = False            # another program holds the input (preflight)
        self.host = host_mod.HostMonitor()
        self.audio_devices: list[dict] = []
        self._pending: list[tuple[str, dict]] = []  # handled in the main loop
        # The Whisper model download, driven from the setup wizard and the
        # settings page. "idle" until someone presses the button; the model
        # is never fetched on its own - see whisper.fetch_model.
        self.whisper_state = "idle"        # idle | downloading | ready | error
        self.whisper_message = ""
        self._whisper_task: asyncio.Task | None = None

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
            hint = audio_mod.device_busy_hint(exc)
            self.device_busy = bool(hint)
            if hint:
                message += f" - {hint}"
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
        self.device_busy = False
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
        if name not in backend_support.BY_NAME:
            print(f"[capture] unknown backend {name!r}; available: "
                  f"{', '.join(backend_support.supported())}")
            return
        reason = backend_support.unsupported_reason(name)
        if reason:
            # The picker does not offer these, so this is a hand-crafted
            # request or a stale page. Refuse it rather than swapping a
            # working backend out for one that cannot open.
            print(f"[capture] cannot switch to {name}: {reason}")
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

    async def apply_backend_options(self, options: dict) -> None:
        """Take new recognition settings and make them live.

        These used to be event.toml-only - diarization, filler words, the
        Whisper commit timings, the Apple locale - which meant a text editor
        and a restart to change how the recognizer behaves. The relay has
        already written them to disk; this is the half that makes the running
        backend use them without waiting for the next launch.

        A backend reads its options when it opens a connection, not on every
        frame, so the object is rebuilt rather than mutated - the same
        close/create/reopen switch_backend does, which is the path that is
        known to preserve the operator's open intent.
        """
        if not options:
            return
        self.cfg.backend_config.update(options)
        was_open = self.backend.wants_open
        if was_open:
            await self.backend.close()
        self.backend = create_backend(self.cfg.backend, self.cfg.backend_config)
        print(f"[capture] recognition options applied: {', '.join(sorted(options))}")
        if was_open:
            await self.backend.open(self.sample_rate)
            if self.backend.error:
                print(f"[capture] reopen failed: {self.backend.error}")
            else:
                print(f"[capture] {self.cfg.backend} stream reopened with the new options")

    # -- the Whisper model ------------------------------------------------

    def whisper_model_dir(self) -> Path:
        return Path(self.cfg.backend_config.get(
            "whisper_model_dir",
            backend_support.whisper_model_dir(config_mod.DATA_DIR)))

    def whisper_snapshot(self) -> dict:
        """What the setup wizard and the settings page render as the
        "Download the Whisper model" row. `present` is a directory check, so
        it stays true across restarts without anyone having to remember that
        a download happened."""
        present = backend_support.whisper_model_present(self.whisper_model_dir())
        return {
            "installed": backend_support.whisper_installed(),
            # Whether this is a frozen app or a source checkout. It decides
            # what the UI says when Whisper is missing: "pip install" is the
            # answer in a checkout and useless advice inside an installed
            # app, where the only honest answer is that this build does not
            # have it. The Intel macOS build is exactly that case -
            # onnxruntime, which faster-whisper needs, ships no macOS x86_64
            # wheel for this Python.
            "packaged": config_mod.FROZEN,
            # Source checkouts can install it from the app; a frozen build
            # cannot, so the UI offers a button in one case and an
            # explanation in the other.
            "installable": not config_mod.FROZEN,
            "present": present,
            "state": "ready" if present and self.whisper_state == "idle"
                     else self.whisper_state,
            "message": self.whisper_message,
            "model": self.cfg.backend_config.get(
                "whisper_model", backend_support.WHISPER_DEFAULT_MODEL),
            "path": str(self.whisper_model_dir()),
        }

    def start_whisper_download(self) -> None:
        """Fetch the model on a worker thread, leaving the audio pipeline
        running. A few hundred megabytes over venue wifi takes minutes, and
        blocking the event loop for that would stop the level meter, the
        heartbeat and any captions already flowing."""
        if self._whisper_task is not None and not self._whisper_task.done():
            print("[capture] whisper model download already running")
            return
        self.whisper_state = "downloading"
        self.whisper_message = "starting"

        def progress(message: str) -> None:
            self.whisper_message = message

        async def run() -> None:
            from .backends import whisper as whisper_mod
            size = self.cfg.backend_config.get(
                "whisper_model", backend_support.WHISPER_DEFAULT_MODEL)
            target = self.whisper_model_dir()
            loop = asyncio.get_running_loop()
            try:
                # One button, whatever this machine actually needs. From
                # source that is usually both steps; in a packaged app the
                # library is already there and only the model is missing.
                if not whisper_mod.whisper_available() and not config_mod.FROZEN:
                    print("[capture] installing faster-whisper")
                    await loop.run_in_executor(
                        None, whisper_mod.install_package, progress)
                print(f"[capture] downloading whisper model {size} into {target}")
                await loop.run_in_executor(
                    None, whisper_mod.fetch_model, size, target, progress)
            except Exception as exc:
                self.whisper_state = "error"
                self.whisper_message = f"{exc.__class__.__name__}: {exc}"
                print(f"[capture] whisper model download failed: {exc}")
                return
            self.whisper_state = "ready"
            self.whisper_message = ""
            print(f"[capture] whisper model {size} ready")

        self._whisper_task = asyncio.create_task(run())

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
                elif action == "download_whisper_model":
                    self.start_whisper_download()
                elif action == "set_backend_options":
                    options = payload.get("options")
                    await self.apply_backend_options(
                        options if isinstance(options, dict) else {})
                elif action == "relay_moving":
                    port = payload.get("port")
                    if isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535:
                        new_url = config_mod.relay_url_with_port(self.cfg.relay_url, port)
                        self.cfg.relay_url = new_url
                        self.relay.move(new_url)
                        print(f"[capture] relay moving to port {port}; following")
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
            msg = {
                "type": "final" if event.is_final else "interim",
                "text": event.text,
                "t": round(event.t, 3),
            }
            if event.speech_end is not None:
                msg["speech_end"] = round(event.speech_end, 3)
            self.relay.send(msg)

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
                "audio_peak": round(float(getattr(self.input, "peak", 0.0)), 4),
                "clipping": bool(getattr(self.input, "clipping", False)),
                "stream_open": self.backend.stream_open,
                "backend": self.cfg.backend,
                # Only what this machine can run. The status page turns this
                # straight into its picker, and offering apple_speech on a
                # Windows booth is offering a choice that can only ever
                # report an error.
                "available_backends": backend_support.supported(),
                "has_deepgram_key": bool(os.environ.get("DEEPGRAM_API_KEY", "").strip()),
                "device": str(self.input.device),
                "device_index": self.input.device.index,
                "audio_devices": self.audio_devices,
                "whisper": self.whisper_snapshot(),
                "mask_profanity": self.backend.mask_profanity,
                "blocked_word_count": self.backend.blocked_word_count,
                "device_busy": self.device_busy,
                "host": self.host.snapshot(),
                "error": error,
            })
            await self._apply_control()
            await asyncio.sleep(1.0 / STATUS_HZ)

    async def pump_host(self) -> None:
        """CPU and memory of this machine, for the preflight. Cheap enough to
        keep running through the event, so the status page can also say
        when the computer gets busy mid-service."""
        while True:
            self.host.sample()
            await asyncio.sleep(host_mod.SAMPLE_SECONDS)

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
        hint = audio_mod.device_busy_hint(failure) if failure is not None else ""
        self.device_busy = bool(hint)
        if hint:
            self.audio_error += f" - {hint}"
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
            asyncio.create_task(self.pump_host()),
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
    parser.add_argument("--backend", default=None,
                        help="auto | " + " | ".join(backend_support.BY_NAME))
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
        # Same resolution the config file gets, so --backend auto works and
        # --backend apple_speech on a PC lands somewhere usable instead of
        # on a backend that cannot open.
        chosen = backend_support.resolve(args.backend)
        if chosen != args.backend and args.backend != "auto":
            print(f"[capture] {args.backend} cannot run on this machine "
                  f"({backend_support.unsupported_reason(args.backend)}); "
                  f"using {chosen}")
        cfg.backend = chosen
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
