"""Deepgram streaming backend (Nova-3).

Talks to Deepgram's streaming websocket directly rather than through the SDK.
That keeps the dependency list to `websockets` (already pinned for the relay),
avoids the SDK's own transitive version pins, and - the reason that matters most
here - leaves the connection lifecycle completely explicit. Billing is by
connection time, so the socket opens when an operator presses the button and not
one second earlier.

The API key is read from the DEEPGRAM_API_KEY environment variable. It is never
written to a config file, never logged, and never sent to the relay.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from urllib.parse import urlencode

import websockets

from .base import ASRBackend

DEEPGRAM_WS = "wss://api.deepgram.com/v1/listen"

# Deepgram closes an idle socket after ~10s of no audio; KeepAlive prevents that
# during a silent stretch of an event.
KEEPALIVE_SECONDS = 8.0
# Long enough for in-flight finals to arrive after CloseStream, short enough that
# an operator pressing "close" sees it happen.
DRAIN_SECONDS = 2.0
MAX_KEYTERMS = 200


def load_keyterms(path: Path) -> list[str]:
    """One term per line. '#' comments and blank lines ignored.

    Editable per event without touching code - faculty names, building names,
    program names, anything the model would otherwise spell wrong.
    """
    if not path.exists():
        return []
    terms = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        terms.append(line)
    return terms[:MAX_KEYTERMS]


class DeepgramBackend(ASRBackend):
    name = "deepgram"

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        cfg = self.config
        self.model = str(cfg.get("model", "nova-3"))
        self.language = str(cfg.get("language", "en-US"))
        self.punctuate = bool(cfg.get("punctuate", True))
        self.smart_format = bool(cfg.get("smart_format", True))
        self.endpointing = int(cfg.get("endpointing", 300))
        # Speaker changes matter to a deaf reader as much as the words: in a
        # panel or Q&A, one unbroken stream cannot tell a question from its
        # answer. Deepgram labels every word with a speaker index; a change
        # becomes a ">>" marker at the start of that speaker's words, the
        # convention broadcast captions already use. Names still come from
        # the operator (the status page's caption box) - ASR only knows
        # "speaker 0" and "speaker 1".
        self.diarize = bool(cfg.get("diarize", True))
        # A word the model itself doubts is shown as [inaudible] rather than
        # as a confident guess: a deaf reader cannot check it against the
        # audio, and a marked gap is honest where a wrong name is not. 0
        # disables it. Applies to committed lines only.
        self.inaudible_below = float(cfg.get("inaudible_below", 0.3))
        # Verbatim by default - the accessible choice. Both are explicit so
        # nobody turns the filter on for a service without seeing that it
        # changes what deaf attendees receive versus hearing ones.
        self.profanity_filter = bool(cfg.get("profanity_filter", False))
        self.filler_words = bool(cfg.get("filler_words", False))
        self.keyterms_file = Path(cfg.get("keyterms_file", "config/keyterms.txt"))
        self.keyterms: list[str] = []
        self.sample_rate = 16000
        self._last_speaker: int | None = None

        self._ws = None
        self._tasks: list[asyncio.Task] = []
        self._want_open = False       # the operator's intent, survives dropouts
        self._connected = False
        self._sent_bytes = 0
        self._opened_at: float | None = None
        # Wall clock when the first audio byte of the current connection went
        # out. Deepgram times its results from that byte, so this converts a
        # result's `start + duration` into "when those words were spoken" for
        # the relay's latency measurement. Reset on every (re)connect.
        self._audio_started_at: float | None = None

    # -- connection ------------------------------------------------------

    def _url(self) -> str:
        params = [
            ("model", self.model),
            ("language", self.language),
            ("encoding", "linear16"),
            ("sample_rate", str(self.sample_rate)),
            ("channels", "1"),
            ("interim_results", "true"),
            ("punctuate", "true" if self.punctuate else "false"),
            ("smart_format", "true" if self.smart_format else "false"),
            ("endpointing", str(self.endpointing)),
        ]
        if self.diarize:
            params.append(("diarize", "true"))
        if self.profanity_filter:
            params.append(("profanity_filter", "true"))
        if self.filler_words:
            params.append(("filler_words", "true"))
        # Nova-3 uses keyterm prompting; older models use keyword boosting.
        key = "keyterm" if self.model.startswith("nova-3") else "keywords"
        params += [(key, term) for term in self.keyterms]
        return f"{DEEPGRAM_WS}?{urlencode(params)}"

    async def open(self, sample_rate: int) -> None:
        if self._want_open:
            return
        api_key = os.environ.get("DEEPGRAM_API_KEY", "").strip()
        if not api_key:
            self._error = "DEEPGRAM_API_KEY is not set"
            return
        self.sample_rate = int(sample_rate)
        self.keyterms = load_keyterms(self.keyterms_file)
        self._last_speaker = None
        self._want_open = True
        self._error = None
        self._tasks.append(asyncio.create_task(self._run(api_key)))

    async def close(self) -> None:
        self._want_open = False
        self._open = False
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                # Ask Deepgram to flush what it has, then keep listening until
                # it closes the socket on us (it does, after the last final)
                # or DRAIN_SECONDS pass. A quarter-second wait here used to
                # lose the tail of every stream close, backend switch, and
                # keyterm reload.
                await ws.send(json.dumps({"type": "CloseStream"}))
                if self._tasks:
                    await asyncio.wait(list(self._tasks), timeout=DRAIN_SECONDS)
            except Exception:
                pass
            try:
                await ws.close()
            except Exception:
                pass
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
        self._connected = False
        self._opened_at = None

    async def _run(self, api_key: str) -> None:
        """Keep a socket up for as long as the operator wants one."""
        backoff = 0.5
        while self._want_open:
            try:
                async with websockets.connect(
                    self._url(),
                    additional_headers={"Authorization": f"Token {api_key}"},
                    ping_interval=20,
                    ping_timeout=20,
                    max_queue=64,
                ) as ws:
                    self._ws = ws
                    self._open = True
                    self._connected = True
                    self._error = None
                    self._opened_at = time.time()
                    self._audio_started_at = None
                    self._sent_bytes = 0
                    backoff = 0.5
                    keeper = asyncio.create_task(self._keepalive(ws))
                    try:
                        await self._receive(ws)
                    finally:
                        keeper.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._error = f"{exc.__class__.__name__}: {exc}"
            finally:
                self._ws = None
                self._connected = False
                self._open = False

            if not self._want_open:
                break
            # A drop mid-event must not end captions for the rest of the event.
            await asyncio.sleep(backoff)
            backoff = min(10.0, backoff * 2)

    async def _keepalive(self, ws) -> None:
        while True:
            await asyncio.sleep(KEEPALIVE_SECONDS)
            try:
                await ws.send(json.dumps({"type": "KeepAlive"}))
            except Exception:
                return

    async def _receive(self, ws) -> None:
        async for raw in ws:
            if isinstance(raw, bytes):
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            kind = msg.get("type")
            if kind == "Results":
                alts = (msg.get("channel") or {}).get("alternatives") or [{}]
                is_final = bool(msg.get("is_final"))
                text = self._text_with_speakers(alts[0] or {}, is_final)
                if text:
                    self._emit(text, is_final=is_final, speech_end=self._speech_end(msg))
            elif kind == "Error":
                self._error = str(msg.get("description") or msg.get("message") or msg)
            # Metadata, SpeechStarted and UtteranceEnd need no action here.

    def _text_with_speakers(self, alt: dict, is_final: bool) -> str:
        """The committed transcript rebuilt from its words: ">>" wherever the
        speaker changes, and "[inaudible]" where the model's own confidence
        in a word falls below `inaudible_below`.

        Only finals are rebuilt: Deepgram's speaker labels and confidences on
        interim results are still settling, and a marker that flickers in and
        out on the live row would be worse than none. The last committed
        speaker carries across segments, so a marker appears only on an
        actual change - not at the start of every sentence from one person.
        """
        transcript = alt.get("transcript", "")
        words = alt.get("words") or []
        if not is_final or not words:
            return transcript
        has_speakers = self.diarize and any(isinstance(w, dict) and "speaker" in w for w in words)
        mark_doubt = self.inaudible_below > 0
        if not has_speakers and not mark_doubt:
            return transcript
        out: list[str] = []
        prev = self._last_speaker
        for w in words:
            if not isinstance(w, dict):
                continue
            token = w.get("punctuated_word") or w.get("word") or ""
            if not token:
                continue
            speaker = w.get("speaker")
            if has_speakers and isinstance(speaker, int) and speaker != prev:
                if prev is not None or out:
                    out.append(">>")
                prev = speaker
            confidence = w.get("confidence")
            if (mark_doubt and isinstance(confidence, (int, float))
                    and confidence < self.inaudible_below):
                # Collapse a run of doubtful words into one marker.
                if not out or out[-1] != "[inaudible]":
                    out.append("[inaudible]")
                continue
            out.append(token)
        if has_speakers:
            self._last_speaker = prev
        return " ".join(out)

    def _speech_end(self, msg: dict) -> float | None:
        """When the last word of this result was spoken, as wall-clock time.

        `start` and `duration` are seconds into the audio we have sent on this
        connection. Audio goes out as it is captured, in 100 ms blocks, so
        "first byte sent" is within a block of "first byte heard" - close
        enough for a latency figure whose interesting range is whole seconds.
        """
        if self._audio_started_at is None:
            return None
        try:
            start = float(msg.get("start", 0.0))
            duration = float(msg.get("duration", 0.0))
        except (TypeError, ValueError):
            return None
        if start < 0 or duration < 0:
            return None
        return self._audio_started_at + start + duration

    # -- audio -----------------------------------------------------------

    async def send_audio(self, frames: bytes) -> None:
        ws = self._ws
        if ws is None or not self._connected:
            return                     # closed, or reconnecting: drop, never queue
        try:
            await ws.send(frames)
            if self._audio_started_at is None:
                self._audio_started_at = time.time()
            self._sent_bytes += len(frames)
        except Exception as exc:
            self._error = f"send failed: {exc.__class__.__name__}"

    async def reload_keyterms(self) -> None:
        """Pick up an edited keyterms file mid-event.

        Deepgram fixes keyterms at connect time, so this reopens the stream.
        Captions stop for about a second.
        """
        if not self._want_open:
            self.keyterms = load_keyterms(self.keyterms_file)
            return
        rate = self.sample_rate
        await self.close()
        await self.open(rate)

    # -- reporting -------------------------------------------------------

    @property
    def connection_seconds(self) -> float:
        """What Deepgram is billing for, so the operator can see the meter run."""
        return 0.0 if self._opened_at is None else time.time() - self._opened_at

    @property
    def wants_open(self) -> bool:
        return self._want_open
