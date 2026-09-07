"""Import everything the packaged Windows app imports, and check the platform
defaults are the Windows ones.

Run against the *build* virtual environment after PyInstaller finishes - see
.github/workflows/release-windows.yml. A GitHub runner has no display and no
audio device, so the tray cannot be started there; what this catches instead
is the failure a frozen build is actually prone to, an import that only
resolves on the developer's machine, plus the platform-default regression
this file exists because of: a Windows install coming up on apple_speech.

Exits non-zero with a message on failure, so a broken build never reaches a
release page.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    import launcher.embedded            # noqa: F401 - the shared supervisor
    import relay.server                 # noqa: F401 - the whole relay
    import winapp.tray                  # noqa: F401 - pystray and the icons
    from capture.backends import registry, support
    from relay import config as relay_config

    problems = []

    available = support.supported()
    if "apple_speech" in available:
        problems.append(f"apple_speech is offered on this platform: {available}")
    if support.resolve_auto() == "apple_speech":
        problems.append("the automatic backend default is apple_speech")
    if support.resolve("apple_speech") == "apple_speech":
        problems.append("apple_speech is not substituted when it cannot run")
    if set(registry.BACKENDS) != set(support.BY_NAME):
        problems.append("registry.BACKENDS and support.CATALOGUE disagree")

    viewer = Path(relay_config.VIEWER_DIR)
    for page in ("setup.html", "status.html", "settings.html", "wall.html", "phone.html"):
        if not (viewer / page).is_file():
            problems.append(f"{page} is missing from the bundle")

    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        return 1

    print(f"ok - backends offered here: {', '.join(available)}")
    print(f"ok - automatic default: {support.resolve_auto()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
