# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Campus Captions engine: relay + capture, no UI.

The front end is macapp/CampusCaptions.swift, a native menu bar app that
bundles this as a helper in Contents/Resources/engine and talks to it over
127.0.0.1. So there is no BUNDLE step here and no rumps or pyobjc in the
analysis - this is a console executable that the shell spawns.

Build with macapp/build.sh, not `pyinstaller` directly.
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
    [str(ROOT / "macapp" / "engine.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    # The front end is Swift now, so none of this belongs in the engine.
    excludes=["rumps", "AppKit", "Foundation", "objc", "PyObjCTools"],
    noarchive=False,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Campus Captions Engine",
    debug=False,
    strip=False,
    upx=False,
    # A console executable: the shell reads its first stdout line to learn the
    # port, and leaves stderr attached so a traceback reaches the log.
    console=True,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="engine",
)
