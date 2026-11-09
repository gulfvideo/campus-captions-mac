"""Checks for a newer build on GitHub Releases and installs it.

The manifest is one JSON document, attached to each release as `latest.json`:

    {
      "version": "0.4.0-beta",
      "notes":   "what changed",
      "builds": {
        "macos-arm64":    {"url": "...", "sha256": "...", "size": 78123456}
      }
    }

Keyed by platform because PyInstaller freezes for the host architecture; a
machine whose key is absent is told there is no build rather than handed one
that cannot launch.

What guarantees the download is the build we meant to ship:

  * HTTPS to GitHub, and no redirect anywhere else. GitHub serves release
    assets from a githubusercontent.com host after a redirect, so that host
    is allowed too.
  * The size and SHA-256 from the manifest, checked before anything is opened.
  * If the running app carries a Team ID - that is, once it is signed with a
    Developer ID certificate rather than ad-hoc - the replacement must carry the
    same one, checked with codesign. That is the real integrity guarantee, it is
    enforced by macOS rather than by this file, and it arrives for free with the
    certificate. While the app is ad-hoc signed there is no such identity to
    check against, and the manifest is the whole trust root; `TRUST_NOTE` below
    is what the status page says about that.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import platform
import re
import plistlib
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from launcher.version import VERSION, VERSION_NUMBER, _version_tuple

REPO = "gulfvideo/campus-captions-mac"
DEFAULT_MANIFEST_URL = f"https://github.com/{REPO}/releases/latest/download/latest.json"

# Only ever fetch from GitHub. A manifest that points its download at another
# host is not a redirect worth following - it is either a mistake or the
# interesting half of an attack. The githubusercontent hosts are where GitHub
# redirects release-asset downloads.
ALLOWED_HOSTS = ("github.com", "objects.githubusercontent.com",
                 "release-assets.githubusercontent.com")

CONNECT_TIMEOUT = 15.0
MANIFEST_MAX_BYTES = 64 * 1024        # a JSON document, not a payload
DOWNLOAD_MAX_BYTES = 600 * 1024 * 1024
CHUNK = 256 * 1024

TRUST_NOTE = (
    "this build is ad-hoc signed, so an update is trusted on its checksum "
    "alone and macOS will ask each tester for microphone access again "
    "afterwards - a Developer ID certificate fixes both"
)


_UNSET = object()      # distinct from None, which is a real answer here


class UpdateError(Exception):
    """Anything that should stop an update and be shown to the operator."""


def platform_key() -> str:
    """Which build in the manifest belongs to this machine."""
    machine = platform.machine().lower()
    if sys.platform == "darwin" and machine in ("arm64", "aarch64"):
        return "macos-arm64"
    return f"{sys.platform}-{machine or 'unknown'}"


# launcher/version.py parses leniently on purpose - it runs inside a PyInstaller
# .spec, where a ValueError surfaces as a failed build pointing at a version
# string. Here leniency is the wrong trade: "0.4.0; rm -rf /" parsed forgivingly
# becomes 0.4.0 and *offers itself as an upgrade*. A version this does not
# recognise is not an update.
_STRICT_VERSION = re.compile(r"^\d{1,5}(\.\d{1,5}){0,3}$")


def is_newer(candidate: str, current: str = VERSION) -> bool:
    """Whether `candidate` is a version worth offering.

    Compares the numeric part only. Every release of this project is tagged
    -beta (see launcher/version.py), so the suffix carries no ordering: the day
    it does, a 0.4.0 shipping after 0.4.0-beta would need a real precedence
    rule, and this returns False rather than offering a downgrade.
    """
    def number(value: str) -> str:
        return str(value or "").strip().split("-", 1)[0].split("+", 1)[0]

    left, right = number(candidate), number(current)
    if not _STRICT_VERSION.match(left) or not _STRICT_VERSION.match(right):
        return False
    return _version_tuple(left) > _version_tuple(right)


@dataclass
class Build:
    url: str
    sha256: str
    size: int


@dataclass
class Manifest:
    version: str
    notes: str = ""
    builds: dict[str, Build] = field(default_factory=dict)

    def for_this_machine(self) -> Build | None:
        return self.builds.get(platform_key())


def _https_host_ok(url: str) -> bool:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname in ALLOWED_HOSTS


def parse_manifest(raw: bytes) -> Manifest:
    """Turn the fetched document into a Manifest, or say why it is not one.

    Deliberately strict. This decides what gets written over the app the
    operator runs a service on, so a field that is the wrong shape is a reason
    to stop rather than to guess.
    """
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise UpdateError(f"update manifest is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise UpdateError("update manifest is not a JSON object")

    version = str(data.get("version") or "").strip()
    if not version:
        raise UpdateError("update manifest has no version")

    builds: dict[str, Build] = {}
    for key, value in (data.get("builds") or {}).items():
        if not isinstance(value, dict):
            continue
        url = str(value.get("url") or "").strip()
        digest = str(value.get("sha256") or "").strip().lower()
        size = value.get("size")
        if not _https_host_ok(url):
            continue                      # not ours; ignore rather than fetch
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            continue
        if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= DOWNLOAD_MAX_BYTES:
            continue
        builds[str(key)] = Build(url=url, sha256=digest, size=size)

    return Manifest(version=version,
                    notes=str(data.get("notes") or "").strip()[:4000],
                    builds=builds)


def request_headers() -> dict[str, str]:
    return {"User-Agent": f"CampusCaptions/{VERSION}"}


def _fetch(url: str, limit: int) -> bytes:
    if not _https_host_ok(url):
        raise UpdateError(f"refusing to fetch update data from {url}")
    request = urllib.request.Request(url, headers=request_headers())
    try:
        with urllib.request.urlopen(request, timeout=CONNECT_TIMEOUT) as response:
            # A redirect that left the allowed host would have been followed by
            # urllib before we ever saw it, so check where we actually landed.
            if not _https_host_ok(response.geturl()):
                raise UpdateError("update request was redirected off the update host")
            return response.read(limit + 1)[:limit + 1]
    except urllib.error.HTTPError as exc:
        raise UpdateError(_http_message(exc.code)) from exc
    except UpdateError:
        raise
    except Exception as exc:
        # urllib's own wording here is DNS-speak ("nodename nor servname
        # provided"), and this lands verbatim in front of an operator during
        # a service. Say the thing they can act on.
        from urllib.parse import urlparse
        host = urlparse(url).hostname or "the update host"
        raise UpdateError(
            f"could not reach {host} - check this computer's internet "
            f"connection") from exc


def _http_message(code: int) -> str:
    """What an operator can act on, rather than a status number."""
    if code == 404:
        return "no release carries an update manifest yet"
    return f"the update host answered {code}"


def team_id(bundle: Path) -> str | None:
    """The Developer ID team the bundle is signed with, if any.

    None for an ad-hoc signature, which is what this project ships until a
    certificate exists - see TRUST_NOTE.
    """
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.run(["codesign", "-dv", "--verbose=2", str(bundle)],
                             capture_output=True, text=True, timeout=30)
    except Exception:
        return None
    for line in (out.stderr or "").splitlines():
        if line.startswith("TeamIdentifier="):
            value = line.split("=", 1)[1].strip()
            return None if value in ("", "not set") else value
    return None


def running_bundle() -> Path | None:
    """The .app this process is running from, or None from a source checkout."""
    if sys.platform != "darwin":
        return None
    for parent in Path(sys.executable).resolve().parents:
        if parent.suffix == ".app":
            return parent
    return None


def verify_download(path: Path, build: Build) -> None:
    """Size then digest, before anything opens the file.

    Size first because it is free and catches the overwhelmingly common case -
    a truncated download - without reading 75 MB twice.
    """
    actual = path.stat().st_size
    if actual != build.size:
        raise UpdateError(
            f"download is {actual} bytes, manifest says {build.size}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    if digest.hexdigest() != build.sha256:
        raise UpdateError("download does not match the checksum in the manifest")


def download(build: Build, into: Path, on_progress=None) -> Path:
    """Fetch one build to `into`, verify it, and return the file.

    Written to a .part and renamed only once verified, so an interrupted
    download can never be mistaken for a finished one on the next launch.
    """
    if not _https_host_ok(build.url):
        raise UpdateError(f"refusing to download from {build.url}")
    into.mkdir(parents=True, exist_ok=True)
    final = into / build.url.rsplit("/", 1)[-1]
    partial = final.with_suffix(final.suffix + ".part")
    request = urllib.request.Request(build.url, headers=request_headers())
    got = 0
    try:
        with urllib.request.urlopen(request, timeout=CONNECT_TIMEOUT) as response:
            if not _https_host_ok(response.geturl()):
                raise UpdateError("download was redirected off the update host")
            with partial.open("wb") as handle:
                while chunk := response.read(CHUNK):
                    got += len(chunk)
                    if got > build.size:
                        # Stop rather than fill the disk: a body longer than the
                        # manifest promised is already not the file we wanted.
                        raise UpdateError("download is larger than the manifest says")
                    handle.write(chunk)
                    if on_progress:
                        on_progress(got, build.size)
    except UpdateError:
        partial.unlink(missing_ok=True)
        raise
    except urllib.error.HTTPError as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(_http_message(exc.code)) from exc
    except Exception as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"download failed: {exc}") from exc

    verify_download(partial, build)
    partial.replace(final)
    return final


# Waits for the app to quit, swaps the bundle, relaunches - and puts the old one
# back if the copy fails, because the failure mode to design against is a tester
# left with no app at all the morning of a service.
SWAP_SCRIPT = r"""#!/bin/sh
target="$1"; staged="$2"; pid="$3"
waited=0
while kill -0 "$pid" 2>/dev/null; do
  sleep 0.25
  waited=$((waited + 1))
  [ "$waited" -gt 240 ] && exit 1
done
backup="$target.replaced"
rm -rf "$backup"
mv "$target" "$backup" || exit 1
if ditto "$staged" "$target"; then
  rm -rf "$backup" "$staged"
  open "$target"
else
  rm -rf "$target"
  mv "$backup" "$target"
  open "$target"
  exit 1
fi
"""


def app_from_disk_image(dmg: Path, staging: Path, expect_team: str | None) -> Path:
    """Copy the .app out of a downloaded .dmg, checking it on the way.

    The disk image is what the release workflow already builds, so this reads
    what is actually shipped rather than asking the pipeline to produce a second
    format nobody opens.
    """
    mount = Path(tempfile.mkdtemp(prefix="cc-update-"))
    attached = False
    try:
        subprocess.run(["hdiutil", "attach", str(dmg), "-nobrowse", "-readonly",
                        "-mountpoint", str(mount)],
                       check=True, capture_output=True, timeout=120)
        attached = True
        apps = sorted(mount.glob("*.app"))
        if not apps:
            raise UpdateError("the downloaded disk image holds no application")
        source = apps[0]

        check = subprocess.run(["codesign", "--verify", "--strict", str(source)],
                               capture_output=True, text=True, timeout=120)
        if check.returncode != 0:
            raise UpdateError(
                f"the downloaded app is not validly signed: "
                f"{(check.stderr or '').strip()[:200]}")

        # Only meaningful once this project ships with a Developer ID
        # certificate. Until then the running app has no team to match and this
        # is skipped - see TRUST_NOTE.
        if expect_team:
            found = team_id(source)
            if found != expect_team:
                raise UpdateError(
                    f"the downloaded app is signed by {found or 'nobody'}, "
                    f"not by {expect_team}")

        staging.mkdir(parents=True, exist_ok=True)
        staged = staging / source.name
        if staged.exists():
            shutil.rmtree(staged, ignore_errors=True)
        subprocess.run(["ditto", str(source), str(staged)],
                       check=True, capture_output=True, timeout=600)
        return staged
    except subprocess.CalledProcessError as exc:
        raise UpdateError(
            f"could not open the downloaded disk image: "
            f"{(exc.stderr or b'').decode('utf-8', 'replace').strip()[:200]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise UpdateError("opening the downloaded disk image timed out") from exc
    finally:
        if attached:
            with contextlib.suppress(Exception):
                subprocess.run(["hdiutil", "detach", str(mount), "-quiet"],
                               capture_output=True, timeout=60)
        with contextlib.suppress(Exception):
            mount.rmdir()


def bundle_version(app: Path) -> str | None:
    """CFBundleShortVersionString out of a staged .app.

    Last check before the swap: the manifest said what this build is, and the
    bundle itself has to agree. A mismatch means the manifest and the file it
    points at have drifted apart, which is exactly when not to overwrite the
    working copy.
    """
    try:
        with (app / "Contents" / "Info.plist").open("rb") as handle:
            return str(plistlib.load(handle).get("CFBundleShortVersionString") or "") or None
    except Exception:
        return None


class Updater:
    """One per relay session. Operator-driven, never silent.

    Nothing here runs on its own except the check. Downloading 75 MB over a
    venue's wifi during a service, and restarting the app that is currently
    captioning it, are both decisions for whoever is standing at the desk -
    so `install` refuses outright while the event is live, and the rest waits
    to be asked.
    """

    def __init__(self, manifest_url: str = "", staging: Path | None = None,
                 is_live=None):
        self.manifest_url = (manifest_url or "").strip() or DEFAULT_MANIFEST_URL
        self.staging = staging or (Path.home() / "Library" / "Application Support"
                                   / "Campus Captions" / "updates")
        # Called with no arguments; True means captions are running right now.
        self._is_live = is_live or (lambda: False)
        self.state = "idle"      # idle|checking|available|downloading|ready|installing|error
        self.error: str | None = None
        self.available: Manifest | None = None
        self.downloaded: Path | None = None
        self.staged: Path | None = None
        self.progress = 0.0
        self._team_cached: object = _UNSET
        self._lock = asyncio.Lock()

    # -- reporting -------------------------------------------------------

    def status(self) -> dict:
        build = self.available.for_this_machine() if self.available else None
        return {
            "current": VERSION,
            "state": self.state,
            "error": self.error,
            "platform": platform_key(),
            "available": self.available.version if self.available else None,
            "notes": self.available.notes if self.available else "",
            "size": build.size if build else None,
            "progress": round(self.progress, 3),
            "signed": bool(self._team),
            # Shown next to the Install button rather than buried in a log.
            "trust_note": "" if self._team else TRUST_NOTE,
            "can_install": self.state == "ready" and not self._is_live(),
            "blocked_by_event": bool(self._is_live()),
        }

    @property
    def _team(self) -> str | None:
        """Cached: this cannot change while the app is running, and status()
        is polled by the settings page - spawning codesign on every poll would
        be a subprocess a second for an answer that is fixed at launch."""
        if self._team_cached is _UNSET:
            bundle = running_bundle()
            self._team_cached = team_id(bundle) if bundle else None
        return self._team_cached

    def _fail(self, exc: Exception) -> None:
        self.state = "error"
        self.error = str(exc)
        print(f"[update] {self.error}")

    # -- steps -----------------------------------------------------------

    async def check(self) -> dict:
        """Fetch the manifest and decide whether anything is on offer."""
        if self._lock.locked():
            return self.status()
        async with self._lock:
            self.state, self.error = "checking", None
            loop = asyncio.get_running_loop()
            try:
                raw = await loop.run_in_executor(
                    None, _fetch, self.manifest_url, MANIFEST_MAX_BYTES)
                manifest = parse_manifest(raw)
            except UpdateError as exc:
                self._fail(exc)
                return self.status()

            if not is_newer(manifest.version):
                self.available = None
                self.state = "idle"
                print(f"[update] {VERSION} is current")
                return self.status()

            build = manifest.for_this_machine()
            if build is None:
                self.available = None
                self.state = "idle"
                print(f"[update] {manifest.version} exists but has no "
                      f"{platform_key()} build")
                return self.status()

            self.available = manifest
            self.state = "available"
            print(f"[update] {manifest.version} available "
                  f"({build.size / 1048576:.0f} MB)")
            return self.status()

    async def fetch(self) -> dict:
        """Download the offered build and verify it. Does not install."""
        if self._lock.locked():
            return self.status()
        async with self._lock:
            build = self.available.for_this_machine() if self.available else None
            if build is None:
                self._fail(UpdateError("nothing to download - check first"))
                return self.status()

            self.state, self.error, self.progress = "downloading", None, 0.0

            def progress(got: int, total: int) -> None:
                self.progress = got / total if total else 0.0

            loop = asyncio.get_running_loop()
            try:
                self.downloaded = await loop.run_in_executor(
                    None, download, build, self.staging, progress)
                if sys.platform == "darwin":
                    self.staged = await loop.run_in_executor(
                        None, app_from_disk_image,
                        self.downloaded, self.staging, self._team)
                    found = bundle_version(self.staged)
                    if found and self.available and found != self.available.version:
                        raise UpdateError(
                            f"the download says it is {found}, the manifest "
                            f"said {self.available.version}")
            except UpdateError as exc:
                self._fail(exc)
                return self.status()

            self.progress = 1.0
            self.state = "ready"
            print(f"[update] {self.available.version} downloaded and verified")
            return self.status()

    def install(self) -> dict:
        """Hand the swap to a helper and ask the app to quit.

        The bundle cannot replace itself while it is running, so a small script
        outlives this process, waits for it to go, copies the new app over the
        old one and reopens it - putting the old one back if the copy fails.
        """
        if self.state != "ready" or not self.staged:
            self._fail(UpdateError("nothing is ready to install"))
            return self.status()
        if self._is_live():
            self._fail(UpdateError(
                "captions are live - stop the event before updating"))
            return self.status()

        target = running_bundle()
        if target is None:
            self._fail(UpdateError(
                "this is a source checkout, not an installed app - "
                "git pull instead"))
            return self.status()

        script = self.staging / "swap.sh"
        script.write_text(SWAP_SCRIPT, encoding="utf-8")
        script.chmod(0o755)
        try:
            subprocess.Popen(
                [str(script), str(target), str(self.staged), str(os.getpid())],
                start_new_session=True,          # outlives this process
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            self._fail(UpdateError(f"could not start the installer: {exc}"))
            return self.status()

        self.state = "installing"
        print(f"[update] installing {self.available.version}; the app will "
              f"restart itself")
        return self.status()
