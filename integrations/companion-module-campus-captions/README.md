# companion-module-campus-captions

A [Bitfocus Companion](https://bitfocus.io/companion) module for the Campus
Captions relay, so the caption system sits on the same control surface as the
switcher and the playback instead of in a browser tab nobody can reach
mid-service.

It adds nothing to the relay. Every action here is an endpoint the operator
API already exposed; the module wraps them in something an AV operator can
press, and adds the half a Generic HTTP connection cannot do — keys that
reflect live state.

## Loading it into Companion

The module is not published to Bitfocus, so Companion loads it from disk.

1. Install its dependencies (needs Node 18 or newer):

   ```bash
   cd integrations/companion-module-campus-captions
   npm install
   ```

2. In Companion, open the launcher window → the gear/settings icon →
   **Developer modules path**, and point it at the **`integrations` folder**,
   not at the module folder itself. Companion scans that folder for modules.

3. Restart Companion. Add a connection and search for **Campus Captions**.

Companion reloads a developer module when its files change, so edits to `src/`
take effect without restarting the whole app.

## How it talks to the relay

Two connections, both authenticated with the operator token as
`Authorization: Bearer`:

- **HTTP POST** to `/api/control` and `/api/event` for actions.
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
