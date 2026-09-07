# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Campus Captions macOS menu bar app.

Build with macapp/build.sh, not `pyinstaller` directly - that script also
(re)builds and signs the Swift speech_helper this bundles, and ad-hoc signs
the finished .app afterward. See macapp/menubar.py for what actually runs.
"""

from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821 - PyInstaller injects SPECPATH

# A .spec is exec'd, not imported, and ROOT is not on sys.path when it runs -
# so read the one place the version lives rather than importing it.
_version_ns: dict = {}
exec((ROOT / "launcher" / "version.py").read_text(encoding="utf-8"), _version_ns)
VERSION = _version_ns["VERSION"]

# Only the *example* config ships in the bundle. The real event.toml, the
# .env with the keys, and any uploaded images live in
# ~/Library/Application Support/Campus Captions/ (see relay/config.py) - the
# app creates them on first run - so nothing personal or secret travels with
# a zipped .app, and writing settings never breaks the bundle's signature.
datas = [
    (str(ROOT / "viewer"), "viewer"),
    (str(ROOT / "config" / "event.example.toml"), "config"),
    (str(ROOT / "config" / "keyterms.txt"), "config"),
    (str(ROOT / "config" / "blocklist.txt"), "config"),
    (str(ROOT / "config" / "mock_script.txt"), "config"),
    (str(ROOT / "macapp" / "icons" / "menubar_idle.png"), "macapp/icons"),
    (str(ROOT / "macapp" / "icons" / "menubar_streaming.png"), "macapp/icons"),
    (str(ROOT / "macapp" / "icons" / "menubar_inactive.png"), "macapp/icons"),
]

# The Swift on-device speech helper, when one has been built. PyInstaller
# errors on a `datas` entry that does not exist, and build.sh normally builds
# it first - but it needs the macOS 26 SDK, which not every machine (or CI
# runner) has. Omitting it produces an app that works on everything except
# apple_speech, which apple_speech.py reports as "speech_helper is missing
# from this app bundle" rather than failing mysteriously. Better than no
# macOS build at all; worse than a complete one, so build.sh only skips it
# when told to.
_helper = ROOT / "capture" / "backends" / "native" / "build" / "speech_helper"
if _helper.exists():
    datas.append((str(_helper), "capture/backends/native/build"))
else:
    print("[spec] WARNING: no speech_helper - apple_speech will not work "
          "in this bundle")

hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
]

a = Analysis(  # noqa: F821
    [str(ROOT / "macapp" / "menubar.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Campus Captions",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Campus Captions",
)

app = BUNDLE(  # noqa: F821
    coll,
    name="Campus Captions.app",
    icon=str(ROOT / "macapp" / "icons" / "AppIcon.icns"),
    bundle_identifier="edu.faulkner.campus-captions",
    info_plist={
        "CFBundleName": "Campus Captions",
        "CFBundleDisplayName": "Campus Captions",
        "CFBundleShortVersionString": VERSION,
        "LSUIElement": True,
        "NSMicrophoneUsageDescription":
            "Campus Captions needs the microphone to generate live captions.",
        "NSSpeechRecognitionUsageDescription":
            "Campus Captions uses on-device speech recognition to generate "
            "live captions. Audio never leaves this Mac.",
        "NSHighResolutionCapable": True,
    },
)
