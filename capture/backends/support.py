"""Which ASR backends exist, what they are, and which ones this machine can run.

Deliberately free of every heavy import. registry.py cannot answer these
questions without importing all three backend modules, and through them numpy,
websockets and PortAudio - but two callers need the answers and must not pay
that price:

  * relay/server.py, for the setup wizard's backend step. The relay imports no
    audio libraries at all (see .github/workflows/ci.yml for why), so it could
    not import registry.
  * capture/config.py, which resolves `backend = "auto"` before any backend
    class is constructed.

So the platform rules and the operator-facing descriptions live here, and
registry.py keeps the name -> class map. ASRBackend.unsupported_reason() stays
the authority for a backend refusing to open; this is the same answer asked
ahead of time.
"""

from __future__ import annotations

import importlib.util
import platform
import sys
from dataclasses import dataclass
from pathlib import Path

# The macOS version that first shipped SpeechAnalyzer/SpeechTranscriber, the
# API capture/backends/apple_speech.py's helper is built on.
APPLE_SPEECH_MIN_MACOS = 26


def apple_speech_unsupported() -> str | None:
    """None if this machine can run apple_speech, else why it can't.

    apple_speech.py imports this rather than the other way round, so there is
    one copy of the rule and no import of that module (or of the Swift helper
    paths it computes) just to ask the question.
    """
    if sys.platform != "darwin":
        return "apple_speech only runs on macOS"
    try:
        major = int(platform.mac_ver()[0].split(".")[0])
    except (ValueError, IndexError):
        return None  # unknown version string; let the helper itself decide
    if major < APPLE_SPEECH_MIN_MACOS:
        return (f"apple_speech needs macOS {APPLE_SPEECH_MIN_MACOS} or newer "
                f"(this Mac has {platform.mac_ver()[0]})")
    return None


# Where the Whisper speech model is kept, under whichever per-machine data
# directory the caller has (relay/config.py and capture/config.py compute the
# same one). An explicit location rather than faster-whisper's default
# ~/.cache/huggingface: the app offers a one-click download of this model, so
# it needs somewhere it can check for, write to, and describe to an operator -
# and "in the folder the tray menu opens" beats "in a hidden cache".
WHISPER_MODEL_SUBDIR = ("models", "whisper")
# The default model. Small enough to download over a venue's wifi, good
# enough to caption a lecture. Mirrors [whisper] model in the example config.
WHISPER_DEFAULT_MODEL = "base.en"


def whisper_model_dir(data_dir) -> Path:
    return Path(data_dir).joinpath(*WHISPER_MODEL_SUBDIR)


def whisper_model_present(model_dir) -> bool:
    """Has a Whisper model actually been downloaded into `model_dir`?

    Takes the model directory itself, not the data directory, so an
    event.toml that points `whisper_model_dir` somewhere else is checked
    where it actually is.

    A directory check, not an import: the relay calls this too, and it must
    not pull faster-whisper (and numpy behind it) into its process just to
    decide whether to offer a download button. CTranslate2 models always
    carry a model.bin, so its presence anywhere beneath the directory is the
    signal; a half-finished download leaves the .incomplete files
    huggingface_hub writes and no model.bin, which correctly reads as absent.
    """
    try:
        return any(Path(model_dir).rglob("model.bin"))
    except OSError:
        return False


def whisper_installed() -> bool:
    """Is faster-whisper importable? Answered without importing it - the
    relay asks this too, and importing it here would drag numpy and
    ctranslate2 into a process that has no use for either."""
    return importlib.util.find_spec("faster_whisper") is not None


@dataclass(frozen=True)
class BackendInfo:
    """One row of the setup wizard's backend step, and one option in the
    status page's picker. The copy lives here rather than in the HTML so the
    two pages cannot drift apart, and so a backend added to registry.py is
    described in exactly one place."""

    name: str
    label: str
    summary: str


CATALOGUE: tuple[BackendInfo, ...] = (
    BackendInfo(
        "apple_speech", "Apple on-device speech",
        "Free, private, and needs no account - the audio never leaves this Mac. "
        "Requires macOS 26 or newer."),
    BackendInfo(
        "whisper", "Whisper (on this computer)",
        "Free, private, and once its model is downloaded it works with no "
        "internet at all. Slower than Apple speech, and it can be told the "
        "names and terms your event uses."),
    BackendInfo(
        "mock", "Practice mode (fake captions)",
        "Plays a scripted transcript instead of listening. Nothing is "
        "recorded - use it to rehearse the wall and phone pages before the "
        "event."),
)

BY_NAME: dict[str, BackendInfo] = {info.name: info for info in CATALOGUE}

# Only backends whose constraint is the *machine* belong here. A missing
# optional pip install is not one: Whisper can be installed without changing
# computers, so it stays offered and reports its own missing dependency when
# opened.
_PLATFORM_RULES = {"apple_speech": apple_speech_unsupported}


def unsupported_reason(name: str) -> str | None:
    """Why `name` can never run on this machine, or None if it can."""
    rule = _PLATFORM_RULES.get(name)
    if rule is None:
        return None if name in BY_NAME else f"unknown backend {name!r}"
    return rule()


def supported() -> list[str]:
    """Every backend this machine could run, in CATALOGUE order."""
    return [info.name for info in CATALOGUE if unsupported_reason(info.name) is None]


def resolve_auto() -> str:
    """What `backend = "auto"` means here.

    The first backend this machine can run: on a current Mac that is
    apple_speech, and everywhere else it falls through to practice mode.
    Never whisper, whose wheels are an opt-in download. The setup wizard is
    where a real choice gets made; this is only what a machine comes up on
    before anyone has made one.
    """
    for info in CATALOGUE:
        if info.name != "whisper" and unsupported_reason(info.name) is None:
            return info.name
    return "mock"


def resolve(name: str) -> str:
    """`name`, or something runnable if this machine cannot run `name`.

    Substituting rather than failing is deliberate: the alternative is a
    volunteer at a booth twenty minutes before doors with no captions and a
    config file to edit. The capture client prints what it did, and the
    status page shows which backend is actually running.
    """
    if not name or name == "auto":
        return resolve_auto()
    if name in BY_NAME and unsupported_reason(name) is not None:
        return resolve_auto()
    return name


def describe(name: str) -> dict:
    """One backend as JSON for the setup wizard."""
    info = BY_NAME.get(name)
    reason = unsupported_reason(name)
    return {
        "name": name,
        "label": info.label if info else name,
        "summary": info.summary if info else "",
        "supported": reason is None,
        "unsupported_reason": reason or "",
    }
