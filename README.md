# Campus Captions

Live captioning for campus events — lectures, chapel, panels, ceremonies.
A capture client turns room audio into transcript events, a relay fans them out,
and two viewer pages render them: a broadcast graphic for the LED wall and a
phone page with scrollback.

**Complete.** All three stages are built.

| Stage | Contents |
|---|---|
| 1 | `viewer/wall.html`, `viewer/phone.html`, `tools/mock_relay.py` |
| 2 | `relay/`, `viewer/status.html`, `tools/mock_source.py` |
| 3 | `capture/`, its ASR backends, `launcher/`, `SETUP.md` |

**Running an event? Read [SETUP.md](SETUP.md), not this file.** It covers
installing, ProPresenter, firewall and IP requirements, audio device selection,
the query parameters, keyterms, and a pre-event checklist.

**Just want to run it, not work on it?** Take the download for your machine
from [Releases](https://github.com/koakley3/campus-captions/releases). No
Python, no terminal, no zip to unpack:

| | |
|---|---|
| Windows | `CampusCaptions-Setup-<version>.exe` — double-click, click through the installer |
| macOS | `CampusCaptions.zip` — unzip, drag to Applications, Control-click → Open the first time |

Neither is code-signed, so each shows a one-time warning the first time it runs
on a given computer (Windows: "More info" → "Run anyway"; macOS:
Control-click → Open). See [SETUP.md §1](SETUP.md) for both, in order.

This README is for working on the code.

---

## Operating it: the short version

The installed app runs as a **notification-area icon on Windows** and a **menu
bar icon on macOS** — right-click it for the status, settings, wall and phone
pages, and to quit.

From a source checkout, double-click **`Start Captions.bat`** on Windows or
**`Start Captions.command`** on macOS. It creates the virtual environment on
first run, starts the relay and the capture client, opens the status page, and
supervises both until the window is closed.

Either way the **first run opens a setup wizard** in the browser — microphone,
transcription backend, event title, then the addresses to hand out — and then a
**guided tour** that points at each control on the real page and says what it
is for. Every run after that goes straight to the status page. Everything an
operator needs during an event is on the status page; one-time setup is on
Settings, which has a **Run setup again** link back to the wizard. **Take the
tour** in the top corner of the status, tuner and settings pages replays the
tour whenever someone wants it again.

---

## Install (development)

```bash
python -m venv .venv
# Windows
.venv\Scripts\python.exe -m pip install -r requirements.txt
# macOS / Linux
.venv/bin/python -m pip install -r requirements.txt
```

Pinned to the exact versions tested on Python 3.14.6 / Windows 11; every one has
a cp314 wheel, so nothing is compiled at install time.

## Running the pieces by hand

Two processes. The relay, then the capture client:

```bash
.venv\Scripts\python.exe -m relay
# macOS / Linux: .venv/bin/python -m relay
```

```bash
.venv\Scripts\python.exe -m capture
# macOS / Linux: .venv/bin/python -m capture
```

`-m capture` defaults to the mock backend, so it needs no audio hardware and no
API key. Useful flags:

```bash
.venv\Scripts\python.exe -m capture --list-devices
.venv\Scripts\python.exe -m capture --backend deepgram --device "Scarlett"
.venv\Scripts\python.exe -m capture --no-audio          # no input device at all
```

Then open the **status page** — <http://localhost:8000/status>. It shows
whether audio is arriving, whether the ASR backend is producing text, how many
viewers are connected, whether the ASR stream is open, and it carries the
buttons to open and close that stream - everything an operator watches during
an event. One-time setup - API keys, integrations, keyterms/blocklist, the
welcome-screen wording and photo - lives on **Settings**
(<http://localhost:8000/settings>, linked from the status page), so the page
running all night isn't cluttered with things touched once before doors open.

- status — <http://localhost:8000/status>
- settings — <http://localhost:8000/settings>
- wall — <http://localhost:8000/wall>
- phone — <http://localhost:8000/phone>

The relay binds all interfaces, so phones on the same wifi use
`http://<relay-ip>:8000/phone`. The status page prints that exact URL for you.

**The ASR stream starts closed.** Audio levels flow immediately so you can check
the microphone, but no transcript is produced until someone presses *Open ASR
stream*. This mirrors Deepgram billing, which is charged on connection time, not
speech time — you can leave the whole system running through setup and rehearsal
at no cost and open the stream a minute before the event.

## ASR backends

One interface, `capture/backends/base.py`: audio frames in, interim/final
transcript events out. The relay and the viewers never learn which backend is
running — they only ever see the messages in `docs/MESSAGES.md`.

```python
backend = create("deepgram", config)
await backend.open(sample_rate=48000)   # a paid connection starts HERE
await backend.send_audio(frames)
async for event in backend.events(): ...
await backend.close()                   # and stops HERE
```

| Backend | File | Notes |
|---|---|---|
| `mock` | `capture/backends/mock.py` | replays `config/mock_script.txt`, ignores audio, costs nothing |
| `deepgram` | `capture/backends/deepgram.py` | Nova-3 streaming, keyterm prompting, key from `.env` |
| `apple_speech` | `capture/backends/apple_speech.py` | on-device SpeechAnalyzer, macOS 26+, free, no network after first model download |
| `whisper` | `capture/backends/whisper.py` | on-device via faster-whisper, free, works on any OS, needs `requirements-whisper.txt` |

`apple_speech` is Swift-only under the hood - there's no Python or Objective-C
bridge to Apple's SpeechAnalyzer/SpeechTranscriber APIs - so the class in
`apple_speech.py` runs `capture/backends/native/speech_helper.swift` as a
subprocess: raw PCM in on stdin, one JSON transcript line out on stdout per
update. See `capture/backends/native/build.sh` for how that binary gets
codesigned with just enough entitlement to pass the Speech Recognition TCC
check without tripping AMFI.

`whisper` simulates streaming by re-decoding a buffer on a timer (Whisper
itself transcribes whole clips, not a stream) - expect roughly a second more
latency than Deepgram and less stable interims. Good for offline demos, a
venue with no internet, or any machine `apple_speech` can't run on. Its
dependencies are ~200MB and optional on purpose: `python -m pip install -r
requirements-whisper.txt` only on a machine that actually uses it.

**Adding another backend** is one new file next to those four, one line in
`capture/backends/registry.py`, and one row in `capture/backends/support.py`'s
`CATALOGUE` (the operator-facing name and description). `registry.py` asserts
at import that the two agree. Nothing in the relay or the viewers changes.

### Which backend a machine gets

`capture/backends/support.py` answers "does this backend exist, and can this
computer run it" **without importing any backend** — the relay and
`capture/config.py` both need the answer, and the relay must stay clear of
numpy, websockets and PortAudio (see `.github/workflows/ci.yml`). It is the
authority for three things:

- **`backend = "auto"`**, the shipped default in `config/event.example.toml`:
  the first free, no-account backend this machine can run — `apple_speech` on
  a Mac new enough for it, `mock` everywhere else. Never `deepgram` (it bills)
  and never `whisper` (its wheels are an opt-in download).
- **What gets offered.** The setup wizard and the status page's picker list
  `supported()` only, so `apple_speech` is simply absent on Windows and Linux.
- **What happens to a config naming a backend this machine cannot run.** It is
  substituted for one that works, and the capture client prints why. Failing
  instead would mean a volunteer with no captions and a config file to edit.

This is what the shipped default used to get wrong: `event.example.toml` named
`apple_speech`, and a fresh clone has no `config/event.toml`, so every Windows
install came up on a macOS-only backend that could only ever report an error.
`tests/test_backend_support.py` pins the behaviour, including that the shipped
example never names a platform-bound backend again.

The mock backend is first-class, not a stub: it obeys the same open/close
lifecycle, so the whole operator flow can be rehearsed without hardware, an API
key, or spend. In a script file, `#` starts a comment, a blank line is a pause,
and `[clear]` wipes the display the way an operator would between segments.

`tools/mock_source.py` is a lighter alternative that speaks the source protocol
directly, for testing the relay without the capture client at all.

### Viewers with no relay at all

`tools/mock_relay.py` from stage 1 still works and still needs no dependencies —
standard library only. It serves the viewer pages and plays a script, with no
status page, no control channel and no capture client. Useful on a machine with
nothing installed:

```bash
python tools/mock_relay.py
```

## Packaging

Two packagers, one shared core. `launcher/embedded.py` runs the relay and the
capture client as threads of one process — a frozen app has no `python -m X` to
shell out to, so `launch.py`'s two-subprocess model cannot apply. Both front
ends are thin: they draw a menu, show an alert, and hand a status callback to
`embedded.Supervisor`. Everything else — starting the relay, restarting it when
the Settings page changes the port, pointing the capture client at whichever
port it actually got — lives in the shared file, so a fix cannot land on one
platform and quietly miss the other.

`launcher/version.py` is the only place the version number is written; both
`.spec` files and the Windows installer read it.

### Windows: an installer

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt `
    -r winapp\requirements-build.txt -r requirements-whisper.txt
powershell -ExecutionPolicy Bypass -File winapp\build.ps1
```

Two outputs in `winapp\dist`: the app folder, and
`CampusCaptions-Setup-<version>.exe` — the single file to hand out. The
installer step needs [Inno Setup 6](https://jrsoftware.org/isdl.php);
without it the app still builds and the script says so.

`winapp/tray.py` is the entry point: a notification-area icon (`pystray`), no
console window. A **onedir** build, not onefile — a onefile re-extracts the
whole interpreter to a temp folder on every launch, which on a booth PC with an
aggressive antivirus is twenty seconds of a double-click appearing to do
nothing. The installer hides the folder in Program Files anyway.

`winapp/icons/*.ico` are generated from the macOS app icon by
`winapp/icons/make_icons.py` and committed, so a Windows build needs neither
Pillow nor the `.icns`. They are the blue app tile with a status dot rather
than the macOS menu bar's template images: Windows does not recolour tray
icons, so a black one vanishes on the default dark taskbar.

Building it needs no Windows machine — see **Releasing** below.

### macOS: a menu bar app

```
.venv/bin/python3 -m pip install -r requirements.txt -r macapp/requirements-build.txt
macapp/build.sh
```

Output is `macapp/dist/Campus Captions.app` — copy it to `/Applications` and
double-click. It has no console window; a graduation-cap icon appears in the
menu bar instead. `macapp/menubar.py` is the entry point.

The app bundles a prebuilt `speech_helper` (see `capture/backends/native/`) so
`apple_speech` works with no Xcode on the machine it's installed on.

`macapp/make_dmg.sh` wraps the built app in a disk image, which is what
releases ship: opening a `.dmg` and dragging one icon onto Applications is the
gesture Mac users know, and it puts the app somewhere permanent. A zip left it
loose in Downloads and made "extract this first" a step — the same step the
Windows installer exists to remove. `hdiutil` only, so it needs no Homebrew.

The Intel build ships without Whisper: `onnxruntime`, a faster-whisper
dependency, publishes no macOS x86_64 wheel for Python 3.14, and installing it
fails the job outright. The other three backends work there, and the app
reports Whisper as not included in that build rather than offering it.

**Two Mac builds, one per architecture.** PyInstaller freezes for the host
architecture, so an arm64 app does not launch on an Intel Mac at all — Rosetta
translates Intel to ARM, not the reverse. CI builds both. The Intel runner
(`macos-15-intel`) is GitHub's last x86_64 image and goes away in August 2027;
macOS 26 itself still runs on four Intel models, so `apple_speech` is
reachable there and the Swift helper is still worth building.

The app bundles a prebuilt `speech_helper` (see `capture/backends/native/`) so
`apple_speech` works with no Xcode on the machine it's installed on.

### What ships, and what doesn't

Only `config/event.example.toml` goes into either bundle. The real
`event.toml`, the `.env` with the keys, and any uploaded images live in a
per-user folder — `%LOCALAPPDATA%\Campus Captions` on Windows,
`~/Library/Application Support/Campus Captions` on macOS (see
`relay/config.py`'s `_data_dir()`), created on first run. So nothing personal
or secret travels with a copy of the app, an upgrade keeps the operator's
settings, and writing settings needs neither a writable Program Files nor a
valid macOS code signature.

### Releasing

`.github/workflows/release.yml` builds both apps and attaches them to the
GitHub release for a `v*` tag — so cutting a release needs neither a Windows
machine nor a Mac. Two independent jobs, deliberately not chained: a macOS
runner that cannot build the Swift speech helper must not stop the Windows
installer from reaching the release.

Neither job writes the release body. Every release here carries hand-written
notes, so the workflow only attaches files: an existing release's notes
survive a re-run, and the two jobs cannot overwrite each other. A tag pushed
with no release yet gets an empty one to fill in.

`tools/windows_build_check.py` and `tools/macos_build_check.py` run before
either upload and fail the build on a hidden import PyInstaller missed or a
platform default that regressed — a runner has no display, no audio device
and no microphone permission, so importing what the frozen app imports is the
most a machine can check.

**The macOS job may need a Mac after all.** `speech_helper.swift` is built
against SpeechAnalyzer, which needs the macOS 26 SDK (Xcode 26). If
`macos-latest` is older than that, the job stops with an explanation rather
than publishing an app whose free on-device backend silently does nothing.
Two ways out: build the `.app` on a Mac with Xcode 26 and upload
`CampusCaptions.zip` to the release by hand, or re-run the workflow from the
Actions tab with **"Build the macOS app even if the Swift speech helper will
not compile"** ticked, accepting that `apple_speech` will not work in that
build. `macapp/build.sh --skip-helper` is the same choice locally.

Run the workflow from the Actions tab (`workflow_dispatch`) to get a build
without cutting a release; each app is uploaded as a workflow artifact and no
release is touched.

`launcher/version.py` is the one place the version is written. It carries the
full label including any pre-release suffix (`0.3.0-beta`); `VERSION_NUMBER`
is the numeric part, which is what the Windows VERSIONINFO resource and Inno
Setup's `VersionInfoVersion` need.

### Signing

Neither is signed. The macOS app is ad-hoc signed (no paid Apple Developer ID),
so first launch on another Mac needs Control-click → Open once to get past
Gatekeeper. The Windows installer is unsigned, so it shows SmartScreen's
"Windows protected your PC" once per machine — More info → Run anyway. Both are
one-time per computer, not per launch. A certificate is the only thing that
removes either.

## Configuration

**The port is not a reason to stop.** If `[relay] port` is taken by another
program when things start, the launcher, the menu bar app and `python -m
relay` all start on the next free port and say so, in the launcher window
or a Mac alert, with the reminder that the wall and phone URLs now carry
that port. To make a different port permanent, the Settings page has a
*Network — relay port* field: `POST /api/relay` saves it to `[relay] port`,
tells the capture client over its own link (`relay_moving`, see
`docs/MESSAGES.md`) so it follows without a restart, and asks the uvicorn
server to exit; `relay/__main__.py` and the menu bar app both loop and
bring it straight back on the new port, and the Settings page follows once
the new port answers. The operator cookie is not port-specific, so nothing
has to be re-entered. A local `[capture] relay_url` follows `[relay] port`
from the same file, so the number lives in one place.

`config/event.toml` (copy it from `config/event.example.toml`; the real one is
gitignored). The relay reads the `[relay]` section — host, port, and the
`source_token` that capture clients must present on `/ws/source` — and
`[youtube]`, covered below. Everything else belongs to the capture client.

Environment always wins, so the launcher can override without editing files:
`CAPTIONS_HOST`, `CAPTIONS_PORT`, `CAPTIONS_SOURCE_TOKEN`,
`CAPTIONS_OPERATOR_TOKEN`. No Deepgram key ever appears in a config file —
that stays in the environment.

Running from source, `config/event.toml` and `.env` live in the checkout. The
packaged macOS app keeps them, and any uploaded welcome photo or logo, in
`~/Library/Application Support/Campus Captions/` — never inside the `.app`,
which is signed and gets passed around as a zip.

`keyterms.txt` and `blocklist.txt` are copied there too, on first launch, for
the same reason: they are meant to be hand-edited, and the copies shipped
inside the bundle cannot be — the `.app` is read-only, and changing a file in
it invalidates the code signature. The menu bar's **Show Config Folder in
Finder** opens the folder that actually gets read. An existing file is never
overwritten, so edits survive an update.

Two secrets gate the relay, and both are generated into `.env` on first start
if nothing sets them, so a fresh checkout is protected without anyone inventing
a password:

- **`CAPTIONS_SOURCE_TOKEN`** — who may push captions. The capture client reads
  the same `.env`, so the two agree automatically. An empty token is refused,
  not treated as "no token": the audience is on the booth LAN too (the QR code
  put them there), so it was never a closed network.
- **`CAPTIONS_OPERATOR_TOKEN`** — who may open the status page and call the
  control API. The launcher opens `/status?token=…`, which signs that browser
  in for a month; scripts send it as `Authorization: Bearer`. See
  `docs/MESSAGES.md` "Operator authentication".

## Relay endpoints

| Endpoint | Purpose |
|---|---|
| `GET /wall` `/phone` `/status` `/settings` `/tune` `/setup` | the six pages, served `no-store` (all but `/wall`/`/phone` are operator-only) |
| `GET /<name>` | any other `viewer/<name>.html`, no code change needed (operator-only unless listed public) |
| `WS /ws/viewer?role=wall\|phone` | caption feed; `role` sets replay depth |
| `WS /ws/source` | capture client, bidirectional; source token as `Authorization: Bearer` |
| `WS /ws/status` | full status snapshot ~2.5×/second (operator-only) |
| `GET /api/status` | one snapshot as JSON, plus the relay's LAN address (operator-only) |
| `GET /api/site` | public metadata: base URL, phone URL, available languages |
| `GET /api/qr.svg` | the phone URL as a server-rendered QR code, for `/qr` |
| `GET /api/transcript.{srt,vtt,txt,json,csv}` | recorded captions as a file, `?lang=` and `?offset=` (operator-only); `.txt` takes `?timestamps=0` |
| `POST /api/control` | `{"action": "open_stream"}` and friends - forwarded to the capture client, see `docs/MESSAGES.md` for the full list; `{"action": "caption", "text": "[Applause]"}` is relay-local |
| `POST /api/event` | `{"state": "live"}` and the welcome-screen text; `PUT`/`DELETE /api/event/image` for its photo, `GET` is public |
| `GET /api/setup` | what the first-run wizard needs: the backends this machine can run and how to describe them, plus the addresses to hand out (operator-only) |
| `POST /api/setup` | `{"completed": bool}` and/or `{"tour_completed": bool}` — the wizard's Finish and Skip, and the tour's (operator-only) |
| `POST /api/setup` | `{"completed": true}` - the wizard's Finish and its Skip, written to `[setup]` in `event.toml` |
| `GET`/`POST /api/wordlist/{keyterms,blocklist}` | the two hand-maintained word lists as plain text; saving rewrites the file and reloads it into the capture client. The body is raw text, not JSON - a campus's worth of names runs past `MAX_JSON_BYTES` |
| `POST /api/ui` | `{"window_mode": "app"\|"browser"}` - whether the app's own pages open in a chromeless window or a browser tab, saved to `[ui]` in `event.toml` |
| `GET`/`POST /api/backend-options` | recognition tuning that used to be `event.toml`-only: Deepgram's `diarize`/`filler_words`/`inaudible_below`, Whisper's `compute_type`/`interim_interval`/`silence_commit`, and the Apple locale. Saved to disk *and* pushed to the running capture client, which rebuilds its backend |
| `POST /api/relay` | `{"port": 8001}` - save the port and restart the relay on it; the capture client follows, see below |
| `POST /api/preflight` | run the preflight check now: a verdict plus findings with what to do about each, see below |
| `POST /api/propresenter` | direct ProPresenter caption output: settings, enable, probe, clear - relay-local, see below |
| `POST /api/overlay-keys` | `{"create": "obs"}` / `{"revoke": "obs"}` - wall output keys, see below |
| `POST /api/youtube-captions` | `{"enabled": true}` - relay-local, see below |

Pages are served with `no-store`: ProPresenter can hold a page for a whole
event, and an operator editing query params must always get the current file.

## Pushing captions to YouTube Live

Optional, and lives entirely in the relay (`relay/youtube.py`) - it works no
matter which ASR backend is running, and toggling it on the settings page
never touches the capture client. The settings page's YouTube Live captions
box pastes and saves the stream's Captions ingestion URL directly - no config file to
open - via `POST /api/youtube-captions`, which calls `relay/config.py`'s
`save_youtube_url()` to patch just the `[youtube]` section of
`config/event.toml` in place, so it survives a relay restart. Every `final`
caption then gets POSTed to that URL as plain text, with `seq`/`lang` query
parameters and a handful of headers - the same wire format professional
captioning vendors already use to feed YouTube, reverse-engineered from vendor
documentation since Google doesn't publish it directly.

Only finals are ever sent, never interims: YouTube's endpoint has no way to
correct or delete text once sent, unlike the general version of this protocol.
Operator-facing setup is in `SETUP.md` section 3b, including the ~15+ second
delay YouTube's own pipeline adds on top of everything else.

The settings page's box only ever controls the **source-language** track. A
translated language (see below) can get its own YouTube broadcast caption
track too, via a `YOUTUBE_CAPTION_URL_<LANG>` environment variable (e.g.
`YOUTUBE_CAPTION_URL_ES`) - deliberately env-only with no status-page field,
since it's a secondary option layered on the primary one, not a replacement
for it. `relay/youtube.py`'s `urls_from_env()` finds these.

## Translation and multiple languages

Optional. Easiest path: paste an Azure Translator key and a target language
list into the settings page's **Translation (Azure)** panel - see SETUP.md
3e for where to get a key. That's `POST /api/translation`, which writes the
key to `.env` and the languages to `config/event.toml` and reconnects the
translator immediately, no restart. Equivalently, by hand: set
`[languages] offer = ["es", "fr"]` (or whatever codes) and
`[translate] provider = "azure"` with `AZURE_TRANSLATOR_KEY` in `.env`.
Either way, every **committed** line gets translated into each offered
language -
interims never are, since they change several times a second and would
multiply the translation bill by roughly twenty for text nobody can read that
fast anyway. `relay/translate.py`'s `Translator` interface makes the provider
swappable (one class, one line in `create()`); it's best-effort by
construction, so an Azure outage degrades to source-only rather than losing
captions.

This is also what `relay/hub.py`'s per-language plumbing exists for: each
language is an independent broadcast stream with its own replay buffer
(`Hub.finals` is `dict[lang, deque]`, not a single deque), so a phone that
joins mid-event and picks Spanish replays *Spanish* history, not English.
`GET /ws/viewer?lang=es` subscribes to one; the phone view's language button
(hidden unless there are ≥2 real languages) does this for you. Only languages
with a working translator are ever advertised - listing a language with no
translator behind it would put a dead choice in front of the audience.

## The preflight check

`relay/preflight.py` turns what the relay already knows - plus a few live
network probes - into a short list of plain-language findings, each with what
to do about it, and one verdict: **ready**, **ready with warnings**, or **not
ready** (which becomes "another computer or an appliance is recommended" when
the computer itself is the problem). The status page's *Preflight check* panel
runs it (`POST /api/preflight`) and shows the last report; the report also
rides in every status snapshot as `preflight`.

What it looks at: the capture client and its audio input (including "another
program has the device", which the capture client detects from PortAudio's
open failure and reports as `device_busy`), the transcription backend, key
and stream, the caption latency figures, the caption computer's CPU and
memory and its busiest programs (`capture/host.py`, via psutil, reported as
`host` in every status message), a TLS handshake to each service in use
(Deepgram, Azure Translator, YouTube), and whether a wall, a phone and
ProPresenter are actually connected. The thresholds are named at the top of
the module; `docs/HOST-COMPATIBILITY.md` is where a venue's results go. The
evaluation is a pure function of a snapshot, so `tests/test_preflight.py`
covers every verdict without a relay.

## Direct ProPresenter output

`relay/propresenter.py` pushes captions into a local ProPresenter over its
network API (Settings → Network → Enable Network), the same relay-local
output-endpoint pattern as the YouTube pusher: a Settings page panel, an
enable toggle, health states, finals only by default. Two surfaces, one
config key apart:

| `surface` | ProPresenter API calls | What the church sets up |
|---|---|---|
| `stage_message` | `PUT /v1/stage/message` (text), `DELETE` to clear | a stage layout with a Stage Message element on the screen that should show captions |
| `message` | `GET /v1/messages` to find the Message by name, `POST /v1/message/{id}/trigger` with one token filled, `GET .../clear` | a Message called (by default) "Live Captions" with one text token called "Caption"; its theme decides the look |

Which one is right is the N-04 spike's question, answered on a real
ProPresenter - `docs/PROPRESENTER_SPIKE.md` is the test plan. Nothing else in
ProPresenter is ever touched: no slides, playlists, props or macros.

Behaviour worth knowing: `reveal` decides how the caption arrives - `line`
(the default) changes the surface once per finished sentence, `word` also
streams the in-progress text, and when it does only the newest pending
interim is ever delivered (a slot, not a queue). The wall takes the same
setting under the same name, so it is one thing to explain to an operator.
Captions are wrapped here to at most two lines against `chars_per_line`,
because the API gives no way to ask how wide the text box is and
ProPresenter will otherwise reflow and shrink a paragraph to fit; a sentence
too long for one page becomes several, each held long enough to read. Every API call is
timed and the panel shows the median and p95 round trip - the N-06 "adds no
more than 500 ms at p95" figure, measured. Three consecutive failures mark it
`disconnected`; it then probes every few seconds and resumes on its own.
Pausing or ending the event, an operator Clear, and Disable all clear the
caption surface. Phones, translation, the recorder and YouTube are untouched
by any of it - a dead ProPresenter costs one output, not the service.
`tests/fake_propresenter.py` imitates the API for the unit tests, the smoke
test, and a local soak (`python tests/fake_propresenter.py 1025`).

## Wall output keys

`POST /api/overlay-keys` creates one revocable key per browser-source output
("obs", "propresenter", "lobby"). While any key exists, `/ws/viewer?role=wall`
without a valid `?key=` is accepted and closed with code 4403, and revoking a
key closes that output's live sockets at once - so a forgotten OBS scene or an
old prop can be switched off without editing every URL. With no keys the wall
is open, as before; phones are never gated. The tuner offers existing keys
and puts the chosen one into the URL it builds.

## The QR page and public site metadata

`/qr` is a full-screen, ProPresenter-friendly page showing a QR code to the
phone view, rendered server-side as SVG (`GET /api/qr.svg`, via `segno` - no
client-side QR library, and it stays sharp at any LED resolution). It follows
the wall view's rule: never an error, a spinner, or a placeholder.

Both `/qr` and the phone view ask `GET /api/site` for the address to
advertise - `[relay] public_hostname` when a tunnel is configured, this
machine's LAN IP otherwise - so the URL is never hardcoded and never
"localhost", which would be useless on someone's phone. `/api/site` is
deliberately separate from `/api/status`: a phone or a QR screen needs a base
URL and a language list, nothing about audio levels or viewer counts.
`/robots.txt` disallows everything, since a live room transcript should never
end up in a search index.

## Recording a transcript, and exporting it to YouTube after the fact

Separate from the live YouTube push above: the relay also keeps a full,
per-language, timed record of every committed line (`relay/transcript.py`'s
`TranscriptRecorder`), independent of whether live YouTube pushing is even
configured. Press **Mark video start** on the status page right when the
recording or stream actually begins - an `.srt` is timed from the start of
the *video*, not the start of the world, so lines spoken before the mark are
dropped from the export (they stay in the underlying record) rather than all
piling up at `00:00:00`. `GET /api/transcript.srt?lang=es` downloads one
language's file; the status page's "Recording & transcript" panel links to
whatever's been recorded so far, in every format.

The same record exports as five file types, all from one cue list
(`TranscriptRecorder.cues()` does the timing and display-splitting once; each
`to_*` only formats, so a cue has the same number and times in every file):

| Extension | For |
|---|---|
| `.srt` | SubRip - YouTube, Vimeo, most editors |
| `.vtt` | WebVTT - HTML5 `<track>`, HLS sidecars |
| `.txt` | readable transcript, one recorded line per line (not display-split), `[HH:MM:SS]` prefixed; `?timestamps=0` for bare text |
| `.json` | cues with start/end seconds, text, and the utterance id shared with translations |
| `.csv` | one row per cue, timestamps both formatted and as seconds |

`relay/transcript.py`'s `FORMATS` table is the single list of what exists;
the route, the status page links and this table follow it. `?offset=` applies
to all of them.

`tools/youtube_upload.py` is a separate, optional CLI - the relay never
imports it - that uploads a `.srt` to a video's caption track via the YouTube
Data API (OAuth "Desktop app" flow, token cached at `config/google_token.json`,
gitignored). `--from-relay --lang es` fetches straight from a running relay
instead of a local file. Needs `requirements-youtube.txt`, and costs quota
(400 of the default 10,000 daily units per upload - roughly 25/day).

**HTML is read from disk on every request; routes are registered once at
startup.** So editing `wall.html` takes effect on the next reload, but changing
anything under `relay/` or `capture/` needs the launcher restarted. Dropping a
new page into `viewer/` needs neither — it is reachable by name immediately.

---

## Wall view

The broadcast graphic. ProPresenter loads this URL into a web fill element.
Treat it as on-air graphics: it **never** shows an error, a spinner, a
connecting state or a placeholder. Anything that is not caption text renders as
a fully empty transparent page, and diagnostics go to the console or the debug
overlay.

Two lines, fixed layout. New text pushes the older line up and out. Space for
both lines is always reserved so the bar never jumps between one- and two-line
states. Interims rewrite the bottom line in place; only a final commits a line
and only a final may break one.

### Query parameters

Everything is tunable from the URL, so it can be adjusted inside ProPresenter in
the field with no rebuild. The bare URL with no parameters works.

| Param | Default | Effect |
|---|---|---|
| `size` | `6.5` | font size, **vh** |
| `bottom` | `9` | bar offset up from the bottom edge, vh |
| `width` | `86` | bar width, vw (fixed, so the bar never changes width) |
| `pad` | `2` | bar padding, vh |
| `radius` | `1.5` | corner radius, vh |
| `opacity` | `0.85` | bar opacity |
| `bar` | `000000` | bar colour, hex, no `#` |
| `color` | `ffffff` | text colour, hex, no `#` |
| `align` | `left` | text alignment: `left`, `center` or `right` |
| `font` | — | `font=atkinson` for Atkinson Hyperlegible, served by the relay |
| `idle` | `5` | seconds of no new text before the bar fades out; `0` never fades |
| `fade` | `600` | fade duration, ms |
| `maxchars` | `42` | characters per line |
| `watchdog` | `120` | seconds with no traffic before the page reloads itself |
| `debug` | off | diagnostics overlay, top left |
| `text` | — | render static text, open no socket (framing mode) |
| `soak` | — | synthetic captions, e.g. `?soak=1`, `?soak=6` for six times speed |
| `relay` | — | override the relay host, e.g. `relay=10.0.4.12:8000` |
| `lang` | — | which caption stream to show, e.g. `lang=es`; unset means the room's language. The tuner has a dropdown for it |
| `key` | — | output key, required once any key exists (Settings → Wall output keys); the tuner fills it in |

All sizing is in vh/vw so the graphic scales to whatever the LED processor is
actually running.

**`maxchars` and `width` are related.** A line is one line — it is clipped, never
wrapped, because wrapping would reflow the graphic on air. At the defaults
(`size=6.5`, `width=86`) about 42 characters fit on a 16:9 output. If you raise
`size`, lower `maxchars` to match. `?debug=1` prints `*** TEXT CLIPPED ***` when
a line does not fit, which is the fastest way to tune the pair against a real
wall.

### Framing and soak

```
/wall?text=The quick brown fox jumps over the lazy dog
/wall?text=WWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWW      (42 W's - width ruler)
/wall?soak=1&debug=1
```

`?text=` opens no websocket at all and disables the idle fade, so the graphic can
be framed and sized in ProPresenter with nothing else running. `?soak=` generates
captions indefinitely for long-run testing, including periodic clears.

### Stability

ProPresenter's web fill has a history of hanging on long-lived pages, so the wall
view holds a fixed footprint: exactly two text nodes for the life of the page,
one `setInterval` on the whole page, one pending reconnect timer at most, no
array that grows, no DOM that grows. Reconnect is capped exponential backoff
(0.5s → 10s) with ±25% jitter, retrying forever and showing nothing. If the
connection stays dead past `watchdog` seconds the page reloads itself.

---

## Phone view

Same feed, built for reading rather than for broadcast. Dark, full bleed, no
chrome, safe-area aware. Committed lines accumulate as scrollback so someone who
looks up late can catch what they missed; the in-progress line shows dimmed at
the bottom.

Auto-scrolls while the reader is at the bottom. Scrolling up unpins it and shows
a **Jump to latest** button — the feed keeps arriving without yanking the view.
The one visible control is a small **Aa** button in the bottom corner; tapping
it opens a compact panel anchored above the button rather than a full-screen
takeover, so captions keep scrolling behind it:

- **Text size** — eight steps from 19 px to 64 px, with clamped +/- stepping.
  Shown as a plain px count rather than named sizes: labelling the large end
  "Huge" reads as a judgment on whoever needs it.
- **Text colour** — five swatches plus a native colour picker. A choice that
  would fall below 4.5:1 against the background is refused, so nobody can
  accidentally make their own captions unreadable and then be unable to find
  the button again.
- **Font** — the default system stack, or Atkinson Hyperlegible, designed by
  the Braille Institute for legibility. The font ships inside the app and is
  served by the relay, so choosing it never makes the page depend on the
  internet.

The panel closes on an outside tap, Escape, tapping **Aa** again, or five
seconds after the last interaction with it. All three choices persist per
device via `localStorage`, independent of anyone else viewing the same stream.
The page holds a screen wake lock, re-acquiring it if the OS drops it, so
phones do not sleep mid-service.

| Param | Default | Effect |
|---|---|---|
| `size` | `24` | starting text size, px (a saved on-device choice overrides it) |
| `color` | `f2f2f2` | text colour (a saved on-device choice overrides it) |
| `bg` | `000000` | background colour |
| `lines` | `200` | scrollback cap — the DOM is trimmed above this |
| `debug` | off | diagnostics overlay |
| `relay` | — | override the relay host |

Unlike the wall view, the phone view may hint at connection state: a hairline at
the very top edge turns amber while the link is down. Scrollback is deduped by
message id, so reconnecting does not duplicate lines.

---

## Message contract

`docs/MESSAGES.md` is normative — the relay, the capture client, the mock relay
and both viewers implement exactly that. Two rules drive everything else:

1. The relay assigns `seq`; sources never do.
2. Only a `final` may commit a line or cause a line break.

Message types: `hello`, `interim`, `final`, `clear`, `heartbeat`, plus `status`
and `control` on the capture side.

---

## Setup wizard

`viewer/setup.html`, at `/setup`. Five steps: welcome, microphone (with a live
level meter, because "we captioned the built-in mic all night" is the failure
worth spending a step on), transcription backend, event title and notice, and
the addresses to hand out.

**A signed-in operator hitting `/status` is redirected here until it is
finished.** That is deliberately the same URL the launcher prints, the tray
menu opens and every page of the docs names — a first run lands somewhere
useful without a second link to teach. `[setup] completed = true` in
`event.toml` is what ends it; Settings has a **Run setup again** link back.

An `event.toml` that has no `[setup]` section is treated as a first run — that
covers the shipped example and every config written before the wizard existed.
*No config file at all* is not, since a booth whose `event.toml` was deleted
mid-event should not be put in front of a wizard.

The wizard saves nothing of its own beyond that one flag: the microphone, the
backend and the Deepgram key all go through `/api/control`, and the title and
notice through `/api/event` — the endpoints the status and settings pages
already use. One writer per setting, so the three pages cannot drift.

### The guided tour

`viewer/tour.js`. Twenty-three steps across the status page, the tuner and
Settings, each one dimming the page, ringing a real control and saying what it
is for — pointing at the thing itself rather than at a picture of it, so it
cannot go quietly out of date the way a screenshot does.

It starts itself on a machine nobody has been shown round yet, and **Take the
tour** in the header of all three pages replays it. Steps whose panel is
collapsed open it first; steps on a Settings tab switch to it. A step whose
target is not on the page — a panel a build does not have — is skipped rather
than shown ringing nothing.

**Where it is up to** is in `localStorage`, because the tour crosses three
pages and has to survive the navigation. **Whether it has ever been finished**
is on the relay, in `[setup] tour_completed` beside `completed`: an app window
and an ordinary tab are not always the same storage, and being shown round
twice because you opened the other one reads as a bug. Skipping counts as
finishing — someone who has said no once should not be asked every launch.

A config file that does not mention the tour answers for it with
`setup.completed`, so a booth that was already running does not get ambushed by
one, while a fresh install gets the wizard and then the tour. Finishing the
wizard writes the flag explicitly so that inference cannot later reverse it.

**Shared JavaScript, under a hash-based CSP.** The pages' policy names the
hashes of their own inline scripts and deliberately has no `script-src 'self'`,
so `tour.js` cannot be loaded with `src=`. `<!-- include tour.js -->` in a page
is replaced with the file's contents by `expand_includes()` in
`relay/server.py` before the page is hashed, so one copy serves three pages
with the strict policy intact. The page cache is keyed on the included file's
modification time as well as the page's, so editing `tour.js` updates every
page carrying it.

## App window

`launcher/appwindow.py`. Chromium-family browsers open a chromeless window
with `--app=URL`: one window, no address bar, no tabs, its own taskbar or Dock
entry. That is the whole mechanism - no Electron, nothing extra to download,
which matters when the alternative is adding a hundred megabytes to an
installer to get a window frame.

Both packaged front ends, the source launcher and the tray/menu-bar Open items
go through `open_link(url, mode, data_dir)`, which returns **what it actually
did** rather than what it was asked for: Firefox and Safari have no equivalent
flag, so a machine with only those gets a tab, and the caller says so instead
of pretending. `[ui] window_mode` in `event.toml` holds the preference, and
Settings > System changes it.

The window gets its own browser profile under the app's data directory rather
than the operator's default one. Sharing the default profile inherits their
extensions and their restore-tabs prompt, and on some versions attaches to the
already-running browser instead of opening a window at all.

## Settings page

`viewer/settings.html`, at `/settings`. Five tabs - Event, Captions, Audience,
Outputs, System - with the panels inside a tab always open. It was eleven
collapsed panels in one column, which made finding a setting a matter of
opening lids until the right thing appeared; within a group there are few
enough that a lid saves nothing and costs a click. The selected tab is
remembered per browser and carried in the URL hash, so "Settings > Captions"
in a note to a volunteer lands where it says it will.

**Nothing on this page requires a text editor.** The keyterms and blocklist
files are edited in place (`/api/wordlist`), and the recognition settings that
were `event.toml`-only - diarization, filler words, the inaudible threshold,
Whisper's compute type and commit timings, the Apple locale - have real inputs
that save to disk and apply to the running capture client without a restart
(`/api/backend-options`). A backend reads its options when it opens a
connection rather than per frame, so applying them rebuilds the backend the
same way switching one does.

Whisper's speech model is a few hundred megabytes and is **never fetched
implicitly** - there is a Download button here and on the setup wizard, and
`capture/backends/support.py` reports whether one is present with a directory
check rather than an import, so the relay can render that row without pulling
faster-whisper and numpy into its process.

## Status page

Dark, four tiles, runtime audio-device selection, four stream controls, and the
two URLs you need. Every judgement —
whether audio counts as live, whether the backend is producing text — is made by
the relay and merely rendered here, so anything else that displays status agrees
with it.

The relay never learns which ASR backend is running. It reports the name the
capture client gives it, so a local on-device backend added later shows up on
this page with no change to the relay, the status page or the viewers.

## Layout

```
Start Captions.bat            double-click this
launcher/launch.py            venv, both processes, browser, supervision
SETUP.md                      the operator and technician document
docs/MESSAGES.md              the message contract
docs/GIT-ETIQUETTE.md         how we write commits, and how to run the tests
.gitmessage                   commit template - git config commit.template .gitmessage
tools/hooks/pre-push          refuses a push that fails the smoke test

.env.example                  copy to .env: Deepgram/Azure keys, YouTube URLs
config/event.example.toml     copy to event.toml and edit
config/keyterms.txt           institution vocabulary, edited per event
config/blocklist.txt          words/phrases stripped from every caption, edited per event
config/mock_script.txt        transcript the mock backend plays
requirements-whisper.txt      optional: the whisper backend's ~200MB of deps
requirements-youtube.txt      optional: tools/youtube_upload.py's deps

relay/server.py               FastAPI app: pages, websockets, control API
relay/hub.py                  fan-out, per-language replay buffers, sequencing
relay/state.py                what the status page is told
relay/config.py               config/event.toml + environment
relay/youtube.py              pushes finals to YouTube Live caption URLs
relay/translate.py            Translator interface + Azure provider
relay/transcript.py           per-language timed record -> .srt export

capture/__main__.py           the capture client
capture/audio.py              device selection by index or name, level metering
capture/relay_client.py       link to the relay, reconnects forever
capture/backends/base.py      the ASR interface - read this one first
capture/backends/mock.py      scripted backend
capture/backends/deepgram.py  Nova-3 streaming
capture/backends/apple_speech.py  on-device SpeechAnalyzer, macOS 26+
capture/backends/native/      the Swift helper apple_speech.py subprocesses
capture/backends/whisper.py   on-device via faster-whisper, any OS
capture/backends/registry.py  name -> class; add a backend here

viewer/wall.html              broadcast graphic for ProPresenter
viewer/phone.html             audience view with scrollback, language picker
viewer/status.html            the operator's page
viewer/tune.html              slider-driven builder for the wall-view URL
viewer/qr.html                full-screen QR code to the phone view

tools/mock_relay.py           stdlib-only relay, for viewer work with nothing installed
tools/mock_source.py          source-protocol stand-in, for relay work
tools/youtube_upload.py       optional: upload a recorded .srt to a video
```

Viewers are self-contained single files: no build step, no framework, no CDN, no
external network dependency beyond the relay itself.

## Optional phone slide previews

[Slides + Captions](docs/PHONE-SLIDES.md) adds relay-cached, read-only static
ProPresenter cue thumbnails to the phone viewer. It is disabled by default and
independent of outgoing caption delivery. See the setup and preview limitations
before enabling it for an event.
