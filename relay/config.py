"""Relay configuration: config/event.toml, overridable by environment.

Stage 3 adds [capture] and [deepgram] sections to the same file; the relay
reads [relay], [youtube], [languages] and [translate], and ignores the rest.
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

# Repo root when run from source; the PyInstaller bundle's resource directory
# (where the .spec's `datas` land) when frozen into the macOS app.
ROOT = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "event.toml"
EXAMPLE_CONFIG = ROOT / "config" / "event.example.toml"
VIEWER_DIR = ROOT / "viewer"
ENV_FILE = ROOT / ".env"


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


def save_env_var(key: str, value: str, path: Path = ENV_FILE) -> None:
    """Write key=value into .env, in place, preserving every other line."""
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
class RelayConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    source_token: str = ""
    public_hostname: str = ""
    youtube_url: str = ""
    youtube_lang: str = "en-US"
    youtube_start_enabled: bool = False

    # [languages] / [translate]
    source_language: str = "en"
    offer_languages: tuple[str, ...] = ()
    translate_provider: str = "none"

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
        CAPTIONS_HOST, CAPTIONS_PORT, CAPTIONS_SOURCE_TOKEN
    """
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

            cfg.config_path = chosen
        except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
            # A broken config must not stop an event from starting.
            print(f"[relay] ignoring unreadable config {chosen}: {exc}")

    cfg.host = os.environ.get("CAPTIONS_HOST", cfg.host)
    cfg.port = int(os.environ.get("CAPTIONS_PORT", cfg.port))
    cfg.source_token = os.environ.get("CAPTIONS_SOURCE_TOKEN", cfg.source_token)
    cfg.public_hostname = os.environ.get("CAPTIONS_PUBLIC_HOSTNAME", cfg.public_hostname).lower().strip()
    return cfg


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


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
