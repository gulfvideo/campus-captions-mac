"""The one place the shipped version number is written.

Read by the .spec file, the release workflow, the updater and the menu bar's
About item, so none of them can disagree.

Releases of this project are tagged `vX.Y.Z-beta` (see the Releases page), so
a pre-release suffix here has to be survivable rather than a build failure -
hence the parsing below.
"""

VERSION = "0.3.2-beta"

# The numeric part only: "0.3.0-beta" -> "0.3.0".
VERSION_NUMBER = VERSION.split("-", 1)[0].split("+", 1)[0]


def _version_tuple(number: str) -> tuple[int, int, int, int]:
    """(major, minor, patch, build), padded and truncated to exactly four.
    Tolerant on purpose: an unparseable piece counts as 0 rather than raising."""
    parts: list[int] = []
    for piece in number.split(".")[:4]:
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts)  # type: ignore[return-value]
