"""Recording committed lines, and writing them out as caption and transcript
files: SubRip (.srt), WebVTT (.vtt), plain text, JSON and CSV.

Every committed line is kept with the wall-clock window it was spoken in, per
language. That single record feeds every export and both YouTube paths: the
file you upload to a video afterwards, and the live ingestion posts during the
broadcast. All five formats are views of the same cue list - `cues()` does the
timing and display-splitting work once and each `to_*` only formats, so a cue
has the same number, start and end in every file.

## The zero point

An .srt is timed from the start of the *video*, not from the start of the world,
so the recorder needs to know when the video's clock started. The operator sets
that with "Mark video start" on the status page - press it when the YouTube
stream or the recording begins. If it is never pressed the first committed line
becomes zero, which is right often enough to be a sane default and wrong in a
way that is easy to see and fix by re-exporting.
"""

from __future__ import annotations

import csv
import io
import json
import re
import time
from pathlib import Path
from dataclasses import dataclass, field

# Keep a cue on screen for at least this long even if the speaker was terse, and
# never longer than this even if there was a long gap before the commit.
MIN_CUE_SECONDS = 1.2
MAX_CUE_SECONDS = 7.0
MAX_CUES = 20000            # ~9 hours of dense speech; bounded on purpose

# Presentation rules from the DCMP Captioning Key, which is what YouTube's
# own caption editor and most players assume: at most two lines of at most
# 42 characters per cue, a reading rate a viewer can keep up with, and a
# visible gap between consecutive cues so two identical positions do not
# blur into one.
MAX_LINE_CHARS = 42
MAX_LINES = 2
READ_CHARS_PER_SECOND = 17.0
MIN_GAP_SECONDS = 0.08

SAFE_LANG = re.compile(r"^[A-Za-z0-9_-]{1,16}$")


@dataclass
class Cue:
    index: int
    start: float            # seconds from the zero point
    end: float
    text: str
    utterance: str = ""     # id shared by a source line and its translations


# Export formats: extension -> (media type, method name on TranscriptRecorder).
# One table so the HTTP route, the status page links and the docs agree on
# exactly which files exist.
FORMATS = {
    "srt":  ("application/x-subrip; charset=utf-8", "to_srt"),
    "vtt":  ("text/vtt; charset=utf-8", "to_vtt"),
    "txt":  ("text/plain; charset=utf-8", "to_txt"),
    "json": ("application/json; charset=utf-8", "to_json"),
    "csv":  ("text/csv; charset=utf-8", "to_csv"),
}


def wrap_lines(text: str, max_chars: int = MAX_LINE_CHARS) -> list[str]:
    """Word-wrap to lines of at most `max_chars`; a single over-long token is
    cut rather than allowed to overflow."""
    out: list[str] = []
    cur = ""
    for word in text.split():
        while len(word) > max_chars:
            if cur:
                out.append(cur)
                cur = ""
            out.append(word[:max_chars])
            word = word[max_chars:]
        if not cur:
            cur = word
        elif len(cur) + 1 + len(word) <= max_chars:
            cur += " " + word
        else:
            out.append(cur)
            cur = word
    if cur:
        out.append(cur)
    return out


def split_for_display(text: str, max_chars: int = MAX_LINE_CHARS,
                     max_lines: int = MAX_LINES) -> list[str]:
    """One recorded line becomes one or more cue texts of at most `max_lines`
    wrapped lines each, so a long sentence is never a single unreadable
    block.

    The defaults are the cue standard this module writes SRT/VTT against.
    relay/propresenter.py passes its own pair instead: a ProPresenter text
    box is whatever width the church made it, not 42 characters.
    """
    lines = wrap_lines(text, max_chars)
    if not lines:
        return []
    return ["\n".join(lines[i:i + max_lines]) for i in range(0, len(lines), max_lines)]


def format_timestamp(seconds: float, decimal: str = ",") -> str:
    """HH:MM:SS,mmm. SubRip wants a comma before the milliseconds; WebVTT
    wants a full stop - pass `decimal="."` for that."""
    if seconds < 0:
        seconds = 0.0
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{decimal}{millis:03d}"


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

    def archive(self, folder: Path, title: str = "") -> list[Path]:
        """Write every language as .srt and .txt into `folder`, named by
        the time and the event title. Used before a reset so a fast turnaround
        between two services never loses the first one's transcript."""
        written: list[Path] = []
        if not self.count():
            return written
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H%M", time.localtime(self.zero_time or self.started_at))
        name = re.sub(r"[^\w\- ]+", "", title).strip() or "event"
        for lang in self.languages():
            for fmt in ("srt", "txt"):
                path = folder / f"{stamp} {name} {lang}.{fmt}"
                path.write_text(self.export(lang, fmt), encoding="utf-8")
                written.append(path)
        return written

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

    def cues(self, lang: str, offset: float = 0.0) -> list[Cue]:
        """Turn recorded lines into non-overlapping, sanely bounded cues that
        follow the presentation rules above.

        `offset` shifts every cue earlier by that many seconds - the
        recognizer's own delay, so cues line up with the speaker's mouth
        rather than with the moment the line was committed.
        """
        raw = self.lines.get(lang) or []
        if not raw:
            return []
        zero = self.zero_time if self.zero_time is not None else raw[0]["started"]

        # First pass: one window per recorded line, split into display-sized
        # pieces that share the window in proportion to their length.
        pieces: list[tuple[float, float, str, str]] = []
        for entry in raw:
            utterance = str(entry.get("utterance") or entry.get("id") or "")
            start = entry["started"] - zero - offset
            end = entry["committed"] - zero - offset
            # Lines finished before the video began are not part of the video.
            # Marking the start part way through an event is normal, and
            # clamping those earlier lines to zero would pile them on top of
            # each other at 00:00:00 where nobody could read them.
            if end < 0:
                continue
            start = max(0.0, start)
            if end - start > MAX_CUE_SECONDS * MAX_LINES:
                start = end - MAX_CUE_SECONDS * MAX_LINES
            texts = split_for_display(entry["text"])
            if not texts:
                continue
            total = sum(len(t) for t in texts) or 1
            cursor = start
            for i, text in enumerate(texts):
                share = (end - start) * len(text) / total
                piece_end = end if i == len(texts) - 1 else cursor + share
                pieces.append((cursor, piece_end, text, utterance))
                cursor = piece_end

        # Second pass: durations a reader can manage, no overlaps, a gap
        # between neighbours, and nothing longer than MAX_CUE_SECONDS.
        cues: list[Cue] = []
        previous_end = -MIN_GAP_SECONDS
        for i, (start, end, text, utterance) in enumerate(pieces):
            wanted = max(MIN_CUE_SECONDS, len(text.replace("\n", " ")) / READ_CHARS_PER_SECOND)
            if start < previous_end + MIN_GAP_SECONDS:
                start = previous_end + MIN_GAP_SECONDS
            next_start = pieces[i + 1][0] if i + 1 < len(pieces) else None
            if end - start < wanted:
                end = start + wanted
                if next_start is not None and end > next_start - MIN_GAP_SECONDS:
                    end = max(start + MIN_CUE_SECONDS, next_start - MIN_GAP_SECONDS)
            if end - start > MAX_CUE_SECONDS:
                end = start + MAX_CUE_SECONDS
            if end <= start:
                end = start + MIN_CUE_SECONDS
            previous_end = end
            cues.append(Cue(index=len(cues) + 1, start=start, end=end, text=text,
                            utterance=utterance))
        return cues

    def export(self, lang: str, fmt: str, offset: float = 0.0, **options) -> str:
        """Render one language in one of FORMATS. Empty string means nothing
        was recorded for that language, in any format."""
        try:
            _, method = FORMATS[fmt]
        except KeyError:
            raise ValueError(f"unknown export format {fmt!r}") from None
        if not self.cues(lang, offset=offset):
            return ""
        return getattr(self, method)(lang, offset=offset, **options)

    def to_srt(self, lang: str, offset: float = 0.0) -> str:
        blocks = []
        for cue in self.cues(lang, offset=offset):
            blocks.append(
                f"{cue.index}\n"
                f"{format_timestamp(cue.start)} --> {format_timestamp(cue.end)}\n"
                f"{cue.text}\n"
            )
        return "\n".join(blocks)

    def to_vtt(self, lang: str, offset: float = 0.0) -> str:
        """WebVTT: same cues, a header line, full stops in the timestamps, and
        the cue number as an identifier line so a player that shows them
        lines up with the .srt."""
        blocks = ["WEBVTT\n"]
        for cue in self.cues(lang, offset=offset):
            blocks.append(
                f"{cue.index}\n"
                f"{format_timestamp(cue.start, '.')} --> "
                f"{format_timestamp(cue.end, '.')}\n"
                f"{cue.text}\n"
            )
        return "\n".join(blocks)

    def to_txt(self, lang: str, offset: float = 0.0, timestamps: bool = True) -> str:
        """A readable transcript, one recorded line per line - not split for
        display like the caption files, since nobody reads a document two
        lines at a time. With timestamps each line is prefixed by its start
        as [HH:MM:SS]; without, it is the bare text."""
        out = []
        for stamp, text in self._recorded_lines(lang, offset):
            if timestamps:
                out.append(f"[{format_timestamp(stamp)[:8]}] {text}")
            else:
                out.append(text)
        return "\n".join(out) + ("\n" if out else "")

    def _recorded_lines(self, lang: str, offset: float) -> list[tuple[float, str]]:
        """(start seconds, text) per recorded line, same zero point and cutoff
        rules as cues() but without the display split."""
        raw = self.lines.get(lang) or []
        if not raw:
            return []
        zero = self.zero_time if self.zero_time is not None else raw[0]["started"]
        out = []
        for entry in raw:
            if entry["committed"] - zero - offset < 0:
                continue
            out.append((max(0.0, entry["started"] - zero - offset), entry["text"]))
        return out

    def to_json(self, lang: str, offset: float = 0.0) -> str:
        cues = self.cues(lang, offset=offset)
        raw = self.lines.get(lang) or []
        zero = self.zero_time if self.zero_time is not None else (raw[0]["started"] if raw else None)
        payload = {
            "language": lang,
            "zero_time": zero,                 # unix seconds the cues count from
            "zero_marked": self.zero_time is not None,
            "offset": offset,
            "cue_count": len(cues),
            "cues": [
                {
                    "index": cue.index,
                    "start": round(cue.start, 3),
                    "end": round(cue.end, 3),
                    "text": cue.text,
                    "utterance": cue.utterance,
                }
                for cue in cues
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    def to_csv(self, lang: str, offset: float = 0.0) -> str:
        """One row per cue. Timestamps twice: formatted for a person reading
        the sheet, and as plain seconds for anything doing arithmetic on it.
        A cue's display line break becomes a space."""
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["index", "start", "end", "start_seconds", "end_seconds",
                         "language", "utterance", "text"])
        for cue in self.cues(lang, offset=offset):
            writer.writerow([
                cue.index,
                format_timestamp(cue.start, "."),
                format_timestamp(cue.end, "."),
                f"{cue.start:.3f}",
                f"{cue.end:.3f}",
                lang,
                cue.utterance,
                cue.text.replace("\n", " "),
            ])
        return buf.getvalue()

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
