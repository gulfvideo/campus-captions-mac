"""Recording committed lines, and writing them out as SubRip (.srt).

Every committed line is kept with the wall-clock window it was spoken in, per
language. That single record feeds both YouTube paths: the file you upload to a
video afterwards, and the live ingestion posts during the broadcast.

## The zero point

An .srt is timed from the start of the *video*, not from the start of the world,
so the recorder needs to know when the video's clock started. The operator sets
that with "Mark video start" on the status page - press it when the YouTube
stream or the recording begins. If it is never pressed the first committed line
becomes zero, which is right often enough to be a sane default and wrong in a
way that is easy to see and fix by re-exporting.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

# Keep a cue on screen for at least this long even if the speaker was terse, and
# never longer than this even if there was a long gap before the commit.
MIN_CUE_SECONDS = 1.2
MAX_CUE_SECONDS = 8.0
MAX_CUES = 20000            # ~9 hours of dense speech; bounded on purpose

SAFE_LANG = re.compile(r"^[A-Za-z0-9_-]{1,16}$")


@dataclass
class Cue:
    index: int
    start: float            # seconds from the zero point
    end: float
    text: str


def format_timestamp(seconds: float) -> str:
    """SubRip wants HH:MM:SS,mmm - comma, not a full stop."""
    if seconds < 0:
        seconds = 0.0
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


@dataclass
class TranscriptRecorder:
    zero_time: float | None = None
    started_at: float = field(default_factory=time.time)
    lines: dict[str, list[dict]] = field(default_factory=dict)
    dropped: int = 0

    # -- recording -------------------------------------------------------

    def mark_start(self, when: float | None = None) -> float:
        """Set the video's zero point. Pressing it again re-zeros everything."""
        self.zero_time = when if when is not None else time.time()
        return self.zero_time

    def observe(self, msg: dict) -> None:
        """Fed every outbound message; keeps the finals."""
        if msg.get("type") != "final":
            return
        text = (msg.get("text") or "").strip()
        if not text:
            return
        lang = msg.get("lang") or "und"
        committed = float(msg.get("t") or time.time())
        # t0 is when the utterance began - the first interim after the previous
        # commit. Without it we would have to guess how long the line took.
        started = float(msg.get("t0") or committed)
        if self.zero_time is None:
            self.zero_time = started

        bucket = self.lines.setdefault(lang, [])
        if len(bucket) >= MAX_CUES:
            self.dropped += 1
            return
        bucket.append({
            "text": text,
            "started": started,
            "committed": committed,
            "id": msg.get("id", ""),
            "utterance": msg.get("utterance", msg.get("id", "")),
        })

    def clear(self) -> None:
        self.lines.clear()
        self.zero_time = None
        self.dropped = 0

    # -- export ----------------------------------------------------------

    def languages(self) -> list[str]:
        return sorted(k for k, v in self.lines.items() if v)

    def count(self, lang: str | None = None) -> int:
        if lang:
            return len(self.lines.get(lang, ()))
        return sum(len(v) for v in self.lines.values())

    def cues(self, lang: str) -> list[Cue]:
        """Turn recorded lines into non-overlapping, sanely bounded cues."""
        raw = self.lines.get(lang) or []
        if not raw:
            return []
        zero = self.zero_time if self.zero_time is not None else raw[0]["started"]

        cues: list[Cue] = []
        previous_end = 0.0
        for entry in raw:
            start = entry["started"] - zero
            end = entry["committed"] - zero
            # Lines finished before the video began are not part of the video.
            # Marking the start part way through an event is normal, and
            # clamping those earlier lines to zero would pile them on top of
            # each other at 00:00:00 where nobody could read them.
            if end <= 0:
                continue
            if end - start < MIN_CUE_SECONDS:
                end = start + MIN_CUE_SECONDS
            if end - start > MAX_CUE_SECONDS:
                start = end - MAX_CUE_SECONDS
            # A translated line is committed after its source line, and a
            # speaker can start again before the previous commit lands, so
            # overlaps are normal. SubRip tolerates them badly; nudge instead.
            if start < previous_end:
                start = previous_end
            if end <= start:
                end = start + MIN_CUE_SECONDS
            previous_end = end
            cues.append(Cue(index=len(cues) + 1, start=max(0.0, start),
                            end=max(0.0, end), text=entry["text"]))
        return cues

    def to_srt(self, lang: str) -> str:
        blocks = []
        for cue in self.cues(lang):
            blocks.append(
                f"{cue.index}\n"
                f"{format_timestamp(cue.start)} --> {format_timestamp(cue.end)}\n"
                f"{cue.text}\n"
            )
        return "\n".join(blocks)

    def stats(self) -> dict:
        now = time.time()
        return {
            "recording": True,
            "zero_set": self.zero_time is not None,
            "zero_age": None if self.zero_time is None
                        else round(now - self.zero_time, 1),
            "languages": self.languages(),
            "lines": {lang: len(v) for lang, v in self.lines.items() if v},
            "total": self.count(),
            "dropped": self.dropped,
        }
