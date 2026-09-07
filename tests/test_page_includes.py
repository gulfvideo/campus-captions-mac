"""relay/server.py: the shared-script include that lets several viewer pages
carry one copy of tour.js, and the CSP hash that has to cover it.

The pages' Content-Security-Policy names hashes of their own inline scripts
and deliberately does not allow script-src 'self', so a shared module cannot
be loaded with src=. It is stitched in when the page is served instead, which
means the hash has to be taken *after* the substitution and the page cache
has to notice when the shared file - not the page - is what changed.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException  # noqa: E402

from relay.server import (INCLUDE_RE, expand_includes, includes_current,  # noqa: E402
                          page_csp)

VIEWER = Path(__file__).resolve().parents[1] / "viewer"


class ExpandTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.viewer = Path(self.dir.name)
        (self.viewer / "shared.js").write_text("var shared = 1;\n", encoding="utf-8")

    def tearDown(self):
        self.dir.cleanup()

    def test_marker_is_replaced_with_the_file_in_a_script_tag(self):
        html, used = expand_includes(b"<body><!-- include shared.js --></body>", self.viewer)
        self.assertIn(b"<script>", html)
        self.assertIn(b"var shared = 1;", html)
        self.assertNotIn(b"include shared.js", html)
        self.assertEqual(len(used), 1)

    def test_a_page_with_no_marker_is_returned_untouched(self):
        page = b"<body><script>var a = 1;</script></body>"
        html, used = expand_includes(page, self.viewer)
        self.assertEqual(html, page)
        self.assertEqual(used, ())

    def test_several_markers_are_all_expanded(self):
        (self.viewer / "other.js").write_text("var other = 2;\n", encoding="utf-8")
        html, used = expand_includes(
            b"<!-- include shared.js -->\n<!-- include other.js -->", self.viewer)
        self.assertIn(b"var shared = 1;", html)
        self.assertIn(b"var other = 2;", html)
        self.assertEqual(len(used), 2)

    def test_whitespace_around_the_name_is_allowed(self):
        html, _ = expand_includes(b"<!--include   shared.js-->", self.viewer)
        self.assertIn(b"var shared = 1;", html)

    def test_a_missing_file_is_a_server_error_not_a_silent_empty_script(self):
        # Silently serving a page whose script did not arrive would leave a
        # dashboard that looks right and does nothing.
        with self.assertRaises(HTTPException) as caught:
            expand_includes(b"<!-- include nope.js -->", self.viewer)
        self.assertEqual(caught.exception.status_code, 500)

    def test_a_name_may_not_climb_out_of_the_viewer_directory(self):
        secret = self.viewer.parent / "secret.js"
        secret.write_text("stolen", encoding="utf-8")
        try:
            for name in (b"../secret.js", b"/etc/passwd.js", b"sub/shared.js"):
                page = b"<!-- include " + name + b" -->"
                html, used = expand_includes(page, self.viewer)
                # The pattern does not match a path, so nothing is read and
                # the marker is left alone rather than expanded.
                self.assertEqual(html, page, name.decode())
                self.assertEqual(used, ())
                self.assertNotIn(b"stolen", html)
        finally:
            secret.unlink()

    def test_only_js_is_includable(self):
        (self.viewer / "event.toml").write_text("secret = 1", encoding="utf-8")
        page = b"<!-- include event.toml -->"
        self.assertIsNone(INCLUDE_RE.search(page))
        self.assertEqual(expand_includes(page, self.viewer)[0], page)


class StalenessTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.viewer = Path(self.dir.name)
        self.js = self.viewer / "shared.js"
        self.js.write_text("var shared = 1;\n", encoding="utf-8")

    def tearDown(self):
        self.dir.cleanup()

    def test_an_untouched_include_is_still_current(self):
        _, used = expand_includes(b"<!-- include shared.js -->", self.viewer)
        self.assertTrue(includes_current(used))

    def test_rewriting_the_shared_file_makes_the_cached_page_stale(self):
        _, used = expand_includes(b"<!-- include shared.js -->", self.viewer)
        os.utime(self.js, (time.time() + 5, time.time() + 5))
        self.assertFalse(includes_current(used))

    def test_deleting_the_shared_file_makes_it_stale_rather_than_raising(self):
        _, used = expand_includes(b"<!-- include shared.js -->", self.viewer)
        self.js.unlink()
        self.assertFalse(includes_current(used))


class CspTests(unittest.TestCase):
    """The whole point of expanding server-side: the policy has to cover the
    script that was stitched in, or the browser blocks it and the page is
    inert with nothing in the console to explain why."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.viewer = Path(self.dir.name)
        (self.viewer / "shared.js").write_text("var shared = 1;\n", encoding="utf-8")

    def tearDown(self):
        self.dir.cleanup()

    @staticmethod
    def digest(script: bytes) -> str:
        normalized = script.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        return "'sha256-" + base64.b64encode(hashlib.sha256(normalized).digest()).decode() + "'"

    def test_the_policy_names_the_included_script(self):
        html, _ = expand_includes(
            b"<script>var page = 1;</script>\n<!-- include shared.js -->", self.viewer)
        csp = page_csp(html)
        self.assertIn(self.digest(b"var page = 1;"), csp)
        self.assertIn(self.digest(b"\nvar shared = 1;\n\n"), csp)
        self.assertNotIn("'self'", csp.split("style-src")[0])

    def test_editing_the_shared_file_changes_the_hash(self):
        page = b"<!-- include shared.js -->"
        first = page_csp(expand_includes(page, self.viewer)[0])
        (self.viewer / "shared.js").write_text("var shared = 2;\n", encoding="utf-8")
        self.assertNotEqual(first, page_csp(expand_includes(page, self.viewer)[0]))


class ShippedPagesTests(unittest.TestCase):
    """The pages as they are actually shipped."""

    def test_the_three_operator_pages_include_the_tour(self):
        for name in ("status.html", "settings.html", "tune.html"):
            page = (VIEWER / name).read_bytes()
            self.assertIsNotNone(INCLUDE_RE.search(page), name)
            self.assertIn(b"data-tour-start", page, name)

    def test_every_included_file_exists_and_the_policy_covers_it(self):
        for name in ("status.html", "settings.html", "tune.html"):
            html, used = expand_includes((VIEWER / name).read_bytes(), VIEWER)
            self.assertTrue(used, name)
            self.assertIn(b"CampusTour", html, name)
            csp = page_csp(html)
            scripts = re.findall(rb"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",
                                 html, re.DOTALL)
            self.assertEqual(len(scripts), 2, name)   # the page's own, and the tour
            for script in scripts:
                self.assertIn(CspTests.digest(script), csp, name)


if __name__ == "__main__":
    unittest.main()
