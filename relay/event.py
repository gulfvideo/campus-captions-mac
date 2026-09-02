"""Where the event is, for everyone who is not the operator.

Before this existed, an attendee who scanned the QR code early got a black
screen with nothing on it, and a deaf attendee whose captions stopped could not
tell a pause from an outage - the wall faded out identically for both. This
module is the single answer to "what should the audience be seeing right now":

    starting  the welcome screen - event name, a message, an optional photo -
              until the operator presses Start event
    live      captions
    paused    captions stay up, with a banner ("Intermission", or the default
              "Captions paused") - set by the operator
    ended     "This event has ended" over the same welcome screen

The state rides in the `hello` message every viewer gets on connect, in every
heartbeat, and as an `event` message the moment it changes, so a phone that
connected twenty minutes early switches to captions the instant the operator
presses Start. It is also persisted in event.toml so a relay restart mid-event
does not put the welcome screen back up.

Two images, both optional and both decorative: the welcome screen's background
photo, and a small logo shown in a header row above the captions while they are
live. Viewers draw a dark scrim and a panel behind the welcome text so the words
stay readable over any photo, and the logo's height is capped so it costs one
caption line at most. Only JPEG, PNG and WebP are accepted - never SVG, which
can carry scripts - and never more than MAX_IMAGE_BYTES.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

STATES = ("starting", "live", "paused", "ended")
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MEDIA_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
TEXT_FIELDS = ("state", "title", "message", "notice", "pause_message", "logo_alt")


def sniff_image(data: bytes) -> str | None:
    """The file's real type from its first bytes - the extension or the
    Content-Type header are whatever the uploader said, not what it is."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


@dataclass
class ImageSlot:
    """One named image on disk: config/<name>.<ext>, whichever ext exists."""

    directory: Path | None
    name: str
    path: Path | None = None

    def find(self) -> None:
        self.path = None
        if self.directory is None:
            return
        for ext in MEDIA_TYPES:
            candidate = self.directory / f"{self.name}.{ext}"
            if candidate.is_file():
                self.path = candidate
                return

    @property
    def media_type(self) -> str:
        if self.path is None:
            return ""
        return MEDIA_TYPES.get(self.path.suffix.lstrip(".").lower(), "application/octet-stream")

    def version(self) -> int:
        """Changes whenever the file does, so viewers can cache the image by
        URL and still pick up a replacement."""
        if self.path is None:
            return 0
        try:
            return int(self.path.stat().st_mtime)
        except OSError:
            return 0

    def set(self, data: bytes) -> None:
        if self.directory is None:
            raise ValueError("no place to store an image")
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError("image must be under 5 MB")
        ext = sniff_image(data)
        if ext is None:
            raise ValueError("image must be a JPEG, PNG, or WebP file")
        self.clear()
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{self.name}.{ext}"
        path.write_bytes(data)
        self.path = path

    def clear(self) -> None:
        if self.directory is not None:
            for ext in MEDIA_TYPES:
                try:
                    (self.directory / f"{self.name}.{ext}").unlink()
                except FileNotFoundError:
                    pass
        self.path = None


@dataclass
class EventState:
    state: str = "starting"
    title: str = ""
    message: str = ""
    notice: str = ""
    pause_message: str = ""
    # What a screen reader says for the logo - "Faulkner University", not
    # "logo.png". Set by the operator alongside the image.
    logo_alt: str = ""
    image_dir: Path | None = None
    changed_at: float = field(default_factory=time.time)
    welcome: ImageSlot = field(default=None)   # type: ignore[assignment]
    logo: ImageSlot = field(default=None)      # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.welcome = ImageSlot(self.image_dir, "welcome")
        self.logo = ImageSlot(self.image_dir, "logo")
        self.find_image()

    @classmethod
    def from_config(cls, cfg, image_dir: Path) -> "EventState":
        return cls(
            state=cfg.event_state if cfg.event_state in STATES else "starting",
            title=cfg.event_title,
            message=cfg.event_message,
            notice=cfg.event_notice,
            pause_message=cfg.event_pause_message,
            logo_alt=getattr(cfg, "event_logo_alt", ""),
            image_dir=image_dir,
        )

    # -- images ------------------------------------------------------------

    def find_image(self) -> None:
        self.welcome.find()
        self.logo.find()

    # The welcome photo keeps its original names; callers and tests use them.
    @property
    def image_path(self) -> Path | None:
        return self.welcome.path

    @property
    def image_media_type(self) -> str:
        return self.welcome.media_type

    def image_version(self) -> int:
        return self.welcome.version()

    def set_image(self, data: bytes) -> None:
        self.welcome.set(data)
        self.changed_at = time.time()

    def clear_image(self) -> None:
        self.welcome.clear()
        self.changed_at = time.time()

    def set_logo(self, data: bytes) -> None:
        self.logo.set(data)
        self.changed_at = time.time()

    def clear_logo(self) -> None:
        self.logo.clear()
        self.changed_at = time.time()

    # -- state -------------------------------------------------------------

    def update(self, **fields: str) -> None:
        for key, value in fields.items():
            if key == "state" and value not in STATES:
                raise ValueError(f"unknown event state {value!r}")
            if key not in TEXT_FIELDS:
                raise ValueError(f"unknown event field {key!r}")
            setattr(self, key, value)
        self.changed_at = time.time()

    def public(self) -> dict:
        """What viewers get. No file paths, nothing operator-only."""
        return {
            "state": self.state,
            "title": self.title,
            "message": self.message,
            "notice": self.notice,
            "pause_message": self.pause_message,
            "image": self.welcome.path is not None,
            "image_v": self.welcome.version(),
            "logo": self.logo.path is not None,
            "logo_v": self.logo.version(),
            "logo_alt": self.logo_alt,
            "changed_at": round(self.changed_at, 3),
        }
