#!/bin/sh
# Builds Campus Captions.app for distribution to beta testers.
#
#   .venv/bin/python3 -m pip install -r requirements.txt -r macapp/requirements-build.txt \
#       -r requirements-whisper.txt
#   macapp/build.sh
#
# requirements-whisper.txt is included even though the source-tree README
# calls it optional: there, skipping it keeps an Apple-speech-only install lean,
# but the packaged app advertises "Whisper" as a clickable backend on the
# status page with no terminal to `pip install` from - if the wheel isn't in
# this venv when PyInstaller runs, picking it there just silently never opens.
#
# Output: macapp/dist/Campus Captions.app
#
# Signing: if a "Developer ID Application" certificate is in the keychain
# (or CODESIGN_IDENTITY names one), every binary and the bundle are signed
# with it, hardened runtime on, ready for macapp/make_dmg.sh to notarize.
# Without one - a contributor's Mac, or CI with no certificate - the app is
# ad-hoc signed as before, and the first launch on another Mac needs
# Control-click -> Open once to get past Gatekeeper.
#
#   macapp/build.sh [--skip-helper]
#
# --skip-helper leaves capture/backends/native/build/speech_helper alone,
# for a caller that has already built it (CI does, so it can report a
# too-old SDK in its own step rather than inside this script). If no helper
# binary exists at all the app still builds - the spec omits it and
# apple_speech.py says so at runtime - so never pass this without knowing
# why: a bundle with no helper cannot do on-device speech on any Mac.
set -eu

skip_helper=0
for arg in "$@"; do
    case "$arg" in
        --skip-helper) skip_helper=1 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/.." && pwd)"
venv_py="$root/.venv/bin/python3"

if [ "$skip_helper" -eq 0 ]; then
    echo "--- building the on-device speech helper ---"
    "$root/capture/backends/native/build.sh"
fi

if [ -z "${CODESIGN_IDENTITY:-}" ]; then
    CODESIGN_IDENTITY="$(security find-identity -v -p codesigning 2>/dev/null \
        | sed -n 's/.*"\(Developer ID Application: .*\)"$/\1/p' | head -1)"
fi
export CODESIGN_IDENTITY
if [ -n "$CODESIGN_IDENTITY" ]; then
    echo "--- signing with: $CODESIGN_IDENTITY ---"
    # PyInstaller only warns when signing fails; a half-signed bundle would
    # then be found by notarization minutes later, or by a tester.
    export PYINSTALLER_STRICT_BUNDLE_CODESIGN_ERROR=1
else
    echo "--- no Developer ID certificate found: ad-hoc signing ---"
fi

echo "--- running PyInstaller ---"
cd "$here"
rm -rf build dist
"$venv_py" -m PyInstaller --noconfirm --clean CampusCaptions.spec

app="$here/dist/Campus Captions.app"

if [ -z "$CODESIGN_IDENTITY" ]; then
    echo "--- ad-hoc signing the app bundle ---"
    # PyInstaller already ad-hoc signed every binary inside; this re-signs
    # the outer app, which is what Gatekeeper checks on open.
    codesign --force --sign - "$app"
fi
codesign --verify --deep --strict "$app"

echo
echo "built: $app"
echo "zip it for sharing with:"
echo "  ditto -c -k --sequesterRsrc --keepParent \"$app\" \"$here/dist/CampusCaptions.zip\""
