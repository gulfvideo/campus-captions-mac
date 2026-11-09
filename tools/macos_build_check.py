"""Import everything the packaged macOS app imports, and check the platform
defaults are the macOS ones.

Run against the *build* virtual environment after PyInstaller finishes - see
.github/workflows/release.yml. A GitHub runner has no display, no audio
device and no Speech Recognition permission, so the menu bar app cannot be
started there; what this catches instead is the failure a frozen build is
actually prone to, an import that only resolves on the developer's machine.

The platform assertions: apple_speech must be *offered* on a Mac new enough
for it, and
`auto` must resolve to it there rather than dropping to practice mode.

Exits non-zero with a message on failure, so a broken build never reaches a
release page.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    import launcher.embedded            # noqa: F401 - the shared supervisor
    import relay.server                 # noqa: F401 - the whole relay
    from capture.backends import registry, support
    from relay import config as relay_config

    problems = []

    available = support.supported()
    if set(registry.BACKENDS) != set(support.BY_NAME):
        problems.append("registry.BACKENDS and support.CATALOGUE disagree")

    # The runner's own macOS version decides what to expect here, because
    # apple_speech needs 26 and the runner image may be older. Both outcomes
    # are legitimate; a mismatch between the two answers is not.
    try:
        major = int(platform.mac_ver()[0].split(".")[0])
    except (ValueError, IndexError):
        major = 0
    reason = support.unsupported_reason("apple_speech")
    if major >= support.APPLE_SPEECH_MIN_MACOS:
        if reason is not None:
            problems.append(
                f"macOS {major} should be able to run apple_speech, but it is "
                f"refused: {reason}")
        if support.resolve_auto() != "apple_speech":
            problems.append(
                f"on macOS {major} the automatic default should be "
                f"apple_speech, not {support.resolve_auto()}")
    elif reason is None:
        problems.append(
            f"macOS {major} is too old for apple_speech but it is offered anyway")

    if support.unsupported_reason(support.resolve_auto()) is not None:
        problems.append("the automatic default is a backend this Mac cannot run")

    viewer = Path(relay_config.VIEWER_DIR)
    for page in ("setup.html", "status.html", "settings.html", "wall.html", "phone.html"):
        if not (viewer / page).is_file():
            problems.append(f"{page} is missing from the bundle")

    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        return 1

    print(f"ok - this runner is macOS {platform.mac_ver()[0] or 'unknown'}")
    print(f"ok - backends offered here: {', '.join(available)}")
    print(f"ok - automatic default: {support.resolve_auto()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
