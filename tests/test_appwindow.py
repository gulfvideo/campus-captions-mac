"""launcher/appwindow.py: choosing how a page opens.

No browser is launched here - the tests cover the decisions around that, which
are the parts that can be wrong on a machine nobody is watching: what happens
when there is no Chromium-family browser at all, and that open_link always
reports what it actually did rather than what it was asked to do.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from launcher import appwindow  # noqa: E402


class ModeTests(unittest.TestCase):
    def test_modes_are_the_two_the_config_accepts(self):
        self.assertEqual(set(appwindow.MODES), {"app", "browser"})
        self.assertEqual(appwindow.DEFAULT_MODE, "app")


class OpenLinkTests(unittest.TestCase):
    def test_browser_mode_never_opens_an_app_window(self):
        with mock.patch.object(appwindow, "open_app_window") as window, \
             mock.patch.object(appwindow.webbrowser, "open") as tab:
            self.assertEqual(appwindow.open_link("http://x/", "browser", "/tmp"), "browser")
        window.assert_not_called()
        tab.assert_called_once()

    def test_app_mode_uses_a_window_when_one_opens(self):
        with mock.patch.object(appwindow, "open_app_window", return_value=True), \
             mock.patch.object(appwindow.webbrowser, "open") as tab:
            self.assertEqual(appwindow.open_link("http://x/", "app", "/tmp"), "app")
        tab.assert_not_called()

    def test_app_mode_falls_back_to_a_tab_and_says_so(self):
        # A machine with only Firefox. The caller needs the real answer, not
        # the requested one, so it can tell the operator why the window they
        # asked for did not appear.
        with mock.patch.object(appwindow, "open_app_window", return_value=False), \
             mock.patch.object(appwindow.webbrowser, "open") as tab:
            self.assertEqual(appwindow.open_link("http://x/", "app", "/tmp"), "browser")
        tab.assert_called_once()

    def test_no_browser_at_all_is_not_fatal(self):
        # Nothing to open a page with must not take the relay down; it is
        # already running and serving that page to anything that asks.
        with mock.patch.object(appwindow, "open_app_window", return_value=False), \
             mock.patch.object(appwindow.webbrowser, "open", side_effect=OSError("none")):
            self.assertEqual(appwindow.open_link("http://x/", "app", "/tmp"), "browser")


class DiscoveryTests(unittest.TestCase):
    def test_describe_reports_unavailable_with_no_browser(self):
        with mock.patch.object(appwindow, "find_browser", return_value=None):
            self.assertEqual(appwindow.describe(), {"available": False, "browser": ""})

    def test_describe_names_the_browser_it_would_use(self):
        with mock.patch.object(appwindow, "find_browser",
                               return_value="/usr/bin/google-chrome"):
            self.assertEqual(appwindow.describe(),
                             {"available": True, "browser": "google-chrome"})

    def test_open_app_window_reports_failure_rather_than_raising(self):
        with mock.patch.object(appwindow, "find_browser", return_value="/nope/chrome"), \
             mock.patch.object(appwindow.subprocess, "Popen",
                               side_effect=OSError("no such file")):
            self.assertIs(appwindow.open_app_window("http://x/", "/tmp"), False)

    def test_profile_is_its_own_directory(self):
        # Never the operator's default profile: an app window sharing it
        # inherits their extensions and restore-tabs prompt, and on some
        # versions attaches to the running browser instead of opening.
        self.assertEqual(appwindow.profile_dir("/data"), Path("/data/appwindow"))


if __name__ == "__main__":
    unittest.main()
