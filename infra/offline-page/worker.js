// Sits in front of faulknercaptions.com and passes every request straight
// through to the booth Mac's tunnel. Only when the Mac does not answer -
// Campus Captions closed (cloudflared returns 502) or the Mac off or asleep
// (the tunnel is down and Cloudflare returns 530, "Error 1033") - does a
// person opening the page get a plain "captions aren't running" page instead
// of a Cloudflare error. API calls and the caption WebSocket get the real
// error back: the phone page already retries those on its own.

const DOWN = new Set([502, 503, 504, 520, 521, 522, 523, 524, 530]);

export default {
  async fetch(request) {
    let response;
    try {
      response = await fetch(request);
    } catch (err) {
      response = null;
    }
    if (response && !DOWN.has(response.status)) return response;
    if (!wantsPage(request)) {
      return response || new Response("captions unavailable", { status: 503 });
    }
    return new Response(PAGE, {
      status: 503,
      headers: {
        "content-type": "text/html; charset=utf-8",
        "cache-control": "no-store",
        "retry-after": "30",
      },
    });
  },
};

// A browser opening a page, as opposed to fetch() from the phone page's
// script or a WebSocket upgrade.
export function wantsPage(request) {
  if (request.method !== "GET") return false;
  if (request.headers.get("upgrade")) return false;
  return (request.headers.get("accept") || "").includes("text/html");
}

// Self-contained and in the phone page's own look: black, white text, large
// type. Reloads itself every 30 seconds, so a phone left open on it lands on
// live captions once the booth starts them.
export const PAGE = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>Live captions</title>
<style>
  :root { color-scheme: dark; }
  html, body { margin: 0; height: 100%; background: #000; color: #fff; }
  body {
    display: flex; align-items: center; justify-content: center; padding: 24px;
    box-sizing: border-box;
    font: 1.1rem/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  main {
    max-width: 34rem; width: 100%; text-align: center; padding: 22px 24px;
    border: 1px solid rgba(255,255,255,0.18); border-radius: 16px;
    background: rgba(255,255,255,0.04);
  }
  h1 { font-size: 1.6rem; line-height: 1.25; margin: 0 0 12px; }
  p { margin: 0 0 10px; }
  .small { color: #d9d9d9; font-size: 0.95rem; margin: 0; }
</style>
</head>
<body>
<main>
  <h1>Live captions aren't running right now</h1>
  <p>Captions will appear here when the program starts.</p>
  <p class="small">This page checks again every 30 seconds. You can leave it open.</p>
</main>
</body>
</html>`;
