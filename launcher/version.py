"""The one place the shipped version number is written.

Read by both packagers' .spec files, by the Windows installer script, and by
the About items in the two tray/menu-bar front ends. It used to be a literal
in four of those, which is three chances to ship an installer whose Add or
Remove Programs entry disagrees with the app's own About box.
"""

VERSION = "0.2.5"

# (major, minor, patch, build) for the Windows VERSIONINFO resource, which
# takes four integers and nothing else.
VERSION_TUPLE = tuple(int(part) for part in VERSION.split(".")) + (0,)
