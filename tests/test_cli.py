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

    def test_bare_file_argument_becomes_set(self):
        args = cli.parse_args(["/home/me/clip.mp4"])
        self.assertEqual((args.command, args.file), ("set", "/home/me/clip.mp4"))

    def test_all_commands_parse(self):
        for cmd in ("daemon", "pause", "resume", "toggle", "stop", "status", "quit-daemon", "gui"):
            self.assertEqual(cli.parse_args([cmd]).command, cmd)
        self.assertTrue(cli.parse_args(["status", "--json"]).json)

    def test_format_status(self):
        text = cli.format_status({"state": "playing", "video": "/v.mp4", "scaling": "fill",
                                  "backend": "extension", "occluded": False})
        self.assertIn("playing", text)
        self.assertIn("/v.mp4", text)
        self.assertIn("extension", text)
        paused = cli.format_status({"state": "paused", "video": "/v.mp4", "scaling": "fit",
                                    "backend": "extension", "occluded": False})
        self.assertIn("paused (by you)", paused)
        none = cli.format_status({"state": "idle", "video": "", "scaling": "fill", "backend": "none",
                                  "occluded": None})
        self.assertIn("extension enabled", none)


if __name__ == "__main__":
    unittest.main()
