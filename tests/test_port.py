"""Port fallback and the pieces a port change touches. Standard library only.

relay/config.py: port_is_free, choose_port, save_relay_port.
capture/config.py: relay_url_with_port (a local relay_url follows the
relay's port; a remote one is left alone).
"""

from __future__ import annotations

import socket
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capture import config as capture_config  # noqa: E402
from relay import config  # noqa: E402


def held_port() -> tuple[socket.socket, int]:
    """A listening socket on a port from a quiet range, so the port is really
    taken and the ones above it are plausibly free.

    Not port 0: that hands out an ephemeral port, and right after a smoke
    run the ephemeral range is full of Windows' closing connections, which
    made the "next free port" search come up empty for twenty ports in a
    row. The relay's own port lives well below that range too.
    """
    for port in range(18000, 19000):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", port))
            sock.listen(1)
            return sock, port
        except OSError:
            sock.close()
    raise RuntimeError("no free port between 18000 and 18999 for the test")


class ChoosePortTests(unittest.TestCase):
    def test_free_port_is_kept(self):
        sock, port = held_port()
        sock.close()
        self.assertTrue(config.port_is_free("127.0.0.1", port))
        self.assertEqual(config.choose_port("127.0.0.1", port), port)

    def test_taken_port_moves_to_the_next_free_one(self):
        sock, port = held_port()
        try:
            self.assertFalse(config.port_is_free("127.0.0.1", port))
            chosen = config.choose_port("127.0.0.1", port)
            self.assertGreater(chosen, port)
            self.assertTrue(config.port_is_free("127.0.0.1", chosen))
        finally:
            sock.close()

    def test_all_candidates_taken_raises(self):
        sock, port = held_port()
        try:
            with self.assertRaises(OSError):
                config.choose_port("127.0.0.1", port, tries=1)
        finally:
            sock.close()


class SaveRelayPortTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "event.toml"

    def tearDown(self):
        self.dir.cleanup()

    def read(self) -> dict:
        with self.path.open("rb") as fh:
            return tomllib.load(fh)

    def test_rewrites_port_in_place_keeping_its_comment(self):
        self.path.write_text(
            "[relay]\nhost = \"0.0.0.0\"   # all interfaces\nport = 8000   # phones use this\n"
            "source_token = \"\"\n\n[languages]\nsource = \"en\"\n", encoding="utf-8")
        config.save_relay_port(self.path, 8001)
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("port = 8001   # phones use this", text)
        self.assertIn('host = "0.0.0.0"   # all interfaces', text)
        self.assertEqual(self.read()["relay"]["port"], 8001)
        self.assertEqual(self.read()["languages"]["source"], "en")

    def test_adds_port_when_missing_and_section_when_missing(self):
        self.path.write_text("[relay]\nhost = \"0.0.0.0\"\n", encoding="utf-8")
        config.save_relay_port(self.path, 9000)
        self.assertEqual(self.read()["relay"]["port"], 9000)
        other = Path(self.dir.name) / "fresh.toml"
        config.save_relay_port(other, 9100)
        with other.open("rb") as fh:
            self.assertEqual(tomllib.load(fh)["relay"]["port"], 9100)


class RelayUrlTests(unittest.TestCase):
    def test_local_relay_follows_the_port(self):
        self.assertEqual(
            capture_config.relay_url_with_port("ws://127.0.0.1:8000/ws/source", 8001, local_only=True),
            "ws://127.0.0.1:8001/ws/source")
        self.assertEqual(
            capture_config.relay_url_with_port("ws://localhost:8000/ws/source", 8001, local_only=True),
            "ws://localhost:8001/ws/source")

    def test_remote_relay_is_left_alone_unless_asked(self):
        remote = "ws://10.0.4.12:8000/ws/source"
        self.assertEqual(capture_config.relay_url_with_port(remote, 8001, local_only=True), remote)
        self.assertEqual(capture_config.relay_url_with_port(remote, 8001),
                         "ws://10.0.4.12:8001/ws/source")

    def test_junk_url_is_returned_unchanged(self):
        self.assertEqual(capture_config.relay_url_with_port("not a url", 8001), "not a url")


if __name__ == "__main__":
    unittest.main()
