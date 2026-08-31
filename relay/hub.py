"""Fan-out, replay and sequencing.

The hub is the only thing that assigns `seq`, so viewers can trust it as a total
order (see docs/MESSAGES.md). Sources hand it text; it hands viewers numbered,
timestamped frames.

Transport lives in server.py. The hub never touches a socket - it writes to a
queue per client, and the server drains it. That keeps a slow viewer on a bad
wifi link from stalling the wall view.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import time
import uuid
from collections import deque

from .state import RelayState

# How much history a joining viewer gets. The wall only ever shows two lines, so
# sending it more would just be pushed straight off screen.
REPLAY_FOR_ROLE = {"wall": 2, "phone": 30}
REPLAY_BUFFER = 200

# A viewer this far behind is not going to catch up; dropping it is better than
# letting it hold memory or stall the event.
CLIENT_QUEUE_MAX = 256

MAX_TEXT_CHARS = 2000
SOURCE_MESSAGE_TYPES = {"interim", "final", "clear", "status"}
CONTROL_ACTIONS = {"open_stream", "close_stream", "clear", "reload_keyterms"}

_counter = itertools.count(1)


def clean_text(value: object) -> str:
    """Whatever a backend hands us becomes plain, bounded, single-line text."""
    if not isinstance(value, str):
        return ""
    out = "".join(ch for ch in value if ch >= " " or ch == "\t")
    out = " ".join(out.split())
    return out[:MAX_TEXT_CHARS]


class Client:
    """One subscriber. `kind` is viewer, status or source."""

    __slots__ = ("cid", "kind", "role", "peer", "queue", "dropped", "joined_at")

    def __init__(self, kind: str, role: str, peer: str):
        self.cid = next(_counter)
        self.kind = kind
        self.role = role
        self.peer = peer
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=CLIENT_QUEUE_MAX)
        self.dropped = False
        self.joined_at = time.time()

    def send(self, payload: str) -> bool:
        try:
            self.queue.put_nowait(payload)
            return True
        except asyncio.QueueFull:
            self.dropped = True
            return False

    def __repr__(self) -> str:  # pragma: no cover - logging only
        return f"<{self.kind}:{self.role} {self.peer} #{self.cid}>"


class Hub:
    def __init__(self, session_id: str | None = None):
        self.session_id = session_id or uuid.uuid4().hex[:8]
        self.seq = 0
        self.viewers: dict[int, Client] = {}
        self.status_clients: dict[int, Client] = {}
        self.sources: dict[int, Client] = {}
        self.finals: deque[dict] = deque(maxlen=REPLAY_BUFFER)
        self.state = RelayState(session_id=self.session_id)
        self.on_event = None          # optional callback(msg) for logging

    # -- outbound --------------------------------------------------------

    def _stamp(self, msg: dict) -> dict:
        self.seq += 1
        msg["seq"] = self.seq
        msg.setdefault("t", round(time.time(), 3))
        return msg

    def broadcast(self, msg: dict) -> dict:
        """Number a message, remember it if it is a final, fan it out."""
        msg = self._stamp(msg)
        kind = msg.get("type")
        if kind == "final":
            msg["id"] = f"{self.session_id}:{msg['seq']}"
            self.finals.append(msg)
        elif kind == "clear":
            self.finals.clear()

        payload = json.dumps(msg, ensure_ascii=False)
        for client in list(self.viewers.values()):
            if not client.send(payload):
                self.viewers.pop(client.cid, None)
        if self.on_event:
            self.on_event(msg)
        return msg

    def push_status(self) -> None:
        if not self.status_clients:
            return
        payload = json.dumps(self.snapshot(), ensure_ascii=False)
        for client in list(self.status_clients.values()):
            if not client.send(payload):
                self.status_clients.pop(client.cid, None)

    def send_control(self, action: str) -> int:
        """Forward an operator action to every connected capture client."""
        if action not in CONTROL_ACTIONS:
            raise ValueError(f"unknown control action: {action}")
        payload = json.dumps({
            "type": "control",
            "action": action,
            "t": round(time.time(), 3),
        })
        sent = 0
        for client in list(self.sources.values()):
            if client.send(payload):
                sent += 1
        # A clear is the operator's, not the backend's: apply it to viewers
        # immediately so the wall goes blank even if no capture client is up.
        if action == "clear":
            self.broadcast({"type": "clear"})
        return sent

    # -- inbound ---------------------------------------------------------

    def ingest(self, raw: str, peer: str) -> None:
        """Handle one frame from a capture client. Never raises on bad input."""
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        if not isinstance(msg, dict):
            return
        kind = msg.get("type")
        if kind not in SOURCE_MESSAGE_TYPES:
            return

        if kind == "status":
            self.state.note_status(msg, peer)
            return

        if kind == "clear":
            self.broadcast({"type": "clear"})
            return

        text = clean_text(msg.get("text"))
        if not text:
            return
        if kind == "interim":
            self.state.note_interim()
            self.broadcast({"type": "interim", "text": text})
        elif kind == "final":
            self.state.note_final(text)
            self.broadcast({"type": "final", "text": text})

    # -- membership ------------------------------------------------------

    def add_viewer(self, role: str, peer: str) -> Client:
        role = role if role in REPLAY_FOR_ROLE else "wall"
        client = Client("viewer", role, peer)
        self.viewers[client.cid] = client
        client.send(json.dumps({
            "type": "hello",
            "seq": 0,
            "session_id": self.session_id,
            "server_time": round(time.time(), 3),
            "protocol": 1,
        }))
        depth = REPLAY_FOR_ROLE[role]
        for msg in list(self.finals)[-depth:] if depth else []:
            replayed = dict(msg)
            replayed["replay"] = True
            client.send(json.dumps(replayed, ensure_ascii=False))
        return client

    def add_status(self, peer: str) -> Client:
        client = Client("status", "status", peer)
        self.status_clients[client.cid] = client
        client.send(json.dumps(self.snapshot(), ensure_ascii=False))
        return client

    def add_source(self, peer: str) -> Client:
        client = Client("source", "source", peer)
        self.sources[client.cid] = client
        self.state.source_connected = True
        self.state.source_peer = peer
        return client

    def remove(self, client: Client) -> None:
        self.viewers.pop(client.cid, None)
        self.status_clients.pop(client.cid, None)
        if self.sources.pop(client.cid, None) is not None and not self.sources:
            self.state.source_dropped()

    # -- reporting -------------------------------------------------------

    def viewer_counts(self) -> dict[str, int]:
        counts = {"total": 0, "wall": 0, "phone": 0, "status": len(self.status_clients)}
        for client in self.viewers.values():
            counts["total"] += 1
            counts[client.role] = counts.get(client.role, 0) + 1
        return counts

    def snapshot(self) -> dict:
        return self.state.snapshot(self.viewer_counts(), self.seq)

    def heartbeat(self) -> None:
        self.broadcast({
            "type": "heartbeat",
            "session_id": self.session_id,
            "viewers": len(self.viewers),
        })
