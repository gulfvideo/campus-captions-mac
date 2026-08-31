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
| 3 | `capture/`, both ASR backends, `launcher/`, `SETUP.md` |

**Running an event? Read [SETUP.md](SETUP.md), not this file.** It covers
ProPresenter, firewall and IP requirements, audio device selection, the query
parameters, keyterms, and a pre-event checklist.

This README is for working on the code.

---

## Operating it: the short version

Double-click **`Start Captions.bat`**. It creates the virtual environment on
first run, starts the relay and the capture client, opens the status page, and
supervises both until the window is closed. Everything an operator needs is on
the status page.

---

## Install (development)

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Pinned to the exact versions tested on Python 3.14.6 / Windows 11; every one has
a cp314 wheel, so nothing is compiled at install time.

## Running the pieces by hand

Two processes. The relay, then the capture client:

```bash
.venv\Scripts\python.exe -m relay
```

```bash
.venv\Scripts\python.exe -m capture
```

`-m capture` defaults to the mock backend, so it needs no audio hardware and no
API key. Useful flags:

```bash
.venv\Scripts\python.exe -m capture --list-devices
.venv\Scripts\python.exe -m capture --backend deepgram --device "Scarlett"
.venv\Scripts\python.exe -m capture --no-audio          # no input device at all
```

Then open the **status page** — <http://localhost:8000/status>. That is the only
page an operator needs. It shows whether audio is arriving, whether the ASR
backend is producing text, how many viewers are connected, whether the ASR
stream is open, and it carries the buttons to open and close that stream.

- status — <http://localhost:8000/status>
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

**Adding a local on-device backend later** is one new file next to those two and
one line in `capture/backends/registry.py`. Nothing in the relay or the viewers
changes.

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

## Configuration

`config/event.toml` (copy it from `config/event.example.toml`; the real one is
gitignored). The relay reads only the `[relay]` section — host, port, and an
optional `source_token` that capture clients must present on `/ws/source`. Empty
means no token, which is fine on a closed booth network.

Environment always wins, so the launcher can override without editing files:
`CAPTIONS_HOST`, `CAPTIONS_PORT`, `CAPTIONS_SOURCE_TOKEN`. No Deepgram key ever
appears in a config file — that stays in the environment.

## Relay endpoints

| Endpoint | Purpose |
|---|---|
| `GET /wall` `/phone` `/status` | the three pages, served `no-store` |
| `WS /ws/viewer?role=wall\|phone` | caption feed; `role` sets replay depth |
| `WS /ws/source?token=…` | capture client, bidirectional |
| `WS /ws/status` | full status snapshot ~2.5×/second |
| `GET /api/status` | one snapshot as JSON, plus the relay's LAN address |
| `POST /api/control` | `{"action": "open_stream"}` and the other three |

Pages are served with `no-store`: ProPresenter can hold a page for a whole
event, and an operator editing query params must always get the current file.

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
The one control is a text-size cycler in the bottom corner, which remembers the
choice on that device. The page requests a screen wake lock so phones do not
sleep mid-service.

| Param | Default | Effect |
|---|---|---|
| `size` | `24` | starting text size, px (the on-screen cycler overrides it) |
| `color` | `f2f2f2` | text colour |
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

Dark, four tiles, four buttons, and the two URLs you need. Every judgement —
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

.env.example                  copy to .env, put the Deepgram key there
config/event.example.toml     copy to event.toml and edit
config/keyterms.txt           institution vocabulary, edited per event
config/mock_script.txt        transcript the mock backend plays

relay/server.py               FastAPI app: pages, websockets, control API
relay/hub.py                  fan-out, replay buffer, sequencing
relay/state.py                what the status page is told
relay/config.py               config/event.toml + environment

capture/__main__.py           the capture client
capture/audio.py              device selection by index or name, level metering
capture/relay_client.py       link to the relay, reconnects forever
capture/backends/base.py      the ASR interface - read this one first
capture/backends/mock.py      scripted backend
capture/backends/deepgram.py  Nova-3 streaming
capture/backends/registry.py  name -> class; add a backend here

viewer/wall.html              broadcast graphic for ProPresenter
viewer/phone.html             audience view with scrollback
viewer/status.html            the operator's page

tools/mock_relay.py           stdlib-only relay, for viewer work with nothing installed
tools/mock_source.py          source-protocol stand-in, for relay work
```

Viewers are self-contained single files: no build step, no framework, no CDN, no
external network dependency beyond the relay itself.
