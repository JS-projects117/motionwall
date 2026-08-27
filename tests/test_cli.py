import unittest

from motionwall import cli


class CliTests(unittest.TestCase):
    def test_default_is_gui(self):
        self.assertEqual(cli.parse_args([]).command, "gui")

    def test_set_requires_file(self):
        args = cli.parse_args(["set", "/tmp/x.mp4"])
        self.assertEqual((args.command, args.file), ("set", "/tmp/x.mp4"))
        with self.assertRaises(SystemExit):
            cli.parse_args(["set"])

    def test_bare_file_argument_opens_gui(self):
        args = cli.parse_args(["/home/me/clip.mp4"])
        self.assertEqual((args.command, args.files), ("gui", ["/home/me/clip.mp4"]))
        args = cli.parse_args(["gui", "/a.mp4", "/b.mp4"])
        self.assertEqual(args.files, ["/a.mp4", "/b.mp4"])
        self.assertEqual(cli.parse_args(["gui"]).files, [])

    def test_all_commands_parse(self):
        for cmd in ("daemon", "pause", "resume", "toggle", "stop", "status", "quit-daemon", "gui"):
            self.assertEqual(cli.parse_args([cmd]).command, cmd)
        self.assertTrue(cli.parse_args(["status", "--json"]).json)

    def test_format_status(self):
        self.assertIn("not running", cli.format_status(None))
        text = cli.format_status({"state": "paused", "pause_reason_label": "screen locked", "video": "/v.mp4",
                                  "players": [{"monitor": "HDMI-1", "hwdec-current": "nvdec", "estimated-vf-fps": 29.97,
                                               "video-params/w": 1920, "video-params/h": 1080,
                                               "frame-drop-count": 0, "restarts": 0}],
                                  "extension_present": False, "mpv": "/usr/bin/mpv"})
        self.assertIn("paused (screen locked)", text)
        self.assertIn("decode=nvdec", text)
        self.assertIn("1920x1080", text)
        self.assertIn("fps=30.0", text)


if __name__ == "__main__":
    unittest.main()
