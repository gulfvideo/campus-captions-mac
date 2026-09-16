#!/bin/sh
# Builds Campus Captions.app: a native Swift front end wrapping the Python
# engine.
#
#   .venv/bin/python3 -m pip install -r requirements.txt -r macapp/requirements-build.txt \
#       -r requirements-whisper.txt
#   macapp/build.sh
#
# Output: macapp/dist/Campus Captions.app
#
#   Contents/MacOS/Campus Captions     the Swift shell (menu bar + windows)
#   Contents/Resources/engine/         the frozen relay + capture client
#   Contents/Resources/*.png           menu bar icons, read by the shell
#
# The shell is what the operator sees and what macOS signs as the app; the
# engine is a helper it spawns and can restart without the menu bar going with
# it. See macapp/CampusCaptions.swift.
#
# The app is ad-hoc signed (no Apple Developer ID), so the first launch on
# another Mac needs Control-click -> Open once to get past Gatekeeper.
#
#   macapp/build.sh [--skip-helper]
#
# --skip-helper leaves capture/backends/native/build/speech_helper alone, for a
# caller that has already built it (CI does, so it can report a too-old SDK in
# its own step). A bundle with no helper cannot do on-device speech on any Mac.
set -eu

skip_helper=0
for arg in "$@"; do
    case "$arg" in
        --skip-helper) skip_helper=1 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

# The key this repository was developed against. Never ship it.
dev_activation_key="VNnDLSsu74JRfZLCsgOYANpbnqfLtXAvuGJsDLfGqRU="

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/.." && pwd)"
venv_py="$root/.venv/bin/python3"

if [ "$skip_helper" -eq 0 ]; then
    echo "--- building the on-device speech helper ---"
    "$root/capture/backends/native/build.sh"
fi

cd "$here"
rm -rf build dist
mkdir -p build

# The activation key compiled into the shell decides which codes unlock this
# build. Shipping the one used during development would accept codes issued by
# whoever has that throwaway private half, so refuse rather than warn.
if grep -q "REPLACE_WITH_BASE64_ED25519_PUBLIC_KEY\|$dev_activation_key" CampusCaptions.swift; then
    echo "refusing to build: Activation.publicKey is still a development key." >&2
    echo "Run macapp/make-activation-keys.sh and paste the public half in." >&2
    exit 1
fi

echo "--- building the Swift shell ---"
# -parse-as-library because the file uses @main rather than top-level code.
swiftc -O -parse-as-library CampusCaptions.swift -o build/shell

echo "--- freezing the engine ---"
"$venv_py" -m PyInstaller --noconfirm --clean Engine.spec

app="$here/dist/Campus Captions.app"
echo "--- assembling $app ---"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
cp build/shell "$app/Contents/MacOS/Campus Captions"
cp Shell-Info.plist "$app/Contents/Info.plist"
cp icons/AppIcon.icns "$app/Contents/Resources/AppIcon.icns"
# Flat in Resources: the shell looks them up with Bundle.main.url(forResource:).
cp icons/menubar_idle.png icons/menubar_streaming.png icons/menubar_inactive.png \
   "$app/Contents/Resources/"
# PyInstaller's onedir output, whole, under the name the shell expects.
cp -R dist/engine "$app/Contents/Resources/engine"

echo "--- signing ---"
# Deliberately NOT --options runtime on the engine. Hardened runtime turns on
# library validation, and PyInstaller's nested Python.framework carries its own
# separate ad-hoc signature - so dlopen refuses it with "mapping process and
# mapped file (non-platform) have different Team IDs" and the engine dies before
# it prints anything. The speech helper can use it because it is one flat binary
# with nothing nested.
#
# With a real Developer ID this becomes signing every nested binary with the
# same identity first (then hardened runtime is fine, because the Team IDs
# match) or adding com.apple.security.cs.disable-library-validation. Neither is
# reachable ad-hoc, and notarization is what needs hardened runtime anyway.
codesign --force --sign - "$app/Contents/Resources/engine/Campus Captions Engine"
codesign --force --sign - "$app"

echo
echo "built: $app"
codesign --verify --strict "$app" && echo "signature: ok"
echo "zip it for sharing with:"
echo "  ditto -c -k --sequesterRsrc --keepParent \"$app\" \"$here/dist/CampusCaptions.zip\""
