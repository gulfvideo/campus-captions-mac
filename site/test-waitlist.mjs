/* Smallest thing that fails if the waitlist handler breaks.
 *   node site/test-waitlist.mjs
 * No framework on purpose: it is one module with two exported functions. */

import assert from 'node:assert/strict';
import { onRequestPost, onRequestGet } from './functions/api/waitlist.js';

/* A KV namespace that lives in a Map. */
function fakeKV() {
  const m = new Map();
  return {
    m,
    async put(k, v) { m.set(k, v); },
    async get(k, opts) {
      const v = m.get(k);
      return v === undefined ? null : (opts?.type === 'json' ? JSON.parse(v) : v);
    },
    async list({ prefix = '' } = {}) {
      return {
        keys: [...m.keys()].filter((k) => k.startsWith(prefix)).map((name) => ({ name })),
        list_complete: true,
      };
    },
  };
}

const post = (body, env, headers = {}) =>
  onRequestPost({
    env,
    request: new Request('https://campuscaptions.app/api/waitlist', {
      method: 'POST',
      headers: { 'content-type': 'application/json', ...headers },
      body: typeof body === 'string' ? body : JSON.stringify(body),
    }),
  });

const good = { name: 'Dana Reyes', email: 'dana@example.edu', org: 'Example College' };

let env = { WAITLIST: fakeKV(), ADMIN_PASSWORD: 'correct horse' };

// --- accepts a real signup -------------------------------------------------
let r = await post(good, env);
assert.equal(r.status, 200, 'a valid signup is accepted');
assert.equal(env.WAITLIST.m.size, 1, 'and is stored');
const stored = JSON.parse([...env.WAITLIST.m.values()][0]);
assert.equal(stored.email, 'dana@example.edu');
assert.ok(stored.at, 'a timestamp is recorded');
assert.equal(stored.notes, '', 'absent fields become empty strings, not undefined');

// --- rejects what it should ------------------------------------------------
for (const [bad, why] of [
  [{ ...good, email: 'not-an-email' }, 'a malformed email'],
  [{ ...good, email: 'a@b' }, 'an email with no real TLD'],
  [{ ...good, name: '   ' }, 'a whitespace-only name'],
  [{ ...good, org: '' }, 'a missing organization'],
]) {
  assert.equal((await post(bad, env)).status, 400, `rejects ${why}`);
}
assert.equal((await post('{nope', env)).status, 400, 'rejects malformed JSON');
assert.equal((await post({ ...good, notes: 'x'.repeat(9000) }, env)).status, 413,
  'rejects an oversized body before parsing it');
assert.equal(env.WAITLIST.m.size, 1, 'none of those were stored');

// --- honeypot: looks accepted, stores nothing ------------------------------
r = await post({ ...good, website: 'http://spam.example' }, env);
assert.equal(r.status, 200, 'a bot gets a 200 so it does not retry');
assert.equal(env.WAITLIST.m.size, 1, 'but nothing was stored');

// --- long fields are truncated, not rejected -------------------------------
await post({ ...good, email: 'long@example.edu', notes: 'y'.repeat(5000) }, env);
const long = JSON.parse([...env.WAITLIST.m.values()].find((v) => v.includes('long@')));
assert.equal(long.notes.length, 2000, 'notes are capped at the declared limit');

// --- export is closed without the password ---------------------------------
const get = (auth) =>
  onRequestGet({
    env,
    request: new Request('https://campuscaptions.app/api/waitlist', {
      headers: auth ? { authorization: 'Basic ' + Buffer.from(auth).toString('base64') } : {},
    }),
  });

assert.equal((await get()).status, 401, 'no credentials: refused');
assert.equal((await get('admin:wrong')).status, 401, 'wrong password: refused');
assert.equal((await get('admin:correct hors')).status, 401, 'a prefix of the password: refused');

// --- export returns the rows -----------------------------------------------
r = await get('admin:correct horse');
assert.equal(r.status, 200, 'the right password opens the export');
const csv = await r.text();
assert.match(csv, /dana@example\.edu/, 'the signup is in the CSV');
assert.equal(csv.split('\r\n').length, 3, 'header plus two rows');

// --- CSV injection is defanged ---------------------------------------------
env = { WAITLIST: fakeKV(), ADMIN_PASSWORD: 'p' };
await post({ ...good, name: '=HYPERLINK("http://evil","click")' }, env);
const csv2 = await (await get('a:p')).text();
assert.match(csv2, /"'=HYPERLINK/, 'a leading = is escaped so Excel treats it as text');

console.log('waitlist: all checks passed');
