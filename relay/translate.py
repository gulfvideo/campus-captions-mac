"""Translation of committed caption lines.

Only finals are translated. Interims change several times a second, so
translating them would multiply the bill by roughly twenty and produce text that
rewrites itself faster than anyone can read. A viewer watching Spanish therefore
sees committed lines only, which is the right reading experience anyway.

Translation runs in the relay rather than the capture client because the relay
is where fan-out and the per-language replay buffers already live. One source
line becomes one HTTP request covering every target language at once.

Two providers. "apple" is Apple's on-device Translation framework through a
small Swift helper (capture/backends/native/translate_helper.swift): free,
offline, nothing leaves the Mac. "azure" is Microsoft Translator over HTTPS;
its keys come from the environment, never from a config file:
    AZURE_TRANSLATOR_KEY, AZURE_TRANSLATOR_REGION
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from pathlib import Path

from .config import FROZEN, ROOT
from .tunnel import child_env

AZURE_DEFAULT_ENDPOINT = "https://api.cognitive.microsofttranslator.com"
REQUEST_TIMEOUT = 8.0

# AZURE_TRANSLATOR_ENDPOINT exists for regional/custom Translator resources.
# The subscription key is sent to whatever host it names, so it is held to
# Microsoft's own domains over https - anything else is ignored with a
# message rather than quietly handing the key to a stranger.
ALLOWED_ENDPOINT_SUFFIXES = (
    ".microsofttranslator.com",
    ".cognitiveservices.azure.com",
    ".cognitiveservices.azure.us",
    ".cognitiveservices.azure.cn",
)


def endpoint_from_env() -> str:
    raw = os.environ.get("AZURE_TRANSLATOR_ENDPOINT", "").strip()
    if not raw:
        return AZURE_DEFAULT_ENDPOINT
    try:
        parts = urllib.parse.urlsplit(raw)
        host = (parts.hostname or "").lower()
    except ValueError:
        parts, host = None, ""
    if parts is not None and parts.scheme == "https" and host.endswith(ALLOWED_ENDPOINT_SUFFIXES):
        return raw
    print("[relay] ignoring AZURE_TRANSLATOR_ENDPOINT: must be an https Microsoft "
          "Translator endpoint - using the default")
    return AZURE_DEFAULT_ENDPOINT
CACHE_SIZE = 512
MAX_CHARS = 2000


class AzureTranslator:
    """`translate` returns {language: text}; missing keys just mean that
    language failed, and the source line has already gone out regardless."""

    name = "azure"

    def __init__(self, key: str, region: str = "",
                 endpoint: str = AZURE_DEFAULT_ENDPOINT):
        self._key = key
        self._region = region
        self._endpoint = (endpoint or AZURE_DEFAULT_ENDPOINT).rstrip("/")
        self._cache: OrderedDict[tuple, dict[str, str]] = OrderedDict()
        self.calls = 0
        self.characters = 0
        self.failures = 0
        self.last_error = None
        self.healthy = True

    # -- cache -----------------------------------------------------------

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

    # -- request ---------------------------------------------------------

    def _build_request(self, text: str, source: str,
                       targets: list[str]) -> urllib.request.Request:
        params = [("api-version", "3.0"), ("from", source)]
        params += [("to", t) for t in targets]
        url = f"{self._endpoint}/translate?{urllib.parse.urlencode(params)}"
        body = json.dumps([{"Text": text}]).encode("utf-8")
        headers = {
            "Ocp-Apim-Subscription-Key": self._key,
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(body)),
        }
        if self._region:
            # Required for a regional resource; harmless for a global one.
            headers["Ocp-Apim-Subscription-Region"] = self._region
        return urllib.request.Request(url, data=body, headers=headers,
                                      method="POST")

    def _call(self, text: str, source: str, targets: list[str]) -> dict[str, str]:
        request = self._build_request(text, source, targets)
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            payload = json.load(response)
        out: dict[str, str] = {}
        if isinstance(payload, list) and payload:
            for item in (payload[0].get("translations") or []):
                lang, translated = item.get("to"), item.get("text")
                if isinstance(lang, str) and isinstance(translated, str):
                    out[lang] = translated
        return out

    async def translate(self, text: str, source: str,
                        targets: list[str]) -> dict[str, str]:
        text = text.strip()[:MAX_CHARS]
        if not text or not targets:
            return {}
        key = (text, source, tuple(sorted(targets)))
        hit = self._cached(key)
        if hit is not None:
            return dict(hit)

        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None, self._call, text, source, targets)
            self.calls += 1
            # Azure bills per source character, once, no matter how many target
            # languages ride along on the request.
            self.characters += len(text)
            self.last_error = None
            self.healthy = True
            self._store(key, result)
            return result
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            self._fail(f"HTTP {exc.code}: {detail or exc.reason}")
        except Exception as exc:
            self._fail(f"{exc.__class__.__name__}: {exc}")
        return {}

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
    """Same contract as AzureTranslator, answered by the on-device helper.

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

    _cached = AzureTranslator._cached
    _store = AzureTranslator._store
    _fail = AzureTranslator._fail
    stats = AzureTranslator.stats

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


def create(provider: str, targets: list[str]) -> AzureTranslator | AppleTranslator | None:
    """Build a translator, or None when translation is not configured.

    Returning None is a normal state, not an error: it is what a single-language
    event looks like, and the relay then advertises only the source language.
    """
    provider = (provider or "none").strip().lower()
    if provider in ("", "none") or not targets:
        return None
    if provider == "apple":
        if sys.platform != "darwin":
            print("[relay] Apple translation needs macOS - captions will be "
                  "source language only")
            return None
        return AppleTranslator()
    if provider == "azure":
        key = os.environ.get("AZURE_TRANSLATOR_KEY", "").strip()
        if not key:
            print("[relay] translation configured but AZURE_TRANSLATOR_KEY is "
                  "not set - captions will be source language only")
            return None
        return AzureTranslator(
            key=key,
            region=os.environ.get("AZURE_TRANSLATOR_REGION", "").strip(),
            endpoint=endpoint_from_env(),
        )
    print(f"[relay] unknown translation provider {provider!r}, "
          f"continuing without translation")
    return None


class TranslationWorker:
    """Serialises translation so each language stream stays in order.

    One worker, one queue. A burst of finals is translated one after another, so
    a slow request delays lines but never reorders them. The source line has
    already been delivered by the time anything reaches here, so translation
    failing degrades to source-only rather than losing captions.
    """

    def __init__(self, translator: AzureTranslator | AppleTranslator, source: str,
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
