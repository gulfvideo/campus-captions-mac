"""Relay configuration: config/event.toml, overridable by environment.

Stage 3 adds [capture] and [deepgram] sections to the same file; the relay
reads [relay], [youtube], [languages] and [translate], and ignores the rest.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import sys
import tomllib
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Repo root when run from source; the PyInstaller bundle's resource directory
# (where the .spec's `datas` land) when frozen into the macOS app.
ROOT = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "event.toml"
EXAMPLE_CONFIG = ROOT / "config" / "event.example.toml"
VIEWER_DIR = ROOT / "viewer"
ENV_FILE = ROOT / ".env"


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


def setup_logging(name: str = "relay") -> None:
    """Mirror this process's stdout/stderr into a rotating log file, so a
    crash or a silent failure - like a backend that reports an error nobody
    was watching the terminal to see - leaves something to reference
    afterward instead of vanishing with the packaged app's nonexistent
    console. Idempotent: safe to call every time load() runs.
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

    Mirrors capture/config.py's copy of this function rather than importing
    it - the relay and the capture client are meant to run as fully separate
    processes with no dependency on each other. Without this, a key saved by
    /api/translation only takes effect until the relay restarts: the write
    lands in .env, but nothing reads it back until a process asks.
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


# A .env line is "KEY=VALUE" and nothing else, so a key or value carrying a
# newline (or any other control character) would smuggle extra lines into the
# file - and load_env_file() would faithfully load those as real variables on
# the next start, including ones like AZURE_TRANSLATOR_ENDPOINT that decide
# where a key gets sent. Refused at the write, whatever the caller checked.
ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def has_control_chars(value: str) -> bool:
    return any(ch < " " or ch == "\x7f" for ch in value)


def write_private(path: Path, text: str) -> None:
    """Write a file only its owner can read (0600). On Windows the mode is
    advisory; the per-user profile ACL does the equivalent job there."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def save_env_var(key: str, value: str, path: Path = ENV_FILE) -> None:
    """Write key=value into .env, in place, preserving every other line.

    Raises ValueError rather than writing anything that could not be read
    back as exactly one KEY=VALUE line.
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


def _ensure_secret(env_key: str, current: str, what: str) -> str:
    """Return a non-empty secret for `env_key`, generating and persisting one
    into .env when nothing is configured.

    Both tokens the relay depends on used to default to "no token": the
    source token because a booth network was assumed closed, and the operator
    surface because it had no token at all. The audience is invited onto that
    network by the QR code, so "empty means open" was never a safe default.
    Generating one here means a first run is protected without anyone having
    to invent a password, and the value lands in .env so the capture client
    (which reads the same file) and the next restart both pick it up.
    """
    value = (current or "").strip()
    if value:
        return value
    value = secrets.token_urlsafe(24)
    os.environ[env_key] = value
    try:
        save_env_var(env_key, value)
        print(f"[relay] generated a {what} and saved it to .env as {env_key}")
    except (OSError, ValueError) as exc:
        print(f"[relay] generated a {what} for this run only - could not save "
              f"it to .env ({exc}); set {env_key} yourself to make it stick")
    return value


@dataclass
class RelayConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    source_token: str = ""
    # Gates the status page, the tuner, every /api/* route and /ws/status.
    # Never comes from event.toml: env or .env only, generated on first run.
    operator_token: str = ""
    public_hostname: str = ""
    tunnel_start_enabled: bool = False
    youtube_url: str = ""
    youtube_lang: str = "en-US"
    youtube_start_enabled: bool = False

    # [languages] / [translate]
    source_language: str = "en"
    offer_languages: tuple[str, ...] = ()
    translate_provider: str = "none"

    # [event] - the welcome screen and where the event is; see relay/event.py.
    # The state is persisted too, so a relay restart mid-event resumes "live"
    # instead of putting the welcome screen back in front of the audience.
    event_state: str = "starting"
    event_title: str = ""
    event_message: str = ""
    event_notice: str = ""
    event_pause_message: str = ""
    event_logo_alt: str = ""

    config_path: Path | None = None

    @property
    def token_required(self) -> bool:
        return bool(self.source_token)

    def base_url(self, ip: str) -> str:
        """What to print on the QR code and hand to phones.

        Uses the Cloudflare Tunnel hostname when one is configured (see
        public_hostname above) rather than a separately-configured URL - one
        setting says both "this is where the tunnel points" and "this is what
        the QR code should say", so they cannot drift apart.
        """
        if self.public_hostname:
            return f"https://{self.public_hostname}"
        return f"http://{ip}:{self.port}"


def load(path: Path | None = None) -> RelayConfig:
    """Read config, falling back to the example file, then to defaults.

    Environment always wins, so the launcher and the booth can override without
    editing a file:
        CAPTIONS_HOST, CAPTIONS_PORT, CAPTIONS_SOURCE_TOKEN, CAPTIONS_OPERATOR_TOKEN
    Both tokens are generated and written to .env when nothing sets them.
    """
    setup_logging()
    load_env_file()
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
            cfg.public_hostname = str(section.get("public_hostname", cfg.public_hostname)).lower().strip()
            cfg.tunnel_start_enabled = bool(
                (data.get("tunnel", {}) or {}).get("start_enabled", cfg.tunnel_start_enabled))
            youtube = data.get("youtube", {}) or {}
            cfg.youtube_url = str(youtube.get("url", cfg.youtube_url))
            cfg.youtube_lang = str(youtube.get("lang", cfg.youtube_lang))
            cfg.youtube_start_enabled = bool(youtube.get("start_enabled", cfg.youtube_start_enabled))

            langs = data.get("languages", {}) or {}
            cfg.source_language = str(langs.get("source", cfg.source_language))
            offered = langs.get("offer") or []
            if isinstance(offered, list):
                cfg.offer_languages = tuple(str(x) for x in offered)
            cfg.translate_provider = str(
                (data.get("translate", {}) or {}).get("provider", cfg.translate_provider))

            ev = data.get("event", {}) or {}
            cfg.event_state = str(ev.get("state", cfg.event_state))
            cfg.event_title = str(ev.get("title", cfg.event_title))
            cfg.event_message = str(ev.get("message", cfg.event_message))
            cfg.event_notice = str(ev.get("notice", cfg.event_notice))
            cfg.event_pause_message = str(ev.get("pause_message", cfg.event_pause_message))
            cfg.event_logo_alt = str(ev.get("logo_alt", cfg.event_logo_alt))

            cfg.config_path = chosen
        except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
            # A broken config must not stop an event from starting.
            print(f"[relay] ignoring unreadable config {chosen}: {exc}")

    cfg.host = os.environ.get("CAPTIONS_HOST", cfg.host)
    cfg.port = int(os.environ.get("CAPTIONS_PORT", cfg.port))
    cfg.source_token = os.environ.get("CAPTIONS_SOURCE_TOKEN", cfg.source_token)
    cfg.public_hostname = os.environ.get("CAPTIONS_PUBLIC_HOSTNAME", cfg.public_hostname).lower().strip()
    cfg.source_token = _ensure_secret("CAPTIONS_SOURCE_TOKEN", cfg.source_token, "source token")
    cfg.operator_token = _ensure_secret(
        "CAPTIONS_OPERATOR_TOKEN", os.environ.get("CAPTIONS_OPERATOR_TOKEN", ""), "operator token")
    return cfg


def _quote(value: str) -> str:
    """A TOML basic string. Control characters are escaped rather than
    written raw - a bare newline inside the quotes is a parse error, and an
    unparseable event.toml is silently replaced by defaults at the next
    start (see load()), which would drop the public hostname and the
    source token on the floor."""
    out = []
    for ch in value:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch < " " or ch == "\x7f":
            out.append("\\u%04X" % ord(ch))
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _quote_list(items: list[str]) -> str:
    return "[" + ", ".join(_quote(x) for x in items) + "]"


def save_youtube_url(path: Path, url: str, lang: str) -> None:
    """Write url/lang into [youtube] in `path`, in place.

    So pasting a link on the status page never requires opening a text editor.
    Hand-rolled rather than pulling in a TOML-writing dependency for two
    lines: finds the [youtube] section (creating it if missing) and replaces
    or inserts just the `url` and `lang` keys, leaving every comment and every
    other section exactly as it was.
    """
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = text.splitlines()
    header = "[youtube]"

    start = next((i for i, line in enumerate(lines) if line.strip() == header), None)
    end = len(lines)
    if start is not None:
        end = next(
            (i for i in range(start + 1, len(lines))
             if lines[i].strip().startswith("[") and lines[i].strip().endswith("]")),
            len(lines),
        )

    wanted = {"url": _quote(url), "lang": _quote(lang)}
    if start is not None:
        for i in range(start + 1, end):
            key = lines[i].split("=", 1)[0].strip()
            if key in wanted:
                lines[i] = f"{key} = {wanted.pop(key)}"
        if wanted:
            insertion = [f"{key} = {value}" for key, value in wanted.items()]
            lines[start + 1:start + 1] = insertion
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(header)
        lines.extend(f"{key} = {value}" for key, value in wanted.items())

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def save_translate_settings(path: Path, provider: str, offer_languages: list[str]) -> None:
    """Write provider into [translate] and the target list into [languages],
    in place - mirrors save_youtube_url above. The Azure key itself never
    comes near this file; it lives in .env, via save_env_var, same as the
    Deepgram key. This only saves *which* languages to translate into, so a
    relay restart picks up what the status page's translation panel set.
    """
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = text.splitlines()

    def upsert(header: str, wanted: dict[str, str]) -> None:
        nonlocal lines
        start = next((i for i, line in enumerate(lines) if line.strip() == header), None)
        end = len(lines)
        if start is not None:
            end = next(
                (i for i in range(start + 1, len(lines))
                 if lines[i].strip().startswith("[") and lines[i].strip().endswith("]")),
                len(lines),
            )
            for i in range(start + 1, end):
                key = lines[i].split("=", 1)[0].strip()
                if key in wanted:
                    lines[i] = f"{key} = {wanted.pop(key)}"
            if wanted:
                insertion = [f"{key} = {value}" for key, value in wanted.items()]
                lines[start + 1:start + 1] = insertion
        else:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(header)
            lines.extend(f"{key} = {value}" for key, value in wanted.items())

    upsert("[translate]", {"provider": _quote(provider)})
    upsert("[languages]", {"offer": _quote_list(offer_languages)})

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def save_tunnel_settings(path: Path, hostname: str, start_enabled: bool) -> None:
    """Write hostname into [relay] public_hostname and the operator's
    on/off intent into [tunnel] start_enabled, in place - mirrors
    save_youtube_url above. The tunnel token itself never comes near this
    file; it lives in .env via save_env_var, same as the Deepgram key.
    """
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = text.splitlines()

    def upsert(header: str, wanted: dict[str, str]) -> None:
        nonlocal lines
        start = next((i for i, line in enumerate(lines) if line.strip() == header), None)
        end = len(lines)
        if start is not None:
            end = next(
                (i for i in range(start + 1, len(lines))
                 if lines[i].strip().startswith("[") and lines[i].strip().endswith("]")),
                len(lines),
            )
            for i in range(start + 1, end):
                key = lines[i].split("=", 1)[0].strip()
                if key in wanted:
                    lines[i] = f"{key} = {wanted.pop(key)}"
            if wanted:
                insertion = [f"{key} = {value}" for key, value in wanted.items()]
                lines[start + 1:start + 1] = insertion
        else:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(header)
            lines.extend(f"{key} = {value}" for key, value in wanted.items())

    upsert("[relay]", {"public_hostname": _quote(hostname)})
    upsert("[tunnel]", {"start_enabled": "true" if start_enabled else "false"})

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def save_event_settings(path: Path, fields: dict[str, str]) -> None:
    """Write any of state/title/message/notice/pause_message into [event],
    in place - same shape as save_tunnel_settings above. Called on every
    change from the status page, so the welcome screen and the event state
    survive a relay restart."""
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = text.splitlines()
    header = "[event]"
    wanted = {key: _quote(str(value)) for key, value in fields.items()}

    start = next((i for i, line in enumerate(lines) if line.strip() == header), None)
    if start is not None:
        end = next(
            (i for i in range(start + 1, len(lines))
             if lines[i].strip().startswith("[") and lines[i].strip().endswith("]")),
            len(lines),
        )
        for i in range(start + 1, end):
            key = lines[i].split("=", 1)[0].strip()
            if key in wanted:
                lines[i] = f"{key} = {wanted.pop(key)}"
        if wanted:
            insertion = [f"{key} = {value}" for key, value in wanted.items()]
            lines[start + 1:start + 1] = insertion
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(header)
        lines.extend(f"{key} = {value}" for key, value in wanted.items())

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
