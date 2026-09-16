# Activation codes

Each tester gets one code. It unlocks the app *and* authorises update
downloads — the same code, because two credential systems for one small group
of testers is one more than anybody will keep track of. The gate that already
serves updates ([`infra/update-gate/`](../infra/update-gate/), from the update
channel work) issues and signs them.

## What this does and does not do

Be clear about this before relying on it.

| | |
|---|---|
| Stops the app being passed around casually | **yes** — a copy without a code shows the activation window and goes no further |
| Cutting off one tester | **yes** — delete their row; the app stops at its next check |
| Knowing who activated | **yes** — the Worker logs the tester label |
| Forging a code | **no** — records are Ed25519-signed; the private half never ships |
| Stopping someone who edits the app bundle | **no** |

The last row is the honest limit. The check runs on the tester's machine, so
anyone willing to open the bundle and patch it out can. What raises that bar is
not more code here — it is a **Developer ID certificate**: under a hardened
runtime macOS refuses to launch a bundle whose signature no longer matches, so
patching the check breaks the app instead of unlocking it. Until then this is a
lock, not a vault. It is still worth having: it stops the realistic case, which
is a well-meaning volunteer forwarding a zip to another campus.

## Offline behaviour is deliberate

Once activated, the signed record is cached and keeps working for **30 days**
with no network at all. A venue whose wifi is down on a Sunday morning must
still be able to caption the service — an activation scheme that fails closed
during an event is worse than no scheme.

Revocation therefore takes effect within that window rather than instantly.
That is the right trade for this app. Shorten `Activation.graceDays` if you
disagree, but do not take it to zero.

## Setting it up

### 1. Generate the keypair

```bash
macapp/make-activation-keys.sh
```

Paste the public half into `Activation.publicKey` in
[`macapp/CampusCaptions.swift`](../macapp/CampusCaptions.swift), and store the
private half as a Worker secret:

```bash
cd infra/update-gate && npx wrangler secret put ACTIVATION_PRIVATE_KEY
```

`macapp/build.sh` refuses to build while the development key is still in the
source, so a build that would accept codes signed by a throwaway key cannot be
made by accident.

### 2. Add the route to the Worker

In `infra/update-gate/worker.js`, before the object-serving path:

```js
// POST/GET /activate — exchange a valid code for a signed activation record.
// Signed rather than a bare 200 so the app cannot be fooled by anything that
// can answer on this hostname; the private key never leaves the Worker.
if (new URL(request.url).pathname === "/activate") {
  const record = JSON.parse(await env.CODES.get(key));   // key: sha256 of the code
  const expires = Math.floor(Date.now() / 1000) + 60 * 60 * 24 * 45;
  const tester = record.tester || "unknown";
  const priv = await crypto.subtle.importKey(
    "raw", Uint8Array.from(atob(env.ACTIVATION_PRIVATE_KEY), c => c.charCodeAt(0)),
    { name: "Ed25519" }, false, ["sign"]);
  const signature = await crypto.subtle.sign(
    { name: "Ed25519" }, priv, new TextEncoder().encode(`${tester}|${expires}`));
  return new Response(JSON.stringify({
    tester, expires,
    signature: btoa(String.fromCharCode(...new Uint8Array(signature))),
  }), { headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });
}
```

The record is signed over `tester|expires` — exactly what `Activation.verify`
in the Swift reconstructs. Note the 45-day expiry against the app's 30-day
grace: the app re-checks well before the record actually lapses, so a working
tester never hits a hard stop, while a revoked one loses access within a month.

### 3. Issue codes

Unchanged from the update channel — `infra/update-gate/newcode.sh "Jane"`
prints the code and the command that authorises it. The same string is what the
tester types into the activation window.

## What a tester sees

First launch shows one window asking for the code, with the app in the Dock
while it is up. Paste, Return, done — the code is also written to `.env` as
`CAPTIONS_UPDATE_TOKEN`, so updates work without pasting it a second time in
Settings.

After that the window never appears again unless the code is revoked or 30 days
pass with no network.
