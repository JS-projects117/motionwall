import os
import tempfile
import unittest
from unittest import mock

from motionwall import control
from motionwall.config import Config
from test_engine import FakeMpvServer  # tests/ is on sys.path under unittest discover


class ApplyAppearanceTests(unittest.TestCase):
    def test_pushes_properties_to_every_extension_player(self):
        with tempfile.TemporaryDirectory() as tmp:
            servers = [FakeMpvServer(os.path.join(tmp, f"motionwall-ext-mpv-{i}.sock")) for i in range(2)]
            cfg = Config(brightness=12, hue=-40, zoom=100, rotate=270)
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmp}), \
                    mock.patch.object(control, "_bus", return_value=None):
                self.assertEqual(control.extension_sockets(),
                                 [os.path.join(tmp, f"motionwall-ext-mpv-{i}.sock") for i in range(2)])
                self.assertEqual(control.apply_appearance(cfg), 2)
            for srv in servers:
                self.assertEqual(srv.props["brightness"], 12)
                self.assertEqual(srv.props["hue"], -40)
                self.assertEqual(srv.props["video-zoom"], 1.0)
                self.assertEqual(srv.props["video-rotate"], 270)

    def test_no_players_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmp}), \
                    mock.patch.object(control, "_bus", return_value=None):
                self.assertEqual(control.apply_appearance(Config()), 0)


if __name__ == "__main__":
    unittest.main()
