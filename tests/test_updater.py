"""relay/updater.py: what the app will accept as an update.

This module decides what gets written over the application an operator runs a
service on, so most of these tests are about refusal - a manifest pointing
somewhere else, a truncated download, a file whose checksum does not match, an
update attempted mid-service. Standard library only; nothing reaches the
network."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from launcher.version import VERSION  # noqa: E402
from relay import updater as up  # noqa: E402

HOST = up.ALLOWED_HOSTS[0]
GOOD_DIGEST = "a" * 64


def manifest(version="99.0.0-beta", **overrides) -> bytes:
    doc = {
        "version": version,
        "notes": "what changed",
        "builds": {
            "macos-arm64": {"url": f"https://{HOST}/cc-arm64.dmg",
                            "sha256": GOOD_DIGEST, "size": 1024},
            "macos-x86_64": {"url": f"https://{HOST}/cc-x86.dmg",
                             "sha256": GOOD_DIGEST, "size": 1024},
        },
    }
    doc.update(overrides)
    return json.dumps(doc).encode()


class VersionTests(unittest.TestCase):
    def test_a_higher_version_is_offered(self):
        self.assertTrue(up.is_newer("99.0.0-beta", "0.3.0-beta"))

    def test_the_same_version_is_not(self):
        self.assertFalse(up.is_newer(VERSION, VERSION))

    def test_a_lower_version_is_never_offered(self):
        """A rolled-back manifest must not push testers backwards."""
        self.assertFalse(up.is_newer("0.1.0-beta", "0.3.0-beta"))

    def test_the_beta_suffix_does_not_affect_ordering(self):
        self.assertTrue(up.is_newer("0.4.0", "0.3.0-beta"))
        self.assertFalse(up.is_newer("0.3.0", "0.3.0-beta"))

    def test_junk_is_not_an_update(self):
        for junk in ("", None, "next", "../../etc/passwd", "0.4.0; rm -rf /"):
            with self.subTest(junk=junk):
                self.assertFalse(up.is_newer(junk, "0.3.0-beta"))


class ManifestTests(unittest.TestCase):
    def test_a_good_manifest_parses(self):
        m = up.parse_manifest(manifest())
        self.assertEqual("99.0.0-beta", m.version)
        self.assertEqual(2, len(m.builds))
        self.assertEqual(1024, m.builds["macos-arm64"].size)

    def test_a_build_hosted_somewhere_else_is_dropped(self):
        """The one that matters: a manifest is only as trustworthy as where it
        can send you, so an off-host URL is ignored rather than fetched."""
        for bad in ("https://evil.example/cc.dmg",
                    f"http://{HOST}/cc.dmg",            # plaintext
                    f"https://{HOST}.evil.example/cc.dmg",
                    "file:///etc/passwd"):
            with self.subTest(url=bad):
                doc = json.loads(manifest())
                doc["builds"]["macos-arm64"]["url"] = bad
                m = up.parse_manifest(json.dumps(doc).encode())
                self.assertNotIn("macos-arm64", m.builds)

    def test_a_malformed_digest_is_dropped(self):
        for bad in ("", "abc", "z" * 64, GOOD_DIGEST.upper() + "a"):
            with self.subTest(digest=bad):
                doc = json.loads(manifest())
                doc["builds"]["macos-arm64"]["sha256"] = bad
                self.assertNotIn(
                    "macos-arm64",
                    up.parse_manifest(json.dumps(doc).encode()).builds)

    def test_an_impossible_size_is_dropped(self):
        for bad in (0, -1, True, "1024", None, up.DOWNLOAD_MAX_BYTES + 1):
            with self.subTest(size=bad):
                doc = json.loads(manifest())
                doc["builds"]["macos-arm64"]["size"] = bad
                self.assertNotIn(
                    "macos-arm64",
                    up.parse_manifest(json.dumps(doc).encode()).builds)

    def test_a_manifest_that_is_not_json_is_an_error(self):
        with self.assertRaises(up.UpdateError):
            up.parse_manifest(b"<html>404</html>")

    def test_a_manifest_with_no_version_is_an_error(self):
        with self.assertRaises(up.UpdateError):
            up.parse_manifest(b'{"builds": {}}')

    def test_notes_are_bounded(self):
        m = up.parse_manifest(manifest(notes="x" * 99999))
        self.assertLessEqual(len(m.notes), 4000)


class DownloadVerificationTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "cc.dmg"
        self.body = b"pretend disk image" * 40
        self.path.write_bytes(self.body)
        self.build = up.Build(
            url=f"https://{HOST}/cc.dmg",
            sha256=hashlib.sha256(self.body).hexdigest(),
            size=len(self.body))

    def tearDown(self):
        self.dir.cleanup()

    def test_a_matching_file_verifies(self):
        up.verify_download(self.path, self.build)      # does not raise

    def test_a_truncated_download_is_refused(self):
        self.path.write_bytes(self.body[:-10])
        with self.assertRaises(up.UpdateError) as caught:
            up.verify_download(self.path, self.build)
        self.assertIn("bytes", str(caught.exception))

    def test_a_file_of_the_right_length_but_wrong_content_is_refused(self):
        """Size alone would pass this. It is why the digest is also checked."""
        self.path.write_bytes(b"X" * len(self.body))
        with self.assertRaises(up.UpdateError) as caught:
            up.verify_download(self.path, self.build)
        self.assertIn("checksum", str(caught.exception))


class RefusalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.live = False
        self.up = up.Updater(staging=Path(self.dir.name),
                             is_live=lambda: self.live)

    def tearDown(self):
        self.dir.cleanup()

    async def test_downloading_before_checking_is_an_error(self):
        status = await self.up.fetch()
        self.assertEqual("error", status["state"])
        self.assertIn("check first", status["error"])

    def test_installing_nothing_is_an_error(self):
        status = self.up.install()
        self.assertEqual("error", status["state"])

    def test_a_live_event_blocks_the_install(self):
        """The point of the whole guard: restarting the app mid-service is a
        worse outcome than running an old build for another hour."""
        self.up.state = "ready"
        self.up.staged = Path(self.dir.name) / "Campus Captions.app"
        self.live = True
        status = self.up.install()
        self.assertEqual("error", status["state"])
        self.assertIn("live", status["error"])
        self.assertTrue(status["blocked_by_event"])

    def test_status_reports_the_ad_hoc_trust_note(self):
        """While the app is ad-hoc signed the operator is told so, next to the
        button, rather than it being silently weaker."""
        status = self.up.status()
        self.assertFalse(status["signed"])
        self.assertIn("microphone", status["trust_note"])
        self.assertEqual(VERSION, status["current"])

    def test_can_install_is_false_until_something_is_ready(self):
        self.assertFalse(self.up.status()["can_install"])
        self.up.state = "ready"
        self.assertTrue(self.up.status()["can_install"])
        self.live = True
        self.assertFalse(self.up.status()["can_install"])


class AccessCodeTests(unittest.TestCase):
    """The per-tester code that gates downloads. It is a credential, so the
    two things that matter are that it is attached when it should be and never
    appears where it should not."""

    def setUp(self):
        self.previous = os.environ.get(up.ACCESS_TOKEN_ENV)
        os.environ[up.ACCESS_TOKEN_ENV] = "tester-code-123"

    def tearDown(self):
        if self.previous is None:
            os.environ.pop(up.ACCESS_TOKEN_ENV, None)
        else:
            os.environ[up.ACCESS_TOKEN_ENV] = self.previous

    def test_the_code_travels_as_a_bearer_token(self):
        self.assertEqual("Bearer tester-code-123",
                         up.request_headers()["Authorization"])

    def test_no_code_means_no_header_rather_than_an_empty_one(self):
        os.environ.pop(up.ACCESS_TOKEN_ENV)
        self.assertNotIn("Authorization", up.request_headers())

    def test_status_says_a_code_is_set_but_never_what_it_is(self):
        status = up.Updater(staging=Path(tempfile.gettempdir())).status()
        self.assertTrue(status["has_access_code"])
        self.assertNotIn("tester-code-123", json.dumps(status))

    def test_a_refused_code_is_explained_rather_than_numbered(self):
        self.assertIn("revoked", up._http_message(401))
        self.assertIn("revoked", up._http_message(403))

    def test_having_no_code_at_all_says_so_instead(self):
        os.environ.pop(up.ACCESS_TOKEN_ENV)
        self.assertIn("no update code", up._http_message(401))

    def test_the_code_is_never_sent_off_the_allowed_host(self):
        """_fetch refuses before a request object is built, so there is no
        path on which the header could be attached to a foreign host."""
        for bad in ("https://evil.example/latest.json",
                    f"http://{HOST}/latest.json"):
            with self.subTest(url=bad):
                with self.assertRaises(up.UpdateError):
                    up._fetch(bad, 1024)

    def test_a_download_from_a_foreign_host_is_refused_too(self):
        build = up.Build(url="https://evil.example/cc.dmg",
                         sha256=GOOD_DIGEST, size=1024)
        with self.assertRaises(up.UpdateError):
            up.download(build, Path(tempfile.gettempdir()))


class FetchTargetTests(unittest.TestCase):
    def test_fetching_from_another_host_is_refused_outright(self):
        for bad in ("https://evil.example/latest.json",
                    f"http://{HOST}/latest.json",
                    "file:///etc/passwd"):
            with self.subTest(url=bad):
                with self.assertRaises(up.UpdateError):
                    up._fetch(bad, 1024)

    def test_each_platform_maps_to_the_build_it_can_run(self):
        """PyInstaller freezes for the host architecture, so an arm64 build
        does not launch on an Intel Mac at all. Only Apple Silicon is shipped;
        an Intel Mac gets a key no manifest carries, and so no update."""
        cases = {
            ("darwin", "arm64"): "macos-arm64",
            ("darwin", "aarch64"): "macos-arm64",
            ("darwin", "x86_64"): "darwin-x86_64",
        }
        for (system, machine), expected in cases.items():
            with self.subTest(system=system, machine=machine):
                with mock.patch.object(up.sys, "platform", system), \
                     mock.patch.object(up.platform, "machine", lambda: machine):
                    self.assertEqual(expected, up.platform_key())

    def test_a_platform_we_do_not_ship_for_is_offered_nothing(self):
        """A Linux checkout - which is what CI is - gets a key no manifest
        entry uses, so it is told there is no build rather than handed one
        that cannot run."""
        with mock.patch.object(up.sys, "platform", "linux"), \
             mock.patch.object(up.platform, "machine", lambda: "x86_64"):
            self.assertEqual("linux-x86_64", up.platform_key())
            self.assertIsNone(up.parse_manifest(manifest()).for_this_machine())

    def test_a_manifest_with_no_builds_at_all_offers_nothing(self):
        self.assertIsNone(up.parse_manifest(manifest(builds={})).for_this_machine())


if __name__ == "__main__":
    unittest.main()
