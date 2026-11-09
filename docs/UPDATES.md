# Shipping updates to testers

The repository is private. Its release assets need a token to download, and an
app that carried one would be handing every tester read access to the whole
repository — a token in a bundle is extractable in about a minute. So the
builds are copied to object storage the project owns, and the app checks a
small JSON document there.

The bucket is **not public**. It has no `r2.dev` URL and no public custom
domain. Everything goes through a Cloudflare Worker
([`infra/update-gate/`](../infra/update-gate/)) that checks a **per-tester
access code** before streaming anything out of R2. Each tester gets their own,
it travels as a bearer token, and revoking one is deleting a single row.

### What that does and does not prevent

Worth being exact, because the difference matters:

| | |
|---|---|
| Anonymous download from the open internet | **prevented** — no code, no bytes |
| A leaked link being useful on its own | **prevented** — the URL is not the credential |
| Cutting off one tester without disturbing the rest | **yes** — delete their row |
| Knowing who downloaded what | **yes** — the Worker logs the tester label, never the code |
| A tester copying the app to someone else | **not prevented** |
| The copied app being *run* by that person | **not prevented** |

Nothing can prevent the last two. Once a bundle is on someone's disk, any check
the app performs on itself runs on their machine, and anyone who can edit a file
inside the bundle can edit the check out of it. A licence prompt would look like
security without being any, so there isn't one. What is enforceable is
distribution — who can obtain a build, and whether you can stop them obtaining
the next one — and that is what the gate does.

Signing and notarization are the separate axis: they stop a *tampered* build
from running at all, which is a different problem from an unapproved person
running an untampered one.

```
release tag v0.4.0-beta
        │
        ├── macos job ────┐  built assets
        │                 ▼
        └── publish-update  ──►  R2 bucket
                                   /0.4.0-beta/CampusCaptions-0.4.0-beta-AppleSilicon.dmg
                                   /0.4.0-beta/CampusCaptions-0.4.0-beta-Intel.dmg
                                   /latest.json          ← written last
```

`latest.json` is written **after** the binaries. Published first, a tester who
happened to check in that window would be sent to a URL that does not exist
yet.

## One-time setup

### 1. The bucket

In the Cloudflare dashboard, **R2 → Create bucket**, named `campus-captions-updates`.
Enabling R2 asks for a card on file even though this stays inside the free
tier; the whole thing is a few hundred megabytes and egress from R2 is free.

Leave **Public access** switched off, and attach no `r2.dev` URL and no public
custom domain. A public binding would be a second door into the same bucket
with no code on it, and nothing would tell you it was open.

`updates.gulfvideo.io` is bound to the Worker instead (below). That name is not
arbitrary — it is in `ALLOWED_HOSTS` in [`relay/updater.py`](../relay/updater.py),
and the app refuses to fetch from anywhere else. Changing the domain means
changing that list and shipping a build that knows the new one.

### 1b. The gate

```bash
cd infra/update-gate
npx wrangler kv namespace create CODES      # paste the id into wrangler.toml
npx wrangler deploy
```

Then issue each tester a code:

```bash
./newcode.sh "Jane — north campus"
```

It prints the code to hand over and the one command that authorises it. The
code is shown once and stored nowhere in plaintext — KV holds only its SHA-256,
so a read-only leak of the namespace yields digests rather than working
credentials. Losing a code means issuing a new one, which is the right trade for
a credential.

Revoking is the delete command the same script prints. The next time that app
checks, it is told its code was refused and stops there.

The Worker's logs (`npx wrangler tail`) name the tester and the object, never
the code. One label appearing from many machines at once is the signal to
replace that code.

### 2. The credentials CI uses

**R2 → Manage API tokens → Create**, with **Object Read & Write** on that one
bucket. Then in the repository, **Settings → Secrets and variables → Actions**:

| Kind | Name | Value |
|---|---|---|
| Secret | `R2_ACCESS_KEY_ID` | from the R2 token |
| Secret | `R2_SECRET_ACCESS_KEY` | from the R2 token |
| Secret | `R2_ENDPOINT` | `https://<account-id>.r2.cloudflarestorage.com` |
| Secret | `R2_BUCKET` | `campus-captions-updates` |
| Variable | `UPDATE_BASE_URL` | `https://updates.gulfvideo.io` |

The token is scoped to this one bucket on purpose. The worst thing a leaked CI
token should be able to do is serve a bad build — not read anything else in the
Cloudflare account.

## Cutting a release

Unchanged: push a `v*` tag. The macOS job builds both architectures as it
always did, and `publish-update` runs after it succeeds. If it fails no
manifest is published and everyone stays on the version they have, which is
the safe outcome — a manifest naming a build that failed offers a download
that 404s.

The release notes become the "what changed" text the app shows.

To check a manifest by hand before trusting it:

```bash
python tools/publish_update.py --assets dist/ --version 0.4.0-beta --out /tmp/latest.json
```

It warns when a platform has no build, because the app only offers a build
matching the machine it runs on — a missing one silently leaves those testers
on the old version with no sign anything is wrong.

## What a tester sees

First they paste the access code you sent them — **Settings → App version** —
which is stored in `.env` beside the Deepgram key and the tunnel token, is
never read back out to the browser, and is never printed to a log. Until a code
is present, Check is disabled and the panel says so.

Then **Check for updates**, Download, Install. Three buttons, no timers. Downloading 75 MB over a venue's wifi and restarting
the app that is currently captioning are both decisions for whoever is standing
at the desk, so the app will not make either on its own — and `install` refuses
outright while the event is live.

Installing hands off to a small script that outlives the app, waits for it to
quit, copies the new bundle over the old one and reopens it. If the copy fails
the old app is put back and reopened, because the failure to design against is
a tester left with no application at all on a Sunday morning.

## What makes a download trustworthy

* HTTPS to a host in `ALLOWED_HOSTS`, and a redirect off it is refused — which
  is also what keeps the access code from ever being attached to a request to
  somebody else's server.
* The size and SHA-256 from the manifest, checked before the file is opened.
* The disk image mounts and `codesign --verify --strict` passes.
* `CFBundleShortVersionString` inside the bundle matches what the manifest
  claimed, so a manifest and a binary that have drifted apart stop here.

### The gap, while the app is ad-hoc signed

Today the app is signed with `codesign --sign -`. Its designated requirement is
a single code-directory hash:

```
$ codesign -d -r- "Campus Captions.app"
designated => cdhash H"08c58e6c0c4d9bd404ccff401a41bcf99a5caf9e"
```

Two things follow, and both end with one purchase.

**Microphone access resets on every update.** macOS records that cdhash when a
tester grants it. Every build produces a different one, so as far as TCC is
concerned each update is an unrelated app and the prompt comes back — on a
machine nobody is standing at. The app says so next to the Install button
rather than letting it be a surprise.

**The checksum is the whole trust root.** Anyone who could write to the bucket
could write a matching manifest too — the gate keeps unapproved people from
*reading* it, but the CI credentials are what protect writing, and they are
scoped to this one bucket for exactly that reason. A Developer ID certificate closes this
properly: `expect_team` in `app_from_disk_image` starts matching a real Team
ID, and a build signed by anyone else is refused — enforced by macOS rather
than by our own code, which is a better guarantee than any signature scheme
this project would hand-roll.

An Apple Developer Program membership is $99/yr and fixes both, plus the
right-click-to-open dance a new tester currently has to be talked through. The
updater is built to start using it the moment it exists: sign with the
certificate in `macapp/build.sh`, and the check turns itself on.
