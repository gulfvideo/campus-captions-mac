#!/bin/sh
# Builds Campus Captions.app for distribution to beta testers.
#
#   .venv/bin/python3 -m pip install -r requirements.txt -r macapp/requirements-build.txt \
#       -r requirements-whisper.txt
#   macapp/build.sh
#
# requirements-whisper.txt is included even though the source-tree README
# calls it optional: there, skipping it keeps a Deepgram-only install lean,
# but the packaged app advertises "Whisper" as a clickable backend on the
# status page with no terminal to `pip install` from - if the wheel isn't in
# this venv when PyInstaller runs, picking it there just silently never opens.
#
# Output: macapp/dist/Campus Captions.app
#
# The app is ad-hoc signed (no Apple Developer ID), so the first launch on
# another Mac needs Control-click -> Open once to get past Gatekeeper. There
# is no way around that without a paid Developer ID to notarize with.
set -eu

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/.." && pwd)"
venv_py="$root/.venv/bin/python3"

echo "--- building the on-device speech helper ---"
"$root/capture/backends/native/build.sh"

echo "--- running PyInstaller ---"
cd "$here"
rm -rf build dist
"$venv_py" -m PyInstaller --noconfirm --clean CampusCaptions.spec

app="$here/dist/Campus Captions.app"

echo "--- ad-hoc signing the app bundle ---"
# Not --deep: the bundled speech_helper is already signed with its own
# entitlements (native/build.sh); a deep re-sign here would overwrite that
# signature with one that has no entitlements at all, and it would SIGKILL at
# launch. Signing only the outer app is what Gatekeeper checks on open.
codesign --force --sign - "$app"

echo
echo "built: $app"
echo "zip it for sharing with:"
echo "  ditto -c -k --sequesterRsrc --keepParent \"$app\" \"$here/dist/CampusCaptions.zip\""
