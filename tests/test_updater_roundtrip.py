"""relay/updater.py end to end: a real HTTPS server, a real manifest built by
tools/publish_update.py, a real signed .dmg, and the real Updater.

The transport policy is the one thing not exercised here - ALLOWED_HOSTS is
pointed at localhost so the test can serve anything at all - and it is covered
directly in test_updater.py. Everything else is the shipping code path: fetch,
parse, download, size and checksum, mount, signature, staging.

macOS only (hdiutil/codesign) and skipped without openssl."""

from __future__ import annotations

import http.server
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import urllib.request
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from relay import updater as up  # noqa: E402
from tests.test_updater_macos import make_app, make_dmg  # noqa: E402


ACCESS_CODE = "0123456789abcdef0123456789abcdef"


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


@unittest.skipUnless(sys.platform == "darwin", "macOS only")
@unittest.skipUnless(have("openssl"), "needs openssl for a test certificate")
class RoundTripTests(unittest.TestCase):
    # The disk image and the certificate are identical for every test here, and
    # building them per test meant twelve hdiutil creates per suite run - which
    # is where hdiutil starts intermittently returning 1. Build once, copy in.
    @classmethod
    def setUpClass(cls):
        cls.shared = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        shared = Path(cls.shared.name)

        src = shared / "src"
        src.mkdir()
        make_app(src, version="99.0.0-beta")
        cls.pristine_dmg = make_dmg(src, shared / "pristine.dmg")

        cls.cert = shared / "cert.pem"
        cls.key = shared / "key.pem"
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", str(cls.key), "-out", str(cls.cert),
             "-days", "1", "-subj", "/CN=localhost",
             "-addext", "subjectAltName=DNS:localhost"],
            check=True, capture_output=True, timeout=120)

    @classmethod
    def tearDownClass(cls):
        cls.shared.cleanup()

    def setUp(self):
        # The server's handler threads are daemons and can still be finishing
        # a response when the test ends. Racing them to delete the directory
        # underneath is not what any of these tests are about.
        self.dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.dir.name)
        self.served = self.root / "served" / "99.0.0-beta"
        self.served.mkdir(parents=True)

        # A real signed app in a real disk image, named the way the release
        # workflow names it so publish_update.py classifies it. Copied rather
        # than rebuilt, so the tampering test can mutate its own.
        self.dmg = self.served / "CampusCaptions-99.0.0-beta-AppleSilicon.dmg"
        shutil.copy2(self.pristine_dmg, self.dmg)

        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(self.cert), str(self.key))
        root = self.root / "served"

        seen: list[str] = []
        self.seen = seen

        class Handler(http.server.SimpleHTTPRequestHandler):
            """Behaves like infra/update-gate: a valid bearer code or 401."""

            def __init__(self, *a, **kw):
                super().__init__(*a, directory=str(root), **kw)

            def do_GET(self):
                header = self.headers.get("Authorization", "")
                seen.append(header)
                if header != f"Bearer {ACCESS_CODE}":
                    self.send_response(401)
                    self.end_headers()
                    self.wfile.write(b"Not authorised\n")
                    return
                super().do_GET()

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

        # Real manifest, from the real publishing tool, over the real files.
        subprocess.run(
            [sys.executable, "tools/publish_update.py",
             "--assets", str(self.served),
             "--base", f"https://localhost:{self.port}",
             "--version", "99.0.0-beta", "--notes", "test build",
             "--out", str(self.root / "served" / "latest.json")],
            check=True, capture_output=True, timeout=120)

        self.hosts = up.ALLOWED_HOSTS
        up.ALLOWED_HOSTS = ("localhost",)
        self.previous_code = os.environ.get(up.ACCESS_TOKEN_ENV)
        os.environ[up.ACCESS_TOKEN_ENV] = ACCESS_CODE

        # Teach urllib about this one test certificate, rather than giving
        # updater.py a way to skip verification. The code under test still
        # performs a real TLS handshake and a real certificate check - against
        # a CA the test owns rather than a disabled one.
        trusted = ssl.create_default_context()
        trusted.load_verify_locations(str(self.cert))
        urllib.request.install_opener(urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=trusted)))

    def tearDown(self):
        if self.previous_code is None:
            os.environ.pop(up.ACCESS_TOKEN_ENV, None)
        else:
            os.environ[up.ACCESS_TOKEN_ENV] = self.previous_code
        up.ALLOWED_HOSTS = self.hosts
        urllib.request.install_opener(None)
        self.server.shutdown()
        self.server.server_close()      # release the listening socket too
        self.dir.cleanup()

    def updater(self, live=False) -> up.Updater:
        return up.Updater(
            manifest_url=f"https://localhost:{self.port}/latest.json",
            staging=self.root / "staging",
            is_live=lambda: live)

    def test_check_then_download_produces_a_verified_staged_app(self):
        import asyncio

        async def run():
            u = self.updater()
            status = await u.check()
            self.assertEqual("available", status["state"], status.get("error"))
            self.assertEqual("99.0.0-beta", status["available"])
            self.assertEqual("test build", status["notes"])
            self.assertGreater(status["size"], 0)

            status = await u.fetch()
            self.assertEqual("ready", status["state"], status.get("error"))
            self.assertEqual(1.0, status["progress"])
            self.assertTrue(u.staged.is_dir())
            self.assertEqual("99.0.0-beta", up.bundle_version(u.staged))
            self.assertTrue(status["can_install"])

        asyncio.run(run())

    def test_a_tampered_download_is_refused(self):
        """The manifest is written first, then the file is changed underneath
        it - which is what a compromised download host looks like."""
        import asyncio

        async def run():
            u = self.updater()
            await u.check()
            self.dmg.write_bytes(self.dmg.read_bytes() + b"tampered")
            status = await u.fetch()
            self.assertEqual("error", status["state"])
            self.assertIn("manifest", status["error"])
            self.assertIsNone(u.staged)

        asyncio.run(run())

    def test_a_manifest_offering_an_older_version_changes_nothing(self):
        import asyncio

        path = self.root / "served" / "latest.json"
        doc = json.loads(path.read_text())
        doc["version"] = "0.0.1-beta"
        path.write_text(json.dumps(doc))

        async def run():
            u = self.updater()
            status = await u.check()
            self.assertEqual("idle", status["state"])
            self.assertIsNone(status["available"])

        asyncio.run(run())

    def test_a_live_event_still_blocks_the_install_after_a_good_download(self):
        import asyncio

        async def run():
            u = self.updater(live=True)
            await u.check()
            status = await u.fetch()
            self.assertEqual("ready", status["state"], status.get("error"))
            self.assertFalse(status["can_install"])
            status = u.install()
            self.assertEqual("error", status["state"])
            self.assertIn("live", status["error"])

        asyncio.run(run())


    def test_every_request_carried_the_access_code(self):
        """Both the manifest and the build are behind the gate - an update
        path that authenticated one and not the other would leave the large,
        interesting file open to anyone with the URL."""
        import asyncio

        async def run():
            u = self.updater()
            await u.check()
            status = await u.fetch()
            self.assertEqual("ready", status["state"], status.get("error"))

        asyncio.run(run())
        self.assertGreaterEqual(len(self.seen), 2)
        self.assertTrue(all(h == f"Bearer {ACCESS_CODE}" for h in self.seen),
                        f"a request went out unauthenticated: {self.seen}")

    def test_a_revoked_code_cannot_download_anything(self):
        import asyncio

        os.environ[up.ACCESS_TOKEN_ENV] = "revoked-code"

        async def run():
            u = self.updater()
            status = await u.check()
            self.assertEqual("error", status["state"])
            self.assertIn("revoked", status["error"])
            self.assertIsNone(u.available)
            # ...and with nothing on offer, there is nothing to fetch either.
            self.assertEqual("error", (await u.fetch())["state"])

        asyncio.run(run())

    def test_with_no_code_the_operator_is_told_to_paste_one(self):
        import asyncio

        os.environ.pop(up.ACCESS_TOKEN_ENV, None)

        async def run():
            status = await self.updater().check()
            self.assertEqual("error", status["state"])
            self.assertIn("no update code", status["error"])
            self.assertFalse(status["has_access_code"])

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
