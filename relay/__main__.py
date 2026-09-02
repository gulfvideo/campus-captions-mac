"""python -m relay - start the relay.

    python -m relay
    python -m relay --port 8080
    python -m relay --config config/event.toml
"""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from . import config as config_module
from .server import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Campus Captions relay server.")
    parser.add_argument("--host", default=None, help="bind address")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--log-level", default="warning",
                        help="uvicorn log level (default: warning - the relay "
                             "prints its own lines)")
    args = parser.parse_args()

    cfg = config_module.load(args.config)
    if args.host:
        cfg.host = args.host
    if args.port:
        cfg.port = args.port

    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port,
                log_level=args.log_level, ws_ping_interval=20,
                ws_ping_timeout=20, ws_max_size=64 * 1024)


if __name__ == "__main__":
    main()
