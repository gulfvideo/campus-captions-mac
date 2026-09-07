"""The guided tour: the flag that decides whether a machine gets shown round,
and the step list itself.

The steps are JavaScript, so they are checked here the way a reviewer would:
by reading them out of viewer/tour.js and asking whether every selector they
point at is really on the page they claim, and whether the order makes sense
to somebody being walked through it.
"""

from __future__ import annotations

import re
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from relay import config  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TOUR = ROOT / "viewer" / "tour.js"
PAGE_FILE = {"/status": "status.html", "/tune": "tune.html", "/settings": "settings.html"}


def steps() -> list[dict]:
    """Every step in tour.js, as {page, target, title}."""
    body = TOUR.read_text(encoding="utf-8")
    body = body[body.index("var STEPS = ["):body.index("  // ---- things a step may need")]
    out = []
    for block in re.split(r"\n    \{", body)[1:]:
        page = re.search(r"page:\s*'([^']+)'", block)
        target = re.search(r"target:\s*'([^']+)'", block)
        title = re.search(r"title:\s*'([^']*)'|title:\s*\"([^\"]*)\"", block)
        out.append({
            "page": page.group(1) if page else None,
            "target": target.group(1) if target else None,
            "title": (title.group(1) or title.group(2)) if title else "",
        })
    return out


class StepTests(unittest.TestCase):
    def setUp(self):
        self.steps = steps()

    def test_the_steps_were_read(self):
        self.assertGreaterEqual(len(self.steps), 20)

    def test_every_step_names_a_page_that_exists(self):
        for s in self.steps:
            self.assertIn(s["page"], PAGE_FILE, s["title"])

    def test_every_target_is_an_id_that_is_actually_on_that_page(self):
        """The one failure mode that matters: a step pointing at nothing.
        The tour skips a missing target rather than showing an empty ring,
        so a renamed id would quietly delete a step instead of erroring."""
        pages = {p: (ROOT / "viewer" / f).read_text(encoding="utf-8")
                 for p, f in PAGE_FILE.items()}
        for s in self.steps:
            if not s["target"]:
                continue
            self.assertTrue(s["target"].startswith("#"),
                            f"{s['title']}: {s['target']} is not an id")
            # Checked by hand rather than with assertIn, whose failure
            # message would be the whole page.
            if f'id="{s["target"][1:]}"' not in pages[s["page"]]:
                self.fail(f"the step {s['title']!r} points at {s['target']}, "
                          f"which is not on {PAGE_FILE[s['page']]}")

    def test_the_tour_visits_each_page_once_and_in_order(self):
        # Bouncing between pages would mean a page load mid-explanation.
        visited = [s["page"] for s in self.steps]
        runs = [p for i, p in enumerate(visited) if i == 0 or p != visited[i - 1]]
        self.assertEqual(runs, ["/status", "/tune", "/settings"])

    def test_it_opens_on_the_status_page(self):
        self.assertEqual(self.steps[0]["page"], "/status")

    def test_the_last_step_is_marked_as_the_last(self):
        body = TOUR.read_text(encoding="utf-8")
        self.assertEqual(body.count("last: true"), 1)

    def test_every_page_the_tour_visits_can_start_it(self):
        for name in PAGE_FILE.values():
            page = (ROOT / "viewer" / name).read_text(encoding="utf-8")
            self.assertIn("data-tour-start", page, name)
            self.assertIn("include tour.js", page, name)


class TourFlagTests(unittest.TestCase):
    """Whether this machine has been shown round, in event.toml beside
    setup.completed."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "event.toml"

    def tearDown(self):
        self.dir.cleanup()

    def read(self) -> dict:
        return tomllib.loads(self.path.read_text(encoding="utf-8"))

    def test_it_is_written_into_the_setup_section(self):
        config.save_tour_complete(self.path, True)
        self.assertIs(self.read()["setup"]["tour_completed"], True)

    def test_it_can_be_cleared_again(self):
        config.save_tour_complete(self.path, True)
        config.save_tour_complete(self.path, False)
        self.assertIs(self.read()["setup"]["tour_completed"], False)

    def test_it_sits_beside_setup_completed_rather_than_replacing_it(self):
        config.save_setup_complete(self.path, True)
        config.save_tour_complete(self.path, False)
        setup = self.read()["setup"]
        self.assertIs(setup["completed"], True)
        self.assertIs(setup["tour_completed"], False)

    def test_a_config_that_does_not_mention_the_tour_answers_with_setup(self):
        """A booth configured before the tour existed has been running for a
        while and should not be ambushed by one; a fresh install has not been
        through the wizard yet, and gets both."""
        self.path.write_text("[setup]\ncompleted = true\n", encoding="utf-8")
        self.assertIs(config.load(self.path).tour_completed, True)

        self.path.write_text("[setup]\ncompleted = false\n", encoding="utf-8")
        self.assertIs(config.load(self.path).tour_completed, False)

    def test_an_explicit_answer_wins_over_setup_completed(self):
        self.path.write_text("[setup]\ncompleted = true\ntour_completed = false\n",
                             encoding="utf-8")
        self.assertIs(config.load(self.path).tour_completed, False)

    def test_a_config_with_no_setup_section_at_all_is_left_alone(self):
        # Not a fresh install: a machine whose event.toml predates the wizard.
        self.path.write_text("[relay]\nport = 8765\n", encoding="utf-8")
        cfg = config.load(self.path)
        self.assertIs(cfg.setup_completed, False)
        self.assertIs(cfg.tour_completed, False)


if __name__ == "__main__":
    unittest.main()
