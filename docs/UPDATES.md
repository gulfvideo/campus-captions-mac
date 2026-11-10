# Shipping updates

The repository is public and MIT licensed, so builds live where they are made:
on the GitHub Releases page. There is no update server, no credential and no
tester code. An installed app checks one JSON document on the latest release.

```
release tag v0.4.0-beta
        │
        └── macos job ──►  GitHub release v0.4.0-beta
                             CampusCaptions-0.4.0-beta-AppleSilicon.dmg
                             latest.json   ← size, SHA-256 and URL of the .dmg
```

The app fetches
`https://github.com/gulfvideo/campus-captions-mac/releases/latest/download/latest.json`.
GitHub points `releases/latest/` at the newest release that is **not** marked
a prerelease, which is why the workflow publishes a full release even though
every version is tagged `-beta`. A draft or prerelease is invisible to the app.

## Cutting a release

Bump `VERSION` in [`launcher/version.py`](../launcher/version.py), commit,
then push an annotated tag. The tag message becomes the "what changed" text
the app shows:

```bash
git tag -a v0.4.0-beta -m "Fixes the thing"
git push origin v0.4.0-beta
```

Some minutes later the release carries the disk image and `latest.json`. If
the build fails nothing is published and everyone stays on the version they
have, which is the safe outcome.

To check a manifest by hand:

```bash
python tools/publish_update.py --assets macapp/dist --version 0.4.0-beta --out /tmp/latest.json
```

## What a tester sees

**Settings → App version → Check for updates**, then Download, then Install.
Three buttons, no timers. Downloading 75 MB over a venue's wifi and restarting
the app that is currently captioning are both decisions for whoever is standing
at the desk, so the app will not make either on its own, and `install` refuses
outright while the event is live.

Installing hands off to a small script that outlives the app, waits for it to
quit, copies the new bundle over the old one and reopens it. If the copy fails
the old app is put back and reopened.

## What makes a download trustworthy

* HTTPS to GitHub, and a redirect anywhere other than GitHub's own asset host
  is refused (`ALLOWED_HOSTS` in [`relay/updater.py`](../relay/updater.py)).
* The size and SHA-256 from the manifest, checked before the file is opened.
* The disk image mounts and `codesign --verify --strict` passes.
* `CFBundleShortVersionString` inside the bundle matches what the manifest
  claimed, so a manifest and a binary that have drifted apart stop here.

### The gap, for an ad-hoc signed build

`macapp/build.sh` signs with the Faulkner Developer ID (team `7V4HF8KNDA`)
when the certificate is in the keychain, and ad-hoc otherwise. An ad-hoc
build has two weaknesses.

**Microphone access resets on every update.** macOS records the build's code
hash when a tester grants it. Every ad-hoc build produces a different one, so
each update looks like an unrelated app and the prompt comes back. The app
says so next to the Install button. A Developer ID build is identified by its
team and bundle ID instead, so access survives updates from the first signed
build onward.

**The checksum is the whole trust root.** Anyone who can publish a release
can publish a matching manifest. In a Developer ID build, `expect_team` in
`app_from_disk_image` matches the running app's Team ID, and an update signed
by anyone else is refused. An installed ad-hoc copy has no team to match, so
its first update to a signed build is trusted on the checksum alone, as before.
