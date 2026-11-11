"""viewer/status.html: the structure the console's own script depends on.

The console was rebuilt around two screens - Ready before anything is
running, Live once it is - driven by one field in the status snapshot. Its
script reaches for elements by id, and a renamed or deleted id fails
silently in a browser: the page renders, the control simply stops working.
These tests read the page the way that script does and say so out loud.

Standard library only; no browser and no relay.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

VIEWER = Path(__file__).resolve().parents[1] / "viewer"
STATUS = (VIEWER / "status.html").read_text(encoding="utf-8")
THEME = (VIEWER / "theme.css").read_text(encoding="utf-8")


def ids(html: str) -> set[str]:
    return set(re.findall(r'id="([A-Za-z0-9_-]+)"', html))


def script_of(html: str) -> str:
    blocks = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)
    return "\n".join(blocks)


def body_of(js: str, name: str) -> str:
    """The source of one top-level `function name(...) { ... }`, found by
    counting braces from its opening one. Crude, and enough: the console's
    script has no strings or regexes containing unbalanced braces."""
    start = js.index("function " + name + "(")
    depth, i = 0, js.index("{", start)
    for i in range(i, len(js)):
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
            if depth == 0:
                return js[start:i + 1]
    raise AssertionError("unbalanced braces in " + name)


class IdsTests(unittest.TestCase):
    """Every id the script asks for by name exists in the markup."""

    def test_every_dollar_lookup_resolves(self):
        present = ids(STATUS)
        asked = set(re.findall(r"\$\('([A-Za-z0-9_-]+)'\)", script_of(STATUS)))
        missing = sorted(asked - present)
        if missing:
            self.fail("status.html's script looks up ids that are not on the "
                      "page: " + ", ".join(missing))

    def test_the_two_console_screens_are_both_there(self):
        self.assertIn('id="view-ready"', STATUS)
        self.assertIn('id="view-live"', STATUS)

    def test_the_rail_and_its_panes_line_up(self):
        # showPane() derives one id from the other; a rail button with no
        # pane would hide every pane and show nothing.
        rails = set(re.findall(r'id="nav-([a-z]+)" data-pane="([a-z]+)"', STATUS))
        for nav, pane in rails:
            self.assertEqual(nav, pane)
            self.assertIn(f'id="pane-{pane}"', STATUS)

    def test_go_live_and_its_two_halves_all_exist(self):
        # The promise made when the console was redesigned: one button for
        # the ordinary path, and the two things it does still available on
        # their own. Losing either half silently is the regression to catch.
        for button in ("btn-golive", "btn-open", "btn-captions"):
            self.assertIn(f'id="{button}"', STATUS, button)

    def test_the_audience_switch_offers_every_event_state(self):
        # The four states the relay accepts (relay/event.py STATES), each one
        # tap away at the top of the console, whichever screen is showing.
        for state in ("starting", "live", "paused", "ended"):
            self.assertIn(f'data-state="{state}"', STATUS, state)
        self.assertLess(STATUS.index('id="audience"'), STATUS.index('id="view-ready"'))


class BehaviourTests(unittest.TestCase):
    def test_go_live_opens_the_stream_and_starts_the_event(self):
        js = script_of(STATUS)
        handler = js[js.index("$('btn-golive').onclick"):]
        handler = handler[:handler.index("$('btn-open').onclick")]
        self.assertIn("open_stream", handler)
        self.assertIn("state: 'live'", handler)

    def test_starting_captions_alone_does_not_start_the_event(self):
        js = script_of(STATUS)
        handler = js[js.index("$('btn-open').onclick"):]
        handler = handler[:handler.index("$('btn-captions').onclick")]
        self.assertIn("open_stream", handler)
        self.assertNotIn("state: 'live'", handler)

    def test_choosing_live_starts_captions_if_they_are_stopped(self):
        """Live with captions stopped shows every attendee "Captions paused",
        which is what the audience switch exists to prevent."""
        js = script_of(STATUS)
        fn = js[js.index("function setAudience"):js.index("function render(s)")]
        self.assertIn("state === 'live' && !open", fn)
        self.assertIn("open_stream", fn)

    def test_the_screen_shown_is_decided_by_whether_the_stream_is_open(self):
        js = script_of(STATUS)
        self.assertIn("$('view-ready').hidden = open;", js)
        self.assertIn("$('view-live').hidden = !open;", js)

    def test_the_state_words_match_what_the_relay_actually_sends(self):
        """relay/state.py emits these and no others. A map keyed on invented
        names falls through to the raw value, which is how 'live' once showed
        up on the console as a red fault."""
        js = script_of(STATUS)
        audio = js[js.index("var AUDIO = {"):js.index("var ASR = {")]
        for state in ("live", "clipping", "silent", "stale", "no_source"):
            self.assertIn(state + ":", audio, state)
        asr = js[js.index("var ASR = {"):js.index("var lastFinal")]
        for state in ("producing", "open_no_text", "closed", "no_source"):
            self.assertIn(state + ":", asr, state)

    def test_render_re_enables_what_renderoffline_switched_off(self):
        """renderOffline() disables controls when the relay drops. Anything it
        turns off has to be turned back on when the relay answers again, or a
        page that started offline keeps dead buttons forever."""
        js = script_of(STATUS)
        offline = body_of(js, "renderOffline")
        # render() and the helpers it calls, and nothing else: a control
        # re-enabled only in, say, runPreflight() is re-enabled only if
        # somebody presses that button, which is not the same thing.
        reached = body_of(js, "render")
        # Comments first: render() carries a line saying what renderOffline()
        # switched off, and reading that as a call to it would pull the
        # offline renderer into the live path and make this test vacuous.
        code = re.sub(r"//[^\n]*", "", reached)
        for callee in sorted(set(re.findall(r"\b(render[A-Z]\w*)\(", code))):
            if callee != "renderOffline":
                reached += body_of(js, callee)

        disabled = set(re.findall(r"\$\('([a-z-]+)'\)\.disabled", offline))
        revived = set(re.findall(r"\$\('([a-z-]+)'\)\.disabled\s*=", reached))
        # These two are driven by their own select element, enabled from the
        # device and backend lists the relay sends.
        revived |= {"audio-device", "backend-select"}
        missing = sorted(disabled - revived)
        self.assertEqual(missing, [],
                         "renderOffline disables these and nothing render() "
                         "reaches re-enables them: " + ", ".join(missing))


class ThemeTests(unittest.TestCase):
    def test_the_palette_is_defined_once(self):
        for token in ("--bg", "--card", "--raised", "--text", "--text-2",
                      "--accent", "--ok", "--warn", "--bad"):
            self.assertIn(token + ":", THEME, token)

    def test_no_page_redefines_a_colour_the_theme_owns(self):
        """A page may alias a legacy name onto the palette; it may not hold a
        second opinion about what --bg or --text are."""
        for name in ("status.html", "settings.html", "tune.html", "setup.html"):
            page = (VIEWER / name).read_text(encoding="utf-8")
            style = "\n".join(re.findall(r"<style>(.*?)</style>", page, re.DOTALL))
            for token in ("--bg", "--text", "--accent", "--ok", "--bad"):
                hit = re.search(rf"{token}\s*:\s*#", style)
                if hit:
                    self.fail(f"{name} sets {token} to a literal colour; "
                              f"theme.css owns it")


if __name__ == "__main__":
    unittest.main()
