import os
import shutil
import tempfile
import unittest
from pathlib import Path

from motionwall import library


class LibraryTests(unittest.TestCase):
    def test_add_remove_and_ordering(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "library.json"
            self.assertEqual(library.load(path), [])
            library.add("/a.mp4", path)
            library.add("/b.mp4", path)
            self.assertEqual(library.load(path), ["/b.mp4", "/a.mp4"])
            library.add("/a.mp4", path)  # re-adding moves to front, no duplicate
            self.assertEqual(library.load(path), ["/a.mp4", "/b.mp4"])
            library.remove("/b.mp4", path)
            self.assertEqual(library.load(path), ["/a.mp4"])

    def test_corrupt_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "library.json"
            path.write_text("[1, 2, {")
            self.assertEqual(library.load(path), [])
            path.write_text('{"not": "a list"}')
            self.assertEqual(library.load(path), [])

    def test_thumb_path_changes_with_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = Path(tmp) / "v.mp4"
            video.write_bytes(b"abc")
            first = library.thumb_path(str(video), Path(tmp))
            self.assertTrue(first.name.endswith(".jpg"))
            video.write_bytes(b"abcdef")
            os.utime(video, (1, 1))
            self.assertNotEqual(first, library.thumb_path(str(video), Path(tmp)))

    def test_is_video(self):
        self.assertTrue(library.is_video("/x/Clip.MP4"))
        self.assertTrue(library.is_video("a.webm"))
        self.assertFalse(library.is_video("a.png"))

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
    def test_make_thumbnail_real(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = os.path.join(tmp, "t.mp4")
            import subprocess
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                            "testsrc=size=64x36:rate=10", "-t", "0.5", "-pix_fmt", "yuv420p", video], check=True)
            dest = Path(tmp) / "thumb.jpg"
            self.assertTrue(library.make_thumbnail(video, dest, width=32))
            self.assertGreater(dest.stat().st_size, 100)


if __name__ == "__main__":
    unittest.main()
