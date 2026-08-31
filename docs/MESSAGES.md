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
| `/ws/viewer?role=wall\|phone` | wall view, phone view | relay → viewer |
| `/ws/source` | capture client | both ways |
| `/ws/status` | status page | relay → status |

`role` affects only how many committed lines are replayed on connect
(`wall` → 2, `phone` → 30). It changes nothing else.

`/ws/viewer` also takes **`lang`**. Each language is an independent stream with
its own replay buffer, so a viewer that asks for `lang=es` receives only Spanish
finals and replays Spanish history on reconnect. Omitting it means the source
language. A viewer never receives a language it did not ask for.

HTTP: `GET /wall`, `GET /phone`, `GET /status`, `GET /api/status`,
`POST /api/control`.

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
{"type":"hello","seq":0,"session_id":"7f3a1c9e","server_time":1756590000.12,
 "protocol":1,"lang":"en"}
```

`lang` echoes back the stream this viewer was actually joined to, which may
differ from what it asked for if the language is not available.

`hello` always carries `seq: 0` — it is the one exception to monotonic `seq`,
because the replay burst that follows carries the *original* sequence numbers of
those lines. On `hello` a viewer resets its dedupe state and, if `session_id`
differs from the one it last saw, clears the display: a new `session_id` means
the relay restarted and any text still on screen is stale.

### `interim`

```json
{"type":"interim","seq":42,"t":1756590001.9,"text":"our next speaker is doctor"}
```

Replaces the current uncommitted line in place. Superseded by the next `interim`
or `final`. Never breaks a line, never reflows, never enters phone scrollback as
its own entry. Interims are not replayed to late joiners.

### `final`

```json
{"type":"final","seq":43,"t":1756590003.4,"id":"7f3a1c9e:43",
 "lang":"en","text":"Our next speaker is Dr. Vasquez."}
```

Commits a line. A `final` replaces the in-progress line if one is showing,
otherwise it starts a new line. If the text is longer than the viewer's
characters-per-line setting the viewer splits it on word boundaries and commits
each piece as its own line — this is the only place a line break happens.

`id` is `{session_id}:{seq}` and is stable across replays, so the phone view can
dedupe when it reconnects.

`lang` is always present on `interim` and `final`. Translations arrive as their
own `final` messages on their own language stream, after the source line - so a
Spanish viewer sees committed lines only, and never interims. That is
deliberate: interims change several times a second, and translating them would
multiply cost by roughly twenty while making the text unreadable.

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

### `heartbeat`

```json
{"type":"heartbeat","seq":45,"t":1756590105.0,"session_id":"7f3a1c9e","viewers":12}
```

Every 5 seconds. A viewer that has seen no frame of any kind for 20 seconds
treats the link as dead even if the socket still claims to be open. A changed
`session_id` here has the same meaning as in `hello`.

---

## Capture → relay

`interim`, `final` and `clear` in the shapes above, minus `seq` and `id` — the
relay assigns those on the way out. Plus:

```json
{"type":"status","t":1756590001.0,"audio_level":0.34,"stream_open":true,
 "backend":"deepgram","device":"Scarlett 2i2 (in 1)",
 "last_final_t":1756589994.5,"error":null}
```

Roughly 4× per second. `audio_level` is 0–1 RMS over the last frame and is
reported whether or not the ASR stream is open, so the booth can confirm the
microphone before any billable connection exists. `error` is a short human
string for the status page only — it never reaches a viewer.

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
| `reload_keyterms` | re-read `config/keyterms.txt` and reopen the stream with the new list |

Originates from the status page via `POST /api/control`.

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
    "device": "Scarlett 2i2 (in 1)",
    "status_age": 0.21,
    "error": null
  },
  "audio":   {"level": 0.27, "smoothed": 0.19, "state": "live", "age": 0.4},
  "asr":     {"stream_open": true, "state": "producing", "finals": 412,
              "interims": 5133, "last_final_age": 1.8,
              "last_final_text": "Our next speaker is Dr. Vasquez."},
  "viewers": {"total": 34, "wall": 2, "phone": 31, "status": 1}
}
```

The relay decides what the words mean, so every status display agrees:

| Field | Values |
|---|---|
| `audio.state` | `live`, `silent`, `stale`, `no_source` |
| `asr.state` | `producing`, `open_no_text`, `closed`, `no_source` |

`audio.level` is the instantaneous RMS, for a meter. `audio.smoothed` is an
exponential moving average, and it — not the raw level — decides `live`, so one
stray sample of room tone can never claim audio is arriving. The state holds
`live` for a few seconds after speech stops, so it does not flicker between
sentences.

`GET /api/status` adds:

```json
{"relay": {"host": "0.0.0.0", "port": 8000, "ip": "192.168.8.169",
           "token_required": false, "config": "config/event.toml"}}
```

`ip` is the relay's LAN address, which is what the status page prints for
phones and for the ProPresenter URL.

## Public and operator routes

The relay serves two audiences, and `[site] mode` decides whether that
distinction is enforced.

| Route | `mode = "lan"` | `mode = "public"` |
|---|---|---|
| `/phone`, `/qr`, `/wall` | open | open |
| `/ws/viewer`, `/api/site`, `/api/qr.svg`, `/robots.txt` | open | open |
| `/status`, `/tune`, `/api/status`, `/ws/status`, `POST /api/control` | open | **admin token required** |

In public mode the operator routes answer **404**, not 403 — a visitor who does
not have the token should not learn they exist. The token is supplied as
`?admin=…` or an `X-Admin-Token` header, and the operator pages forward whatever
token they were opened with to their own API and websocket calls.

The rule is default-deny: any page not explicitly public is operator-only,
including one reached by filename through the catch-all route. A page added to
`viewer/` later is protected before anyone remembers to protect it.

`/robots.txt` disallows everything in both modes. A live transcript of a room
should never end up in a search engine.

## Control API

```
POST /api/control    {"action": "open_stream"}
  ->  200 {"ok": true, "action": "open_stream", "delivered_to": 1}
  ->  400 if the action is not one of the four in the control table above
```

`delivered_to` is the number of capture clients that received it — `0` means
nothing is listening, which the status page reports rather than pretending the
action worked.

`clear` is the exception: the relay applies it to viewers itself, in addition to
forwarding it, so the wall blanks even when no capture client is connected.

## What the relay rejects

Anything arriving on `/ws/source` that is not in the table above is dropped
without reply: malformed JSON, non-objects, unknown types, `final` with empty or
whitespace-only text, and `control` frames (a source may not command the relay).
Text is stripped of control characters, collapsed to a single line, and cut at
2000 characters before it is numbered. Viewers render with `textContent`, so
markup in a transcript displays as literal text and cannot execute.
