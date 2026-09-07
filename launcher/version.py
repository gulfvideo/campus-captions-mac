"""The one place the shipped version number is written.

Read by both packagers' .spec files, by the Windows installer script, and by
the About items in the two tray/menu-bar front ends. It used to be a literal
in four of those, which is three chances to ship an installer whose Add or
Remove Programs entry disagrees with the app's own About box.

Releases of this project are tagged `vX.Y.Z-beta` (see the Releases page), so
a pre-release suffix here has to be survivable rather than a build failure -
hence the parsing below.
"""

VERSION = "0.3.0-beta"

# The numeric part only: "0.3.0-beta" -> "0.3.0". Windows' VERSIONINFO
# resource takes four integers and nothing else, and Inno Setup's AppVersion
# is happier without a suffix too, so the label and the number are separated
# here rather than at four call sites.
VERSION_NUMBER = VERSION.split("-", 1)[0].split("+", 1)[0]


def _version_tuple(number: str) -> tuple[int, int, int, int]:
    """(major, minor, patch, build), padded and truncated to exactly four.

    Tolerant on purpose: this runs inside a PyInstaller .spec, where a
    ValueError surfaces as a failed build with a traceback pointing at a
    version string rather than at whatever the person was actually doing.
    A version that cannot be parsed is worth shipping as 0.0.0.0 with a
    wrong-looking Properties tab; it is not worth blocking a release.
    """
    parts: list[int] = []
    for piece in number.split(".")[:4]:
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts)  # type: ignore[return-value]


VERSION_TUPLE = _version_tuple(VERSION_NUMBER)
