"""relay/updater.py on macOS: the two pieces that only exist as real tooling.

The swap script and the disk-image reader cannot be usefully faked - the whole
risk in them is what hdiutil, codesign, ditto and a detached shell actually do.
So this builds a real signed .app, wraps it in a real .dmg, and runs the real
script. Skipped anywhere that is not macOS, which includes CI."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from relay import updater as up  # noqa: E402


def make_dmg(source_dir: Path, dmg: Path, attempts: int = 4) -> Path:
    """hdiutil create, retried.

    It talks to diskarbitration rather than just writing a file, and under a
    run of back-to-back creates and attaches it intermittently returns 1 with
    nothing useful on stderr. Retrying is the documented shape of working with
    it; a test suite that fails one run in eight teaches people to re-run
    rather than to read failures.
    """
    last = ""
    for attempt in range(attempts):
        result = subprocess.run(
            ["hdiutil", "create", "-quiet", "-srcfolder", str(source_dir),
             "-volname", "Campus Captions", "-format", "UDZO", str(dmg)],
            capture_output=True, text=True, timeout=180)
        if result.returncode == 0:
            return dmg
        last = (result.stderr or result.stdout or "").strip()
        dmg.unlink(missing_ok=True)
        time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"hdiutil create failed {attempts} times: {last}")


def make_app(where: Path, name: str = "Campus Captions.app",
             version: str = "99.0.0-beta", sign: bool = True) -> Path:
    """A minimal but genuinely signable bundle."""
    app = where / name
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True, exist_ok=True)
    binary = macos / "Campus Captions"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    with (app / "Contents" / "Info.plist").open("wb") as handle:
        plistlib.dump({
            "CFBundleName": "Campus Captions",
            "CFBundleIdentifier": "edu.faulkner.campus-captions-test",
            "CFBundleExecutable": "Campus Captions",
            "CFBundleShortVersionString": version,
            "CFBundlePackageType": "APPL",
        }, handle)
    if sign:
        subprocess.run(["codesign", "--force", "--sign", "-", str(app)],
                       check=True, capture_output=True)
    return app


@unittest.skipUnless(sys.platform == "darwin", "macOS only")
class DiskImageTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def make_dmg(self, source_dir: Path, name: str = "cc.dmg") -> Path:
        return make_dmg(source_dir, self.root / name)

    def test_a_signed_app_is_copied_out_of_the_image(self):
        staging_src = self.root / "src"
        staging_src.mkdir()
        make_app(staging_src)
        dmg = self.make_dmg(staging_src)

        staged = up.app_from_disk_image(dmg, self.root / "staged", None)
        self.assertTrue(staged.is_dir())
        self.assertEqual("Campus Captions.app", staged.name)
        self.assertTrue((staged / "Contents" / "MacOS" / "Campus Captions").exists())
        self.assertEqual("99.0.0-beta", up.bundle_version(staged))

    def test_the_image_is_detached_afterwards(self):
        """A mount left behind after every check would pile up all service."""
        staging_src = self.root / "src"
        staging_src.mkdir()
        make_app(staging_src)
        before = subprocess.run(["hdiutil", "info"], capture_output=True,
                                text=True).stdout.count("/dev/disk")
        up.app_from_disk_image(self.make_dmg(staging_src), self.root / "s", None)
        after = subprocess.run(["hdiutil", "info"], capture_output=True,
                               text=True).stdout.count("/dev/disk")
        self.assertEqual(before, after)

    def test_an_image_with_no_app_is_refused(self):
        empty = self.root / "empty"
        empty.mkdir()
        (empty / "readme.txt").write_text("nothing here")
        with self.assertRaises(up.UpdateError) as caught:
            up.app_from_disk_image(self.make_dmg(empty), self.root / "s", None)
        self.assertIn("no application", str(caught.exception))

    def test_a_broken_signature_is_refused(self):
        """Tampering after signing is what this catches - the bundle mounts
        fine and only codesign notices."""
        staging_src = self.root / "src"
        staging_src.mkdir()
        app = make_app(staging_src)
        (app / "Contents" / "MacOS" / "Campus Captions").write_text(
            "#!/bin/sh\necho tampered\n")
        with self.assertRaises(up.UpdateError) as caught:
            up.app_from_disk_image(self.make_dmg(staging_src),
                                   self.root / "s", None)
        self.assertIn("not validly signed", str(caught.exception))

    def test_the_wrong_team_is_refused(self):
        """Ad-hoc today, so nothing matches - which is the point: once a
        Developer ID certificate exists, a build signed by anyone else stops
        here instead of being installed."""
        staging_src = self.root / "src"
        staging_src.mkdir()
        make_app(staging_src)
        with self.assertRaises(up.UpdateError) as caught:
            up.app_from_disk_image(self.make_dmg(staging_src),
                                   self.root / "s", expect_team="ABCDE12345")
        self.assertIn("ABCDE12345", str(caught.exception))


@unittest.skipUnless(sys.platform == "darwin", "macOS only")
class SwapScriptTests(unittest.TestCase):
    """The script that outlives the app and replaces it. The failure to design
    against is a tester left with no application at all, so the rollback path
    matters as much as the success path."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.script = self.root / "swap.sh"
        self.script.write_text(up.SWAP_SCRIPT)
        self.script.chmod(0o755)
        # `open` would really launch something on this machine. Shadow it, so
        # the script under test is the real one but the relaunch is observable
        # instead of disruptive.
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.opened = self.root / "opened.txt"
        fake = self.bin / "open"
        fake.write_text(f'#!/bin/sh\necho "$@" >> "{self.opened}"\n')
        fake.chmod(0o755)
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}")

    def tearDown(self):
        self.dir.cleanup()

    def run_swap(self, target: Path, staged: Path, pid: int) -> int:
        return subprocess.run(
            [str(self.script), str(target), str(staged), str(pid)],
            env=self.env, capture_output=True, timeout=90).returncode

    def dead_pid(self) -> int:
        proc = subprocess.Popen(["/bin/sh", "-c", "exit 0"])
        proc.wait()
        return proc.pid

    def test_the_new_app_replaces_the_old_one_and_is_reopened(self):
        target = make_app(self.root, "Installed.app", version="1.0.0")
        staged = make_app(self.root / "staging", "Installed.app",
                          version="99.0.0-beta")
        self.assertEqual(0, self.run_swap(target, staged, self.dead_pid()))
        self.assertEqual("99.0.0-beta", up.bundle_version(target))
        self.assertIn("Installed.app", self.opened.read_text())

    def test_the_staged_copy_and_the_backup_are_cleaned_up(self):
        target = make_app(self.root, "Installed.app", version="1.0.0")
        staged = make_app(self.root / "staging", "Installed.app")
        self.run_swap(target, staged, self.dead_pid())
        self.assertFalse(staged.exists())
        self.assertFalse(Path(f"{target}.replaced").exists())

    def test_a_failed_copy_puts_the_old_app_back(self):
        """The rollback. If ditto cannot write the new bundle, the tester must
        still have the one that was working ten seconds ago."""
        target = make_app(self.root, "Installed.app", version="1.0.0")
        missing = self.root / "staging" / "NeverBuilt.app"
        self.assertNotEqual(0, self.run_swap(target, missing, self.dead_pid()))
        self.assertTrue(target.is_dir())
        self.assertEqual("1.0.0", up.bundle_version(target))
        self.assertIn("Installed.app", self.opened.read_text())

    def test_it_waits_for_the_app_to_exit_before_touching_anything(self):
        """Copying over a bundle whose process is still running is how you get
        a half-replaced app."""
        target = make_app(self.root, "Installed.app", version="1.0.0")
        staged = make_app(self.root / "staging", "Installed.app",
                          version="99.0.0-beta")
        holder = subprocess.Popen(["/bin/sh", "-c", "sleep 2"])
        swap = subprocess.Popen(
            [str(self.script), str(target), str(staged), str(holder.pid)],
            env=self.env)
        time.sleep(1.0)
        self.assertEqual("1.0.0", up.bundle_version(target),
                         "swapped while the app was still running")
        holder.wait()
        swap.wait(timeout=60)
        self.assertEqual("99.0.0-beta", up.bundle_version(target))


if __name__ == "__main__":
    unittest.main()
