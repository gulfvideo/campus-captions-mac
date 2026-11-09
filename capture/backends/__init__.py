"""ASR backends. See base.py for the interface every one of them implements."""

from .base import ASRBackend, TranscriptEvent
from .support import CATALOGUE, BackendInfo, resolve, resolve_auto, supported

__all__ = ["ASRBackend", "TranscriptEvent", "BACKENDS", "create",
           "BackendInfo", "CATALOGUE", "resolve", "resolve_auto", "supported"]

# BACKENDS and create() are resolved on first use, not at import.
#
# registry.py imports all four backend modules, and through them websockets,
# numpy and PortAudio. `support` answers "which backends exist and can this
# machine run them" without any of that, and two callers need only that much:
# the relay (which imports no audio libraries at all) and capture/config.py,
# which resolves `backend = "auto"` before a backend is ever constructed.
# Importing this package for `supported()` used to drag the whole audio stack
# in behind it. PEP 562 keeps that cost on the code that actually builds a
# backend, with no change to how anything imports these two names.
_LAZY = {"BACKENDS", "create"}


def __getattr__(name: str):
    if name in _LAZY:
        from . import registry
        return getattr(registry, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
