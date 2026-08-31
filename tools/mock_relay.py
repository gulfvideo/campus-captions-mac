"""Stage-1 development relay.

Speaks the message contract in docs/MESSAGES.md and serves the viewer pages, so
wall.html and phone.html can be built and soak-tested before the real relay
exists. Standard library only - no pip install, no venv, nothing to break on a
locked-down machine.

This is a test rig, not the production relay. It has no source websocket, no
status page, no control channel and no authentication. relay/ replaces it in
stage 2; this stays around because a zero-dependency script that drives the
viewers is useful on any machine.

    python tools/mock_relay.py
    python tools/mock_relay.py --port 8000 --interim-ms 180 --gap-ms 900

Then open  http://localhost:8000/wall  and  http://localhost:8000/phone
"""

import argparse
import asyncio
import base64
import hashlib
import json
import mimetypes
import random
import struct
import sys
import time
import uuid
from collections import deque
from pathlib import Path

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
ROOT = Path(__file__).resolve().parent.parent
VIEWER_DIR = ROOT / "viewer"
DEFAULT_SCRIPT = ROOT / "config" / "mock_script.txt"

REPLAY_FOR_ROLE = {"wall": 2, "phone": 30}
HEARTBEAT_SECONDS = 5.0


# --------------------------------------------------------------------------
# websocket framing (RFC 6455, the parts a caption feed needs)
# --------------------------------------------------------------------------

def ws_frame(payload: bytes, opcode: int = 0x1) -> bytes:
    """Server -> client frame. Never masked, never fragmented."""
    header = bytearray([0x80 | opcode])
    n = len(payload)
    if n < 126:
        header.append(n)
    elif n < 65536:
        header.append(126)
        header += struct.pack("!H", n)
    else:
        header.append(127)
        header += struct.pack("!Q", n)
    return bytes(header) + payload


async def ws_read_frame(reader: asyncio.StreamReader):
    """Read one client frame. Returns (opcode, payload) or None at EOF."""
    head = await reader.readexactly(2)
    opcode = head[0] & 0x0F
    masked = bool(head[1] & 0x80)
    length = head[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", await reader.readexactly(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", await reader.readexactly(8))[0]
    mask = await reader.readexactly(4) if masked else b""
    data = await reader.readexactly(length) if length else b""
    if masked:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return opcode, data


def handshake_response(key: str) -> bytes:
    accept = base64.b64encode(
        hashlib.sha1((key + WS_GUID).encode()).digest()
    ).decode()
    return (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
    ).encode()


# --------------------------------------------------------------------------
# hub
# --------------------------------------------------------------------------

class Viewer:
    __slots__ = ("queue", "role", "peer")

    def __init__(self, role: str, peer: str):
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self.role = role
        self.peer = peer


class Hub:
    def __init__(self, replay_max: int = 60):
        self.session_id = uuid.uuid4().hex[:8]
        self.seq = 0
        self.viewers: set[Viewer] = set()
        self.finals: deque = deque(maxlen=replay_max)

    def _next(self, msg: dict) -> dict:
        self.seq += 1
        msg["seq"] = self.seq
        msg.setdefault("t", round(time.time(), 3))
        return msg

    def broadcast(self, msg: dict) -> None:
        msg = self._next(msg)
        if msg["type"] == "final":
            msg["id"] = f"{self.session_id}:{msg['seq']}"
            self.finals.append(msg)
        elif msg["type"] == "clear":
            self.finals.clear()
        payload = json.dumps(msg, ensure_ascii=False).encode()
        for v in list(self.viewers):
            try:
                v.queue.put_nowait(payload)
            except asyncio.QueueFull:
                # A viewer that cannot keep up is dropped rather than allowed
                # to stall the feed for everyone else.
                self.viewers.discard(v)
                print(f"  ! dropped slow viewer {v.peer}", flush=True)

    def join(self, v: Viewer) -> None:
        self.viewers.add(v)
        hello = {
            "type": "hello",
            "seq": 0,
            "session_id": self.session_id,
            "server_time": round(time.time(), 3),
            "protocol": 1,
        }
        v.queue.put_nowait(json.dumps(hello).encode())
        n = REPLAY_FOR_ROLE.get(v.role, 2)
        for msg in list(self.finals)[-n:] if n else []:
            replayed = dict(msg)
            replayed["replay"] = True
            v.queue.put_nowait(json.dumps(replayed, ensure_ascii=False).encode())

    def leave(self, v: Viewer) -> None:
        self.viewers.discard(v)


# --------------------------------------------------------------------------
# script player
# --------------------------------------------------------------------------

def load_script(path: Path) -> list:
    """Lines to speak. '#' comments, blank line = pause, [clear] = clear."""
    if not path.exists():
        print(f"!! script not found: {path}", file=sys.stderr)
        return ["Mock script file is missing.", "Check config/mock_script.txt."]
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        out.append(line)          # blank lines are kept: they are the pauses
    while out and not out[0]:
        out.pop(0)
    return out or ["Mock script is empty."]


async def play(hub: Hub, args) -> None:
    lines = load_script(Path(args.script))
    print(f"   script: {len(lines)} entries from {args.script}")
    idx = 0
    passes = 0
    while True:
        if idx >= len(lines):
            passes += 1
            if not args.loop:
                print("   script finished (pass without --loop); idling")
                while True:
                    await asyncio.sleep(3600)
            idx = 0
            print(f"   script pass {passes} complete, looping")
        line = lines[idx]
        idx += 1

        if not line:
            await asyncio.sleep(args.gap_ms / 1000.0 * 3)
            continue
        if line.lower() == "[clear]":
            hub.broadcast({"type": "clear"})
            await asyncio.sleep(args.gap_ms / 1000.0)
            continue

        # Build the line up word by word as interims, then commit it. This is
        # the shape real streaming ASR produces.
        words = line.split()
        built = ""
        for w in words:
            built = f"{built} {w}".strip()
            hub.broadcast({"type": "interim", "text": built})
            jitter = 1.0 + random.uniform(-args.jitter, args.jitter)
            await asyncio.sleep(max(0.01, args.interim_ms / 1000.0 * jitter))
        hub.broadcast({"type": "final", "text": line})
        await asyncio.sleep(args.gap_ms / 1000.0)


async def heartbeat(hub: Hub) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        hub.broadcast({
            "type": "heartbeat",
            "session_id": hub.session_id,
            "viewers": len(hub.viewers),
        })


# --------------------------------------------------------------------------
# http + websocket server
# --------------------------------------------------------------------------

ROUTES = {
    "/": "wall.html",
    "/wall": "wall.html",
    "/phone": "phone.html",
    "/wall.html": "wall.html",
    "/phone.html": "phone.html",
}


def http_response(status: str, body: bytes, ctype: str = "text/plain") -> bytes:
    return (
        f"HTTP/1.1 {status}\r\n"
        f"Content-Type: {ctype}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Cache-Control: no-store\r\n"
        "Connection: close\r\n\r\n"
    ).encode() + body


def serve_static(path: str) -> bytes:
    name = ROUTES.get(path)
    if name is None:
        # Anything else is looked up directly in viewer/, no traversal.
        candidate = (VIEWER_DIR / path.lstrip("/")).resolve()
        try:
            candidate.relative_to(VIEWER_DIR.resolve())
        except ValueError:
            return http_response("403 Forbidden", b"forbidden")
        if not candidate.is_file():
            return http_response("404 Not Found", b"not found")
    else:
        candidate = VIEWER_DIR / name
        if not candidate.is_file():
            return http_response("404 Not Found", f"missing {name}".encode())
    ctype = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
    if ctype.startswith("text/") or ctype.endswith("javascript"):
        ctype += "; charset=utf-8"
    return http_response("200 OK", candidate.read_bytes(), ctype)


async def pump(writer: asyncio.StreamWriter, v: Viewer) -> None:
    """One writer task per viewer: the only thing that touches this socket."""
    while True:
        payload = await v.queue.get()
        writer.write(ws_frame(payload))
        await writer.drain()


async def handle(reader, writer, hub: Hub) -> None:
    peer = "?"
    try:
        peer = "%s:%s" % writer.get_extra_info("peername")[:2]
    except Exception:
        pass
    v = None
    pump_task = None
    try:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError,
                asyncio.LimitOverrunError):
            return
        text = head.decode("latin-1")
        request_line = text.split("\r\n", 1)[0]
        parts = request_line.split(" ")
        if len(parts) < 2:
            return
        target = parts[1]
        path, _, query = target.partition("?")
        headers = {}
        for line in text.split("\r\n")[1:]:
            if ": " in line:
                k, _, val = line.partition(": ")
                headers[k.lower()] = val

        upgrading = "websocket" in headers.get("upgrade", "").lower()
        if path.startswith("/ws/") and upgrading:
            key = headers.get("sec-websocket-key", "")
            if not key:
                writer.write(http_response("400 Bad Request", b"bad handshake"))
                await writer.drain()
                return
            role = "wall"
            for pair in query.split("&"):
                if pair.startswith("role="):
                    role = pair[5:] or "wall"
            writer.write(handshake_response(key))
            await writer.drain()

            v = Viewer(role, peer)
            hub.join(v)
            print(f"  + viewer {peer} role={role} "
                  f"({len(hub.viewers)} connected)", flush=True)
            pump_task = asyncio.ensure_future(pump(writer, v))

            while True:
                frame = await ws_read_frame(reader)
                if frame is None:
                    break
                opcode, data = frame
                if opcode == 0x8:                      # close
                    break
                if opcode == 0x9:                      # ping -> pong
                    writer.write(ws_frame(data, 0xA))
                    await writer.drain()
            return

        writer.write(serve_static(path))
        await writer.drain()

    except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
        pass
    except Exception as exc:                            # never kill the server
        print(f"  ! handler error {peer}: {exc!r}", flush=True)
    finally:
        if pump_task:
            pump_task.cancel()
        if v:
            hub.leave(v)
            print(f"  - viewer {peer} left ({len(hub.viewers)} connected)",
                  flush=True)
        try:
            writer.close()
        except Exception:
            pass


async def main_async(args) -> None:
    hub = Hub()
    server = await asyncio.start_server(
        lambda r, w: handle(r, w, hub), args.host, args.port
    )
    shown = "localhost" if args.host in ("0.0.0.0", "") else args.host
    print("mock relay (stage 1 dev rig)")
    print(f"   session {hub.session_id}  listening on {args.host}:{args.port}")
    print(f"   wall    http://{shown}:{args.port}/wall")
    print(f"   phone   http://{shown}:{args.port}/phone")
    print(f"   debug   http://{shown}:{args.port}/wall?debug=1")
    print("   ctrl-c to stop")
    await asyncio.gather(server.serve_forever(), play(hub, args), heartbeat(hub))


def main() -> None:
    p = argparse.ArgumentParser(description="Stage-1 mock relay for the viewers.")
    p.add_argument("--host", default="0.0.0.0",
                   help="bind address (default: all interfaces, so phones on "
                        "the LAN can reach it)")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--script", default=str(DEFAULT_SCRIPT))
    p.add_argument("--interim-ms", type=float, default=180,
                   help="delay between interim words (default 180)")
    p.add_argument("--gap-ms", type=float, default=900,
                   help="pause after each committed line (default 900)")
    p.add_argument("--jitter", type=float, default=0.35,
                   help="random +/- fraction applied to interim timing")
    p.add_argument("--loop", action="store_true", default=True,
                   help="repeat the script forever (default)")
    p.add_argument("--once", dest="loop", action="store_false",
                   help="play the script a single time, then idle")
    args = p.parse_args()
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
