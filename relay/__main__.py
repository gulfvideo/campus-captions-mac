"""python -m relay - start the relay.

    python -m relay
    python -m relay --port 8080
    python -m relay --config config/event.toml

If the configured port is taken by another program the relay starts on the
next free one and says so, rather than refusing to start. Changing the port
on the Settings page restarts the relay here, in this process, on the new
port - the launcher and the menu bar app do the same thing their own way.
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
    parser.add_argument("--port", type=int, default=None,
                        help="port for this run only; a port saved from the "
                             "Settings page takes over after a restart")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--log-level", default="warning",
                        help="uvicorn log level (default: warning - the relay "
                             "prints its own lines)")
    args = parser.parse_args()

    requested = args.port
    while True:
        cfg = config_module.load(args.config)
        if args.host:
            cfg.host = args.host
        if requested:
            cfg.port = requested
            requested = None          # once; a Settings change wins next time
        chosen = config_module.choose_port(cfg.host, cfg.port)
        if chosen != cfg.port:
            print(f"[relay] port {cfg.port} is in use by another program - "
                  f"using {chosen} instead. The wall and phone URLs carry the "
                  f"port; change it for good on the Settings page.")
            cfg.port = chosen

        app = create_app(cfg)
        server = uvicorn.Server(uvicorn.Config(
            app, host=cfg.host, port=cfg.port, log_level=args.log_level,
            ws_ping_interval=20, ws_ping_timeout=20, ws_max_size=64 * 1024,
            timeout_graceful_shutdown=5))
        app.state.server = server
        server.run()
        if not getattr(app.state, "restart_requested", False):
            break
        print(f"[relay] restarting on port {app.state.restart_port}")


if __name__ == "__main__":
    main()
