#!/bin/sh
# Wraps a built Campus Captions.app in a disk image for distribution.
#
#   macapp/make_dmg.sh [output.dmg]
#
# Run after macapp/build.sh. Output defaults to
# macapp/dist/CampusCaptions-<version>.dmg.
#
# Why a .dmg rather than the .zip this project used to ship: opening a disk
# image and dragging one icon onto a folder is the gesture every Mac user
# already knows, and it puts the app in /Applications by construction. A zip
# leaves "Campus Captions.app" loose in Downloads, where it runs perfectly
# well until someone empties that folder.
#
# hdiutil only, no Homebrew, no create-dmg: this runs on a CI runner and on
# whatever Mac happens to be to hand. The window has no custom background or
# icon placement, which would need AppleScript and a GUI session to set up;
# it has the app and an Applications shortcut side by side, which is the part
# that matters.
set -eu

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/.." && pwd)"
app="$here/dist/Campus Captions.app"

if [ ! -d "$app" ]; then
    echo "no app at $app - run macapp/build.sh first" >&2
    exit 1
fi

if [ $# -ge 1 ]; then
    dmg="$1"
else
    version="$("$root/.venv/bin/python3" -c \
        "import sys; sys.path.insert(0, '$root'); from launcher.version import VERSION; print(VERSION)")"
    dmg="$here/dist/CampusCaptions-$version.dmg"
fi

staging="$(mktemp -d)"
trap 'rm -rf "$staging"' EXIT

# ditto, not cp: it preserves the bundle's extended attributes and its ad-hoc
# code signature. A plain cp -R can drop xattrs, and an app whose signature no
# longer verifies is one Gatekeeper refuses with no Open Anyway button.
ditto "$app" "$staging/Campus Captions.app"

# The half of the drag-to-install gesture that makes it obvious: /Applications
# sits in the same window as the app.
ln -s /Applications "$staging/Applications"

rm -f "$dmg"
hdiutil create \
    -volname "Campus Captions" \
    -srcfolder "$staging" \
    -fs HFS+ \
    -format UDZO \
    -quiet \
    "$dmg"

echo "built $dmg"
