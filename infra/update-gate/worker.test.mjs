/**
 * The access gate's refusals. Run with:  node infra/update-gate/worker.test.mjs
 *
 * Worth testing directly rather than in staging: every case here is a way
 * somebody gets a build they were not given, and the failures are silent -
 * a gate that says yes too often looks exactly like a gate that works.
 */
import assert from "node:assert/strict";
import { test } from "node:test";
import worker from "./worker.js";

const CODE = "0123456789abcdef0123456789abcdef";
const DIGEST = "0eb2f1935b3b7f61a6d4d2b0c58cbec14d6ebd1b7c7ee5e2a3f0dbfa5f2b04e4";

async function sha256(text) {
  const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function env(entries = {}, objects = {}) {
  return {
    CODES: { get: async (k) => (k in entries ? entries[k] : null) },
    BUILDS: {
      get: async (k) =>
        k in objects
          ? { body: objects[k], httpEtag: '"e"', writeHttpMetadata() {} }
          : null,
    },
  };
}

async function call(url, { token, method = "GET", codes, objects } = {}) {
  const headers = token ? { Authorization: `Bearer ${token}` } : {};
  return worker.fetch(new Request(url, { method, headers }), env(codes, objects));
}

test("a valid code gets the object", async () => {
  const codes = { [await sha256(CODE)]: '{"tester":"jdean"}' };
  const res = await call("https://updates.gulfvideo.io/latest.json", {
    token: CODE, codes, objects: { "latest.json": "{}" },
  });
  assert.equal(res.status, 200);
});

test("no Authorization header is refused", async () => {
  const codes = { [await sha256(CODE)]: "{}" };
  const res = await call("https://updates.gulfvideo.io/latest.json", { codes });
  assert.equal(res.status, 401);
});

test("an unknown code is refused", async () => {
  const codes = { [await sha256(CODE)]: "{}" };
  const res = await call("https://updates.gulfvideo.io/latest.json", {
    token: "not-a-real-code", codes,
  });
  assert.equal(res.status, 401);
});

test("a revoked code is refused once its entry is gone", async () => {
  const res = await call("https://updates.gulfvideo.io/latest.json", {
    token: CODE, codes: {}, objects: { "latest.json": "{}" },
  });
  assert.equal(res.status, 401);
});

test("the code itself is never a KV key", async () => {
  // If the plaintext were the key, a KV leak would hand over working codes.
  let asked = null;
  const fake = { CODES: { get: async (k) => { asked = k; return null; } },
                 BUILDS: { get: async () => null } };
  await worker.fetch(
    new Request("https://updates.gulfvideo.io/latest.json",
      { headers: { Authorization: `Bearer ${CODE}` } }), fake);
  assert.notEqual(asked, CODE);
  assert.equal(asked, await sha256(CODE));
});

test("no request can reach R2 with a traversal key", async () => {
  // URL parsing already collapses a literal "/../", so those arrive harmless
  // and simply miss. The percent-encoded form survives parsing and is what the
  // explicit check is for. The property that matters is the same either way:
  // whatever the status, R2 is never asked for a key that climbs.
  const codes = { [await sha256(CODE)]: "{}" };
  const asked = [];
  const fake = {
    CODES: { get: async () => "{}" },
    BUILDS: { get: async (k) => { asked.push(k); return null; } },
  };
  for (const path of ["/../secrets", "/a/../../b", "/%2e%2e/x", "/%2E%2E%2Fx"]) {
    const res = await worker.fetch(
      new Request(`https://updates.gulfvideo.io${path}`,
        { headers: { Authorization: `Bearer ${CODE}` } }), fake);
    assert.ok([401, 404].includes(res.status), `${path} gave ${res.status}`);
  }
  assert.deepEqual(asked.filter((k) => k.includes("..")), [],
    `R2 was asked for ${JSON.stringify(asked)}`);
});

test("a missing object is 404, not 200 with nothing", async () => {
  const codes = { [await sha256(CODE)]: "{}" };
  const res = await call("https://updates.gulfvideo.io/0.9.0/nope.dmg", {
    token: CODE, codes, objects: {},
  });
  assert.equal(res.status, 404);
});

test("writes are refused outright", async () => {
  const codes = { [await sha256(CODE)]: "{}" };
  for (const method of ["PUT", "POST", "DELETE"]) {
    const res = await call("https://updates.gulfvideo.io/latest.json", {
      token: CODE, method, codes, objects: { "latest.json": "{}" },
    });
    assert.equal(res.status, 405, `allowed ${method}`);
  }
});

test("an absurdly long code is refused before it is hashed", async () => {
  const codes = { [await sha256(CODE)]: "{}" };
  const res = await call("https://updates.gulfvideo.io/latest.json", {
    token: "x".repeat(10000), codes,
  });
  assert.equal(res.status, 401);
});

test("refusals never say why", async () => {
  // "unknown code" and "revoked code" answering differently would confirm
  // which guesses had once been real.
  const codes = { [await sha256(CODE)]: "{}" };
  const unknown = await call("https://updates.gulfvideo.io/latest.json",
    { token: "wrong", codes });
  const none = await call("https://updates.gulfvideo.io/latest.json", { codes });
  assert.equal(unknown.status, none.status);
  assert.equal(await unknown.text(), await none.text());
});

test("the manifest is never cached, the builds always are", async () => {
  const codes = { [await sha256(CODE)]: "{}" };
  const manifest = await call("https://updates.gulfvideo.io/latest.json", {
    token: CODE, codes, objects: { "latest.json": "{}" } });
  const dmg = await call("https://updates.gulfvideo.io/0.4.0/cc.dmg", {
    token: CODE, codes, objects: { "0.4.0/cc.dmg": "x" } });
  assert.match(manifest.headers.get("Cache-Control"), /no-store/);
  assert.match(dmg.headers.get("Cache-Control"), /immutable/);
});
