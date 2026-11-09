"""relay/translate.py AppleTranslator: the Python side of the on-device
helper, driven against a fake helper so CI (Linux, no Translation framework)
exercises the protocol, the cache, the per-language errors and the restart."""

from __future__ import annotations

import asyncio
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from relay import translate as tr  # noqa: E402

FAKE = '''#!%s
import json, sys
print(json.dumps({"ready": True})); sys.stdout.flush()
for line in sys.stdin:
    req = json.loads(line)
    if req["text"] == "die":
        sys.exit(1)
    out = {t: f"[{t}] {req['text']}" for t in req["targets"] if t != "xx"}
    err = {"xx": "xx is not supported"} if "xx" in req["targets"] else {}
    print(json.dumps({"id": req["id"], "translations": out, "errors": err})); sys.stdout.flush()
''' % sys.executable


class AppleTranslatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        fake = Path(self.dir.name) / "translate_helper"
        fake.write_text(FAKE)
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.previous = tr.HELPER
        tr.HELPER = fake                     # newer than SOURCE, so no build
        self.t = tr.AppleTranslator()

    async def asyncTearDown(self):
        if self.t._proc is not None and self.t._proc.returncode is None:
            self.t._proc.kill()
            await self.t._proc.wait()
        tr.HELPER = self.previous
        self.dir.cleanup()

    async def test_lines_come_back_per_language(self):
        out = await self.t.translate("Good morning.", "en", ["es", "fr"])
        self.assertEqual({"es": "[es] Good morning.", "fr": "[fr] Good morning."}, out)
        self.assertTrue(self.t.healthy)
        self.assertEqual(1, self.t.calls)

    async def test_a_repeated_line_is_served_from_the_cache(self):
        await self.t.translate("Amen.", "en", ["es"])
        await self.t.translate("Amen.", "en", ["es"])
        self.assertEqual(1, self.t.calls)

    async def test_an_unsupported_language_is_reported_but_the_rest_go_out(self):
        out = await self.t.translate("Hello.", "en", ["es", "xx"])
        self.assertEqual({"es": "[es] Hello."}, out)
        self.assertIn("xx", self.t.last_error)

    async def test_a_dead_helper_is_restarted_on_the_next_line(self):
        self.assertEqual({}, await self.t.translate("die", "en", ["es"]))
        self.assertFalse(self.t.healthy)
        out = await self.t.translate("Back.", "en", ["es"])
        self.assertEqual({"es": "[es] Back."}, out)
        self.assertTrue(self.t.healthy)


class CreateTests(unittest.TestCase):
    def test_apple_is_a_provider_on_macos_only(self):
        made = tr.create("apple", ["es"])
        if sys.platform == "darwin":
            self.assertIsInstance(made, tr.AppleTranslator)
        else:
            self.assertIsNone(made)

    def test_no_targets_means_no_translator(self):
        self.assertIsNone(tr.create("apple", []))


if __name__ == "__main__":
    unittest.main()
