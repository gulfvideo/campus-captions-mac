#!/bin/sh
# Builds speech_helper and ad-hoc signs it with the Speech Recognition
# entitlement. Run automatically by apple_speech.py the first time the
# backend opens, or by hand: capture/backends/native/build.sh
set -eu

here="$(cd "$(dirname "$0")" && pwd)"
out="$here/build"
mkdir -p "$out"
bin="$out/speech_helper"

swiftc -O -parse-as-library "$here/speech_helper.swift" \
    -o "$bin" \
    -Xlinker -sectcreate \
    -Xlinker __TEXT \
    -Xlinker __info_plist \
    -Xlinker "$here/Info.plist"

# --options runtime (hardened runtime) is required for notarization (see
# macapp/build.sh) and costs nothing here: tested with
# disable-library-validation removed from Speech.entitlements, the helper
# still loads the Speech frameworks and transcribes under it.
codesign --force --sign - --options runtime --entitlements "$here/Speech.entitlements" "$bin"

echo "built $bin"
