"""Translation of committed caption lines.

Only finals are translated. Interims change several times a second, so
translating them would be twenty times the work and produce text that
rewrites itself faster than anyone can read. A viewer watching Spanish therefore
sees committed lines only, which is the right reading experience anyway.

Translation runs in the relay rather than the capture client because the relay
is where fan-out and the per-language replay buffers already live. One source
line becomes one request covering every target language at once.

The translator is Apple's on-device Translation framework, through a small
Swift helper (capture/backends/native/translate_helper.swift): free, offline,
no account, nothing leaves the Mac.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path

from .config import FROZEN, ROOT
from .tunnel import child_env

CACHE_SIZE = 512
MAX_CHARS = 2000


NATIVE_DIR = (Path(sys._MEIPASS, "capture", "backends", "native") if FROZEN
              else ROOT / "capture" / "backends" / "native")
HELPER = NATIVE_DIR / "build" / "translate_helper"
SOURCE = NATIVE_DIR / "translate_helper.swift"
HELPER_TIMEOUT = 20.0        # the first line loads the model; later ones take ~0.3 s


def _ensure_helper() -> str | None:
    """Build the helper from source if it is missing or stale; an error or None.
    The packaged app ships it prebuilt and has nothing to compile with."""
    if HELPER.exists() and (FROZEN or not SOURCE.exists()
                            or HELPER.stat().st_mtime >= SOURCE.stat().st_mtime):
        return None
    if FROZEN:
        return "translate_helper is missing from this app bundle"
    try:
        result = subprocess.run([str(NATIVE_DIR / "build.sh")], capture_output=True,
                                text=True, timeout=180, env=child_env())
    except FileNotFoundError:
        return "Xcode command line tools not found (swiftc unavailable)"
    except subprocess.TimeoutExpired:
        return "building translate_helper timed out"
    if result.returncode != 0:
        return f"failed to build translate_helper: {result.stderr.strip()[-500:]}"
    return None


class AppleTranslator:
    """`translate` returns {language: text}; a missing key just means that
    language failed, and the source line has already gone out regardless.

    The helper is started on the first line and restarted if it dies. One
    request in flight at a time is all TranslationWorker ever sends."""

    name = "apple"

    def __init__(self):
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._cache: OrderedDict[tuple, dict[str, str]] = OrderedDict()
        self.calls = 0
        self.characters = 0
        self.failures = 0
        self.last_error = None
        self.healthy = True

    def _cached(self, key: tuple) -> dict[str, str] | None:
        hit = self._cache.get(key)
        if hit is not None:
            self._cache.move_to_end(key)
        return hit

    def _store(self, key: tuple, value: dict[str, str]) -> None:
        self._cache[key] = value
        self._cache.move_to_end(key)
        while len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)

    def _fail(self, message: str) -> None:
        self.failures += 1
        self.last_error = message
        self.healthy = False

    def stats(self) -> dict:
        return {
            "provider": self.name,
            "calls": self.calls,
            "characters": self.characters,
            "failures": self.failures,
            "healthy": self.healthy,
            "error": self.last_error,
        }

    async def _start(self) -> None:
        error = await asyncio.get_running_loop().run_in_executor(None, _ensure_helper)
        if error:
            raise RuntimeError(error)
        self._proc = await asyncio.create_subprocess_exec(
            str(HELPER), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=child_env())
        ready = await asyncio.wait_for(self._proc.stdout.readline(), HELPER_TIMEOUT)
        if b'"ready"' not in ready:
            raise RuntimeError("translate_helper did not start")

    async def _ask(self, text: str, source: str, targets: list[str]) -> dict:
        if self._proc is None or self._proc.returncode is not None:
            await self._start()
        self._next_id += 1
        line = json.dumps({"id": self._next_id, "text": text, "source": source,
                           "targets": targets}) + "\n"
        self._proc.stdin.write(line.encode("utf-8"))
        await self._proc.stdin.drain()
        raw = await asyncio.wait_for(self._proc.stdout.readline(), HELPER_TIMEOUT)
        if not raw:
            raise RuntimeError("translate_helper exited")
        reply = json.loads(raw)
        if reply.get("id") != self._next_id:
            raise RuntimeError("translate_helper answered out of order")
        return reply

    async def translate(self, text: str, source: str,
                        targets: list[str]) -> dict[str, str]:
        text = text.strip()[:MAX_CHARS]
        if not text or not targets:
            return {}
        key = (text, source, tuple(sorted(targets)))
        hit = self._cached(key)
        if hit is not None:
            return dict(hit)
        try:
            reply = await self._ask(text, source, targets)
        except Exception as exc:
            self._fail(f"{exc.__class__.__name__}: {exc}")
            if self._proc is not None and self._proc.returncode is None:
                self._proc.kill()
            self._proc = None
            return {}
        result = {k: v for k, v in (reply.get("translations") or {}).items()
                  if isinstance(k, str) and isinstance(v, str)}
        errors = reply.get("errors") or {}
        self.calls += 1
        self.characters += len(text)
        if errors:
            # A language the Mac cannot do is reported, but the ones it can
            # still go out - and the line is not retried, so it is cached.
            self._fail("; ".join(str(v) for v in errors.values())[:300])
        else:
            self.last_error = None
            self.healthy = True
        self._store(key, result)
        return result


def create(targets: list[str]) -> AppleTranslator | None:
    """Build a translator, or None when translation is not configured.

    Returning None is a normal state, not an error: it is what a single-language
    event looks like, and the relay then advertises only the source language.
    It is also what a Mac that cannot run the helper gets, with the reason
    printed so the status page's "source language only" has an explanation.
    """
    if not targets:
        return None
    if sys.platform != "darwin":
        print("[relay] translation needs macOS - captions will be "
              "source language only")
        return None
    error = _ensure_helper()
    if error:
        print(f"[relay] translation unavailable: {error} - captions will be "
              "source language only")
        return None
    return AppleTranslator()


class TranslationWorker:
    """Serialises translation so each language stream stays in order.

    One worker, one queue. A burst of finals is translated one after another, so
    a slow request delays lines but never reorders them. The source line has
    already been delivered by the time anything reaches here, so translation
    failing degrades to source-only rather than losing captions.
    """

    def __init__(self, translator: AppleTranslator, source: str,
                 targets: list[str], emit):
        self.translator = translator
        self.source = source
        self.targets = list(targets)
        self.emit = emit                     # emit(lang, text, utterance_id, t0, t)
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        self.translated = 0
        self.dropped = 0
        self._task: asyncio.Task | None = None

    def submit(self, text: str, utterance_id: str,
               t0: float | None = None, t: float | None = None) -> None:
        """Queue a committed source line. `t0`/`t` are when it was spoken and
        committed; the translated line inherits them so its .srt cue covers
        the same window instead of collapsing to the minimum duration."""
        item = (text, utterance_id, t0, t)
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            # Falling behind: the newest line matters more than the oldest.
            self.dropped += 1
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(item)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            text, utterance_id, t0, t = await self.queue.get()
            try:
                results = await self.translator.translate(
                    text, self.source, self.targets)
                for lang, translated in results.items():
                    self.emit(lang, translated, utterance_id, t0, t)
                if results:
                    self.translated += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:          # never take the relay down
                print(f"[relay] translation worker error: {exc!r}")

    def stats(self) -> dict:
        base = {"targets": list(self.targets),
                "translated_lines": self.translated,
                "queue": self.queue.qsize(),
                "dropped": self.dropped}
        base.update(self.translator.stats())
        return base
