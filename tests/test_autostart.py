import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motionwall import autostart


class AutostartTests(unittest.TestCase):
    def test_enable_disable(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"MOTIONWALL_LAUNCHER": "/opt/mw"}):
            path = Path(tmp) / "autostart" / "motionwall-daemon.desktop"
            autostart.set_enabled(True, path)
            self.assertTrue(autostart.is_enabled(path))
            text = path.read_text()
            self.assertIn("Exec=/opt/mw daemon", text)
            self.assertIn("[Desktop Entry]", text)
            self.assertIn("X-GNOME-Autostart-enabled=true", text)
            autostart.set_enabled(False, path)
            self.assertFalse(autostart.is_enabled(path))
            autostart.set_enabled(False, path)  # idempotent


if __name__ == "__main__":
    unittest.main()
