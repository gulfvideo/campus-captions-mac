"""Capture client configuration: config/event.toml, .env, environment, flags.

Precedence, lowest to highest: defaults -> event.toml -> .env -> environment ->
command line. The Deepgram key only ever comes from .env or the environment.
"""

from __future__ import annotations

import os
import tomllib
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

# Not `from .backends import ...`: that package's registry imports every
# backend module, and websockets/numpy/PortAudio behind them. This one is
# deliberately dependency-free - see its docstring.
from .backends import support as backend_support
# relay/config.py is standard-library only, so the data-folder rules and the
# .env helpers are shared with it rather than mirrored here. The relay (same
# process in the packaged app) creates event.toml there on first run; this
# side only ever reads config and writes .env.
from relay.config import (  # noqa: F401 - re-exported
    APP_NAME, DATA_DIR, DEFAULT_CONFIG, ENV_FILE, EXAMPLE_CONFIG, FROZEN, ROOT,
    has_control_chars, load_env_file, save_env_var, setup_logging, write_private,
)


def resolve_path(value: str) -> str:
    """A relative path in event.toml (keyterms_file, mock_script) means "next
    to my config" first, then "shipped with the app". Absolute paths pass
    through. Matters for the packaged app, whose working directory is not
    the bundle."""
    p = Path(value)
    if p.is_absolute():
        return str(p)
    for base in (DATA_DIR, ROOT):
        candidate = base / p
        if candidate.exists():
            return str(candidate)
    return str(DATA_DIR / p)


@dataclass
class CaptureConfig:
    backend: str = "auto"
    device: str = ""
    relay_url: str = "ws://127.0.0.1:8000/ws/source"
    source_token: str = ""
    start_open: bool = False
    backend_config: dict = field(default_factory=dict)
    config_path: Path | None = None

    @property
    def needs_api_key(self) -> bool:
        return self.backend == "deepgram"


LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def relay_url_with_port(url: str, port: int, local_only: bool = False) -> str:
    """`url` with its port replaced. With local_only, a relay on another
    machine is left alone: its port is that machine's business, and the
    [relay] section in this file describes the local relay, not that one."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or ""
    except ValueError:
        return url
    if not host or (local_only and host.lower() not in LOCAL_HOSTS):
        return url
    netloc = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    if parts.username:
        netloc = f"{parts.username}@{netloc}"
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def load(path: Path | None = None) -> CaptureConfig:
    setup_logging("capture")
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
    # A relay on this machine listens on [relay] port from this same file,
    # so a local relay_url follows it: changing the port on the Settings
    # page (which writes [relay] port) then moves both halves, without a
    # second copy of the number in [capture] relay_url getting stale.
    relay_port = relay_section.get("port")
    if isinstance(relay_port, int) and not isinstance(relay_port, bool):
        cfg.relay_url = relay_url_with_port(cfg.relay_url, relay_port, local_only=True)

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
    # Where the Whisper model lives, unless event.toml names somewhere else.
    # An explicit directory under the app's own data folder rather than
    # faster-whisper's hidden ~/.cache default: the status page offers a
    # one-click download of this model, which means something has to be able
    # to check for it, and an operator has to be able to find it.
    if not backend_config.get("whisper_model_dir"):
        backend_config["whisper_model_dir"] = str(
            backend_support.whisper_model_dir(DATA_DIR))
    # Backend-agnostic, unlike keyterms: blocking happens on already-
    # transcribed text (see ASRBackend._filter_words), so every backend gets
    # it for free rather than needing its own support for it.
    keywords_section = data.get("keywords", {}) or {}
    backend_config["blocklist_file"] = str(
        keywords_section.get("blocklist_file", "config/blocklist.txt"))
    # "mask_profanity", not "profanity_filter": [deepgram] already has its own
    # key by that name (a native API-level filter, unrelated to this one),
    # and both land in this same flat dict.
    backend_config["mask_profanity"] = bool(keywords_section.get("profanity_filter", False))
    for key in ("keyterms_file", "blocklist_file", "mock_script"):
        if isinstance(backend_config.get(key), str):
            backend_config[key] = resolve_path(backend_config[key])
    if "keyterms_file" not in backend_config:
        backend_config["keyterms_file"] = resolve_path("config/keyterms.txt")
    cfg.backend_config = backend_config

    cfg.backend = os.environ.get("CAPTIONS_BACKEND", cfg.backend)
    cfg.device = os.environ.get("CAPTIONS_DEVICE", cfg.device)
    # Last, so it sees the final answer from every source above. "auto" (the
    # shipped default) becomes whichever backend this machine can actually
    # run, and a named backend this machine cannot run - apple_speech on a
    # Mac older than 26, say - is substituted rather than left to fail at the
    # first Open ASR stream.
    requested = cfg.backend
    cfg.backend = backend_support.resolve(requested)
    if cfg.backend != requested and requested != "auto":
        print(f"[capture] {requested} cannot run on this machine "
              f"({backend_support.unsupported_reason(requested)}); using {cfg.backend}")
    # CAPTIONS_PORT is the relay's own override; a local relay_url follows it
    # the same way it follows [relay] port. An explicit CAPTIONS_RELAY_URL
    # still wins outright.
    env_port = os.environ.get("CAPTIONS_PORT", "").strip()
    if env_port.isdigit():
        cfg.relay_url = relay_url_with_port(cfg.relay_url, int(env_port), local_only=True)
    cfg.relay_url = os.environ.get("CAPTIONS_RELAY_URL", cfg.relay_url)
    cfg.source_token = os.environ.get("CAPTIONS_SOURCE_TOKEN", cfg.source_token)
    return cfg
