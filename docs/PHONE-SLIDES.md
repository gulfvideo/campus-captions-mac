# Slides + Captions (static preview)

Captions remain the default. For an event using simple static ProPresenter cues,
attendees can open **Aa → Slides + Captions**, choose either order, and adjust
slide space from 20–60% (default 35%). The caption history scrolls independently;
Pause/Resume and Jump to latest still apply only to captions. Enlarge slide opens
a dialog with Return to captions and Escape support. Layout and space are saved
on that device. Slide images do not provide a text alternative to their content;
operators should continue describing essential visual content in speech.

## Operator setup

In ProPresenter Network settings, note the API port. In the relay's private
`config/event.toml`, add/edit this section and restart the relay:

```toml
[slides]
enabled = true
host = "127.0.0.1"
port = 1025 # replace with the port shown in ProPresenter Network settings
screen = 0 # zero-based audience screen in the current Look
approved_hosts = []
```

The feature has its own connection configuration and never enables outgoing
ProPresenter captions. For another local machine, use a literal RFC1918 IPv4
address in both `host` and `approved_hosts`. Hostnames, URLs, public addresses,
link-local addresses, proxies and redirects are rejected. This allowlist is
local configuration only; no attendee or operator HTTP endpoint accepts an
upstream URL. Invalid optional configuration reports slides unavailable while
captions continue.

Enable audience output and the selected screen's Slide layer in its live Look.
Sharing operates only while the Campus Caption event is **live**. Pausing,
ending, or returning to starting revokes the cached slide. With `enabled=false`,
phones show no slide controls and the relay makes no slide API requests.

## Architecture and privacy

One background task reads visibility/current-cue state approximately every
0.75 seconds per relay process. It retrieves a thumbnail only when the active
presentation UUID/index changes or the prior image was revoked. It rechecks
visibility and identity after decoding before publishing. Phones never contact
ProPresenter. They poll `/api/slides` and receive only `available`, `visible`,
`revision`, `state`, and `stale`. The image URL is constructed locally as
`/api/slides/image/<revision>`; only the currently valid revision is served.
Both responses use no-store, including old-revision 404s.

Internal GET allowlist: `/v1/status/audience_screens`, `/v1/status/layers`,
`/v1/look/current`, `/v1/presentation/slide_index`, and the constrained current
cue thumbnail path. No focused presentation, playlists, next slide, notes,
macros, messages or command endpoints are requested. Upstream bodies are
immediately reduced to visibility and a UUID/integer pair; full JSON responses
are never exposed or logged. The standalone diagnostic also reads `/version`.

Requests have one-second socket timeouts and checked body-read deadlines.
JSON is capped at 64 KiB, images at 4 MiB and four million pixels. The relay
requires image/png, decodes the actual pixels with Pillow, rejects animation,
and re-encodes without metadata. Images are held only in memory. Revision IDs
are SHA-256 hashes of sanitized PNG bytes, never upstream identifiers.

Any detected error clears the cache immediately. Reads also expire an image
whose last successful check is over five seconds old. The phone clears images
when its state request fails or is stale, and retries automatically. An event
change increments a generation counter so an in-flight thumbnail cannot revive
an ended event. Network I/O and image decoding run off the caption event loop.

## Limits and verification

This is a **static cue preview**, not an exact audience-screen composite.
Transparent thumbnails may contain a checkerboard; background media, builds,
video playback, props and other layers are not reproduced. A theme or mask
override on the selected audience Look suppresses sharing because it changes
rendering. Editor Show can produce output with no identifiable active cue and
is therefore treated as no slide. Use this only with static cues whose preview
has been checked against the chosen audience screen. Editing a cue without
changing its identity does not refresh its thumbnail. A transient A→B→A between
polls cannot be detected by this API sampling strategy.

Real Mac results and outstanding manual cases are in
[PHONE-SLIDES-SPIKE.md](PHONE-SLIDES-SPIKE.md). Automated tests use a real local
fake HTTP server and run in CI; ProPresenter is not needed there.

Future full-motion work would need a separate capture/NDI or screen-output
pipeline, bounded encoding and fan-out, explicit audience-output selection,
latency/bandwidth measurements, and equivalent isolation from caption delivery.
No streaming or ProPresenter control capability is included here.
