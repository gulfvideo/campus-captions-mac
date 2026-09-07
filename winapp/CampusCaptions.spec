# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Campus Captions Windows tray app.

Build with winapp/build.ps1, not `pyinstaller` directly - that script also
builds the Inno Setup installer around the result. See winapp/tray.py for what
actually runs.

Output is a onedir bundle (winapp/dist/Campus Captions/), not a single .exe.
A onefile build re-extracts the whole interpreter to a temp folder on every
launch, which on a booth PC with an aggressive antivirus means a scan of a
hundred megabytes each time someone starts the app - twenty seconds of a
double-click doing nothing visible. The installer hides the folder inside
Program Files anyway, so nobody sees it.
"""

from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821 - PyInstaller injects SPECPATH

# A .spec is exec'd, not imported, and ROOT is not on sys.path when it runs -
# so read the one place the version lives rather than importing it.
_version_ns: dict = {}
exec((ROOT / "launcher" / "version.py").read_text(encoding="utf-8"), _version_ns)
VERSION = _version_ns["VERSION"]
VERSION_TUPLE = _version_ns["VERSION_TUPLE"]

# Windows' VERSIONINFO resource: what the .exe's Properties > Details tab
# shows, what Task Manager labels the process, and part of what SmartScreen
# weighs. Generated here rather than committed, so it cannot disagree with
# launcher/version.py. PyInstaller wants it as a file on disk.
VERSION_FILE = ROOT / "winapp" / "build" / "version_info.txt"
VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
VERSION_FILE.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={VERSION_TUPLE},
    prodvers={VERSION_TUPLE},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'Campus Captions'),
        StringStruct('FileDescription', 'Campus Captions - live event captioning'),
        StringStruct('FileVersion', '{VERSION}'),
        StringStruct('InternalName', 'CampusCaptions'),
        StringStruct('OriginalFilename', 'Campus Captions.exe'),
        StringStruct('ProductName', 'Campus Captions'),
        StringStruct('ProductVersion', '{VERSION}')])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")

# Only the *example* config ships. The real event.toml, the .env with the
# keys, and any uploaded images live in %LOCALAPPDATA%\Campus Captions (see
# relay/config.py) - the app creates them on first run - so nothing personal
# or secret travels with a copy of the installer, and writing settings never
# needs Program Files to be writable.
datas = [
    (str(ROOT / "viewer"), "viewer"),
    (str(ROOT / "config" / "event.example.toml"), "config"),
    (str(ROOT / "config" / "keyterms.txt"), "config"),
    (str(ROOT / "config" / "blocklist.txt"), "config"),
    (str(ROOT / "config" / "mock_script.txt"), "config"),
    (str(ROOT / "winapp" / "icons" / "tray_idle.ico"), "winapp/icons"),
    (str(ROOT / "winapp" / "icons" / "tray_streaming.ico"), "winapp/icons"),
    (str(ROOT / "winapp" / "icons" / "tray_inactive.ico"), "winapp/icons"),
]

# uvicorn picks its protocol implementations by string at runtime, so static
# analysis never sees them. Identical list to macapp/CampusCaptions.spec.
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
    # The Windows backend of pystray, chosen at import time the same way.
    "pystray._win32",
]

excludes = [
    # apple_speech cannot run here at all (capture/backends/support.py), and
    # its Swift helper is macOS-only. Nothing imports these on Windows; this
    # is belt and braces so a stray import cannot pull PyObjC into the build.
    "rumps",
    "Foundation",
    "AppKit",
]

a = Analysis(  # noqa: F821
    [str(ROOT / "winapp" / "tray.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    excludes=excludes,
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
    # No console window. The tray icon is the app's only visible surface, and
    # a black terminal behind it is the single thing that most makes a
    # volunteer think something has gone wrong.
    console=False,
    icon=str(ROOT / "winapp" / "icons" / "app.ico"),
    version=str(VERSION_FILE),
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Campus Captions",
)
