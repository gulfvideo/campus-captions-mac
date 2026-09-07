"""Local on-device ASR. No network, no API key, no spend.

This is the file the interface in base.py was designed for: a third backend that
the relay and the viewers cannot tell apart from the other two. Nothing outside
this file and one line of registry.py changed to add it.

It runs Whisper through faster-whisper (CTranslate2). Those are *optional*
dependencies - the booth install stays lean, and only a machine that actually
wants offline ASR pays for them:

    .venv/bin/python -m pip install -r requirements-whisper.txt

## Whisper is not a streaming model

Whisper transcribes a whole clip. Streaming is simulated:

    * audio accumulates in a bounded buffer
    * every interim_interval seconds the buffer so far is transcribed and
      emitted as an INTERIM - it rewrites the line in place, which is exactly
      what the contract wants
    * when RMS drops below the speech floor for silence_commit seconds, the
      utterance is transcribed once more and emitted as a FINAL, and the buffer
      resets

So finals land at natural pauses, which is where a caption line wants to break
anyway. Expect roughly a second of latency behind Deepgram, and less stable
interims - the whole window is re-decoded each pass, so early words can change.
Good for a demo, for rehearsal, and for a venue with no internet. Test it before
trusting it on a live wall.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import numpy as np

from .base import ASRBackend
from .support import WHISPER_DEFAULT_MODEL

try:
    from faster_whisper import WhisperModel
    WHISPER_AVAILABLE = True
    WHISPER_IMPORT_ERROR = ""
except Exception as exc:                       # optional dependency
    WHISPER_AVAILABLE = False
    WHISPER_IMPORT_ERROR = f"{exc.__class__.__name__}: {exc}"

WHISPER_RATE = 16000            # Whisper only ever wants 16 kHz mono
SPEECH_FLOOR = 0.006            # smoothed RMS above this counts as speech

# --- hallucination on silence ---------------------------------------------
# Whisper does not know how to output nothing. Fed a stretch of room tone it
# invents fluent, confident speech - "Okay. Thank you. Bye bye. Good bye." and
# the YouTube-subtitle boilerplate its training data is full of. Reported from
# a real room: two paragraphs of it during a quiet passage.
#
# The RMS gate above stops obvious silence, but a room is never silent: an air
# handler, a projector fan and a congregation shifting in seats all clear a
# level floor while carrying no speech at all. Three checks in front of it,
# cheap and independent:
#
#   1. Silero VAD inside faster-whisper drops non-speech audio before the
#      decoder ever sees it. This is the big one, and it is what the level
#      gate cannot do - it knows what speech sounds like, not just how loud
#      the room is.
#   2. no_speech_prob: the model's own answer to "was that actually speech?".
#      A hallucinated segment usually knows, and says so.
#   3. avg_logprob: how confident the decoder was. Invented text scores well
#      below anything really heard.
#
# Deliberately not a list of banned phrases. "Thank you" and "Okay" are things
# people say from a lectern, and a filter that drops them drops real captions
# on the one occasion someone says them quietly.
VAD_MIN_SILENCE_MS = 700        # a pause shorter than this is speech, not a gap
VAD_SPEECH_PAD_MS = 200         # keep this much either side, so words are whole
NO_SPEECH_MAX = 0.6             # above this, the model doubts it was speech
MIN_AVG_LOGPROB = -1.0          # below this, the decoder was guessing
# Force a commit at this length so decoding cannot crawl. 12 s keeps the
# final's decode near a second on a laptop CPU; 22 s used to push the last
# line of a long sentence four to six seconds behind the speaker.
MAX_UTTERANCE_SECONDS = 12.0
MIN_UTTERANCE_SECONDS = 0.35    # buffer too short to hold a word
# Interim passes decode only this much of the tail. The live row shows the
# words just spoken anyway, and re-decoding the whole utterance every pass is
# what made Whisper's delay grow with every second of speech.
INTERIM_WINDOW_SECONDS = 8.0
# A commit that decodes to nothing after this much speech is marked
# [inaudible] rather than dropped: the audience saw the speaker's lips move.
INAUDIBLE_AFTER_SECONDS = 1.5
# initial_prompt is truncated from the front by the library, so a long list
# loses the names at the top of the file - exactly the ones that matter.
MAX_PROMPT_TERMS = 40

# Commit only after this much *speech*, not just this much buffer. Without it a
# quiet passage mid-sentence looks like the end of an utterance, the fragment
# decodes to nothing, and the audio is discarded without ever being emitted.
MIN_SPEECH_SECONDS = 0.45

# One quiet 100 ms block is not the end of a sentence, so silence has to persist
# for a couple of blocks before the commit clock starts. This is deliberately a
# fixed count rather than a smoothed level: an exponential average decays for
# most of a second after loud speech, and that lag lands directly on top of
# silence_commit - long pauses get missed and every caption line arrives late.
QUIET_BLOCKS_FOR_SILENCE = 2
JUNK_RESET_AFTER = 2.5          # silence with no real speech: drop the buffer

MISSING_DEPS = (
    "whisper backend needs faster-whisper: run "
    "'python -m pip install -r requirements-whisper.txt'"
)


def fetch_model(size: str, model_dir, on_progress=None) -> str:
    """Download a Whisper model into `model_dir` and return its path.

    Called from the operator's "Download" button (see capture/__main__.py's
    download_whisper_model), never implicitly: the download is a few hundred
    megabytes over whatever the venue's wifi is, and a backend that started
    one on its own the first time someone opened the stream is exactly the
    surprise this exists to remove.

    Runs on a worker thread - it is entirely blocking - so it takes no event
    loop and reports through `on_progress(message)` instead of logging into a
    console nobody is watching.
    """
    if not WHISPER_AVAILABLE:
        raise RuntimeError(f"{MISSING_DEPS} ({WHISPER_IMPORT_ERROR})")
    from faster_whisper import download_model

    Path(model_dir).mkdir(parents=True, exist_ok=True)
    if on_progress:
        on_progress(f"downloading the {size} model")
    # huggingface_hub resumes a partial download and no-ops a complete one,
    # so pressing the button twice is safe and a dropped wifi connection
    # costs only the bytes not yet fetched.
    path = download_model(size, output_dir=str(model_dir))
    if on_progress:
        on_progress("")
    return str(path)


# Whisper's temperature *ladder*, not a single value. faster-whisper decodes at
# the first entry, and only if that result trips compression_ratio_threshold
# ("too repetitive") or log_prob_threshold does it retry at the next one. Pass a
# scalar and `temperatures` becomes a one-item list: the library still detects
# the runaway repetition, still logs it, and then has nothing to fall back to
# and returns it anyway. That is how "you're not sure if you're not sure if..."
# reached the wall. Normal decoding is unaffected - the first pass is still 0.0.
TEMPERATURE_LADDER = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)

# Belt and braces for whatever still slips past the ladder. A caption wall is
# the wrong place to find out that a decode looped.
REPEAT_RUN_LIMIT = 4            # this many identical repeats in a row is not speech
REPEAT_KEEP = 2                 # ...trim it back to this many
REPEAT_MAX_PHRASE = 16          # longest repeating unit to look for, in words


def collapse_repeats(text: str, run_limit: int = REPEAT_RUN_LIMIT,
                     keep: int = REPEAT_KEEP,
                     max_phrase: int = REPEAT_MAX_PHRASE) -> str:
    """Trim runaway repetition, leaving ordinary speech alone.

    Whisper loops on a phrase, not always a word - the two seen in the wild
    were "you're not sure if" and a ten-word sentence - so this looks for a
    repeating unit of any length up to max_phrase and takes the longest match
    first, or "a b a b a b" would collapse on "a" and leave the b's stranded.

    Only a run of `run_limit` or more is touched, and it is trimmed rather
    than dropped: people really do stammer, and "I mean, I mean" or "No no"
    is something a deaf reader is entitled to see.
    """
    words = text.split()
    if len(words) < 4:
        return text
    out: list[str] = []
    i = 0
    while i < len(words):
        collapsed = False
        for n in range(min(max_phrase, (len(words) - i) // 2), 0, -1):
            phrase = words[i:i + n]
            reps, j = 1, i + n
            while j + n <= len(words) and words[j:j + n] == phrase:
                reps += 1
                j += n
            if reps >= run_limit:
                out.extend(phrase * keep)
                i = j
                collapsed = True
                break
        if not collapsed:
            out.append(words[i])
            i += 1
    return " ".join(out)


def resample_to_16k(samples: np.ndarray, src_rate: int) -> np.ndarray:
    """Linear interpolation to 16 kHz.

    The other backends are told the device's real rate and resample nothing.
    Whisper has no such option, so this is the one place in the project that
    changes the sample rate. Linear interpolation is coarse but speech-adequate,
    and far cheaper than pulling in a DSP dependency.
    """
    if src_rate == WHISPER_RATE or samples.size == 0:
        return samples
    duration = samples.size / float(src_rate)
    target_n = int(duration * WHISPER_RATE)
    if target_n <= 0:
        return np.zeros(0, dtype=np.float32)
    src_x = np.linspace(0.0, duration, samples.size, endpoint=False)
    dst_x = np.linspace(0.0, duration, target_n, endpoint=False)
    return np.interp(dst_x, src_x, samples).astype(np.float32)


class LocalWhisperBackend(ASRBackend):
    name = "whisper"

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        cfg = self.config
        self.model_size = str(cfg.get("whisper_model", WHISPER_DEFAULT_MODEL))
        self.compute_type = str(cfg.get("whisper_compute_type", "int8"))
        # "cpu", not "auto". CTranslate2's auto-detect claims a GPU it cannot
        # actually use and then dies loading CUDA libraries that are not there.
        # CPU is also the only sensible default on the booth Mac.
        self.device = str(cfg.get("whisper_device", "cpu"))
        self.model_dir = cfg.get("whisper_model_dir") or None
        self.language = str(cfg.get("whisper_language", "") or "") or None
        self.interim_interval = float(cfg.get("whisper_interim_interval", 0.9))
        self.silence_commit = float(cfg.get("whisper_silence_commit", 0.7))
        self.keyterms_file = Path(cfg.get("keyterms_file", "config/keyterms.txt"))

        self.sample_rate = WHISPER_RATE
        self._model = None
        self._loading = False
        self._buffer = np.zeros(0, dtype=np.float32)
        self._speech_in_buffer = False
        self._speech_seconds = 0.0
        self._quiet_run = 0
        self._silence_started: float | None = None
        self._last_interim_at = 0.0
        self._busy = False
        self._task: asyncio.Task | None = None
        self._prompt: str | None = None
        self._last_emitted = ""

    # -- lifecycle -------------------------------------------------------

    async def open(self, sample_rate: int) -> None:
        if self._open:
            return
        if not WHISPER_AVAILABLE:
            self._error = f"{MISSING_DEPS} ({WHISPER_IMPORT_ERROR})"
            return
        self.sample_rate = int(sample_rate)
        self._prompt = self._load_prompt()
        self._reset_buffer()
        self._open = True
        self._error = None
        self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        self._open = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._reset_buffer()
        # The model stays loaded on purpose: reopening is then instant, and
        # unlike Deepgram an idle local model costs nothing.

    async def send_audio(self, frames: bytes) -> None:
        if not self._open or not frames:
            return
        block = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        block = resample_to_16k(block, self.sample_rate)
        if block.size == 0:
            return

        rms = float(np.sqrt(np.mean(block * block) + 1e-12))
        block_seconds = block.size / WHISPER_RATE
        now = time.monotonic()
        if rms >= SPEECH_FLOOR:
            self._speech_in_buffer = True
            self._speech_seconds += block_seconds
            self._quiet_run = 0
            self._silence_started = None
        else:
            self._quiet_run += 1
            if (self._speech_in_buffer and self._silence_started is None
                    and self._quiet_run >= QUIET_BLOCKS_FOR_SILENCE):
                # Backdate to when the quiet actually began, so silence_commit
                # means the pause length a person would measure.
                self._silence_started = now - self._quiet_run * block_seconds

        self._buffer = np.concatenate((self._buffer, block))
        limit = int(MAX_UTTERANCE_SECONDS * WHISPER_RATE)
        if self._buffer.size > limit:
            # Keep the tail. A speaker who never pauses must not make each pass
            # slower than the last.
            self._buffer = self._buffer[-limit:]

    async def reload_keyterms(self) -> None:
        """Whisper has no keyterm API, but initial_prompt biases spelling.

        Same file, same button on the status page, same purpose as Deepgram's
        keyterm prompting - names come out spelled the way the institution
        spells them.
        """
        self._prompt = self._load_prompt()

    # -- internals -------------------------------------------------------

    def _load_prompt(self) -> str | None:
        try:
            if not self.keyterms_file.exists():
                return None
            terms = [
                line.strip()
                for line in self.keyterms_file.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
            return ", ".join(terms[:MAX_PROMPT_TERMS]) if terms else None
        except OSError:
            return None

    def _reset_buffer(self) -> None:
        self._buffer = np.zeros(0, dtype=np.float32)
        self._speech_in_buffer = False
        self._speech_seconds = 0.0
        self._quiet_run = 0
        self._silence_started = None
        self._last_emitted = ""

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        kwargs = {"compute_type": self.compute_type}
        if self.model_dir:
            kwargs["download_root"] = str(self.model_dir)
        try:
            self._model = WhisperModel(self.model_size, device=self.device, **kwargs)
        except Exception as exc:
            if self.device == "cpu":
                raise
            # A configured GPU that does not load must not take the event down;
            # captions on the CPU beat no captions.
            print(f"[whisper] device {self.device!r} unavailable ({exc.__class__.__name__}), "
                  f"falling back to cpu")
            self.device = "cpu"
            self._model = WhisperModel(self.model_size, device="cpu", **kwargs)
        return self._model

    def _transcribe(self, audio: np.ndarray) -> str:
        model = self._ensure_model()
        segments, _info = model.transcribe(
            audio,
            language=self.language,
            beam_size=1,               # greedy: this runs every ~second
            # The ladder, not a bare 0.0 - see TEMPERATURE_LADDER above. Still
            # decodes at 0.0 first, so an ordinary pass is unchanged; the rest
            # only run when this one comes back too repetitive to believe.
            temperature=TEMPERATURE_LADDER,
            condition_on_previous_text=False,   # stops runaway repetition when
                                                # the same window is re-decoded
            initial_prompt=self._prompt,
            # Was False, on the grounds that the RMS gate already did this. It
            # does not: a level floor cannot tell a fan from a voice, and the
            # decoder handed room tone invents speech rather than returning
            # nothing. See the constants above.
            vad_filter=True,
            vad_parameters={
                "min_silence_duration_ms": VAD_MIN_SILENCE_MS,
                "speech_pad_ms": VAD_SPEECH_PAD_MS,
            },
            no_speech_threshold=NO_SPEECH_MAX,
        )
        kept = []
        for seg in segments:
            text = seg.text.strip()
            if not text:
                continue
            # faster-whisper applies no_speech_threshold per window, not per
            # segment, so the check is repeated here where the segment's own
            # scores are available. getattr because a future version that
            # stops reporting one of these should not take captions down.
            no_speech = getattr(seg, "no_speech_prob", 0.0) or 0.0
            logprob = getattr(seg, "avg_logprob", 0.0) or 0.0
            if no_speech > NO_SPEECH_MAX or logprob < MIN_AVG_LOGPROB:
                print(f"[whisper] dropped a segment that reads as invented "
                      f"(no_speech={no_speech:.2f} logprob={logprob:.2f}): "
                      f"{text[:60]!r}")
                continue
            kept.append(text)
        return collapse_repeats(" ".join(kept).strip())

    async def _decode(self, audio: np.ndarray) -> str:
        """Decode off the event loop - this is CPU-bound and blocking."""
        loop = asyncio.get_running_loop()
        self._busy = True
        try:
            return await loop.run_in_executor(None, self._transcribe, audio)
        finally:
            self._busy = False

    async def _run(self) -> None:
        try:
            loop = asyncio.get_running_loop()
            if self._model is None:
                print(f"[whisper] loading whisper model {self.model_size!r} "
                      f"({self.compute_type}) - first run downloads it")
                await loop.run_in_executor(None, self._ensure_model)
                print("[whisper] model ready")

            while self._open:
                await asyncio.sleep(0.1)
                now = time.monotonic()
                seconds = self._buffer.size / WHISPER_RATE
                if not self._speech_in_buffer or seconds < MIN_UTTERANCE_SECONDS:
                    continue
                if self._busy:
                    continue

                silent_for = (
                    0.0 if self._silence_started is None
                    else now - self._silence_started
                )

                # Nothing worth decoding ever arrived - drop it rather than
                # decode silence over and over.
                if (self._speech_seconds < MIN_SPEECH_SECONDS
                        and silent_for >= JUNK_RESET_AFTER):
                    self._reset_buffer()
                    continue

                enough_speech = self._speech_seconds >= MIN_SPEECH_SECONDS
                commit = enough_speech and (
                    silent_for >= self.silence_commit
                    or seconds >= MAX_UTTERANCE_SECONDS - 0.5
                )

                if commit:
                    speech_seconds = self._speech_seconds
                    text = await self._decode(self._buffer.copy())
                    if text:
                        self._emit(text, is_final=True)
                    elif speech_seconds >= INAUDIBLE_AFTER_SECONDS:
                        self._emit("[inaudible]", is_final=True)
                    self._reset_buffer()
                    self._last_interim_at = time.monotonic()
                elif (now - self._last_interim_at) >= self.interim_interval:
                    self._last_interim_at = now
                    window = int(INTERIM_WINDOW_SECONDS * WHISPER_RATE)
                    text = await self._decode(self._buffer[-window:].copy())
                    # Re-decoding the same window often returns the same string;
                    # sending it again would be pure noise on the wire.
                    if text and text != self._last_emitted:
                        self._last_emitted = text
                        self._emit(text, is_final=False)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                  # never kill the capture app
            self._error = f"whisper backend: {exc.__class__.__name__}: {exc}"
            print(f"[whisper] {self._error}")
