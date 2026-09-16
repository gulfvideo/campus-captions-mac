"""Headless entry point for the packaged engine: relay + capture, no UI.

The front end is macapp/CampusCaptions.swift now, so this process has no menu
bar, no rumps and no pyobjc - it starts the same Supervisor the tray app used
and then blocks. Everything the shell needs to talk to it goes out as one JSON
line on stdout, because the alternative is teaching Swift to read .env and a
TOML file to learn two values it can simply be told.
"""

from __future__ import annotations

import json
import sys
import threading

from launcher import embedded
from relay import config as relay_config


def main() -> None:
    cfg = relay_config.load()

    # Port fallback stays here rather than in the shell: the relay thread makes
    # this same choice on its own, and two implementations of "which port" is
    # how they end up disagreeing.
    taken = embedded.port_in_use(cfg.port)
    if taken:
        try:
            cfg.port = relay_config.choose_port(cfg.host, cfg.port + 1)
        except OSError:
            print(json.dumps({"error": "no free port"}), flush=True)
            sys.exit(1)

    supervisor = embedded.Supervisor(cfg)
    # The shell owns the window; opening a browser as well would give the
    # operator two of them.
    supervisor.start(open_browser=False)

    print(json.dumps({
        "port": cfg.port,
        "token": cfg.operator_token,
        "moved_from": relay_config.load().port if taken else None,
    }), flush=True)

    threading.Event().wait()      # daemon threads do the work; park this one


if __name__ == "__main__":
    main()
