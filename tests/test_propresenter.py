"""relay/propresenter.py against the fake ProPresenter API. Standard library
only. Every test drives the real asyncio worker and prober, with a live
HTTP round trip to the fake on a random localhost port."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_propresenter import FakeProPresenter  # noqa: E402
from relay import propresenter, transcript  # noqa: E402
from relay.propresenter import ProPresenterOutput  # noqa: E402


async def settle(out: ProPresenterOutput, timeout: float = 2.0) -> None:
    """Wait until the worker has nothing queued."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if out._finals.empty() and out._pending_interim is None:
            await asyncio.sleep(0.05)
            if out._finals.empty() and out._pending_interim is None:
                return
        await asyncio.sleep(0.02)


async def sent_pages(pp) -> list[str]:
    return [c[2] for c in pp.calls if c[0] == "PUT"]


async def settle_pages(pp, text: str, timeout: float = 8.0) -> list[str]:
    """Wait until every page of `text` has been delivered. settle() cannot do
    this: the finals queue empties on the first page, and the worker then
    holds each page long enough to be read before sending the next."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        pages = await sent_pages(pp)
        if " ".join(p.replace("\n", " ") for p in pages) == text:
            return pages
        await asyncio.sleep(0.05)
    return await sent_pages(pp)


def run(coro):
    return asyncio.run(coro)


class StageMessageTests(unittest.TestCase):
    def test_probe_connects_and_reads_version(self):
        async def go():
            with FakeProPresenter() as pp:
                out = ProPresenterOutput(port=pp.port)
                self.assertEqual(out.state, "off")
                self.assertTrue(await out.probe())
                self.assertEqual(out.state, "connected")
                self.assertEqual(out.version["api_version"], "v1")
        run(go())

    def test_finals_land_on_the_stage_message_in_order(self):
        async def go():
            with FakeProPresenter() as pp:
                out = ProPresenterOutput(port=pp.port)
                await out.start()
                out.submit("First line.", True)
                out.submit("Second line.", True)
                await settle(out)
                puts = [c for c in pp.calls if c[0] == "PUT"]
                self.assertEqual([c[2] for c in puts], ["First line.", "Second line."])
                self.assertEqual(pp.stage_message, "Second line.")
                self.assertEqual(out.pushed, 2)
                self.assertIsNotNone(out.latency())
                await out.stop()
                self.assertIsNone(pp.stage_message)        # stop clears the surface
                self.assertEqual(out.state, "off")
        run(go())

    def test_interims_are_off_by_default_and_coalesce_when_on(self):
        async def go():
            with FakeProPresenter() as pp:
                out = ProPresenterOutput(port=pp.port)
                await out.start()
                out.submit("our next", False)
                await settle(out)
                self.assertEqual([c for c in pp.calls if c[0] == "PUT"], [])

                out.configure(interims=True)
                pp.delay = 0.15                       # slow app: interims pile up
                out.submit("our", False)
                out.submit("our next", False)
                out.submit("our next speaker", False)
                await settle(out, timeout=3.0)
                puts = [c[2] for c in pp.calls if c[0] == "PUT"]
                self.assertLess(len(puts), 3)         # never every interim
                self.assertEqual(puts[-1], "our next speaker")   # always the newest
                await out.stop()
        run(go())

    def test_clear_removes_the_stage_message(self):
        async def go():
            with FakeProPresenter() as pp:
                out = ProPresenterOutput(port=pp.port)
                await out.start()
                out.submit("Line.", True)
                await settle(out)
                await out.clear()
                self.assertIsNone(pp.stage_message)
                self.assertIn(("DELETE", "/v1/stage/message", None), pp.calls)
                await out.stop(clear_surface=False)
        run(go())


class MessageSurfaceTests(unittest.TestCase):
    def test_message_is_resolved_by_name_and_token_filled(self):
        async def go():
            with FakeProPresenter() as pp:
                out = ProPresenterOutput(port=pp.port, surface="message",
                                         message="live captions", token="Caption")
                await out.start()
                self.assertEqual(out.state, "connected")
                self.assertEqual(out.message_id, "11111111-2222-3333-4444-555555555555")
                out.submit("Hello.", True)
                await settle(out)
                self.assertEqual(len(pp.triggered), 1)
                ident, payload = pp.triggered[0]
                self.assertEqual(payload, [{"name": "Caption", "text": {"text": "Hello."}}])
                await out.clear()
                self.assertEqual(pp.triggered[-1], (ident, []))
                await out.stop(clear_surface=False)
        run(go())

    def test_missing_message_or_token_is_degraded_with_a_plain_reason(self):
        async def go():
            with FakeProPresenter() as pp:
                out = ProPresenterOutput(port=pp.port, surface="message", message="Nope")
                await out.probe()
                self.assertEqual(out.state, "degraded")
                self.assertIn("no Message called 'Nope'", out.error)
                out.configure(message="Live Captions", token="Wrong")
                await out.probe()
                self.assertEqual(out.state, "degraded")
                self.assertIn("no token called 'Wrong'", out.error)
        run(go())


class FailureTests(unittest.TestCase):
    def test_app_not_running_is_disconnected_not_an_exception(self):
        async def go():
            with FakeProPresenter() as pp:
                free_port = pp.port
            out = ProPresenterOutput(port=free_port)      # nothing listening now
            self.assertFalse(await out.probe())
            self.assertEqual(out.state, "disconnected")
            self.assertTrue(out.error)
            await out.start()
            out.submit("Lost.", True)
            await settle(out)
            self.assertEqual(out.pushed, 0)             # no hammering a closed app
            await out.stop(clear_surface=False)
        run(go())

    def test_push_failures_degrade_then_disconnect_then_recover(self):
        async def go():
            with FakeProPresenter() as pp:
                out = ProPresenterOutput(port=pp.port)
                await out.start()
                pp.fail_next = 1
                out.submit("A", True)
                await settle(out)
                self.assertEqual(out.state, "degraded")
                self.assertEqual(out.failures, 1)
                out.submit("B", True)
                await settle(out)
                self.assertEqual(out.state, "connected")     # one success heals it

                pp.down = True
                for text in ("C", "D", "E"):
                    out.submit(text, True)
                await settle(out)
                self.assertEqual(out.state, "disconnected")
                pp.down = False
                self.assertTrue(await out.probe())
                self.assertEqual(out.state, "connected")
                out.submit("F", True)
                await settle(out)
                self.assertEqual(pp.stage_message, "F")
                await out.stop(clear_surface=False)
        run(go())

    def test_unknown_api_version_is_flagged_not_refused(self):
        async def go():
            with FakeProPresenter(api_version="v2") as pp:
                out = ProPresenterOutput(port=pp.port)
                await out.probe()
                self.assertEqual(out.state, "unsupported")
                self.assertIn("v2", out.error)
        run(go())

    def test_status_never_carries_caption_text(self):
        async def go():
            with FakeProPresenter() as pp:
                out = ProPresenterOutput(port=pp.port)
                await out.start()
                out.submit("Something said in the room.", True)
                await settle(out)
                status = out.status()
                self.assertNotIn("Something said", repr(status))
                self.assertEqual(status["pushed"], 1)
                self.assertIn("p95_ms", status["latency"])
                await out.stop(clear_surface=False)
        run(go())


if __name__ == "__main__":
    unittest.main()


class ShapingTests(unittest.TestCase):
    """Wrapping and paging are pure functions - test them without a socket."""

    def test_wrap_breaks_on_word_boundaries_and_never_exceeds_width(self):
        lines = transcript.wrap_lines(
            "the dean of students will speak about the semester schedule", 20)
        self.assertTrue(all(len(line) <= 20 for line in lines), lines)
        self.assertEqual(" ".join(lines),
                         "the dean of students will speak about the semester schedule")

    def test_a_token_longer_than_a_line_is_cut_not_dropped(self):
        lines = transcript.wrap_lines("see " + "x" * 25 + " now", 10)
        self.assertTrue(all(len(line) <= 10 for line in lines), lines)
        self.assertIn("now", " ".join(lines))
        self.assertEqual("".join(lines).count("x"), 25)

    def test_paginate_never_exceeds_two_lines_and_keeps_every_word(self):
        text = ("the campus chapel service continues this morning with a reading "
                "from the second letter to the church at corinth followed by "
                "remarks from the dean of students")
        pages = transcript.split_for_display(text, 42, 2)
        self.assertGreater(len(pages), 1)
        for page in pages:
            self.assertLessEqual(len(page.split("\n")), 2, page)
            self.assertTrue(all(len(line) <= 42 for line in page.split("\n")), page)
        self.assertEqual(" ".join(p.replace("\n", " ") for p in pages), text)

    def test_tail_lines_keeps_the_newest_words(self):
        text = "a b c d e f g h i j k l m n o p q r s t u v w x y z"
        got = propresenter.tail_lines(text, 10, 2)
        self.assertLessEqual(len(got.split("\n")), 2)
        self.assertTrue(got.endswith("z"), got)

    def test_lines_is_capped_at_two_however_it_is_set(self):
        out = ProPresenterOutput(lines=9)
        self.assertEqual(out.lines, propresenter.MAX_LINES)
        out.configure(lines=5)
        self.assertEqual(out.lines, propresenter.MAX_LINES)
        out.configure(lines=1)
        self.assertEqual(out.lines, 1)

    def test_reveal_replaces_interims_but_the_old_name_still_works(self):
        out = ProPresenterOutput()
        self.assertEqual(out.reveal, "line")          # finals only, as before
        self.assertFalse(out.interims)
        out.configure(interims=True)
        self.assertEqual(out.reveal, "word")
        self.assertTrue(out.interims)
        out.configure(reveal="line")
        self.assertFalse(out.interims)
        self.assertEqual(ProPresenterOutput(interims=True).reveal, "word")
        with self.assertRaises(ValueError):
            out.configure(reveal="sentence")


class TwoLineDeliveryTests(unittest.TestCase):
    def test_a_long_final_is_paged_two_lines_at_a_time_losing_nothing(self):
        async def go():
            with FakeProPresenter() as pp:
                out = ProPresenterOutput(port=pp.port, chars_per_line=20)
                await out.start()
                text = ("the library will keep extended hours through the end "
                        "of examination week")
                out.submit(text, True)
                sent = await settle_pages(pp, text)
                self.assertGreater(len(sent), 1)       # it did not arrive as one blob
                for page in sent:
                    self.assertLessEqual(len(page.split("\n")), 2, page)
                self.assertEqual(" ".join(p.replace("\n", " ") for p in sent), text)
        run(go())

    def test_an_interim_is_cut_to_two_lines_of_the_newest_words(self):
        async def go():
            with FakeProPresenter() as pp:
                out = ProPresenterOutput(port=pp.port, reveal="word",
                                         chars_per_line=15)
                await out.start()
                out.submit("one two three four five six seven eight nine ten", False)
                await settle(out)
                self.assertIsNotNone(pp.stage_message)
                self.assertLessEqual(len(pp.stage_message.split("\n")), 2,
                                     pp.stage_message)
                self.assertTrue(pp.stage_message.endswith("ten"), pp.stage_message)
                await out.stop()
        run(go())

    def test_a_short_final_still_arrives_as_one_page(self):
        async def go():
            with FakeProPresenter() as pp:
                out = ProPresenterOutput(port=pp.port)
                await out.start()
                out.submit("Good morning.", True)
                await settle(out)
                self.assertEqual(pp.stage_message, "Good morning.")
                self.assertEqual(out.pushed, 1)
                await out.stop()
        run(go())
