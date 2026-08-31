"""ASR backends. See base.py for the interface every one of them implements."""

from .base import ASRBackend, TranscriptEvent
from .registry import BACKENDS, create

__all__ = ["ASRBackend", "TranscriptEvent", "BACKENDS", "create"]
