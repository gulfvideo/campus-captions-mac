"""Relay configuration: config/event.toml, overridable by environment.

Stage 3 adds [capture] and [deepgram] sections to the same file; the relay reads
only [relay] and ignores the rest.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "event.toml"
EXAMPLE_CONFIG = ROOT / "config" / "event.example.toml"
VIEWER_DIR = ROOT / "viewer"


@dataclass
class RelayConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    source_token: str = ""
    config_path: Path | None = None

    @property
    def token_required(self) -> bool:
        return bool(self.source_token)


def load(path: Path | None = None) -> RelayConfig:
    """Read config, falling back to the example file, then to defaults.

    Environment always wins, so the launcher and the booth can override without
    editing a file:
        CAPTIONS_HOST, CAPTIONS_PORT, CAPTIONS_SOURCE_TOKEN
    """
    cfg = RelayConfig()
    chosen = path or (DEFAULT_CONFIG if DEFAULT_CONFIG.exists() else EXAMPLE_CONFIG)
    if chosen and chosen.exists():
        try:
            with chosen.open("rb") as fh:
                data = tomllib.load(fh)
            section = data.get("relay", {})
            cfg.host = str(section.get("host", cfg.host))
            cfg.port = int(section.get("port", cfg.port))
            cfg.source_token = str(section.get("source_token", cfg.source_token))
            cfg.config_path = chosen
        except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
            # A broken config must not stop an event from starting.
            print(f"[relay] ignoring unreadable config {chosen}: {exc}")

    cfg.host = os.environ.get("CAPTIONS_HOST", cfg.host)
    cfg.port = int(os.environ.get("CAPTIONS_PORT", cfg.port))
    cfg.source_token = os.environ.get("CAPTIONS_SOURCE_TOKEN", cfg.source_token)
    return cfg
