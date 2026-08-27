import json
import tempfile
import unittest
from pathlib import Path

from motionwall import config


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = config.Config()
        self.assertEqual(cfg.scaling, "fill")
        self.assertTrue(cfg.mute)
        self.assertEqual(cfg.hwdec, "auto-safe")
        self.assertTrue(cfg.loop)

    def test_roundtrip_preserves_unknown_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"video": "/v.mp4", "scaling": "fit", "future_key": {"a": 1}}))
            cfg = config.load(path)
            self.assertEqual(cfg.video, "/v.mp4")
            self.assertEqual(cfg.scaling, "fit")
            self.assertEqual(cfg.extra, {"future_key": {"a": 1}})
            cfg.mute = False
            config.save(cfg, path)
            data = json.loads(path.read_text())
            self.assertEqual(data["future_key"], {"a": 1})
            self.assertFalse(data["mute"])
            self.assertNotIn("extra", data)

    def test_invalid_values_are_normalised(self):
        cfg = config.Config.from_dict({"scaling": "bogus", "hwdec": "x", "volume": 500, "speed": 99, "idle_minutes": 0})
        self.assertEqual(cfg.scaling, "fill")
        self.assertEqual(cfg.hwdec, "auto-safe")
        self.assertEqual(cfg.volume, 100)
        self.assertEqual(cfg.speed, 4.0)
        self.assertEqual(cfg.idle_minutes, 1)

    def test_type_coercion(self):
        cfg = config.Config.from_dict({"mute": "false", "volume": "42", "speed": "1.5", "loop": 0})
        self.assertFalse(cfg.mute)
        self.assertEqual(cfg.volume, 42)
        self.assertEqual(cfg.speed, 1.5)
        self.assertFalse(cfg.loop)

    def test_missing_or_corrupt_file_gives_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            self.assertEqual(config.load(path), config.Config())
            path.write_text("{not json")
            self.assertEqual(config.load(path), config.Config())


if __name__ == "__main__":
    unittest.main()
