"""Capture client configuration: config/event.toml, .env, environment, flags.

Precedence, lowest to highest: defaults -> event.toml -> .env -> environment ->
command line. The Deepgram key only ever comes from .env or the environment.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# Repo root when run from source; the PyInstaller bundle's resource directory
# (where the .spec's `datas` land) when frozen into the macOS app.
ROOT = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "event.toml"
EXAMPLE_CONFIG = ROOT / "config" / "event.example.toml"
ENV_FILE = ROOT / ".env"


def load_env_file(path: Path = ENV_FILE) -> int:
    """Read KEY=VALUE lines into the environment without overwriting it.

    Hand-rolled so the capture client has no dependency on a dotenv package,
    and so the booth can keep the Deepgram key in one obvious gitignored file.
    """
    if not path.exists():
        return 0
    loaded = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


def save_env_var(key: str, value: str, path: Path = ENV_FILE) -> None:
    """Write key=value into .env, in place, preserving every other line.

    Lets the status page's "add your Deepgram key" field work without anyone
    opening a text editor. Mirrors load_env_file's hand-rolled parsing rather
    than pulling in python-dotenv's writer for one line.
    """
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.split("=", 1)[0].strip() == key:
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@dataclass
class CaptureConfig:
    backend: str = "mock"
    device: str = ""
    relay_url: str = "ws://127.0.0.1:8000/ws/source"
    source_token: str = ""
    start_open: bool = False
    backend_config: dict = field(default_factory=dict)
    config_path: Path | None = None

    @property
    def needs_api_key(self) -> bool:
        return self.backend == "deepgram"


def load(path: Path | None = None) -> CaptureConfig:
    load_env_file()
    cfg = CaptureConfig()
    chosen = path or (DEFAULT_CONFIG if DEFAULT_CONFIG.exists() else EXAMPLE_CONFIG)

    data: dict = {}
    if chosen and chosen.exists():
        try:
            with chosen.open("rb") as fh:
                data = tomllib.load(fh)
            cfg.config_path = chosen
        except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
            print(f"[capture] ignoring unreadable config {chosen}: {exc}")

    section = data.get("capture", {}) or {}
    cfg.backend = str(section.get("backend", cfg.backend))
    cfg.device = str(section.get("device", cfg.device))
    cfg.relay_url = str(section.get("relay_url", cfg.relay_url))
    cfg.start_open = bool(section.get("start_open", cfg.start_open))

    relay_section = data.get("relay", {}) or {}
    cfg.source_token = str(relay_section.get("source_token", ""))

    # Everything a backend might want, flattened. Backends read what they know
    # and ignore the rest, so adding a backend needs no change here.
    backend_config: dict = {}
    backend_config.update({
        "mock_script": str(section.get("mock_script", "config/mock_script.txt")),
        "mock_interim_ms": section.get("mock_interim_ms", 180),
        "mock_gap_ms": section.get("mock_gap_ms", 900),
        "mock_jitter": section.get("mock_jitter", 0.35),
        "mock_loop": section.get("mock_loop", True),
    })
    backend_config.update(data.get("deepgram", {}) or {})
    backend_config.update(data.get("apple_speech", {}) or {})
    # [whisper] keys are prefixed so they cannot collide with [deepgram] - both
    # sections have a "model", and everything lands in one flat dict.
    backend_config.update({
        f"whisper_{key}": value
        for key, value in (data.get("whisper", {}) or {}).items()
    })
    cfg.backend_config = backend_config

    cfg.backend = os.environ.get("CAPTIONS_BACKEND", cfg.backend)
    cfg.device = os.environ.get("CAPTIONS_DEVICE", cfg.device)
    cfg.relay_url = os.environ.get("CAPTIONS_RELAY_URL", cfg.relay_url)
    cfg.source_token = os.environ.get("CAPTIONS_SOURCE_TOKEN", cfg.source_token)
    return cfg
