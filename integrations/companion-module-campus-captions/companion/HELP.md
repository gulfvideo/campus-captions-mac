# Campus Captions

Control the live captioning relay from a Stream Deck: event state, the
recognition stream, canned captions, and keys that light up with what is
actually happening.

## Setting it up

| Field | What to put in it |
| --- | --- |
| **Address** | The LAN address of the Mac running Campus Captions, e.g. `10.0.1.24` |
| **Port** | `8000` unless it was changed |
| **Operator token** | `CAPTIONS_OPERATOR_TOKEN` from the app's `.env` |

To find the token: on the Mac, use the Campus Captions menu bar icon →
**Show Config Folder in Finder**, then open `.env` in that folder.

Two things that will otherwise cost you ten minutes:

- **Use the LAN address, not the public tunnel hostname.** The relay
  deliberately refuses control through the tunnel — that domain serves the
  audience only. A tunnel address here will fail with a 404.
- **Companion must be on the same network as the Mac.** If the Mac is on
  DHCP, give it a reserved address, or this connection breaks the next time
  its lease changes.

## Presets

Drag them straight onto a page. They come in four groups:

- **Event** — Start, Pause, Resume, End, and back to the welcome screen. Each
  lights up when the event is in that state.
- **Captions** — ASR stream toggle (red while running), Clear, Mark video start.
- **Canned captions** — `[Applause]`, `[Music]`, `[Inaudible]`. These commit a
  line exactly as the recogniser would, so they reach the wall, the phones,
  the transcript and any translation.
- **Status** — read-only keys: fault alarm, viewer count, audio state, backend.

## The one key worth having in your eyeline

**Fault alarm.** It goes red when the caption pipeline has a problem the
audience cannot see — no capture client, a dead microphone, a stream that
closed itself — and prints which. It is the difference between noticing a dead
mic because a key went red and noticing because someone waves at you from the
third row.

## Variables

`$(campus-captions:…)` — `event_state`, `asr_state`, `asr_stream_open`,
`audio_state`, `audio_level` (0–100), `backend`, `device`, `fault`,
`viewers_total`, `viewers_phone`, `viewers_wall`, `transcript_lines`,
`tunnel_connected`, `last_caption`.

## If the connection will not come up

| Connection says | Means |
| --- | --- |
| **Authentication failure** | The token is wrong. Copy it again from `.env` — it is long and easy to truncate. |
| **Disconnected** | Nothing is answering. Check the app is running and the address is right. |
| **Bad config** | Address or token is blank. |

A 404 on an action, with the connection otherwise fine, almost always means
the address is the public tunnel hostname rather than the LAN one.
