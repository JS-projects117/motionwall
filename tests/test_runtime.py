import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motionwall import runtime


class RuntimeTests(unittest.TestCase):
    def test_override_env_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "mpv"
            fake.write_text("#!/bin/sh\n")
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            with mock.patch.dict(os.environ, {"MOTIONWALL_MPV": str(fake)}):
                self.assertEqual(runtime.find_mpv(), str(fake))

    def test_local_runtime_fallback_and_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            rt = Path(tmp) / "runtime"
            binary = rt / "usr" / "bin" / "mpv"
            binary.parent.mkdir(parents=True)
            binary.write_text("#!/bin/sh\n")
            binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
            env = {"MOTIONWALL_RUNTIME": str(rt), "PATH": tmp, "LD_LIBRARY_PATH": "/opt/x"}
            with mock.patch.dict(os.environ, env, clear=False):
                os.environ.pop("MOTIONWALL_MPV", None)
                found = runtime.find_mpv()
                self.assertEqual(found, str(binary))
                mpv_env = runtime.mpv_env(found)
                self.assertTrue(mpv_env["LD_LIBRARY_PATH"].startswith(str(rt / "usr" / "lib")))
                self.assertTrue(mpv_env["LD_LIBRARY_PATH"].endswith(":/opt/x"))

    def test_system_mpv_does_not_get_ld_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"MOTIONWALL_RUNTIME": tmp}):
                os.environ.pop("LD_LIBRARY_PATH", None)
                env = runtime.mpv_env("/usr/bin/mpv")
                self.assertNotIn("LD_LIBRARY_PATH", env)

    def test_none_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"MOTIONWALL_RUNTIME": tmp, "PATH": tmp}):
                os.environ.pop("MOTIONWALL_MPV", None)
                self.assertIsNone(runtime.find_mpv())


if __name__ == "__main__":
    unittest.main()
