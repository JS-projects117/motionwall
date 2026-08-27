"""The user's list of wallpaper videos (JSON) and ffmpeg thumbnail generation."""

import hashlib
import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Callable, List, Optional

from .paths import library_file, thumbs_dir

VIDEO_EXTENSIONS = (".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".gif", ".wmv", ".ts", ".mpg", ".mpeg")
VIDEO_MIME_TYPES = ("video/mp4", "video/x-matroska", "video/webm", "video/quicktime", "video/x-msvideo",
                    "video/mpeg", "video/x-m4v", "image/gif", "video/mp2t", "video/x-ms-wmv")


def is_video(path: str) -> bool:
    return path.lower().endswith(VIDEO_EXTENSIONS)


def load(path: Optional[Path] = None) -> List[str]:
    path = path or library_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [p for p in data if isinstance(p, str)] if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save(items: List[str], path: Optional[Path] = None) -> None:
    path = path or library_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, indent=2) + "\n", encoding="utf-8")


def add(video: str, path: Optional[Path] = None) -> List[str]:
    items = load(path)
    video = os.path.abspath(video)
    if video in items:
        items.remove(video)
    items.insert(0, video)  # most recent first
    save(items, path)
    return items


def remove(video: str, path: Optional[Path] = None) -> List[str]:
    items = [i for i in load(path) if i != video]
    save(items, path)
    return items


def thumb_path(video: str, base: Optional[Path] = None) -> Path:
    base = base or thumbs_dir()
    try:
        stamp = f"{os.path.getmtime(video):.0f}:{os.path.getsize(video)}"
    except OSError:
        stamp = "missing"
    digest = hashlib.sha1(f"{os.path.abspath(video)}|{stamp}".encode()).hexdigest()
    return base / f"{digest}.jpg"


def make_thumbnail(video: str, dest: Path, width: int = 480) -> bool:
    """Grab one frame ~1s in with ffmpeg; returns True on success."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", "1", "-i", video,
           "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", str(dest)]
    try:
        subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=30, check=True)
    except (OSError, subprocess.SubprocessError):
        # very short clips: retry from the first frame
        try:
            cmd[cmd.index("-ss") + 1] = "0"
            subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=30, check=True)
        except (OSError, subprocess.SubprocessError):
            return False
    return dest.exists()


def ensure_thumbnail_async(video: str, done: Callable[[str, Optional[str]], None]) -> None:
    """Generate the thumbnail in a worker thread; done(video, path_or_None) runs in that thread."""
    dest = thumb_path(video)
    if dest.exists():
        done(video, str(dest))
        return

    def work():
        ok = make_thumbnail(video, dest)
        done(video, str(dest) if ok else None)
    threading.Thread(target=work, daemon=True, name="motionwall-thumb").start()
