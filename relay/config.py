"""Relay configuration: config/event.toml, overridable by environment.

Stage 3 adds [capture] and [deepgram] sections to the same file; the relay reads
only [relay] and ignores the rest.
"""

from __future__ import annotations

import os
import secrets
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

    # [site]
    public_url: str = ""          # e.g. https://captions.school.edu
    mode: str = "lan"             # lan | public
    admin_token: str = ""

    # [languages]
    source_language: str = "en"
    offer_languages: tuple[str, ...] = ()
    translate_provider: str = "none"

    @property
    def token_required(self) -> bool:
        return bool(self.source_token)

    @property
    def is_public(self) -> bool:
        """Public mode locks the operator routes behind a token.

        On a booth LAN nobody needs a password to press a button. The moment the
        relay is reachable from the internet that stops being true: /api/control
        would let a stranger open the ASR stream and spend money.
        """
        return self.mode.strip().lower() == "public"

    def base_url(self, ip: str) -> str:
        """What to print on the QR code and hand to phones."""
        if self.public_url:
            return self.public_url.rstrip("/")
        return f"http://{ip}:{self.port}"


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

            site = data.get("site", {}) or {}
            cfg.public_url = str(site.get("public_url", cfg.public_url))
            cfg.mode = str(site.get("mode", cfg.mode))
            cfg.admin_token = str(site.get("admin_token", cfg.admin_token))

            langs = data.get("languages", {}) or {}
            cfg.source_language = str(langs.get("source", cfg.source_language))
            offered = langs.get("offer") or []
            if isinstance(offered, list):
                cfg.offer_languages = tuple(str(x) for x in offered)
            cfg.translate_provider = str(
                (data.get("translate", {}) or {}).get("provider",
                                                      cfg.translate_provider))

            cfg.config_path = chosen
        except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
            # A broken config must not stop an event from starting.
            print(f"[relay] ignoring unreadable config {chosen}: {exc}")

    cfg.host = os.environ.get("CAPTIONS_HOST", cfg.host)
    cfg.port = int(os.environ.get("CAPTIONS_PORT", cfg.port))
    cfg.source_token = os.environ.get("CAPTIONS_SOURCE_TOKEN", cfg.source_token)
    cfg.public_url = os.environ.get("CAPTIONS_PUBLIC_URL", cfg.public_url)
    cfg.mode = os.environ.get("CAPTIONS_MODE", cfg.mode)
    cfg.admin_token = os.environ.get("CAPTIONS_ADMIN_TOKEN", cfg.admin_token)

    # A public relay with no admin token would ship the control API to the
    # internet. Generate one rather than leave it open; the launcher prints it.
    if cfg.is_public and not cfg.admin_token:
        cfg.admin_token = secrets.token_urlsafe(12)
        print("[relay] public mode with no admin_token set - generated one for "
              "this run only. Put it in config/event.toml to keep it stable.")
    return cfg
