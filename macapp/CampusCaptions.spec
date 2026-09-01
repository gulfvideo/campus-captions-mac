# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Campus Captions macOS menu bar app.

Build with macapp/build.sh, not `pyinstaller` directly - that script also
(re)builds and signs the Swift speech_helper this bundles, and ad-hoc signs
the finished .app afterward. See macapp/menubar.py for what actually runs.
"""

from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821 - PyInstaller injects SPECPATH

datas = [
    (str(ROOT / "viewer"), "viewer"),
    (str(ROOT / "config" / "event.toml"), "config"),
    (str(ROOT / "config" / "keyterms.txt"), "config"),
    (str(ROOT / "config" / "mock_script.txt"), "config"),
    (str(ROOT / "capture" / "backends" / "native" / "build" / "speech_helper"),
     "capture/backends/native/build"),
    (str(ROOT / "macapp" / "icons" / "menubar_idle.png"), "macapp/icons"),
    (str(ROOT / "macapp" / "icons" / "menubar_streaming.png"), "macapp/icons"),
    (str(ROOT / "macapp" / "icons" / "menubar_inactive.png"), "macapp/icons"),
]

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
        "CFBundleShortVersionString": "0.1.0",
        "LSUIElement": True,
        "NSMicrophoneUsageDescription":
            "Campus Captions needs the microphone to generate live captions.",
        "NSSpeechRecognitionUsageDescription":
            "Campus Captions uses on-device speech recognition to generate "
            "live captions. Audio never leaves this Mac.",
        "NSHighResolutionCapable": True,
    },
)
