# Campus Captions — message contract

Version `1`. Every websocket frame is one JSON object, UTF-8, no framing of its
own. This document is normative: the relay, the capture client, the mock relay
and both viewers all implement exactly this.

Two rules that everything else follows from:

1. **The relay assigns `seq` and is the only clock viewers trust.** Sources do
   not set `seq`.
2. **Only a `final` may commit a line or cause a line break.** An `interim`
   updates the current line in place and nothing else.

Unknown `type` values MUST be ignored silently. Unknown fields MUST be ignored.
`text` is plain text — viewers render it with `textContent`, never `innerHTML`.

---

## Endpoints

| Path | Peer | Direction |
|---|---|---|
| `/ws/viewer?role=wall\|phone&lang=xx` | wall view, phone view | relay → viewer |
| `/ws/source` | capture client | both ways |
| `/ws/status` | status page | relay → status |

`/ws/source` needs the source token, sent as `Authorization: Bearer ...`
(`?token=` on the URL is also accepted). `/ws/status` needs the operator
token - see "Operator authentication" below. Both refuse a handshake whose
`Origin` header names a different host than the request's `Host`: browsers
never apply same-origin policy to WebSockets themselves, so without that a
page on any site could open these from a booth machine's browser.

`role` affects only how many committed lines are replayed on connect
(`wall` → 2, `phone` → 30). `lang` selects which language stream to join
(default: the source language) - each language is an independent stream with
its own replay buffer, so joining `lang=es` replays Spanish history, not
English translated on the fly. It changes nothing else.

HTTP: `GET /wall`, `GET /phone`, `GET /status`, `GET /qr`, `GET /api/status`,
`GET /api/site`, `GET /api/qr.svg`, `GET /api/transcript.{srt,vtt,txt,json,csv}`,
`POST /api/control`, `POST /api/youtube-captions`, `POST /api/translation`,
`POST /api/tunnel`, `POST /api/propresenter`, `POST /api/overlay-keys`.

---

## Common fields

| Field | Type | Meaning |
|---|---|---|
| `type` | string | message kind, required |
| `seq` | int | monotonic per relay session, assigned by the relay |
| `t` | float | unix seconds UTC, stamped at origin |

---

## Relay → viewer

### `hello`

Sent once, first frame after the socket opens.

```json
{"type":"hello","seq":0,"session_id":"7f3a1c9e","server_time":1756590000.12,"protocol":1,"lang":"en"}
```

`hello` always carries `seq: 0` — it is the one exception to monotonic `seq`,
because the replay burst that follows carries the *original* sequence numbers of
those lines. On `hello` a viewer resets its dedupe state and, if `session_id`
differs from the one it last saw, clears the display: a new `session_id` means
the relay restarted and any text still on screen is stale.

`lang` is which language stream this connection is subscribed to - see
`WS /ws/viewer?lang=` below. It echoes back whatever the viewer asked for (or
the source language, if it asked for nothing), so the viewer knows which
stream it actually got.

### `interim`

```json
{"type":"interim","seq":42,"t":1756590001.9,"text":"our next speaker is doctor","lang":"en"}
```

Replaces the current uncommitted line in place. Superseded by the next `interim`
or `final`. Never breaks a line, never reflows, never enters phone scrollback as
its own entry. Interims are not replayed to late joiners. Interims only ever
carry the source language - translation only ever applies to committed lines.

### `final`

```json
{"type":"final","seq":43,"t":1756590003.4,"t0":1756590002.1,"id":"7f3a1c9e:43",
 "utterance":"7f3a1c9e:43","lang":"en",
 "text":"Our next speaker is Dr. Vasquez."}
```

Commits a line. A `final` replaces the in-progress line if one is showing,
otherwise it starts a new line. If the text is longer than the viewer's
characters-per-line setting the viewer splits it on word boundaries and commits
each piece as its own line — this is the only place a line break happens.

`id` is `{session_id}:{seq}` and is stable across replays, so the phone view can
dedupe when it reconnects. `t0` is when the utterance actually started being
spoken (the first `interim` after the previous commit) - `t` is only when it
landed, which an exported `.srt` needs the difference between to time the cue
correctly. `lang` is which language this line is in; a translated final shares
its `utterance` id with the source line it came from, but gets its own `seq`
and `id` and its own place in that language's replay buffer.

### replayed `final`

```json
{"type":"final","seq":41,"t":1756589994.5,"id":"7f3a1c9e:41",
 "text":"Good morning and welcome.","replay":true}
```

Sent as a burst directly after `hello`, oldest first. `replay: true` appears
**only** in that burst. Viewers render replayed lines without the new-text
animation, so a late joiner arrives at a settled screen rather than a flicker of
history. Absence of the field means live.

### `clear`

```json
{"type":"clear","seq":44,"t":1756590100.0}
```

Wipe the display now. Also empties the relay's replay buffer, so a viewer
connecting afterwards gets nothing rather than pre-clear text.

### `event`

```json
{"type":"event","seq":46,"t":1756590106.0,"state":"live","title":"Fall Convocation",
 "message":"","notice":"Captions are automatic.","pause_message":"","image":true,
 "image_v":1756589000,"logo":true,"logo_v":1756589100,"logo_alt":"Faulkner University",
 "disclaimer":"Captions are auto-generated and may not be 100% accurate.",
 "changed_at":1756590106.0}
```

`logo` / `logo_v` / `logo_alt` describe an optional logo at
`GET /api/event/logo?v=<logo_v>` that the phone view shows in a slim header
row above the captions while the state is `live` or `paused` - never over the
welcome screen, which has the title already. `logo_alt` is the accessible
name the operator gave it; empty means decorative.

`disclaimer`, unlike every other field here, is not read fresh off this
message every time - the phone view shows it once, with a dismiss button, the
first time a device sees a given piece of text (`localStorage`, keyed to the
disclaimer string itself), regardless of `state`. Changing the wording makes
it reappear; re-saving the same wording does not. Empty disables it.

Where the event is, sent to every viewer the moment the operator changes it,
and also carried in `hello` (as `event`) and in every `heartbeat`. `state` is
one of `starting` (phones show the welcome screen: `title`, `message`,
`notice`, and the photo at `GET /api/event/image?v=<image_v>` when `image` is
true), `live` (captions), `paused` (captions stay up under a banner reading
`pause_message`, or "Captions paused"), or `ended`. Defaults for empty text
live in the viewer, so an operator who types nothing still gets a sensible
screen. See "Event state" below for the API.

### `heartbeat`

```json
{"type":"heartbeat","seq":45,"t":1756590105.0,"session_id":"7f3a1c9e","viewers":12,
 "event":{"state":"live", ...},
 "health":{"source":true,"audio":"live","asr":"producing","fault":null}}
```

`health` is the one thing a viewer needs to know about the pipeline that the
captions themselves cannot say. `fault` is `null` or one of `no_source`,
`source_error`, `audio_stalled`, `stream_closed`. While `event.state` is
`live` and a fault persists for more than a couple of seconds, the phone view
shows a "Captions paused - technical difficulty" banner and the wall shows a
short status line; both clear on their own. A deaf attendee has no other way to
tell a dead microphone from a pause.
```

Every 5 seconds. A viewer that has seen no frame of any kind for 20 seconds
treats the link as dead even if the socket still claims to be open. A changed
`session_id` here has the same meaning as in `hello`.

---

## Capture → relay

`interim`, `final` and `clear` in the shapes above, minus `seq` and `id` — the
relay assigns those on the way out. Plus:

```json
{"type":"status","t":1756590001.0,"audio_level":0.34,"audio_peak":0.71,
 "clipping":false,"stream_open":true,
 "backend":"deepgram","available_backends":["deepgram","whisper","mock"],
 "has_deepgram_key":true,
 "device":"Scarlett 2i2 (in 1)","device_index":3,
 "audio_devices":[{"index":3,"name":"Scarlett 2i2","hostapi":"Core Audio",
   "channels":2,"default_samplerate":48000,"is_default":true}],
 "last_final_t":1756589994.5,"error":null}
```

An `interim` or `final` from the capture client may also carry `speech_end`:
unix seconds when the last word of that text was actually spoken, as best the
backend knows (Deepgram reports where in the audio each result ends; the mock
says "now"; a backend that cannot tell omits it). The relay measures caption
latency as its own receipt time minus `speech_end`, keeps a rolling window of
200, and reports median and p95 in the status snapshot's `latency` block. The
field itself never reaches viewers.

```json
{"type":"final","text":"Our next speaker is Dr. Vasquez.","t":1756590003.4,
 "speech_end":1756590002.6}
```

Roughly 4× per second. `audio_level` is 0–1 RMS over the last frame and is
reported whether or not the ASR stream is open, so the booth can confirm the
microphone before any billable connection exists. `error` is a short human
string for the status page only — it never reaches a viewer.
`audio_devices` is the capture machine's current input list; `device_index`
identifies the active one. `available_backends` is only the backends this
machine can actually run (`capture/backends/support.py`'s `supported()`), not
every name in the registry - so `apple_speech` is absent on a Mac older than 26,
and the status page cannot offer a choice that could only ever report an
error. `has_deepgram_key` is whether `DEEPGRAM_API_KEY` is currently set in
the environment - never the key itself. They let the status page change
inputs, backends, and the Deepgram key at runtime.

---

## Relay → capture

```json
{"type":"control","action":"open_stream"}
```

| `action` | Effect |
|---|---|
| `open_stream` | connect the ASR backend — for Deepgram this starts billing |
| `close_stream` | disconnect the backend, keep capturing audio levels |
| `clear` | emit a `clear` to viewers |
| `reload_keyterms` | re-read `config/keyterms.txt` (reopens the stream with the new list) and `config/blocklist.txt` (no reopen needed - see below) |
| `select_audio_device` | switch to the integer `device` index; reopen ASR automatically if needed |
| `refresh_audio_devices` | query the capture machine's current input list again |
| `select_backend` | switch to the named `backend`; reopen ASR automatically if needed; refused (backend unchanged) if the name is unknown, if this machine cannot run it (`apple_speech` off macOS), or if it is `deepgram` with no API key set |
| `set_deepgram_key` | set `DEEPGRAM_API_KEY` (the `key` field) in the environment and save it into `.env` on disk; takes effect on the next `open_stream`, no restart needed |
| `set_profanity_filter` | set the boolean `enabled` field on the backend's built-in profanity filter (bleeps common strong language); takes effect on the next line, no reopen |
| `set_audio_gain` | software boost on the input, integer `gain_db` 0-24; applied to the next block, saved to `[capture] gain_db` so it survives a restart |
| `relay_moving` | sent by the relay itself, never on an operator's request (`/api/control` refuses it): the relay is about to restart on the integer `port`. A capture client whose relay is on this machine rewrites its relay URL and reconnects there instead of retrying the old port |

`reload_keyterms` reloading the blocklist needs no reconnect because blocking
happens in `ASRBackend._emit()`, after transcription, the same way on every
backend - unlike keyterms, which a specific backend has to be told about at
connect time.

Originates from the status or settings page via `POST /api/control`.
`set_deepgram_key` is
the one action whose field is a credential: `hub.py`'s own logging and the
console prints in `capture/__main__.py` never include it, only that the
action happened.

Two further actions go through the same `POST /api/control` endpoint but are
**handled by the relay itself and never reach the capture client** -
`hub.RELAY_ACTIONS`, checked before `CONTROL_ACTIONS`:

| `action` | Effect |
|---|---|
| `mark_video_start` | set the transcript recorder's zero point to now; returns `{"zero_time": ...}` |
| `reset_transcript` | clear the recorded transcript (all languages); does not touch live captions |

---

## Sequence of a typical line

```
capture → relay   {"type":"interim","text":"our next"}
relay   → viewer  {"type":"interim","seq":42,"text":"our next"}
capture → relay   {"type":"interim","text":"our next speaker is doctor"}
relay   → viewer  {"type":"interim","seq":43,"text":"our next speaker is doctor"}
capture → relay   {"type":"final","text":"Our next speaker is Dr. Vasquez."}
relay   → viewer  {"type":"final","seq":44,"id":"7f3a1c9e:44","text":"Our next speaker is Dr. Vasquez."}
```

The viewer shows one line the whole time. It is rewritten three times and
committed once.

---

## Status channel

The status page is not a viewer. It connects to `/ws/status` and receives a
full snapshot roughly every 400 ms — no deltas, no history, so a status page
that reconnects is immediately correct. `GET /api/status` returns the same
object once, plus a `relay` block.

```json
{
  "type": "status",
  "t": 1756590105.2,
  "session_id": "7f3a1c9e",
  "uptime": 1893.4,
  "seq": 4821,
  "source": {
    "connected": true,
    "peer": "127.0.0.1:51544",
    "backend": "deepgram",
    "available_backends": ["apple_speech", "deepgram", "whisper", "mock"],
    "has_deepgram_key": true,
    "device": "Scarlett 2i2 (in 1)",
    "device_index": 3,
    "audio_devices": [{"index": 3, "name": "Scarlett 2i2",
                       "hostapi": "Core Audio", "channels": 2,
                       "default_samplerate": 48000, "is_default": true}],
    "status_age": 0.21,
    "error": null
  },
  "audio":   {"level": 0.27, "smoothed": 0.19, "peak": 0.64, "clipping": false,
              "state": "live", "age": 0.4},
  "latency": {"final":   {"samples": 200, "last": 1.3, "median": 1.4, "p95": 2.1},
              "interim": {"samples": 200, "last": 0.6, "median": 0.7, "p95": 1.1}},
  "asr":     {"stream_open": true, "state": "producing", "finals": 412,
              "interims": 5133, "last_final_age": 1.8,
              "last_final_text": "Our next speaker is Dr. Vasquez."},
  "viewers": {"total": 34, "wall": 2, "phone": 31, "status": 1},
  "youtube": {"configured": true, "enabled": false, "pushed": 118, "error": null,
              "url_hint": "upload.youtube.com/closedcaption?cid=…k3Qz", "lang": "en-US"}
}
```

`youtube` is relay-local state, not reported by the capture client - see
"Pushing captions to YouTube Live" below. `configured` is whether a `url` is
set at all; `enabled` is the operator's toggle, which can only be turned on
when `configured` is true. The URL itself is never sent back - it grants
caption-posting capability to whoever has it, and this snapshot goes to every
status client several times a second - only `url_hint` (host, path, and the
last four characters of `cid`) so the status page can say which stream is
saved.

The relay decides what the words mean, so every status display agrees:

| Field | Values |
|---|---|
| `audio.state` | `live`, `clipping`, `silent`, `stale`, `no_source` |

`audio.peak` is the loudest sample in the last frame on the same 0–1 scale as
`level`; `clipping` is true for a few seconds after a frame had a run of
samples at full scale, and `audio.state` says `clipping` for as long as it is.
`latency.final` and `latency.interim` are seconds from speech to relay over a
rolling window of the last 200 lines (see `speech_end` above), or `null` when
the running backend has never reported where its words were spoken. `median`
is the number to quote; `p95` is the number to worry about.
| `asr.state` | `producing`, `open_no_text`, `closed`, `no_source` |

`audio.level` is the instantaneous RMS, for a meter. `audio.smoothed` is an
exponential moving average, and it — not the raw level — decides `live`, so one
stray sample of room tone can never claim audio is arriving. The state holds
`live` for a few seconds after speech stops, so it does not flicker between
sentences.

`GET /api/status` adds:

```json
{"relay": {"host": "0.0.0.0", "port": 8000, "ip": "192.168.8.169",
           "token_required": true, "config": "config/event.toml"}}
```

`ip` is the relay's LAN address, which is what the status page prints for
phones and for the ProPresenter URL.

## Transcript export

```
GET /api/transcript.srt?lang=en&offset=0.8      SubRip           application/x-subrip
GET /api/transcript.vtt?lang=en&offset=0.8      WebVTT           text/vtt
GET /api/transcript.txt?lang=en                 [HH:MM:SS] text  text/plain   (?timestamps=0 for bare text)
GET /api/transcript.json?lang=en                cue list         application/json
GET /api/transcript.csv?lang=en                 one row per cue  text/csv
  ->  200 with Content-Disposition: attachment; filename="captions-YYYYMMDD-HHMM-<lang>.<fmt>"
  ->  404 if nothing has been recorded for that language, or the extension is unknown
  ->  400 if lang is not a plain language code, or offset is outside 0-10
```

Cues follow the DCMP presentation rules: at most two lines of at most 42
characters, a long line split into consecutive cues that share its time
window, a duration a reader can manage (17 characters per second, at least
1.2 s, at most 7 s), and an 80 ms gap between neighbours. Translated lines
inherit the source line's window. `offset` (seconds, 0-10) shifts every cue
earlier to take the recognizer's own delay back out; about 0.8 suits Deepgram.

All five formats are the same cue list (`relay/transcript.py`,
`TranscriptRecorder.cues()`) rendered differently, so a cue has the same
number, start and end in every file. The exception is `.txt`: it is a
transcript to read, not captions to display, so it keeps one recorded line
per line rather than the display split. `lang` defaults to the source
language. All operator-only, like `/api/status`.

The `.json` shape:

```json
{"language": "es", "zero_time": 1756590000.0, "zero_marked": true, "offset": 0.0,
 "cue_count": 1,
 "cues": [{"index": 1, "start": 3.5, "end": 6.9,
           "text": "Bienvenidos a la capilla.", "utterance": "7f3a1c9e:44"}]}
```

`utterance` is the id shared between a source line and its translations (see
`final` above), which is how a Spanish `.json` lines up with the English one.

## Preflight check

```
POST /api/preflight    {}
  ->  200 {"ok": true, "verdict": "warnings",
           "headline": "Ready, with 2 things worth a look before the event.",
           "counts": {"fail": 0, "warn": 2, "ok": 6},
           "checks": [{"id": "wall", "title": "Wall / overlay", "status": "warn",
                       "detail": "No wall view is connected.",
                       "action": "Open the wall URL in ProPresenter, OBS or vMix ..."},
                      ...],
           "probes": {"api.deepgram.com": {"ok": 3, "failed": 0, "median_ms": 61, "worst_ms": 74, "error": null}},
           "ran_at": 1756590200.0, "duration_ms": 412}
  ->  401 without the operator token
```

`verdict` is `ready`, `warnings` or `not_ready`; `headline` is the sentence to
show. Each check has an `id` (stable, for tests and tooling), a `title`, a
`status` of `ok` / `warn` / `fail` / `skip`, a `detail` (what was found) and
usually an `action` (what to do). Failures sort first. The same report is
carried as `preflight` in every status snapshot until the next run. Takes a
few seconds: it does a TLS handshake, three times, to each service the setup
uses. Changes nothing.

The capture client feeds two of the inputs in its `status` message:
`host` - `{"available": true, "cpu_percent": 22.0, "cpu_count": 8,
"mem_percent": 61.0, "mem_available_mb": 6100, "own_cpu_percent": 3.0,
"busiest": [{"name": "ProPresenter", "cpu_percent": 12.0}], "age": 1.2}` -
sampled every two seconds with psutil (`available: false` and nulls when it
is not installed), and `device_busy`, true when opening the audio input
failed in a way that means another program holds it. Both appear in the
status snapshot as `host` and `source.device_busy`.

## Changing the port

```
POST /api/relay    {"port": 8001}
  ->  200 {"ok": true, "port": 8001, "changed": true, "previous": 8000, "capture_clients_told": 1}
  ->  200 {"ok": true, "port": 8000, "changed": false}      already on it
  ->  400 port outside 1024-65535; 409 another program holds it; 401 without the operator token
```

The port is saved into `[relay] port` in event.toml first, then every
connected capture client is sent `relay_moving`, then this response goes
out, then the relay asks its server to exit. The process that runs the
server - `python -m relay`, the launcher's subprocess, the menu bar app -
starts it again on the new port; nothing else about the session survives
(a new `session_id`, so viewers clear and reconnect on their own). The
operator cookie is not port-specific, so the Settings page just moves over.

At start-up the reverse case is handled without asking: if the configured
port is taken by another program, the relay starts on the next free one
above it (up to twenty tries) and says so.

## Operator authentication

Everything that changes state or reveals it - `/status`, `/tune`, any
`viewer/*.html` not explicitly public, `GET /api/status`,
`GET /api/transcript.srt`, every `POST /api/*`, and `/ws/status` - needs the
operator token. It is `CAPTIONS_OPERATOR_TOKEN` in `.env`, generated the
first time the relay (or the launcher) starts.

Two ways to present it:

- **Browser.** Open `/status?token=...` once (the launcher and the menu bar
  app open exactly that link). The relay answers with a `303` to `/status`
  and sets a `captions_operator` cookie (`HttpOnly`, `SameSite=Strict`, 30
  days). Every later page load, fetch, and `/ws/status` handshake from that
  browser carries the cookie. `SameSite=Strict` is what makes the JSON
  `POST`s safe from cross-site request forgery: a request started by another
  site never carries the cookie, and the relay additionally refuses a
  cookie-authenticated `POST` whose `Sec-Fetch-Site` says cross-site.
- **Scripts.** Send `Authorization: Bearer <token>` (e.g.
  `tools/youtube_upload.py --from-relay`).

Without either, pages get a short `401` page saying where the link is, and
API routes get `401 {"detail": "operator token required"}`. This is separate
from, and in addition to, the public-hostname gate below: through the public
hostname these routes are `404` even with a valid token.

The LAN is not a trust boundary - the QR code invites the audience onto it -
which is why being on it proves nothing here.

## Control API

```
POST /api/control    {"action": "open_stream"}
  ->  200 {"ok": true, "action": "open_stream", "delivered_to": 1}
POST /api/control    {"action": "select_audio_device", "device": 3}
  ->  200 {"ok": true, "action": "select_audio_device", "delivered_to": 1}
  ->  400 if the action is unknown or the device index is invalid
POST /api/control    {"action": "select_backend", "backend": "apple_speech"}
  ->  200 {"ok": true, "action": "select_backend", "delivered_to": 1}
  ->  400 if the action is unknown or backend is not a short string
POST /api/control    {"action": "set_deepgram_key", "key": "..."}
  ->  200 {"ok": true, "action": "set_deepgram_key", "delivered_to": 1}
  ->  400 if the action is unknown or key is not a short string
POST /api/control    {"action": "set_profanity_filter", "enabled": true}
  ->  200 {"ok": true, "action": "set_profanity_filter", "delivered_to": 1}
  ->  400 if enabled is not a boolean
```

```
POST /api/control    {"action": "caption", "text": "[Applause]"}
  ->  200 {"ok": true, "action": "caption"}
  ->  400 if text is empty, over 200 characters, or carries control characters
```

A `final` may also carry `"late": true`: the capture client held it while the
relay was unreachable and sent it on reconnect. Its `t` is the time it was
actually committed, so the transcript stays honest; viewers render it like any
other line.

`caption` is handled by the relay itself and needs no capture client: the text
is committed exactly like a final from the recognizer - wall, phones, replay
buffer, transcript, translation, YouTube - with `"kind": "note"` on the frame.
It is how an operator captions what speech recognition cannot hear
(`[Applause]`, `[Music]`, `[Inaudible]`) and names speakers (`>> Dr. Vasquez:`),
both of which FCC and DCMP caption-quality guidance require. The status page has
one-tap buttons for the common ones.

`delivered_to` is the number of capture clients that received it — `0` means
nothing is listening, which the status page reports rather than pretending the
action worked.

## Event state

```
POST /api/event           {"state": "live"}
POST /api/event           {"title": "Fall Convocation", "message": "...", "notice": "...", "pause_message": "Intermission"}
  ->  200 {"ok": true, "state": "live", "title": ..., "image": false, "image_v": 0, ...}
  ->  400 if state is not starting|live|paused|ended, or a field is too long
PUT  /api/event/image     (body: the raw JPEG/PNG/WebP bytes, at most 5 MB)
  ->  200 {"ok": true, ..., "image": true, "image_v": 1756589000}
  ->  400 if the bytes are not a JPEG, PNG, or WebP; 413 if over 5 MB
DELETE /api/event/image   ->  200
GET  /api/event/image     ->  the image (public; cache by ?v=<image_v>)
PUT / DELETE / GET /api/event/logo   ->  the same, for the logo (stored as config/logo.<ext>)
```

`logo_alt` is one more text field on `POST /api/event`, up to 80 characters.
So is `disclaimer`, up to 200 - see the `event` message above for how it
differs in how viewers treat it (shown once per device, not per state).

All operator-only except the `GET`. Any subset of fields may be sent. Every
change is broadcast as an `event` frame and written to `[event]` in
`config/event.toml`; the image is stored as `config/welcome.<ext>`. The type is
sniffed from the bytes, never taken from the header, and SVG is refused because
it can carry scripts. `/api/site` also carries `event`, so the phone page can
draw the welcome screen before its websocket is up.

`clear` is the exception: the relay applies it to viewers itself, in addition to
forwarding it, so the wall blanks even when no capture client is connected.

## Hardening details

- Every viewer page is served with a `Content-Security-Policy` whose
  `script-src` is the SHA-256 hash of that page's own inline script, computed
  when the page is served; nothing external is allowed, pages may be framed
  only by this origin (the tune page's preview), and forms and plugins are off.
  Every other response carries `default-src 'none'`, plus `nosniff`,
  `Referrer-Policy: no-referrer` and `X-Frame-Options: SAMEORIGIN`.
- JSON bodies over 16 KiB are refused with `413` before they are read;
  websocket frames over 64 KiB close the connection (`1009`); a viewer that
  sends anything is closed (`1008`).
- `/api/qr.svg` encodes only this relay's own addresses and accepts colours
  as `#rgb`/`#rrggbb` only.
- `cloudflared` and the Apple speech helper are started with a minimal
  environment (`PATH`, `HOME`, locale, TLS certificate variables) rather than
  the relay's, which holds the API keys.
- The packaged macOS app keeps `event.toml`, `.env` and uploaded images in
  `~/Library/Application Support/Campus Captions/`, never inside the bundle.

## Exposing the relay publicly (a tunnel)

If `[relay] public_hostname` (config/event.toml) is set - typically to a
Cloudflare Tunnel or similar domain that makes this relay reachable from
outside the booth LAN - every request is checked against its `Host` header.
Only `/`, `/wall`, `/phone`, `/qr`, `/api/site`, `/api/qr.svg`, and
`/ws/viewer` work when the `Host` matches `public_hostname`; `/status`,
`/tune`, `GET /api/status`, `POST /api/control`, `POST /api/youtube-captions`,
`POST /api/translation`, `POST /api/tunnel`, `/ws/status`, and `/ws/source` all
reject with 404 (HTTP) or a connection refusal (WebSocket) - never a redirect
to a login, just as if the route did not exist, and regardless of any operator
token presented. `/` itself serves the phone page rather than redirecting to
the operator dashboard for a public `Host`.

This gate holds only when whatever sits in front of the relay forwards the
original `Host` unchanged (Cloudflare Tunnel does). A proxy that rewrites
`Host` to `localhost:8000` would defeat it - which is why the operator token
above is the primary control and this is the second layer. Two related rules
keep it from failing open: the tunnel is never auto-started at boot while
`public_hostname` is empty, and `POST /api/tunnel` refuses to clear the
hostname while the tunnel is enabled.

## Direct ProPresenter output

```
POST /api/propresenter    {"host": "127.0.0.1", "port": 1025, "surface": "stage_message"}
POST /api/propresenter    {"surface": "message", "message": "Live Captions", "token": "Caption"}
POST /api/propresenter    {"language": "es", "reveal": "word"}
POST /api/propresenter    {"lines": 2, "chars_per_line": 42}
POST /api/propresenter    {"enabled": true}       start delivering (probes first)
POST /api/propresenter    {"enabled": false}      stop, clearing the surface
POST /api/propresenter    {"probe": true}         re-check the connection now
POST /api/propresenter    {"clear": true}         take the caption off the surface
  ->  200 {"ok": true, "configured": true, "enabled": true, "state": "connected",
           "host": "127.0.0.1", "port": 1025, "surface": "stage_message",
           "message": "Live Captions", "token": "Caption", "message_id": null,
           "language": "", "reveal": "line", "lines": 2, "chars_per_line": 42,
           "interims": false,
           "version": {"name": "ProPresenter", "platform": "mac", "api_version": "v1"},
           "pushed": 118, "cleared": 3, "failures": 0, "error": null,
           "latency": {"samples": 118, "median_ms": 14, "p95_ms": 38, "last_ms": 13}}
  ->  400 on a bad field; 401 without the operator token
```

Any subset of the settings may be sent together; they are saved into
`[propresenter]` in event.toml. The same object appears as `propresenter` in
every status snapshot. `reveal` is `line` (finished sentences only) or `word`
(also stream the in-progress text); `interims` is the boolean this setting
shipped under, still accepted on the way in and still reported on the way out,
and it means exactly `reveal == "word"`. `lines` is capped at 2 and
`chars_per_line` at 12-120: the caption is wrapped to at most that many lines
of that width before it is sent, and a longer sentence is paged. `state` is one of `off`, `connecting`, `connected`,
`degraded` (recent failures, still trying), `disconnected` (ProPresenter is
not answering; probing every few seconds; the Web Fill fallback is what is on
screen), `unsupported` (it answered with an API version this build does not
know). `latency` is the ProPresenter round trip only - what this output adds
on top of the caption pipeline. The relay talks to ProPresenter's own network
API (`GET /version` - at the root, unlike everything else;
`PUT|DELETE /v1/stage/message`, `GET /v1/messages`,
`POST /v1/message/{id}/trigger`, `GET /v1/message/{id}/clear`) and nothing
else; see relay/propresenter.py. Pausing or ending the event (`POST
/api/event`) clears the surface.

## Wall output keys

```
POST /api/overlay-keys    {"create": "obs"}
  ->  200 {"ok": true, "keys": [{"name": "obs", "key": "3UGo3AhKI0CU",
                                 "url": "http://192.168.8.169:8000/wall?key=3UGo3AhKI0CU"}]}
POST /api/overlay-keys    {"revoke": "obs"}
  ->  200 {"ok": true, "keys": []}
  ->  404 if no key has that name; 400 on a bad name
```

The list also appears as `overlay_keys` in every status snapshot. Keys are
saved into `[overlay] keys` in event.toml as `"name:key"` strings. While the
relay has any keys, `WS /ws/viewer?role=wall` without a valid `key` is
accepted and immediately closed with code `4403`; revoking a key closes the
live sockets that used it with the same code. With no keys, `key` is
ignored. Phones never need one.

## Pushing captions to YouTube Live

```
POST /api/youtube-captions    {"url": "http://upload.youtube.com/closedcaption?cid=..."}
  ->  200 {"ok": true, "configured": true, "enabled": false, "pushed": 0, "error": null, "url_hint": "upload.youtube.com/closedcaption?cid=…", "lang": "en-US"}
  ->  400 unless the host is upload.youtube.com (every committed line is POSTed
          there, so an arbitrary host would be a transcript exfiltration channel)
POST /api/youtube-captions    {"enabled": true}
  ->  200 {"ok": true, ...same shape}
  ->  400 if enabled is not a boolean, or true with no url configured yet
```

Either field may be sent alone or together in one request - the status page's
Save button sends `url`, its Enable/Disable buttons send `enabled`. Unlike
`/api/control`, none of this touches the capture client: `{"url": ...}` calls
`relay/youtube.py`'s `configure()` and also writes it into `config/event.toml`
(`relay/config.py`'s `save_youtube_url`, a targeted text patch of just the
`[youtube]` section - no config file needs to be opened by hand), and
`{"enabled": ...}` toggles a background task that POSTs every `final` line it
sees to that URL, in the same wire format professional captioning vendors
already use to feed YouTube Live (`?seq=N&lang=xx` query params, `text/plain`
body, one request per line, `seq` starting at 1 and incrementing, reset
whenever the URL changes). `interim` lines are never sent - YouTube's endpoint
has no delete/backspace mechanism to correct them with, so only committed text
goes out. Expect YouTube's own pipeline to add substantial further delay
(their documentation for other captioning vendors cites 15+ seconds) on top of
whatever latency the ASR backend itself has.

## Adding an Azure Translator key live

```
POST /api/translation    {"provider": "apple", "languages": "es, fr"}
POST /api/translation    {"provider": "azure", "key": "...", "region": "eastus", "languages": "es, fr"}
  ->  200 {"ok": true, "provider": "apple", "targets": ["es", "fr"], "calls": 0, ...}
  ->  400 if none of provider/key/region/languages are present, provider is
      not apple or azure, or languages doesn't look like a comma-separated
      list of short codes (es, fr, zh-Hans)
```

`provider`, `key`, `region`, and `languages` may be sent alone or together - the status
page's Save button sends whichever fields are non-empty. Leaving `languages`
out of the request means "don't touch the target list", not "clear it" -
unlike the YouTube URL box above, there's no way to explicitly clear the
language list from the GUI yet. Like `/api/youtube-captions`, this never
touches the capture client: `key`/`region` are written to `.env`
(`relay/config.py`'s `save_env_var`, same mechanism the Deepgram key on the
status page uses) and `languages` to `config/event.toml`'s `[languages]`
section (`save_translate_settings`), then the relay's `TranslationWorker` is
torn down and rebuilt against the new settings immediately - a key pasted
mid-event takes effect on the next committed caption line, no restart.
Response fields mirror `relay/translate.py`'s `TranslationWorker.stats()` -
`provider` reads `"none"` when no key has been set yet, even if `languages`
was saved.

## Running a Cloudflare Tunnel live

```
POST /api/tunnel    {"hostname": "captions.example.com", "token": "..."}
  ->  200 {"ok": true, "installed": true, "configured": true, "enabled": false, ...}
POST /api/tunnel    {"enabled": true}
  ->  200 {"ok": true, ..., "public_hostname": "captions.example.com"}
  ->  400 if enabled: true with no token or no hostname set yet
  ->  400 if hostname is "" while the tunnel is enabled (disable it first),
          or is not a plain DNS name
```

`hostname`, `token`, and `enabled` may be sent alone or together. Like
`/api/youtube-captions` and `/api/translation`, this never touches the
capture client: `token` goes to `.env` as `CLOUDFLARE_TUNNEL_TOKEN`
(`save_env_var`, same mechanism as the Deepgram key); `hostname` becomes
`RelayConfig.public_hostname` immediately - see "Exposing the relay
publicly" above - and both `hostname` and the operator's on/off intent are
saved into `config/event.toml` (`save_tunnel_settings`) so a restart comes
back the way it was left.

`enabled: true` spawns `cloudflared tunnel run` (`relay/tunnel.py`) with the
token passed via the `TUNNEL_TOKEN` environment variable rather than
`--token`, so it never appears in `ps` output. `cloudflared` itself is
located via `shutil.which()` plus the well-known Homebrew paths, since a
GUI-launched app doesn't inherit a shell's PATH. Its output is mirrored into
this process's own log (see "Log files" in SETUP.md) with a `[cloudflared]`
prefix; `installed`/`configured`/`enabled`/`running`/`connected`/`error` in
the response and in `/api/status`'s `tunnel` field reflect, respectively:
whether the binary was found, whether a token has been saved, whether the
operator wants it running, whether a subprocess is currently alive, and
whether cloudflared has actually registered a connection to Cloudflare's
edge. An unexpected exit retries automatically after a few seconds unless
cloudflared reported the token itself as invalid, in which case retrying
would never succeed and `enabled` is cleared instead.

## What the relay rejects

Anything arriving on `/ws/source` that is not in the table above is dropped
without reply: malformed JSON, non-objects, unknown types, `final` with empty or
whitespace-only text, and `control` frames (a source may not command the relay).
Text is stripped of control characters, collapsed to a single line, and cut at
2000 characters before it is numbered. Viewers render with `textContent`, so
markup in a transcript displays as literal text and cannot execute.
