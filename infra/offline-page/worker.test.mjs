// node infra/offline-page/worker.test.mjs
import assert from "node:assert/strict";
import worker, { wantsPage } from "./worker.js";

const page = (extra = {}) => new Request("https://faulknercaptions.com/phone",
  { headers: { accept: "text/html,application/xhtml+xml", ...extra } });
const api = () => new Request("https://faulknercaptions.com/api/site",
  { headers: { accept: "*/*" } });
const ws = () => new Request("https://faulknercaptions.com/ws/viewer",
  { headers: { upgrade: "websocket", accept: "text/html" } });

async function run(origin, request) {
  globalThis.fetch = async () => {
    if (origin instanceof Error) throw origin;
    return new Response("origin body", { status: origin });
  };
  const res = await worker.fetch(request);
  return { status: res.status, body: await res.text() };
}

// Mac up: everything passes through untouched.
assert.deepEqual(await run(200, page()), { status: 200, body: "origin body" });
assert.deepEqual(await run(404, page()), { status: 404, body: "origin body" });

// App closed (502) and Mac off (530): a person gets the friendly page.
for (const status of [502, 530]) {
  const r = await run(status, page());
  assert.equal(r.status, 503);
  assert.match(r.body, /aren't running right now/);
}
// A network failure reaching the origin counts as down too.
assert.equal((await run(new Error("boom"), page())).status, 503);

// Scripts and the WebSocket get the real error, not an HTML page.
assert.deepEqual(await run(502, api()), { status: 502, body: "origin body" });
assert.deepEqual(await run(530, ws()), { status: 530, body: "origin body" });

assert.equal(wantsPage(new Request("https://x/", { method: "POST",
  headers: { accept: "text/html" } })), false);

console.log("offline-page worker: all checks passed");
