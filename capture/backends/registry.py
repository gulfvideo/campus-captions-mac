"""Backend name -> class.

Adding a backend is one import and one dict entry here. Nothing in the relay
or the viewers changes.
"""

from __future__ import annotations

from .apple_speech import AppleSpeechBackend
from .base import ASRBackend
from .deepgram import DeepgramBackend
from .mock import MockBackend
from .whisper import LocalWhisperBackend

BACKENDS: dict[str, type[ASRBackend]] = {
    "mock": MockBackend,
    "deepgram": DeepgramBackend,
    "apple_speech": AppleSpeechBackend,
    "whisper": LocalWhisperBackend,
}


def create(name: str, config: dict | None = None) -> ASRBackend:
    try:
        cls = BACKENDS[name]
    except KeyError:
        raise SystemExit(
            f"unknown backend {name!r}. available: {', '.join(sorted(BACKENDS))}"
        ) from None
    return cls(config)
