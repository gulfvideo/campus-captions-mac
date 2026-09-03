"""A stand-in for ProPresenter's network API, for tests and for a local soak
without the real application.

Implements the handful of v1 endpoints relay/propresenter.py uses, with the
request and response shapes from the API reference ProPresenter serves from
its Network settings. Records every call so a test can assert on them, and
can be told to go down or to fail the next N requests.

    with FakeProPresenter() as pp:
        out = ProPresenterOutput(host="127.0.0.1", port=pp.port)
        ...
        pp.calls          # [("PUT", "/v1/stage/message", "Hello"), ...]
        pp.stage_message  # what a stage layout would show right now

Standalone, for a soak with the relay pointed at it:

    python tests/fake_propresenter.py 1025

Not a full emulator: no screens, no slides, no props. That is the point -
the caption output must never need them.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_MESSAGE = {
    "id": {"uuid": "11111111-2222-3333-4444-555555555555", "name": "Live Captions", "index": 0},
    "tokens": [{"name": "Caption", "text": {"text": ""}}],
}


class FakeProPresenter:
    def __init__(self, port: int = 0, api_version: str = "v1", messages: list | None = None):
        self.api_version = api_version
        self.messages = messages if messages is not None else [json.loads(json.dumps(DEFAULT_MESSAGE))]
        self.stage_message: str | None = None
        self.triggered: list[tuple[str, list]] = []   # (message id, token payload)
        self.calls: list[tuple[str, str, object]] = []
        self.down = False                 # respond 503 to everything
        self.fail_next = 0                # respond 500 to the next N requests
        self.delay = 0.0                  # seconds to sleep before answering
        self._server = ThreadingHTTPServer(("127.0.0.1", port), self._handler())
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> "FakeProPresenter":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self) -> "FakeProPresenter":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    def message_by_id(self, ident: str) -> dict | None:
        for m in self.messages:
            if ident in (m["id"]["uuid"], str(m["id"]["index"]), m["id"]["name"]):
                return m
        return None

    # -- handler -------------------------------------------------------------

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):        # keep test output quiet
                return

            def _body(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                if not raw:
                    return None
                try:
                    return json.loads(raw.decode("utf-8"))
                except ValueError:
                    return raw.decode("utf-8", "replace")

            def _send(self, code: int, payload=None):
                data = b"" if payload is None else json.dumps(payload).encode("utf-8")
                self.send_response(code)
                if data:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if data:
                    self.wfile.write(data)

            def _route(self, method: str):
                path = self.path.split("?", 1)[0]
                body = self._body() if method in ("PUT", "POST") else None
                fake.calls.append((method, path, body))
                if fake.delay:
                    time.sleep(fake.delay)
                if fake.down:
                    return self._send(503, {"error": "down"})
                if fake.fail_next > 0:
                    fake.fail_next -= 1
                    return self._send(500, {"error": "injected"})

                # /version, not /v1/version. The real ProPresenter serves its
                # version at the root while everything else sits under /v1,
                # and this fake used to mirror the assumption rather than the
                # application - so the tests passed against a 404 the real
                # thing returns. Answer both paths the real one does: /version
                # 200, /v1/version 404, so a regression to the old path fails
                # here instead of in a booth.
                if method == "GET" and path == "/version":
                    return self._send(200, {"name": "ProPresenter", "platform": "mac",
                                            "os_version": "15.0", "host_description": "fake",
                                            "api_version": fake.api_version})
                if method == "GET" and path == "/v1/version":
                    return self._send(404, {"error": "not found"})
                if path == "/v1/stage/message":
                    if method == "GET":
                        return self._send(200, fake.stage_message or "")
                    if method == "PUT":
                        if not isinstance(body, str):
                            return self._send(400, {"error": "stage message body must be a string"})
                        fake.stage_message = body
                        return self._send(204)
                    if method == "DELETE":
                        fake.stage_message = None
                        return self._send(204)
                if method == "GET" and path == "/v1/messages":
                    return self._send(200, fake.messages)
                if path.startswith("/v1/message/"):
                    parts = path.split("/")
                    ident, action = parts[3], parts[4] if len(parts) > 4 else ""
                    message = fake.message_by_id(ident)
                    if message is None:
                        return self._send(404, {"error": "no such message"})
                    if method == "POST" and action == "trigger":
                        if not isinstance(body, list):
                            return self._send(400, {"error": "trigger body must be a token list"})
                        known = {t["name"] for t in message["tokens"]}
                        for token in body:
                            if token.get("name") not in known:
                                return self._send(400, {"error": f"unknown token {token.get('name')}"})
                        fake.triggered.append((message["id"]["uuid"], body))
                        return self._send(204)
                    if method == "GET" and action == "clear":
                        fake.triggered.append((message["id"]["uuid"], []))
                        return self._send(204)
                return self._send(404, {"error": "not found"})

            def do_GET(self):
                self._route("GET")

            def do_PUT(self):
                self._route("PUT")

            def do_POST(self):
                self._route("POST")

            def do_DELETE(self):
                self._route("DELETE")

        return Handler


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 1025
    with FakeProPresenter(port=port) as pp:
        print(f"fake ProPresenter API on http://127.0.0.1:{pp.port}  (Ctrl-C to stop)")
        try:
            while True:
                time.sleep(1.0)
                if pp.stage_message is not None:
                    print(f"\r  stage message: {pp.stage_message[:70]:<70}", end="", flush=True)
        except KeyboardInterrupt:
            print()
