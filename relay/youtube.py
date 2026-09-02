"""Pushes finalized captions to a YouTube Live caption ingestion URL.

Google doesn't publish this wire format, but it's the same protocol
professional captioning vendors already use to feed YouTube - see
streamtext.net's "Real-Time Caption Ingestion" documentation. A POST per
caption, with `seq` (an incrementing integer starting at 1) and `lang`
appended to the URL as query parameters, a plain-text body, and a handful of
descriptive headers.

The one YouTube-specific rule that matters: unlike the general protocol,
YouTube's endpoint does NOT support the delete/backspace correction mechanism
that would otherwise let you stream word-by-word interim results. Text must
be decided before it's sent. That is exactly what this app's `is_final`
events already are, so only those are ever pushed here - never interims.

YouTube's own captioning pipeline adds a further delay on top of this (their
documentation for other vendors puts it at 15+ seconds) that nothing here can
shorten.

Additional languages - typically a translated stream, see translate.py - get
their own broadcast caption track via YOUTUBE_CAPTION_URL_<LANG> environment
variables (e.g. YOUTUBE_CAPTION_URL_ES). Those URLs are credentials exactly
like the primary one, so they live only in the environment, never in a config
file, and there is deliberately no GUI for them - only the source-language
track (below) is editable from the status page.
"""

from __future__ import annotations

import asyncio
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

REQUEST_TIMEOUT = 5.0
ENV_PREFIX = "YOUTUBE_CAPTION_URL"

# The only place captions may be POSTed. Every committed line of the event
# goes to this URL, so an arbitrary host here would be a transcript
# exfiltration channel - and, pointed at 127.0.0.1 or a LAN address, a way to
# make the relay poke at things it has no business reaching.
ALLOWED_HOSTS = frozenset({"upload.youtube.com"})


def valid_ingestion_url(url: str) -> bool:
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and (parts.hostname or "").lower() in ALLOWED_HOSTS


def env_key_for(lang: str) -> str:
    """es -> YOUTUBE_CAPTION_URL_ES, zh-Hans -> YOUTUBE_CAPTION_URL_ZH_HANS."""
    return f"{ENV_PREFIX}_{lang.upper().replace('-', '_')}"


def urls_from_env(languages: list[str]) -> dict[str, str]:
    """Map language -> ingestion URL for languages other than the source,
    found only in the environment. The source language's URL is GUI-managed
    (see YouTubeCaptionPusher.configure), not read from here."""
    found: dict[str, str] = {}
    for lang in languages:
        value = os.environ.get(env_key_for(lang), "").strip()
        if not value:
            continue
        if not valid_ingestion_url(value):
            print(f"[relay] ignoring {env_key_for(lang)}: not a YouTube ingestion URL "
                  f"(host must be one of {', '.join(sorted(ALLOWED_HOSTS))})")
            continue
        found[lang] = value
    return found


class YouTubeCaptionPusher:
    """One instance per relay session. `enabled` is the operator's toggle;
    `configured` is whether a URL exists at all - the toggle can't be turned
    on without one."""

    def __init__(self, url: str = "", lang: str = "en-US", start_enabled: bool = False):
        self.url = (url or "").strip()
        self.lang = lang or "en-US"
        self.configured = bool(self.url)
        self.enabled = start_enabled and self.configured
        self._seq = 0
        self.pushed = 0
        self.error: str | None = None

    def _build_url(self, seq: int) -> str:
        """Merge seq/lang into whatever query params the base URL already has
        (notably `cid`) rather than assuming none exist."""
        parts = urllib.parse.urlsplit(self.url)
        params = dict(urllib.parse.parse_qsl(parts.query))
        params["seq"] = str(seq)
        params["lang"] = self.lang
        return urllib.parse.urlunsplit((
            parts.scheme, parts.netloc, parts.path,
            urllib.parse.urlencode(params), parts.fragment))

    def _post(self, text: str) -> None:
        """Blocking HTTP call - always run this via an executor, never
        directly on the event loop."""
        # Only advance seq on success: a run of failures must not burn through
        # sequence numbers YouTube never actually saw.
        next_seq = self._seq + 1
        now = datetime.now(timezone.utc)
        request = urllib.request.Request(
            self._build_url(next_seq),
            data=(text + "\n").encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "text/plain; charset=utf-8",
                "Content-Language": self.lang,
                "Date": now.strftime("%a, %d %b %Y %H:%M:%S GMT"),
                "X-Timestamp": now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                response.read()
            self._seq = next_seq
            self.error = None
            self.pushed += 1
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self.error = f"{exc.__class__.__name__}: {exc}"

    async def push(self, text: str) -> None:
        """Fire-and-forget from the caller's point of view: never raises,
        never blocks the relay's event loop."""
        if not self.enabled:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._post, text)

    def configure(self, url: str, lang: str | None = None) -> None:
        """Set a new ingestion URL from the status page - a distinct stream
        gets its own sequence, so seq resets when the URL actually changes."""
        url = (url or "").strip()
        if url != self.url:
            self._seq = 0
            self.error = None
        self.url = url
        self.configured = bool(self.url)
        if not self.configured:
            self.enabled = False
        if lang and lang.strip():
            self.lang = lang.strip()

    def masked_url(self) -> str:
        """Enough to recognise which stream is saved, never enough to post
        to it: host and path plus the last four characters of `cid`. The
        full URL is a credential and goes nowhere near a status snapshot,
        which is pushed to every status client several times a second."""
        if not self.url:
            return ""
        parts = urllib.parse.urlsplit(self.url)
        cid = dict(urllib.parse.parse_qsl(parts.query)).get("cid", "")
        tail = f"?cid=\u2026{cid[-4:]}" if cid else ""
        return f"{parts.netloc}{parts.path}{tail}"

    def status(self) -> dict:
        return {
            "configured": self.configured,
            "enabled": self.enabled,
            "pushed": self.pushed,
            "error": self.error,
            "url_hint": self.masked_url(),
            "lang": self.lang,
        }
