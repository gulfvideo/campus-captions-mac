# Mac follow-up: what still needs a Mac, and how to hand it to Claude Code

Everything on the `security-hardening` branch (PR #3) was built and tested on
Windows against a real relay, with the viewer pages checked in a browser. The
macOS-specific pieces could not be run there. This file is the checklist for
the first pass on a Mac, and at the bottom is a prompt you can paste into
Claude Code to have it do the work and push the results to the same branch.

## What needs to happen

### 1. Build and run from source

- `python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt -r requirements-whisper.txt -r macapp/requirements-build.txt`
- `.venv/bin/python tools/smoke_test.py "$(pwd)"` — every check must pass on macOS too (the script uses only the standard library
  plus `websockets`).
- `./Start\ Captions.command` — the launcher must print a
  `/status?token=…` link and open it; the status page must load signed in.
- On the status page, pick the **Apple on-device** backend and Open the
  stream. This compiles `capture/backends/native/speech_helper.swift` on
  first use, now with a **minimal environment** (`child_env()` in
  `capture/backends/apple_speech.py`). If `swiftc` or `codesign` fails to
  find the toolchain, `DEVELOPER_DIR` / `SDKROOT` are already passed through;
  add whatever else the toolchain needs to `CHILD_ENV_KEYS`, nothing more.

### 2. Build and run the packaged app

- `macapp/build.sh` — output is `macapp/dist/Campus Captions.app`. The spec
  now bundles only `config/event.example.toml`, never `event.toml`.
- Launch the app. On first run it must create
  `~/Library/Application Support/Campus Captions/config/event.toml` (copied
  from the example) and, once the relay starts, write `.env` there with the
  two generated tokens. Nothing may be written inside the `.app`:
  `find "macapp/dist/Campus Captions.app" -newer macapp/dist -name '.env'`
  must find nothing.
- **Open Status Page** from the menu bar must sign the browser in (the link
  carries the token). Upload a welcome photo and a logo; both must land in
  `~/Library/Application Support/Campus Captions/config/`.
- Logs: `~/Library/Logs/Campus Captions/relay.log` and `capture.log`.
- Keyterms: `config/keyterms.txt` is bundled; a copy placed in the
  Application Support `config/` folder must win over the bundled one
  (`resolve_path()` in `capture/config.py`).

### 3. Hardened runtime and the entitlement

`capture/backends/native/Speech.entitlements` grants
`com.apple.security.cs.disable-library-validation`. The security review
flagged it: it is inert today (ad-hoc signing, no hardened runtime) but would
defeat library validation the moment hardened runtime is enabled, and the
public Speech framework should not need it.

- Remove the key, rebuild the helper (`capture/backends/native/build.sh`),
  and confirm the Apple backend still transcribes.
- Then try the helper with hardened runtime:
  `codesign --force --sign - --options runtime --entitlements capture/backends/native/Speech.entitlements capture/backends/native/build/speech_helper`
  and test again. If it now fails to load the Speech frameworks, put the
  key back and record the exact error in the entitlements comment so the
  next person knows why it is there.

### 4. Signing and notarization (needs an Apple Developer ID)

Only if a Developer ID certificate is available on this Mac:

- In `macapp/build.sh`, replace `codesign --force --sign - "$app"` with
  `codesign --force --deep --options runtime --sign "Developer ID Application: <name>" "$app"`
  (deep is fine once the helper is signed by the same identity with its
  entitlements in the same step; test that the helper still launches).
- `ditto -c -k --sequesterRsrc --keepParent "$app" CampusCaptions.zip`
- `xcrun notarytool submit CampusCaptions.zip --keychain-profile <profile> --wait`
- `xcrun stapler staple "$app"`
- Update README.md / SETUP.md: the Control-click → Open instruction and the
  `xattr -dr com.apple.quarantine` advice in `Start Captions.command` go away.

Without a Developer ID, skip this section and leave the ad-hoc signing.

### 5. Accessibility on real devices

- Open the phone view on an iPhone with VoiceOver on. Expect: the welcome
  screen read on arrival; "Captions started" when the operator presses Start;
  each committed line announced once; the language picker announced as a
  pop-up button with the current language; the Pause button's state; "Captions
  paused — technical difficulty" when the stream is closed while live.
- Put the wall on a projector and check the default 3 rows / 36 characters
  at real distance; adjust the recommended preset in `viewer/tune.html`
  (`BUILTIN`) if the room needs a different size.

### 6. Report

Everything found goes into commits on `security-hardening` (or a branch off
it) and a comment on PR #3: what passed, what was changed, what still fails.

---

## Prompt for Claude Code

Paste this into Claude Code on the Mac, in the folder where the repo is
cloned. It is self-contained.

```
Work in this repo on the branch `security-hardening` (git fetch origin &&
git checkout security-hardening). Read docs/MAC-FOLLOWUP.md first; it is the
checklist for this session and you are doing sections 1 through 6 on this Mac.

Rules: run everything yourself rather than asking me to; never commit .env,
config/event.toml, or anything under ~/Library; if a step needs a password,
an Apple ID, or a Developer ID certificate that is not already on this Mac,
skip that step and say so in the report rather than working around it; keep
the existing code style (comments explain why, not what).

Do, in order:

1. Set up: create .venv, install requirements.txt, requirements-whisper.txt
   and macapp/requirements-build.txt. Run tools/smoke_test.py "$(pwd)" and
   make every check pass on macOS. Fix anything platform-specific that
   fails, with a test if the fix is non-trivial.

2. Run from source with "Start Captions.command". Confirm the status page opens
   signed in via the ?token= link. Select the Apple on-device backend and open
   the stream; the Swift helper must compile and transcribe from the built-in
   mic under the minimal child environment. If the toolchain needs more
   environment variables, add only those to CHILD_ENV_KEYS in
   capture/backends/apple_speech.py.

3. Build the packaged app with macapp/build.sh and launch it. Verify, with
   commands whose output you paste into the report: event.toml is created in
   ~/Library/Application Support/Campus Captions/config/ on first run; .env
   with both tokens is written there once the relay starts; nothing new is
   written inside the .app bundle; Open Status Page signs the browser in;
   uploading a welcome photo and a logo from the status page stores them in
   that same config folder; logs land in ~/Library/Logs/Campus Captions/.
   Fix whatever does not hold.

4. Remove com.apple.security.cs.disable-library-validation from
   capture/backends/native/Speech.entitlements, rebuild the helper, and
   confirm the Apple backend still transcribes. Then re-sign the helper with
   --options runtime and test again. If hardened runtime breaks it, restore
   the key with a comment quoting the exact error. Either way, record the
   result.

5. Only if a "Developer ID Application" certificate is already in the
   keychain: switch macapp/build.sh to sign with it and --options runtime,
   notarize with xcrun notarytool (skip if no keychain profile exists), staple,
   and update README.md, SETUP.md and "Start Captions.command" to drop the
   Gatekeeper workaround. Otherwise leave signing as it is and say so.

6. With VoiceOver on an iPhone on the same wifi, open the phone view and go
   through the checklist in section 5 of docs/MAC-FOLLOWUP.md. Fix any
   announcement that is wrong or missing.

7. Commit each logical change separately with a clear message, push to
   origin/security-hardening, and post a comment on PR #3
   (koakley3/campus-captions) summarising what passed, what you changed, and
   what is still open. Then give me the same summary here.
```

## Email to send with it

Subject: Campus Captions — Mac testing pass for PR #3

Jackson — PR #3 on campus-captions has the security and accessibility work
Claude and I did this week. All of it was built and tested on Windows; the
Mac-specific parts have not been run on real hardware yet. Everything that
needs a Mac is written up in docs/MAC-FOLLOWUP.md on the branch, and at the
bottom of that file is a prompt you can paste straight into Claude Code on
your Mac — it will work through the checklist, fix what breaks, push to the
same branch, and comment on the PR.

To start: clone the repo (or pull), `git checkout security-hardening`, open
Claude Code in that folder, and paste the prompt. Budget an hour or so; the
first run compiles the Swift speech helper and downloads a Whisper model.

Two things only you can decide on the Mac: whether there is a Developer ID
certificate to sign and notarize with (if not, the prompt skips it), and
whether the Speech helper still works once its library-validation
entitlement is removed. The prompt handles both cases.

Thanks — Kasey
