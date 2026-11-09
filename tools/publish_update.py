#!/usr/bin/env python3
"""Builds the `latest.json` an installed app checks for updates.

Run after the release workflow has produced its assets, pointed at the
directory holding them:

    python tools/publish_update.py --assets dist/

It reads the real files rather than being told about them - the size and
SHA-256 in the manifest have to describe the bytes that were actually built,
and a hand-maintained manifest is how you ship a checksum for the previous
release. Uploading is deliberately not this script's job; see the "Publish the
update manifest" step in .github/workflows/release.yml.

Asset names come from the release workflow:

    CampusCaptions-<version>-AppleSilicon.dmg  -> macos-arm64
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from launcher.version import VERSION  # noqa: E402
from relay.updater import REPO  # noqa: E402

# Suffix -> the platform key relay/updater.py:platform_key() produces.
PATTERNS = (
    ("-AppleSilicon.dmg", "macos-arm64"),
)


def digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            sha.update(chunk)
    return sha.hexdigest()


def classify(name: str) -> str | None:
    for suffix, key in PATTERNS:
        if name.endswith(suffix):
            return key
    return None


def build(assets: Path, base: str, version: str, notes: str) -> dict:
    base = base.rstrip("/")
    builds: dict[str, dict] = {}
    for path in sorted(assets.iterdir()):
        if not path.is_file():
            continue
        key = classify(path.name)
        if key is None:
            continue
        if key in builds:
            raise SystemExit(
                f"two files claim to be the {key} build: "
                f"{builds[key]['url'].rsplit('/', 1)[-1]} and {path.name}")
        builds[key] = {
            "url": f"{base}/v{version}/{path.name}",
            "sha256": digest(path),
            "size": path.stat().st_size,
        }

    if not builds:
        raise SystemExit(f"no release assets found in {assets}")
    return {"version": version, "notes": notes, "builds": builds}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True,
                        help="directory holding the built .dmg files")
    parser.add_argument("--base", default=f"https://github.com/{REPO}/releases/download",
                        help="base URL; the files are served from <base>/v<version>/")
    parser.add_argument("--version", default=VERSION)
    parser.add_argument("--notes", default="", help="what changed, shown in the app")
    parser.add_argument("--out", type=Path, default=Path("latest.json"))
    args = parser.parse_args()

    manifest = build(args.assets, args.base, args.version, args.notes)
    args.out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"{args.out}: {manifest['version']}")
    for key, entry in manifest["builds"].items():
        print(f"  {key:<16} {entry['size'] / 1048576:6.1f} MB  "
              f"{entry['sha256'][:16]}...  {entry['url']}")

    # The app only offers a build matching the machine it is running on, so a
    # manifest missing one silently leaves those testers on the old version
    # with no indication anything is wrong.
    missing = [k for _, k in PATTERNS if k not in manifest["builds"]]
    if missing:
        print(f"\nWARNING: no build for {', '.join(missing)} - "
              f"those testers will not be offered this release", file=sys.stderr)


if __name__ == "__main__":
    main()
