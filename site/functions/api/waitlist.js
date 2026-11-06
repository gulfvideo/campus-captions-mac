/* Campus Captions waitlist.
 *
 * POST /api/waitlist   — a signup from the site. Stores one JSON blob in KV.
 * GET  /api/waitlist   — every signup as CSV, behind HTTP basic auth.
 *
 * Bindings (Cloudflare Pages → Settings → Functions):
 *   WAITLIST        KV namespace
 *   ADMIN_PASSWORD  secret — the password for the CSV export
 *
 * KV, not D1, because this is an append-only list that one person reads a
 * few times a month. If it ever needs querying or segmenting, that is the
 * day to move it.
 */

const LIMITS = {
  name: 120, email: 200, org: 160, role: 120,
  kind: 80, cadence: 80, notes: 2000,
};

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json', 'cache-control': 'no-store' },
  });

export async function onRequestPost({ request, env }) {
  if (!env.WAITLIST) return json({ error: 'Waitlist storage is not configured.' }, 500);

  // Cap the body before parsing it — an unbounded JSON.parse on a stranger's
  // payload is the one thing worth being strict about here.
  const raw = await request.text();
  if (raw.length > 8000) return json({ error: 'That is too much text.' }, 413);

  let data;
  try { data = JSON.parse(raw); } catch { return json({ error: 'Bad request.' }, 400); }
  if (!data || typeof data !== 'object') return json({ error: 'Bad request.' }, 400);

  // Honeypot. A person never sees this field; a bot fills everything.
  // Answer 200 so the bot believes it worked and does not come back harder.
  if (typeof data.website === 'string' && data.website.trim() !== '') {
    return json({ ok: true });
  }

  const clean = {};
  for (const [key, max] of Object.entries(LIMITS)) {
    clean[key] = typeof data[key] === 'string' ? data[key].trim().slice(0, max) : '';
  }

  if (!clean.name) return json({ error: 'A name is required.' }, 400);
  if (!clean.org) return json({ error: 'An organization is required.' }, 400);
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(clean.email))
    return json({ error: 'That email address does not look right.' }, 400);

  const now = new Date().toISOString();
  const record = {
    ...clean,
    at: now,
    // Cloudflare's own country header. No IP stored: it would be one more
    // piece of personal data to look after for no benefit to anybody.
    country: request.headers.get('cf-ipcountry') || '',
  };

  // Key sorts chronologically, and the email suffix makes a double-submit
  // from the same person land on one key instead of two rows.
  const key = `signup:${now}:${clean.email.toLowerCase()}`;
  await env.WAITLIST.put(key, JSON.stringify(record));

  return json({ ok: true });
}

export async function onRequestGet({ request, env }) {
  if (!env.ADMIN_PASSWORD) return new Response('Export is not configured.', { status: 500 });

  const deny = new Response('Authentication required.', {
    status: 401,
    headers: { 'www-authenticate': 'Basic realm="Campus Captions waitlist"' },
  });

  const header = request.headers.get('authorization') || '';
  if (!header.startsWith('Basic ')) return deny;

  let password;
  try { password = atob(header.slice(6)).split(':').slice(1).join(':'); }
  catch { return deny; }
  if (!timingSafeEqual(password, env.ADMIN_PASSWORD)) return deny;

  const rows = [];
  let cursor;
  do {
    const page = await env.WAITLIST.list({ prefix: 'signup:', cursor });
    for (const k of page.keys) {
      const value = await env.WAITLIST.get(k.name, { type: 'json' });
      if (value) rows.push(value);
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);

  rows.sort((a, b) => (a.at < b.at ? 1 : -1));

  const cols = ['at', 'name', 'email', 'org', 'role', 'kind', 'cadence', 'country', 'notes'];
  const csv = [
    cols.join(','),
    ...rows.map((r) => cols.map((c) => csvCell(r[c])).join(',')),
  ].join('\r\n');

  return new Response('﻿' + csv, {
    headers: {
      'content-type': 'text/csv; charset=utf-8',
      'content-disposition': `attachment; filename="waitlist-${new Date().toISOString().slice(0, 10)}.csv"`,
      'cache-control': 'no-store',
    },
  });
}

/* A leading =, +, - or @ makes Excel treat the cell as a formula. Prefix with
 * an apostrophe so a pasted "=cmd|..." stays text. */
function csvCell(value) {
  let s = String(value ?? '');
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
  return '"' + s.replace(/"/g, '""') + '"';
}

/* Constant-time compare, so a wrong password takes the same time whatever is
 * wrong with it. */
function timingSafeEqual(a, b) {
  const enc = new TextEncoder();
  const x = enc.encode(a);
  const y = enc.encode(b);
  // Lengths differ: still walk the longer one, then fail.
  let diff = x.length ^ y.length;
  const n = Math.max(x.length, y.length);
  for (let i = 0; i < n; i++) diff |= (x[i] ?? 0) ^ (y[i] ?? 0);
  return diff === 0;
}
