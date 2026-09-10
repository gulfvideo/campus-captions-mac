/**
 * Access gate for Campus Captions update downloads.
 *
 * The R2 bucket holding the builds is private. This Worker sits on
 * updates.gulfvideo.io, checks a per-tester access code, and only then streams
 * the object out of R2. Revoking a tester is deleting one KV entry.
 *
 * Codes are stored hashed. A read-only leak of the KV namespace then yields
 * SHA-256 digests rather than working credentials - the same reason a server
 * does not keep plaintext passwords. Lookup is by digest, so the Worker never
 * needs the original either.
 *
 *   KV namespace CODES
 *     key:   sha256 hex of the access code
 *     value: {"tester": "jdean", "issued": "2026-09-10"}   (a label, for logs)
 *
 * Add a tester:
 *     code=$(openssl rand -hex 16)
 *     echo "give this to the tester: $code"
 *     printf %s "$code" | shasum -a 256      # -> KEY
 *     npx wrangler kv key put --binding CODES "<KEY>" '{"tester":"name"}' --remote
 *
 * Revoke one:
 *     npx wrangler kv key delete --binding CODES "<KEY>" --remote
 */

const MAX_CODE_BYTES = 512;

async function digest(code) {
  const bytes = new TextEncoder().encode(code);
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(hash)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function bearer(request) {
  const header = request.headers.get("Authorization") || "";
  const match = /^Bearer\s+(.+)$/i.exec(header.trim());
  return match ? match[1].trim() : "";
}

// One shape of reply for "no" whatever the reason. Distinguishing "no such
// code" from "revoked" would confirm which guesses were once real.
function denied() {
  return new Response("Not authorised\n", {
    status: 401,
    headers: {
      "WWW-Authenticate": 'Bearer realm="campus-captions-updates"',
      "Cache-Control": "no-store",
    },
  });
}

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method not allowed\n", { status: 405 });
    }

    const code = bearer(request);
    if (!code || code.length > MAX_CODE_BYTES) return denied();

    const key = await digest(code);
    const record = await env.CODES.get(key);
    if (record === null) return denied();

    // Which tester fetched what, without ever logging the code. This is the
    // audit trail that makes revocation meaningful - a code appearing from
    // twenty machines is the signal to replace it.
    let tester = "unknown";
    try {
      tester = JSON.parse(record).tester || "unknown";
    } catch (_) {}

    const url = new URL(request.url);
    // Strip the leading slash and refuse anything trying to climb out of the
    // bucket. R2 keys are flat, but a path containing ".." only ever arrives
    // from someone probing.
    const object = decodeURIComponent(url.pathname.replace(/^\/+/, ""));
    if (!object || object.includes("..")) return denied();

    console.log(JSON.stringify({ tester, object, cf: request.cf?.colo }));

    const body = await env.BUILDS.get(object);
    if (body === null) {
      return new Response("Not found\n", { status: 404,
        headers: { "Cache-Control": "no-store" } });
    }

    const headers = new Headers();
    body.writeHttpMetadata(headers);
    headers.set("etag", body.httpEtag);
    // The manifest changes every release and is small; the builds never change
    // once published, and are large.
    headers.set("Cache-Control",
      object.endsWith(".json") ? "no-store" : "public, max-age=31536000, immutable");
    // Nothing here is meant to be reachable from a web page.
    headers.set("Access-Control-Allow-Origin", "null");
    headers.set("X-Content-Type-Options", "nosniff");

    return new Response(request.method === "HEAD" ? null : body.body, { headers });
  },
};
