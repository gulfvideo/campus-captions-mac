"""Backend name -> class.

Adding a backend is one import and one dict entry here, plus one row in
support.py's CATALOGUE so the setup wizard and the status page can describe
it. Nothing in the relay or the viewers changes.
"""

from __future__ import annotations

from .apple_speech import AppleSpeechBackend
from .base import ASRBackend
from .mock import MockBackend
from .support import BY_NAME, supported, unsupported_reason  # noqa: F401 - re-exported
from .whisper import LocalWhisperBackend

BACKENDS: dict[str, type[ASRBackend]] = {
    "mock": MockBackend,
    "apple_speech": AppleSpeechBackend,
    "whisper": LocalWhisperBackend,
}

# support.py describes backends without importing them, so nothing there can
# notice a name added to one file and not the other. Here both halves are in
# scope, so check once at import: a backend missing from CATALOGUE would be
# unnamed and undescribed in the setup wizard, and one listed there but not
# here would be an option that cannot be created.
assert set(BACKENDS) == set(BY_NAME), (
    f"registry and support.CATALOGUE disagree: "
    f"{set(BACKENDS) ^ set(BY_NAME)}")


def create(name: str, config: dict | None = None) -> ASRBackend:
    try:
        cls = BACKENDS[name]
    except KeyError:
        raise SystemExit(
            f"unknown backend {name!r}. available: {', '.join(sorted(BACKENDS))}"
        ) from None
    return cls(config)
