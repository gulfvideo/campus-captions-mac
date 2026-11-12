# companion-module-campus-captions

A [Bitfocus Companion](https://bitfocus.io/companion) module for the Campus
Captions relay, so the caption system sits on the same control surface as the
switcher and the playback instead of in a browser tab nobody can reach
mid-service.

It adds nothing to the relay. Every action here is an endpoint the operator
API already exposed; the module wraps them in something an AV operator can
press, and adds the half a Generic HTTP connection cannot do — keys that
reflect live state.

## Installing it

Needs Companion 4.2 or newer.

1. Download `campus-captions-companion.tgz` from the
   [latest release](https://github.com/gulfvideo/campus-captions-mac/releases/latest).
2. In Companion, open **Modules** → **Import module package** and pick the file.
3. **Connections** → add **Campus Captions**. Address `127.0.0.1` if Companion
   is on the same Mac; token from the menu bar's **Copy Token for Companion**.
4. **Buttons** → **Presets** → Campus Captions, and drag keys onto a page.

### Working on it

`npm install`, then point Companion's **Developer modules path** at the
`integrations` folder (not this folder). Companion reloads the module when
`src/` changes. Check against a running relay with:

```bash
CAPTIONS_URL=127.0.0.1:8000 CAPTIONS_TOKEN=<operator token> node test/smoke.js
```

Use a throwaway relay: the smoke test changes the event state and title.

## How it talks to the relay

Two connections, both authenticated with the operator token as
`Authorization: Bearer`:

- **HTTP POST** to `/api/control`, `/api/event`, `/api/event/next`,
  `/api/youtube-captions`, `/api/propresenter` and `/api/tunnel` for actions.
- **A websocket** to `/ws/status` for state. The relay already pushes a full
  snapshot roughly every 0.4 s, so feedbacks and variables stay current
  without this module polling a machine that is also running speech
  recognition.

The relay accepts a bearer token from any client — the same-origin rule that
protects the browser session applies only to the cookie path — so none of this
required a change on the relay side.

## Layout

```
companion/manifest.json   module metadata Companion reads
companion/HELP.md         the help shown in Companion's UI
src/index.js              lifecycle: config, connect, teardown
src/api.js                HTTP + the status websocket, with reconnect
src/actions.js            what a button can do
src/feedbacks.js          what makes a button light up
src/variables.js          what a button can print
src/presets.js            the ready-made page
src/config.js             the connection settings form
test/smoke.js             drives every action against a live relay
```

## Notes for whoever maintains this

- **The token is a `secret-text` field**, so Companion keeps it in its secrets
  store and reports only the key to its web UI, never the value. It arrives as
  the third argument to `init()` and the second to `configUpdated()` — not on
  `this.config`. `index.js` recombines it with host and port into `this.conn`.
- **Targets module API 1.14**, which Companion accepts across 3.x, 4.x and
  5.x. Moving to the 2.x API means at minimum replacing `runEntrypoint`, which
  does not exist there.
- **Control is LAN-only by design.** If a future version needs to be driven
  from off-site, that is a relay decision about the tunnel, not something to
  work around here.

## Building an importable package

Companion can import a module as a `.tgz` instead of loading it from the
developer path, which is how a non-developer should install it. The official
toolchain produces exactly the layout Companion's own offline bundle uses — a
single bundled CommonJS `main.js` with every dependency inlined, a minimal
`package.json`, and `companion/`:

```bash
npx companion-module-build          # writes pkg/ and campus-captions-<version>.tgz
```

Then in Companion: **Modules → Import module package**, and pick the `.tgz`.
The release workflow does this on every `v*` tag and attaches it as
`campus-captions-companion.tgz`.

Two things the build does for you, which are easy to get wrong by hand:

- **Rewrites `entrypoint` to `../main.js`** and stamps `apiVersion` from the
  installed `@companion-module/base`. The manifest in this repo points at
  `../src/index.js` for developer-path loading; the built one does not.
- **`runtime.type` must be `node22` or `node26`.** Companion 5's manifest
  schema rejects anything else, including `node18` — even though its loader
  still handles node18, the schema does not, and the module is refused before
  it ever runs.

`@companion-module/tools` is pinned to 2.8.0 because 3.x requires
`@companion-module/base` 2.x, and this module targets the 1.14 API.
