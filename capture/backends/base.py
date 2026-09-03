"""The ASR interface: audio frames in, interim/final transcript events out.

This is the whole contract. The relay and the viewers never learn which backend
is running - they only ever see the messages in docs/MESSAGES.md. Adding a local
on-device backend later means writing one new file next to this one and adding a
line to registry.py; nothing else in the project changes.

Lifecycle is explicit on purpose:

    backend = SomeBackend(config)
    await backend.open(sample_rate=48000)     # a paid connection starts HERE
    await backend.send_audio(frames)          # repeatedly
    await backend.close()                     # and stops HERE

Nothing opens a stream implicitly. The capture client runs with the microphone
live and the stream closed all through setup and rehearsal, because Deepgram
bills connection time, not speech time.
"""

from __future__ import annotations

import asyncio
import re
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

# The standard set a broadcast profanity filter catches - not exhaustive (an
# operator with something event-specific to catch uses the blocklist file
# instead, see reload_wordlist), just the common strong language a service
# would want bleeped by default when the toggle is on.
DEFAULT_PROFANITY = frozenset({
    # fuck
    "fuck", "fucks", "fucked", "fucking", "fuckin", "fucker", "fuckers",
    "motherfucker", "motherfuckers", "motherfucking", "clusterfuck",
    # shit
    "shit", "shits", "shitty", "shitting", "bullshit", "dipshit",
    "horseshit", "shithead", "shithole",
    # ass - the compounds only. Bare "ass" is a donkey, and gets read aloud
    # from scripture; blocking it would censor the text of a chapel service.
    "asshole", "assholes", "asshat", "jackass", "dumbass", "dumbasses",
    # bitch
    "bitch", "bitches", "bitching", "bitchy",
    # the rest
    "cunt", "cunts", "cocksucker", "cocksuckers", "dickhead", "dickheads",
    "prick", "pricks", "piss", "pissed", "pissing", "bastard", "bastards",
    "whore", "whores", "slut", "sluts", "twat", "wanker", "bollocks",
    "pussy", "pussies", "tits", "titties",
    # blasphemy. "damn" and "hell" on their own are deliberately absent for
    # the same reason as "ass": both are ordinary words in a sermon.
    "goddamn", "goddamned", "goddammit", "goddamnit",
})

# Every inflection is spelled out above rather than stemmed. Stemming would
# mean matching on prefixes, and a prefix match is exactly what puts a bleep
# in the middle of "assessment".

_WORD_RE = re.compile(r"[\w']+")

# The word regex keeps apostrophes, so "bitch's" arrives as a single token and
# misses a set built from bare words. Strip a trailing possessive before
# looking it up rather than listing every possessive form.
_POSSESSIVE = ("'s", "’s", "s'")


def is_profane(token: str) -> bool:
    word = token.lower()
    if word in DEFAULT_PROFANITY:
        return True
    return any(
        word.endswith(suffix) and word[: -len(suffix)] in DEFAULT_PROFANITY
        for suffix in _POSSESSIVE
    )


def load_wordlist(path: Path) -> list[str]:
    """One term per line, '#' comments and blank lines ignored - same shape
    as deepgram.py's load_keyterms, for words to remove instead of boost."""
    if not path.exists():
        return []
    terms = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        terms.append(line.lower())
    return terms


def _compile_block_pattern(terms: list[str]) -> re.Pattern | None:
    """One regex for the whole blocklist, matching each term as whole words -
    "cash" on the list must not eat the middle of "cashier". Longest first,
    so a phrase like "acme corp" is consumed before a shorter overlapping
    entry could take just "acme" and leave "corp" behind."""
    if not terms:
        return None
    ordered = sorted(set(terms), key=len, reverse=True)
    alternatives = [
        r"\b" + r"\s+".join(re.escape(word) for word in term.split()) + r"\b"
        for term in ordered
    ]
    return re.compile("|".join(alternatives), re.IGNORECASE)


@dataclass(frozen=True)
class TranscriptEvent:
    """One piece of transcript. `is_final` is the only thing that commits a line.

    `speech_end` is the wall-clock moment the last word in this text was
    actually spoken, when the backend can work that out (Deepgram reports
    where in the audio stream each result ends). It is what caption latency
    is measured from: the relay compares it to the time the text arrived.
    None means "not known for this backend" and no latency is claimed.
    """

    text: str
    is_final: bool
    t: float = field(default_factory=time.time)
    speech_end: float | None = None


class ASRBackend(ABC):
    """Base class. Subclasses push events with `self._emit(...)`."""

    name: str = "base"

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self._queue: asyncio.Queue[TranscriptEvent] = asyncio.Queue(maxsize=512)
        self._open = False
        self._error: str | None = None
        self.blocklist_file = Path(self.config.get("blocklist_file", "config/blocklist.txt"))
        # Named distinctly from DeepgramBackend's own `profanity_filter` (its
        # native API-level filter, deepgram-only): that one and this one both
        # read `self.config`, and deepgram.py's __init__ runs after this one,
        # so a shared attribute name would let it silently clobber this.
        self.mask_profanity = bool(self.config.get("mask_profanity", False))
        self._blocked_terms: list[str] = load_wordlist(self.blocklist_file)
        self._block_pattern = _compile_block_pattern(self._blocked_terms)

    # -- lifecycle -------------------------------------------------------

    @abstractmethod
    async def open(self, sample_rate: int) -> None:
        """Start producing transcript. For paid backends, start billing."""

    @abstractmethod
    async def close(self) -> None:
        """Stop producing transcript and release the connection."""

    @abstractmethod
    async def send_audio(self, frames: bytes) -> None:
        """Hand over one block of mono 16-bit PCM at the opened sample rate."""

    async def reload_keyterms(self) -> None:
        """Re-read vocabulary. Backends without keyterms do nothing."""
        return None

    def reload_wordlist(self) -> None:
        """Re-read the blocklist file. Unlike keyterms this needs no backend
        support and no reconnect: filtering happens on already-transcribed
        text in _emit(), below, so every backend gets it for free."""
        self._blocked_terms = load_wordlist(self.blocklist_file)
        self._block_pattern = _compile_block_pattern(self._blocked_terms)

    # -- output ----------------------------------------------------------

    def _filter_words(self, text: str) -> str:
        """Applied to every event, interim and final alike, so a word caught
        on the committed line was never flashed uncensored while still
        interim. A custom-blocked term (word or short phrase) is removed
        outright - the operator decided it should not appear at all; a
        profanity match is replaced with "[bleep]" instead of deleted, the
        broadcast-captioning convention for telling a deaf reader that
        something was said there without exposing the word itself. Phrases
        are matched first, whole, before the single-word profanity pass -
        otherwise "Acme Corp" on the blocklist would only ever lose "Acme"."""
        if self._block_pattern is not None:
            text = self._block_pattern.sub("", text)
        if self.mask_profanity:
            text = _WORD_RE.sub(
                lambda m: "[bleep]" if is_profane(m.group(0)) else m.group(0),
                text)
        return " ".join(text.split())

    def events(self) -> AsyncIterator[TranscriptEvent]:
        """Async iterator of transcript events. One consumer.

        Only safe while the backend cannot be replaced underneath the
        consumer - switching backends builds a new instance with a new
        queue, and an iterator opened here stays parked on the old one.
        Anything that survives a switch should use next_event() instead.
        """
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[TranscriptEvent]:
        while True:
            yield await self._queue.get()

    async def next_event(self, timeout: float | None = None) -> TranscriptEvent | None:
        """One event, or None once `timeout` passes with nothing to report.

        The timeout is the whole point: it hands control back often enough
        for the caller to notice that the backend it is reading has been
        swapped. A closed backend never emits again, so a consumer parked on
        its queue would wait forever - which is exactly how a backend switch
        used to silence captions until the app was restarted.
        """
        if timeout is None:
            return await self._queue.get()
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def _emit(self, text: str, is_final: bool,
              speech_end: float | None = None) -> None:
        text = " ".join(str(text).split())
        if not text:
            return
        text = self._filter_words(text)
        if not text:
            return
        event = TranscriptEvent(text=text, is_final=is_final, speech_end=speech_end)
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            # Interim text is worthless if it is late, so the oldest interim
            # goes. A final is a committed line the reader will never get
            # another way, so it is never the one evicted: only when the
            # whole queue is finals does the oldest of those go, and that
            # means the consumer has been stalled for hundreds of lines.
            held: list[TranscriptEvent] = []
            while True:
                try:
                    held.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            for i, old in enumerate(held):
                if not old.is_final:
                    del held[i]
                    break
            else:
                if held:
                    held.pop(0)
            for old in held:
                self._queue.put_nowait(old)
            try:
                self._queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    # -- reported to the status page -------------------------------------

    @property
    def stream_open(self) -> bool:
        return self._open

    @property
    def wants_open(self) -> bool:
        """Whether the operator intends the stream to be open.

        Usually this equals ``stream_open``. Network backends may temporarily
        disconnect while still trying to satisfy the operator's request.
        """
        return self._open

    @property
    def error(self) -> str | None:
        return self._error

    @property
    def blocked_word_count(self) -> int:
        return len(self._blocked_terms)
