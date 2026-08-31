"""Backend name -> class.

Adding a local on-device backend later is one import and one dict entry here.
Nothing in the relay or the viewers changes.
"""

from __future__ import annotations

from .base import ASRBackend
from .deepgram import DeepgramBackend
from .local import LocalWhisperBackend
from .mock import MockBackend

BACKENDS: dict[str, type[ASRBackend]] = {
    "mock": MockBackend,
    "deepgram": DeepgramBackend,
    "local": LocalWhisperBackend,
}


def create(name: str, config: dict | None = None) -> ASRBackend:
    try:
        cls = BACKENDS[name]
    except KeyError:
        raise SystemExit(
            f"unknown backend {name!r}. available: {', '.join(sorted(BACKENDS))}"
        ) from None
    return cls(config)
