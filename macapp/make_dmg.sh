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

# A Developer ID build gets the disk image signed too, then sent to Apple to
# notarize and the ticket stapled on, so it opens on a Mac with no warning and
# no internet. An ad-hoc build has nothing Apple would accept, so it stops here.
identity="$(codesign -dvv "$app" 2>&1 | sed -n 's/^Authority=\(Developer ID Application: .*\)$/\1/p')"
if [ -z "$identity" ]; then
    echo "ad-hoc signed app: not notarizing"
    exit 0
fi

codesign --sign "$identity" --timestamp "$dmg"

# The profile is made once per Mac with
#   xcrun notarytool store-credentials campus-captions --apple-id ... --team-id ...
# (CI makes one from secrets; see .github/workflows/release.yml).
profile="${NOTARY_PROFILE:-campus-captions}"
# CI keeps the profile in a throwaway keychain, which notarytool only reads
# when told; on a Mac the login keychain is the default and this is unset.
set -- --keychain-profile "$profile"
[ -n "${NOTARY_KEYCHAIN:-}" ] && set -- "$@" --keychain "$NOTARY_KEYCHAIN"
echo "--- notarizing (usually a few minutes) ---"
out="$(xcrun notarytool submit "$dmg" "$@" --wait 2>&1)" || true
echo "$out"
if ! echo "$out" | grep -q "status: Accepted"; then
    id="$(echo "$out" | sed -n 's/^ *id: //p' | head -1)"
    echo "notarization failed. Apple's reasons:" >&2
    [ -n "$id" ] && xcrun notarytool log "$id" "$@" >&2
    exit 1
fi
xcrun stapler staple "$dmg"
spctl --assess --type open --context context:primary-signature --verbose "$dmg"
