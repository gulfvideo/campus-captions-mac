"""Live captions into a YouTube broadcast.

YouTube Studio gives a broadcast a caption ingestion URL when its caption source
is set to "POST captions to URL". Each caption is one HTTP POST:

    POST http://upload.youtube.com/closedcaption?cid=...&lang=en-US&seq=N
    Content-Type: text/plain

    2026-08-31T18:08:46.609
    Our next speaker is Doctor Amara Vasquez.

The first body line is the UTC time the words were spoken, the rest is the text.
`seq` increments per post so YouTube can order them.

**The cid is a secret.** Anyone holding that URL can write captions onto the
broadcast, so it lives in .env like every other credential, never in a config
file:

    YOUTUBE_CAPTION_URL=http://upload.youtube.com/closedcaption?cid=...

A translated stream can be sent to its own broadcast caption track by adding
YOUTUBE_CAPTION_URL_ES, YOUTUBE_CAPTION_URL_ZH_HANS, and so on.

Only committed lines are sent. Interims would post several times a second and
YouTube would show text thrashing.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import urllib.error
import urllib.parse
import urllib.request

REQUEST_TIMEOUT = 6.0
ENV_PREFIX = "YOUTUBE_CAPTION_URL"


def env_key_for(lang: str) -> str:
    """es -> YOUTUBE_CAPTION_URL_ES, zh-Hans -> YOUTUBE_CAPTION_URL_ZH_HANS."""
    return f"{ENV_PREFIX}_{lang.upper().replace('-', '_')}"


def urls_from_env(source_language: str, languages: list[str]) -> dict[str, str]:
    """Map language -> ingestion URL, from the environment only."""
    found: dict[str, str] = {}
    base = os.environ.get(ENV_PREFIX, "").strip()
    if base:
        found[source_language] = base
    for lang in languages:
        value = os.environ.get(env_key_for(lang), "").strip()
        if value:
            found[lang] = value
    return found


def format_timestamp(when: float) -> str:
    """YouTube wants UTC, ISO 8601, milliseconds, no timezone suffix."""
    stamp = dt.datetime.fromtimestamp(when, dt.timezone.utc)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.") + f"{stamp.microsecond // 1000:03d}"


class LiveCaptionSender:
    """One broadcast caption track. Fire-and-forget, order preserved."""

    def __init__(self, url: str, lang: str):
        self.base_url = url
        self.lang = lang
        self.seq = 0
        self.sent = 0
        self.failures = 0
        self.last_error: str | None = None
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._task: asyncio.Task | None = None

    def submit(self, text: str, spoken_at: float) -> None:
        try:
            self.queue.put_nowait((text, spoken_at))
        except asyncio.QueueFull:
            # A late caption is worse than a missing one on a live broadcast.
            self.failures += 1
            try:
                self.queue.get_nowait()
                self.queue.put_nowait((text, spoken_at))
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass

    def _build_url(self) -> str:
        parts = urllib.parse.urlsplit(self.base_url)
        params = dict(urllib.parse.parse_qsl(parts.query))
        params.setdefault("lang", self.lang)
        params["seq"] = str(self.seq)
        return urllib.parse.urlunsplit((
            parts.scheme, parts.netloc, parts.path,
            urllib.parse.urlencode(params), parts.fragment))

    def _post(self, text: str, spoken_at: float) -> None:
        body = (format_timestamp(spoken_at) + "\n" + text + "\n").encode("utf-8")
        request = urllib.request.Request(
            self._build_url(), data=body, method="POST",
            headers={"Content-Type": "text/plain; charset=utf-8",
                     "Content-Length": str(len(body))})
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            response.read()

    async def _run(self) -> None:
        while True:
            text, spoken_at = await self.queue.get()
            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(None, self._post, text, spoken_at)
                self.seq += 1
                self.sent += 1
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except urllib.error.HTTPError as exc:
                self.failures += 1
                self.last_error = f"HTTP {exc.code} {exc.reason}"
            except Exception as exc:
                self.failures += 1
                self.last_error = f"{exc.__class__.__name__}: {exc}"

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

    def stats(self) -> dict:
        return {"lang": self.lang, "sent": self.sent, "seq": self.seq,
                "failures": self.failures, "error": self.last_error,
                "queue": self.queue.qsize()}


class LiveCaptions:
    """All configured broadcast tracks together."""

    def __init__(self, urls: dict[str, str]):
        self.senders = {lang: LiveCaptionSender(url, lang)
                        for lang, url in urls.items()}

    @property
    def active(self) -> bool:
        return bool(self.senders)

    def submit(self, lang: str, text: str, spoken_at: float) -> None:
        sender = self.senders.get(lang)
        if sender is not None:
            sender.submit(text, spoken_at)

    def start(self) -> None:
        for sender in self.senders.values():
            sender.start()

    async def stop(self) -> None:
        for sender in self.senders.values():
            await sender.stop()

    def stats(self) -> dict:
        return {
            "active": self.active,
            "tracks": [s.stats() for s in self.senders.values()],
            "sent": sum(s.sent for s in self.senders.values()),
            "failures": sum(s.failures for s in self.senders.values()),
        }
