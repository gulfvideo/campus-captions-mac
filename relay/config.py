"""Relay configuration: config/event.toml, overridable by environment.

Stage 3 adds [capture] and [deepgram] sections to the same file; the relay
reads [relay], [youtube], [languages] and [translate], and ignores the rest.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import socket
import sys
import tomllib
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Repo root when run from source; the PyInstaller bundle's resource directory
# (where the .spec's `datas` land) when frozen into the packaged app.
FROZEN = bool(getattr(sys, "frozen", False))
ROOT = Path(sys._MEIPASS) if FROZEN else Path(__file__).resolve().parent.parent
APP_NAME = "Campus Captions"


def use_bundled_certs() -> None:
    """Point OpenSSL at the CA store certifi ships inside the bundle.

    The packaged app's OpenSSL looks for its CA store at the path compiled
    into the Python it was built with - python.org's, in CI - which does not
    exist on a tester's Mac, so every HTTPS connection (updates, Deepgram,
    Azure) fails certificate verification. OpenSSL reads this variable.
    """
    if not os.environ.get("SSL_CERT_FILE"):
        import certifi
        os.environ["SSL_CERT_FILE"] = certifi.where()


if FROZEN:
    use_bundled_certs()


def _data_dir() -> Path:
    """Where per-machine state lives in the packaged app: event.toml, .env,
    uploaded images, logs. Never inside the bundle - it is signed, shared as
    a download, and replaced on every update, so a Deepgram key written into
    it would break the signature and travel with the next copy someone
    passes around.

    Each platform's own conventional location, so the folder is where the
    operator's OS (and its backup tool) already expects an app's settings to
    be. Running from source it is simply the checkout.
    """
    if not FROZEN:
        return ROOT
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_DATA_HOME", "").strip()
    return Path(base or Path.home() / ".local" / "share") / APP_NAME


def _log_dir() -> Path:
    """The packaged app has no terminal to print to and `sys._MEIPASS` is a
    temp directory that's gone by the next launch, so its logs go in a
    standard per-user location instead - Console.app finds ~/Library/Logs/*
    on its own, and the menu bar's "Show Logs in Finder" opens this folder.
    Running from source, next to the code."""
    if not FROZEN:
        return ROOT / "logs"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / APP_NAME
    return _data_dir() / "logs"


# Per-machine state - event.toml, .env, uploaded images - lives in the
# checkout when run from source, and in the platform's own per-user
# application-data folder when packaged.
DATA_DIR = _data_dir()
DEFAULT_CONFIG = DATA_DIR / "config" / "event.toml"
EXAMPLE_CONFIG = ROOT / "config" / "event.example.toml"
VIEWER_DIR = ROOT / "viewer"
ENV_FILE = DATA_DIR / ".env"


# Hand-edited files that must exist somewhere writable. resolve_path() looks
# in DATA_DIR before the bundle, so without seeding these the packaged app
# reads the copies inside the bundle - which is read-only and code-signed
# (editing one there breaks the signature: "a sealed resource is missing or
# invalid"). The Settings page would be telling the operator to edit a file
# they cannot edit, and "Reload keyterms & blocklist" would re-read the
# shipped one every time.
SEEDED_CONFIG_FILES = ("keyterms.txt", "blocklist.txt")

# The two hand-maintained word lists, by the short name the Settings page and
# /api/wordlist use. Editing them used to mean finding the file in a text
# editor - the one thing on that page that still sent an operator outside the
# app - so the relay now reads and writes them itself, and this is the only
# mapping from a name in a URL to a path on disk. Nothing else may name a file.
WORDLISTS = {
    "keyterms": "keyterms.txt",
    "blocklist": "blocklist.txt",
}
# A term or short phrase per line. Generous enough for a large campus's worth
# of names, small enough that nothing can fill a disk through this route.
MAX_WORDLIST_BYTES = 256 * 1024


def wordlist_path(name: str) -> Path:
    """Where `name`'s list lives - always inside DATA_DIR, never anywhere the
    caller chose. The capture client's resolve_path() looks in DATA_DIR before
    the bundle, so a file written here is the one it reads."""
    try:
        filename = WORDLISTS[name]
    except KeyError:
        raise ValueError(f"unknown word list {name!r}") from None
    return DATA_DIR / "config" / filename


def ensure_data_dir() -> None:
    """First run of the packaged app: give it real files to edit, copied from
    the ones shipped in the bundle, in the folder the status page writes to
    and the menu bar's "Show Config Folder in Finder" opens."""
    if not FROZEN:
        return
    try:
        DEFAULT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        if not DEFAULT_CONFIG.exists() and EXAMPLE_CONFIG.exists():
            DEFAULT_CONFIG.write_bytes(EXAMPLE_CONFIG.read_bytes())
        for name in SEEDED_CONFIG_FILES:
            shipped, local = ROOT / "config" / name, DATA_DIR / "config" / name
            if not local.exists() and shipped.exists():
                local.write_bytes(shipped.read_bytes())
    except OSError as exc:
        print(f"[relay] could not prepare {DATA_DIR}: {exc}")


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


_logging_ready: set[str] = set()


def setup_logging(name: str = "relay") -> None:
    """Mirror this process's stdout/stderr into a rotating log file, so a
    crash or a silent failure - like a backend that reports an error nobody
    was watching the terminal to see - leaves something to reference
    afterward instead of vanishing with the packaged app's nonexistent
    console. Idempotent per name: safe to call every time load() runs, and
    the packaged app (relay and capture in one process) gets both logs.
    """
    if name in _logging_ready:
        return
    _logging_ready.add(name)
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
    """Write a file only its owner can read (0600)."""
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
    event_disclaimer: str = ""

    # [propresenter] - direct caption output, see relay/propresenter.py
    pp_host: str = "127.0.0.1"
    pp_port: int = 1025
    pp_surface: str = "stage_message"
    pp_message: str = "Live Captions"
    pp_token: str = "Caption"
    pp_language: str = ""            # "" = the source language
    # "line" = finals only, "word" = stream the in-progress text too. The
    # wall takes the same setting under the same name (viewer/wall.html
    # ?reveal=), so an operator learns it once.
    pp_reveal: str = "line"
    pp_lines: int = 2                # never more than two; see propresenter.MAX_LINES
    pp_chars_per_line: int = 42
    pp_start_enabled: bool = False

    # [slides] - separate, read-only audience thumbnails; disabled by default.
    slides_enabled: bool = False
    slides_host: str = "127.0.0.1"
    slides_port: int = 1025
    slides_screen: int = 0
    slides_approved_hosts: tuple[str, ...] = ()

    # [overlay] keys = ["obs:abc123", ...] - one revocable key per browser-source
    # output. Empty means the wall needs no key (the pre-key behaviour).
    overlay_keys: dict[str, str] = field(default_factory=dict)

    # [ui] - how the app opens its own pages. "app" is a chromeless browser
    # window that looks and behaves like a program; "browser" is an ordinary
    # tab. Defaults to app: this is a program as far as its operator is
    # concerned, and a tab among twenty others is easy to lose during an
    # event. A machine with only Firefox falls back to a tab anyway - see
    # launcher/appwindow.py.
    window_mode: str = "app"

    # [setup] - has anyone been through the first-run wizard on this machine?
    # False sends /status to /setup once. Defaults to True when there is no
    # config file to read it from, because that is not a fresh install: it is
    # a machine whose event.toml was deleted or has not been written yet, and
    # sending a working booth to a wizard mid-event would be worse than
    # showing a wizard one time too few. load() sets it to False explicitly
    # when the file exists and does not answer.
    setup_completed: bool = True

    # [setup] - has anyone been shown round the operator pages on this
    # machine? Same default and the same reason: a booth with no config file
    # is not a fresh install, and a guided tour that opens over a live
    # dashboard is worse than one that never offers itself.
    tour_completed: bool = True

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
    ensure_data_dir()
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

            pp = data.get("propresenter", {}) or {}
            cfg.pp_host = str(pp.get("host", cfg.pp_host)).strip()
            cfg.pp_port = int(pp.get("port", cfg.pp_port))
            cfg.pp_surface = str(pp.get("surface", cfg.pp_surface))
            cfg.pp_message = str(pp.get("message", cfg.pp_message))
            cfg.pp_token = str(pp.get("token", cfg.pp_token))
            cfg.pp_language = str(pp.get("language", cfg.pp_language)).strip()
            # `interims = true/false` is what this setting was called before
            # it grew a second value; an existing event.toml still works.
            if "reveal" in pp:
                cfg.pp_reveal = str(pp.get("reveal") or cfg.pp_reveal).strip().lower()
            elif "interims" in pp:
                cfg.pp_reveal = "word" if bool(pp.get("interims")) else "line"
            if cfg.pp_reveal not in ("word", "line"):
                cfg.pp_reveal = "line"
            cfg.pp_lines = max(1, min(2, int(pp.get("lines", cfg.pp_lines))))
            cfg.pp_chars_per_line = max(12, min(120, int(
                pp.get("chars_per_line", cfg.pp_chars_per_line))))
            cfg.pp_start_enabled = bool(pp.get("start_enabled", cfg.pp_start_enabled))

            slides = data.get("slides", {})
            if not isinstance(slides, dict):
                slides = {}
            cfg.slides_enabled = slides.get("enabled", False) is True
            cfg.slides_host = str(slides.get("host", "127.0.0.1"))
            cfg.slides_port = slides.get("port", 1025)
            cfg.slides_screen = slides.get("screen", 0)
            approved = slides.get("approved_hosts", [])
            cfg.slides_approved_hosts = tuple(approved) if isinstance(approved, list) else ()

            keys = (data.get("overlay", {}) or {}).get("keys") or []
            if isinstance(keys, list):
                cfg.overlay_keys = parse_overlay_keys(keys)
            cfg.translate_provider = str(
                (data.get("translate", {}) or {}).get("provider", cfg.translate_provider))

            ui = data.get("ui", {}) or {}
            mode = str(ui.get("window_mode", cfg.window_mode)).strip().lower()
            cfg.window_mode = mode if mode in ("app", "browser") else cfg.window_mode

            # Absent means a config written before the wizard existed, or
            # the example file: either way nobody has been through it here.
            setup = data.get("setup", {}) or {}
            cfg.setup_completed = bool(setup.get("completed", False))
            # A config that predates the tour answers for it with whether the
            # wizard has been done: a booth already set up has been running
            # for a while and does not want to be shown round, a fresh one is
            # about to go through the wizard and then the tour.
            cfg.tour_completed = bool(setup.get("tour_completed",
                                                cfg.setup_completed))

            ev = data.get("event", {}) or {}
            cfg.event_state = str(ev.get("state", cfg.event_state))
            cfg.event_title = str(ev.get("title", cfg.event_title))
            cfg.event_message = str(ev.get("message", cfg.event_message))
            cfg.event_notice = str(ev.get("notice", cfg.event_notice))
            cfg.event_pause_message = str(ev.get("pause_message", cfg.event_pause_message))
            cfg.event_logo_alt = str(ev.get("logo_alt", cfg.event_logo_alt))
            cfg.event_disclaimer = str(ev.get("disclaimer", cfg.event_disclaimer))

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
    """
    save_section(path, "[youtube]", {"url": url, "lang": lang})


def _inline_comment(line: str) -> str:
    """The trailing `# ...` of a TOML line, whitespace and all, or "".

    Only a '#' outside the quoted value counts: a device really can be named
    "Mic #2", and eating the rest of that line would corrupt the file. Used
    to carry a key's explanatory comment across a rewrite - this file is
    meant to stay hand-editable, and the hint next to `device` is worth more
    than the two lines it costs to keep.
    """
    _, sep, rhs = line.partition("=")
    if not sep:
        return ""
    quote = ""
    for i, ch in enumerate(rhs):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            # Keep the gap between value and comment, so the column the file
            # lines its comments up in survives the rewrite.
            return rhs[len(rhs[:i].rstrip()):]
    return ""


def _rewrite_line(line: str, key: str, value: str) -> str:
    """`key = value`, with the line's own inline comment put back in the
    column it was in, so a file whose comments line up still does after a
    longer or shorter value is written into it."""
    out = f"{key} = {value}"
    comment = _inline_comment(line)
    if not comment:
        return out
    column = len(line) - len(comment.lstrip())
    return out + " " * max(column - len(out), 2) + comment.lstrip()


def save_audio_device(path: Path, device: str) -> None:
    """Write the operator's chosen input into [capture] device, in place.

    Saved by *name*, never by index: PortAudio numbers devices in
    enumeration order, so today's index 5 is tomorrow's index 4 once
    something is plugged in or removed, and silently listening to the wrong
    input is the one failure this app must not have. resolve_device()
    already accepts a name substring, so the name round-trips.

    [capture], not [audio] - and scoped to that section on purpose, since
    [whisper] carries its own unrelated `device` (cpu/gpu) that must not be
    rewritten with the name of a microphone.

    This only ever writes the per-machine event.toml (the checkout's when
    running from source, _data_dir()'s when packaged). The shipped example
    keeps `device = ""`, meaning the system default, so a fresh install on
    someone else's computer still comes up on their own default rather than
    on whatever hardware this one happened to have.
    """
    save_section(path, "[capture]", {"device": device})


def save_translate_settings(path: Path, provider: str, offer_languages: list[str]) -> None:
    """Write provider into [translate] and the target list into [languages],
    in place. The Azure key itself never comes near this file; it lives in
    .env, via save_env_var, same as the Deepgram key. This only saves *which*
    languages to translate into, so a relay restart picks up what the status
    page's translation panel set.
    """
    save_section(path, "[translate]", {"provider": provider})
    save_section(path, "[languages]", {"offer": list(offer_languages)})


def save_tunnel_settings(path: Path, hostname: str, start_enabled: bool) -> None:
    """Write hostname into [relay] public_hostname and the operator's on/off
    intent into [tunnel] start_enabled, in place. The tunnel token itself
    never comes near this file; it lives in .env via save_env_var, same as
    the Deepgram key.
    """
    save_section(path, "[relay]", {"public_hostname": hostname})
    save_section(path, "[tunnel]", {"start_enabled": bool(start_enabled)})


# How many ports above the configured one to try when it is taken. Twenty
# is plenty for "something else grabbed 8000" and small enough that the
# operator can still guess where the relay went from the launcher output.
PORT_FALLBACK_TRIES = 20


def port_is_free(host: str, port: int) -> bool:
    """Can the relay bind this port right now?

    A bind test, not a connect probe: binding is what the relay actually has
    to do.
    """
    bind_host = "0.0.0.0" if host in ("", "0.0.0.0", "*") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        # Bind the way uvicorn will: with SO_REUSEADDR, without which a port
        # whose last connections are still in TIME_WAIT - exactly the state a
        # just-restarted relay's own port is in - reports busy for a minute.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((bind_host, port))
        except OSError:
            return False
    # Second opinion, for what the bind cannot see: SO_REUSEADDR hides a
    # listener on some systems. Anything answering on loopback means taken -
    # the same probe the launcher uses, so the two always agree.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            return False
    return True


def choose_port(host: str, preferred: int, tries: int = PORT_FALLBACK_TRIES) -> int:
    """The configured port if it is free, else the next free one above it.

    The launcher, the menu bar app and `python -m relay` all use this, so
    a Sunday morning with something squatting on 8000 starts captions on
    8001 and says so, instead of stopping with a request to edit a file.
    Raises OSError only when every candidate is taken.
    """
    for candidate in range(preferred, min(preferred + tries, 65536)):
        if port_is_free(host, candidate):
            return candidate
    raise OSError(f"no free port between {preferred} and {preferred + tries - 1}")


def save_relay_port(path: Path, port: int) -> None:
    """Write `port` into [relay], in place, keeping the line's own comment
    and every other line as it was."""
    save_section(path, "[relay]", {"port": int(port)})


def _toml_literal(value) -> str:
    """A Python value as the TOML literal save_section writes.

    bool before int, because bool *is* an int in Python and `True` would
    otherwise be written as `1`. float has its own branch for the same class
    of reason: without it a float fell through to the string case and
    `inaudible_below = 0.45` was written as `"0.45"`, which reads back as a
    string and breaks the first comparison the backend makes against it.
    `repr` rather than `str` so the value round-trips exactly.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"refusing to write a non-finite number: {value!r}")
        return repr(value)
    if isinstance(value, (list, tuple)):
        return _quote_list([str(x) for x in value])
    return _quote(str(value))


def parse_overlay_keys(items: list) -> dict[str, str]:
    """["obs:abc123", "propresenter:def456"] -> {"obs": "abc123", ...}.
    Malformed entries are skipped, never fatal - a typo in the config must
    not take the wall down."""
    out: dict[str, str] = {}
    for item in items:
        if not isinstance(item, str) or ":" not in item:
            continue
        name, _, key = item.partition(":")
        name, key = name.strip(), key.strip()
        if name and key:
            out[name] = key
    return out


def format_overlay_keys(keys: dict[str, str]) -> list[str]:
    return [f"{name}:{key}" for name, key in keys.items()]


def save_section(path: Path, header: str, values: dict) -> None:
    """Write `values` into the TOML section `header` (e.g. "[propresenter]")
    in place: keys that exist are rewritten (keeping their inline comment,
    see _inline_comment), the rest are inserted, and every other line of the
    file is left exactly as it was. Values are Python values; _toml_literal
    decides how they are written.

    The generic form of what save_youtube_url, save_translate_settings and
    save_tunnel_settings each do by hand. New sections should use this.
    """
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = text.splitlines()
    wanted = {key: _toml_literal(value) for key, value in values.items()}

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
                lines[i] = _rewrite_line(lines[i], key, wanted.pop(key))
        if wanted:
            lines[start + 1:start + 1] = [f"{key} = {value}" for key, value in wanted.items()]
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(header)
        lines.extend(f"{key} = {value}" for key, value in wanted.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    write_private(path, "\n".join(lines) + "\n")


def save_window_mode(path: Path, mode: str) -> None:
    """Remember whether pages open as an app window or a browser tab.

    In event.toml rather than a browser's storage, because the thing that
    acts on it is the launcher - it decides how to open the status page
    before any browser is involved.
    """
    if mode not in ("app", "browser"):
        raise ValueError(f"unknown window mode {mode!r}")
    save_section(path, "[ui]", {"window_mode": mode})


def save_setup_complete(path: Path, completed: bool = True) -> None:
    """Record that the first-run wizard has been finished (or skipped) on
    this machine, so /status stops sending the operator back to it.

    Written into event.toml rather than kept in memory: the point is that
    the *next* launch goes straight to the dashboard.
    """
    save_section(path, "[setup]", {"completed": bool(completed)})


def save_tour_complete(path: Path, completed: bool = True) -> None:
    """Record that the guided tour has been finished (or skipped) here.

    Beside setup.completed rather than in the browser's storage: the app
    window and an ordinary tab are different origins' worth of storage on
    some machines, and being shown round twice because you opened the other
    one is exactly the kind of thing a volunteer reports as a bug.
    """
    save_section(path, "[setup]", {"tour_completed": bool(completed)})


def save_event_settings(path: Path, fields: dict[str, str]) -> None:
    """Write any of state/title/message/notice/pause_message into [event],
    in place. Called on every change from the status page, so the welcome
    screen and the event state survive a relay restart.

    Values are forced to str: these are all text fields, and a bool slipping
    through would be written as a TOML boolean and read back as one.
    """
    save_section(path, "[event]", {k: str(v) for k, v in fields.items()})
