# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

**Primary — the person in the room reading the captions.** Deaf and
hard-of-hearing attendees, non-native English speakers, and anyone in a
seat where the speaker simply cannot be heard well. They are mid-event, often
holding their own phone, and they did not ask anyone's permission to need
this. They break ties: when audience legibility and operator convenience
conflict, legibility wins.

**Primary, close second — the volunteer in the booth.** Usually a student
worker or a church volunteer, not a trained AV engineer, often running the
switcher and the slides at the same time. They need to know in one glance
whether audio is arriving and text is coming back, and to recover without
reading documentation.

**Secondary — the person evaluating it.** AV directors, accessibility
services and worship/production staff deciding whether to run it at their own
venue. They meet the product through marketing and proof surfaces, not the
console.

## Product Purpose

Live captioning for events that today have none: chapel, lectures,
ceremonies, recitals, services, conferences, panels. One capture client turns
room audio into transcript events, a relay fans them out, and the same
transcript lands in three places at once — a broadcast graphic on the LED
wall, a scrollback page on every phone in the room, and the livestream.

Success is the number of events on a calendar that get captions at all,
run by the staff already in the building.

## Positioning

**The engines can run free and entirely on-device.** Apple's on-device
speech recognition and Whisper both transcribe with no account, no API key,
no per-hour billing and no network after the first model download.

Two claims follow that nobody billing by the hour can make: an event costs
nothing marginal to caption, and the room's audio never has to leave the
building. A captioning vendor cannot match a price of nothing. A cloud-only
competitor cannot promise the audio stays local.

Deepgram remains available for venues that want the lowest latency and accept
cloud transcription under their own account — it is an option the operator
chooses, not the floor the product stands on.

## Operating Context

- A booth or control position with a Windows or Mac computer already in it,
  and a feed off the mixer already being run — the same send that feeds the
  stream or the hearing loop.
- The wall output is consumed as a browser source: a ProPresenter web Prop,
  an OBS browser source, or a vMix title, laid over whatever is already on
  screen. Captions can also be sent into ProPresenter directly, with no web
  layer.
- Audience phones reach the relay over the venue LAN, usually via a QR page
  put on screen before doors. Reaching viewers outside the building requires
  a tunnel the venue sets up.
- A Bitfocus Companion module puts the caption controls on the same hardware
  control surface as the switcher, rather than in a browser tab nobody can
  reach mid-service.
- Events are rehearsed and run against a pre-event checklist and an in-app
  preflight check that returns one verdict: ready, ready with warnings, or
  not ready.

## Capabilities and Constraints

**Shipped and working.** Capture client, relay, and the viewer surfaces —
wall, phone, QR, status console, settings, setup wizard, audio tuner.
Transcription backends: Apple on-device (macOS 26+), Whisper (any OS),
Deepgram (paid, cloud), and a first-class mock/rehearsal backend that replays
a script with no audio hardware, no key and no spend. Optional live
translation with per-language replay buffers, so a phone joining mid-event
and selecting a language replays *that* language's history. Live caption push
to YouTube, plus a timed `.srt` export of every committed line. Keyterm
priming for campus proper nouns, and a blocklist. Installers for Windows and
macOS.

**Terminology this product uses.** Wall view, phone view, QR page, relay,
capture client, status page, preflight, keyterms, rehearsal/mock backend;
*interims* (partial text that changes several times a second, never
translated) versus *committed lines* (final, recorded, translated, exported).

**Constraints that must be preserved.**
- The installers are not code-signed, so each shows a one-time OS warning on
  first run. Do not describe installation as frictionless.
- The relay always requires a source token; the audience shares the booth
  network, so an untokened relay was never safe.
- When a tunnel hostname is configured, traffic arriving on it gets read-only
  viewer access and nothing else — the operator surfaces 404.
- `robots.txt` disallows everything on the relay: a live transcript of a room
  must never reach a search index.
- The Companion module is not published to Bitfocus and is loaded from disk.
- Accuracy depends on the engine, the feed off the board, and whether a
  keyterm list was supplied. No accuracy figure has been measured.

**Open product decisions — do not invent answers.**
- Not for sale. No price, no tier, no licensing model has been set.
- Whether the product becomes commercial, free, or open is undecided.

## Brand Commitments

- The product name is **Campus Captions**.
- **No Faulkner University branding, naming or marks on any public-facing
  surface.** The product is built and run at Faulkner, and that is internal
  fact, not public identity. Future work must not add the affiliation —
  including as social proof — without asking.
- Atkinson Hyperlegible ships self-hosted and must remain **available** as a
  reader-selectable caption face — the phone view's "Dyslexia-friendly"
  option, and `?font=atkinson` on the wall. The default caption face is a
  Helvetica/Arial system stack. The commitment is that the choice exists and
  that the Atkinson option is not swapped for a visually similar face; it is
  an accessibility decision, not a style preference.
- The phone view carries a configurable venue logo slot and a configurable
  per-event disclaimer. Venues brand their own audience view; the product
  does not brand it for them.

## Evidence on Hand

**Real.**
- A complete, running system used for actual events — not a prototype.
  Source at `capture/`, `relay/`, `viewer/`.
- Shipped installers via GitHub Releases on `koakley3/campus-captions`.
- The marketing site, live at `campuscaptions.app`, source at `site/`.
- `SETUP.md` — a genuine operator manual covering install, network, audio
  devices, ProPresenter, keyterms and a pre-event checklist.
- The Companion integration at `integrations/companion-module-campus-captions/`.

**Absent — must not be fabricated.** There are no testimonials, no named
customer venues, no case studies, no press, no measured accuracy or latency
benchmarks, no uptime figures, and no pricing. There is no logo file and no
photography of the system in use. Any of these appearing in future work would
be invented.

## Product Principles

1. **The audience breaks ties.** When legibility for the person reading
   conflicts with density or convenience for the operator, legibility wins.
2. **Never show a fault to a room.** Audience-facing surfaces have no
   spinners, error states or placeholders. When something breaks, the caption
   bar goes empty — not red.
3. **Free and local is the default path, not the degraded one.** The
   zero-cost, no-network configuration is the one a new venue lands on; paid
   cloud transcription is an upgrade someone opts into.
4. **Assume a volunteer, not an engineer.** Anything that cannot be
   recovered from without reading documentation is a defect.
5. **Say what it is.** The product's own limits — unsigned installers,
   variable accuracy, no price yet — get stated plainly rather than smoothed
   over. Overclaiming to an accessibility buyer is the fastest way to lose one.

## Accessibility & Inclusion

**WCAG 2.1 AA on every surface, the operator console included.** This is an
accessibility product; an inaccessible console would be indefensible. Treat
AA as the floor, not the target.

- The phone view gives the reader control over text size and color scheme.
  Those controls are a requirement, not a nicety.
- Atkinson Hyperlegible, drawn for low-vision legibility, is offered as a
  caption face on both the phone and the wall. Keeping that option is a
  requirement; making it the default is not.
- Reduced-motion preferences are honored across surfaces.

**Automatic speech recognition is not a legal accommodation, and the product
must never be presented as one.** Where a student or guest has a documented
accommodation requiring verbatim captioning, that remains a human CART
writer's job and the institution's disability services office's call. Campus
Captions addresses the much larger set of events that currently have no
captions at all. Any future copy implying legal compliance is wrong.
