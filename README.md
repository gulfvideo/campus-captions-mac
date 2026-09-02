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
ProPresenter, firewall and IP requirements, audio device selection, the query
parameters, keyterms, and a pre-event checklist.

**macOS and just want the app, not the source?** Grab the latest build from
[Releases](https://github.com/koakley3/campus-captions/releases) — download
`CampusCaptions.zip`, unzip it, and see "Packaging a macOS app" below for the
first-launch Gatekeeper step.

This README is for working on the code.

---

## Operating it: the short version

Double-click **`Start Captions.bat`** on Windows or **`Start Captions.command`**
on macOS. It creates the virtual environment on
first run, starts the relay and the capture client, opens the status page, and
supervises both until the window is closed. Everything an operator needs is on
the status page.

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

**Adding another backend** is one new file next to those four and one line in
`capture/backends/registry.py`. Nothing in the relay or the viewers changes.

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

## Packaging a distributable .app

For sending a runnable copy to someone who shouldn't need Python, Xcode, or a
terminal at all: `macapp/` builds a self-contained macOS menu bar app with
PyInstaller.

```
.venv/bin/python3 -m pip install -r requirements.txt -r macapp/requirements-build.txt
macapp/build.sh
```

Output is `macapp/dist/Campus Captions.app` — copy it to `/Applications` and
double-click. It has no console window; a graduation-cap icon appears in the
menu bar instead, with menu items to open the status/wall/phone pages and to
quit. `macapp/menubar.py` is the entry point: it runs the relay and capture
client as threads in one process instead of `launch.py`'s two subprocesses,
since a frozen app has no `python -m X` to shell out to.

The app bundles a prebuilt `speech_helper` (see `capture/backends/native/`) so
`apple_speech` works with no Xcode on the machine it's installed on, and
bundles `config/event.toml` as its default configuration — edit that file
before building if the default backend or port should differ for whoever is
receiving the app.

It's ad-hoc signed, not notarized (no paid Apple Developer ID in this setup),
so first launch on another Mac needs Control-click → Open once to get past
Gatekeeper's "unidentified developer" warning. That's a one-time step per
machine, not per launch.

## Configuration

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
| `GET /wall` `/phone` `/status` `/settings` `/tune` | the five pages, served `no-store` (all but `/wall`/`/phone` are operator-only) |
| `GET /<name>` | any other `viewer/<name>.html`, no code change needed (operator-only unless listed public) |
| `WS /ws/viewer?role=wall\|phone` | caption feed; `role` sets replay depth |
| `WS /ws/source` | capture client, bidirectional; source token as `Authorization: Bearer` |
| `WS /ws/status` | full status snapshot ~2.5×/second (operator-only) |
| `GET /api/status` | one snapshot as JSON, plus the relay's LAN address (operator-only) |
| `GET /api/site` | public metadata: base URL, phone URL, available languages |
| `GET /api/qr.svg` | the phone URL as a server-rendered QR code, for `/qr` |
| `GET /api/transcript.srt` | recorded captions as SubRip, `?lang=` (operator-only) |
| `POST /api/control` | `{"action": "open_stream"}` and friends - forwarded to the capture client, see `docs/MESSAGES.md` for the full list; `{"action": "caption", "text": "[Applause]"}` is relay-local |
| `POST /api/event` | `{"state": "live"}` and the welcome-screen text; `PUT`/`DELETE /api/event/image` for its photo, `GET` is public |
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
whatever's been recorded so far.

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
| `idle` | `5` | seconds of no new text before the bar fades out; `0` never fades |
| `fade` | `600` | fade duration, ms |
| `maxchars` | `42` | characters per line |
| `watchdog` | `120` | seconds with no traffic before the page reloads itself |
| `debug` | off | diagnostics overlay, top left |
| `text` | — | render static text, open no socket (framing mode) |
| `soak` | — | synthetic captions, e.g. `?soak=1`, `?soak=6` for six times speed |
| `relay` | — | override the relay host, e.g. `relay=10.0.4.12:8000` |

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
it opens a compact panel (five preset text sizes with clamped +/- stepping, five
color swatches plus a native color picker for anything else) anchored above the
button rather than a full-screen takeover, so captions keep scrolling behind it.
Closes on an outside tap, Escape, or tapping **Aa** again. Both choices persist
per device via `localStorage`, independent of anyone else viewing the same
stream. The page requests a screen wake lock so phones do not sleep mid-service.

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
