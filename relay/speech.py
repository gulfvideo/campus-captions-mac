"""Spoken translation, synthesized on this Mac.

A phone cannot read a translation aloud itself: on iPhone, Safari's
speechSynthesis runs inside Safari's own audio session, which the ring/silent
switch mutes, and no web API reaches that session (WebKit bug 252746; see
the ios-speech-silent-switch note). Media playback does play on silent, so
the booth Mac speaks each line with macOS's own offline voices (`say`) and
the phone plays the clip through an <audio> element. Nothing leaves the Mac,
no account, no cost.

One clip per (language, text), AAC mono 22 kHz, about 15 KB per second of
speech, synthesized on first request and kept in a small in-memory cache.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import tempfile
from collections import OrderedDict

# The region to prefer when a language has several voices installed. Anything
# not listed takes the first installed voice for that language.
PREFER = {"pt": "pt_BR", "es": "es_MX", "en": "en_US",
          "zh": "zh_CN", "zh-Hans": "zh_CN", "zh-Hant": "zh_TW"}

# What the switch-on confirmation says: the language's own name, in it.
GREETING = {
    "en": "English", "es": "Español", "pt": "Português", "fr": "Français",
    "de": "Deutsch", "it": "Italiano", "ko": "한국어", "ja": "日本語",
    "ar": "العربية", "zh": "中文", "zh-Hans": "简体中文", "zh-Hant": "繁體中文",
    "vi": "Tiếng Việt", "ru": "Русский", "uk": "Українська", "hi": "हिन्दी",
    "id": "Bahasa Indonesia", "nl": "Nederlands", "pl": "Polski", "th": "ไทย",
    "tr": "Türkçe",
}

_VOICE_LINE = re.compile(r"^(.+?)\s+([a-z]{2,3}_[A-Za-z0-9]+)\s+#")
# Apple's novelty and legacy voices; used only when a language has nothing else.
_NOVELTY = {"Albert", "Bad News", "Bahh", "Bells", "Boing", "Bubbles", "Cellos", "Eddy",
            "Flo", "Fred", "Good News", "Grandma", "Grandpa", "Jester", "Junior", "Kathy",
            "Organ", "Ralph", "Reed", "Rocko", "Sandy", "Shelley", "Superstar", "Trinoids",
            "Whisper", "Wobble", "Zarvox"}


def _bare(name: str) -> str:
    return re.sub(r"\s*\(.*", "", name)
_CACHE_MAX = 300
_TIMEOUT = 20.0


def parse_voices(listing: str) -> dict[str, str]:
    """`say -v ?` output -> {locale: first voice name}."""
    voices: dict[str, str] = {}
    for line in listing.splitlines():
        m = _VOICE_LINE.match(line)
        if not m:
            continue
        name, locale = m.group(1).strip(), m.group(2)
        have = voices.get(locale)
        if have is None or (_bare(have) in _NOVELTY and _bare(name) not in _NOVELTY):
            voices[locale] = name
    return voices


def voice_for(lang: str, voices: dict[str, str]) -> str | None:
    base = lang.split("-", 1)[0].lower()
    want = PREFER.get(lang) or PREFER.get(base)
    if want and want in voices:
        return voices[want]
    for locale in sorted(voices):
        if locale.split("_", 1)[0].lower() == base:
            return voices[locale]
    return None


class Speech:
    def __init__(self) -> None:
        self._voices: dict[str, str] | None = None
        self._clips: OrderedDict[tuple[str, str], bytes] = OrderedDict()
        self._inflight: dict[tuple[str, str], asyncio.Task] = {}
        # ponytail: two synths at once; a dozen languages would want a pool.
        self._gate = asyncio.Semaphore(2)

    def voices(self) -> dict[str, str]:
        if self._voices is None:
            try:
                out = subprocess.run(["say", "-v", "?"], capture_output=True,
                                     text=True, timeout=10).stdout
            except (OSError, subprocess.TimeoutExpired):
                return {}  # not cached: tried again next request
            self._voices = parse_voices(out)
        return self._voices

    async def voice(self, lang: str) -> str | None:
        return voice_for(lang, await asyncio.to_thread(self.voices))

    async def clip(self, lang: str, text: str) -> bytes | None:
        """AAC bytes for `text` spoken in `lang`, or None with no voice."""
        key = (lang, text)
        hit = self._clips.get(key)
        if hit is not None:
            self._clips.move_to_end(key)
            return hit
        # Every phone on the same language asks for the same line at the same
        # moment: one synthesis, shared, and shielded so a phone dropping off
        # does not cancel it for the others.
        task = self._inflight.get(key)
        if task is None:
            task = self._inflight[key] = asyncio.ensure_future(self._synth(lang, text))
            task.add_done_callback(lambda _t, k=key: self._inflight.pop(k, None))
        return await asyncio.shield(task)

    async def _synth(self, lang: str, text: str) -> bytes | None:
        key = (lang, text)
        voice = await self.voice(lang)
        if not voice or not text.strip():
            return None
        fd, path = tempfile.mkstemp(suffix=".m4a")
        os.close(fd)
        try:
            async with self._gate:
                proc = await asyncio.create_subprocess_exec(
                    "say", "-v", voice, "-o", path, "--data-format=aac",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL)
                try:
                    await asyncio.wait_for(proc.communicate(text.encode("utf-8")), _TIMEOUT)
                except asyncio.TimeoutError:
                    proc.kill()
                    return None
            if proc.returncode != 0:
                return None
            with open(path, "rb") as f:
                data = f.read()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        if not data:
            return None
        self._clips[key] = data
        while len(self._clips) > _CACHE_MAX:
            self._clips.popitem(last=False)
        return data
