"""Capture client configuration: config/event.toml, .env, environment, flags.

Precedence, lowest to highest: defaults -> event.toml -> .env -> environment ->
command line. The Deepgram key only ever comes from .env or the environment.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import tomllib
import urllib.parse
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Not `from .backends import ...`: that package's registry imports every
# backend module, and websockets/numpy/PortAudio behind them. This one is
# deliberately dependency-free - see its docstring.
from .backends import support as backend_support

# Repo root when run from source; the PyInstaller bundle's resource directory
# (where the .spec's `datas` land) when frozen into the macOS app.
FROZEN = bool(getattr(sys, "frozen", False))
ROOT = Path(sys._MEIPASS) if FROZEN else Path(__file__).resolve().parent.parent
# Per-machine state lives outside the .app bundle - see relay/config.py for
# why. The relay (same process in the packaged app) creates event.toml there
# on first run; this side only ever reads config and writes .env.
DATA_DIR = (Path.home() / "Library" / "Application Support" / "Campus Captions") if FROZEN else ROOT
DEFAULT_CONFIG = DATA_DIR / "config" / "event.toml"
EXAMPLE_CONFIG = ROOT / "config" / "event.example.toml"
ENV_FILE = DATA_DIR / ".env"


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


def _log_dir() -> Path:
    """The packaged app has no terminal to print to and `sys._MEIPASS` is a
    temp directory that's gone by the next launch, so its logs go in the
    standard per-user macOS location instead - Console.app finds
    ~/Library/Logs/* on its own. Running from source, next to the code."""
    if getattr(sys, "frozen", False):
        return Path.home() / "Library" / "Logs" / "Campus Captions"
    return ROOT / "logs"


class _Tee:
    """Mirrors stdout/stderr into a logger while leaving the original stream
    untouched, so a terminal run still shows live output exactly as before."""

    def __init__(self, stream, logger: logging.Logger):
        self._stream = stream
        self._logger = logger

    def write(self, data: str) -> int:
        try:
            n = self._stream.write(data)
        except Exception:
            n = len(data)
        text = data.strip()
        if text:
            self._logger.info(text)
        return n

    def flush(self) -> None:
        try:
            self._stream.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        try:
            return self._stream.isatty()
        except Exception:
            return False


_logging_ready = False


def setup_logging(name: str = "capture") -> None:
    """Mirror this process's stdout/stderr into a rotating log file - see
    relay/config.py's copy of this function for why it's duplicated rather
    than shared. Idempotent: safe to call every time load() runs.
    """
    global _logging_ready
    if _logging_ready:
        return
    _logging_ready = True
    try:
        log_dir = _log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_dir / f"{name}.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    except OSError:
        return  # best-effort - a missing log must never stop the app
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    logger = logging.getLogger(f"campus-captions.{name}")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    sys.stdout = _Tee(sys.stdout, logger)
    sys.stderr = _Tee(sys.stderr, logger)
    logger.info(f"----- {name} starting, pid={os.getpid()} -----")


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


# Mirrors relay/config.py: a value with a newline in it would become extra
# KEY=VALUE lines that load_env_file() loads for real on the next start -
# and CAPTIONS_RELAY_URL / CAPTIONS_SOURCE_TOKEN from the environment outrank
# event.toml, so that is a way to redirect this client. Refused at the write.
ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def has_control_chars(value: str) -> bool:
    return any(ch < " " or ch == "\x7f" for ch in value)


def write_private(path: Path, text: str) -> None:
    """Owner-only (0600) file write; advisory on Windows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def save_env_var(key: str, value: str, path: Path = ENV_FILE) -> None:
    """Write key=value into .env, in place, preserving every other line.

    Lets the status page's "add your Deepgram key" field work without anyone
    opening a text editor. Mirrors load_env_file's hand-rolled parsing rather
    than pulling in python-dotenv's writer for one line. Raises ValueError
    rather than writing anything that could not be read back as one line.
    """
    if not isinstance(key, str) or not ENV_KEY_RE.match(key):
        raise ValueError(f"refusing to write .env key {key!r}")
    if not isinstance(value, str) or has_control_chars(value):
        raise ValueError(f"refusing to write {key}: value contains control characters")
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
    write_private(path, "\n".join(lines) + "\n")


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
    setup_logging()
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
    # run, and a named backend this machine cannot run - apple_speech on
    # Windows, which is what a fresh clone used to give everyone - is
    # substituted rather than left to fail at the first Open ASR stream.
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
