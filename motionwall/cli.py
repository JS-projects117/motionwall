"""Command line entry point."""

import argparse
import os
import sys
from typing import List, Optional

from . import __version__

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="motionwall", description="Animated video wallpaper for Ubuntu / GNOME")
    p.add_argument("--version", action="version", version=f"motionwall {__version__}")
    sub = p.add_subparsers(dest="command")
    g = sub.add_parser("gui", help="open the settings window (default)")
    g.add_argument("files", nargs="*", metavar="VIDEO", help="video file(s) to add and use")
    sub.add_parser("daemon", help="run the wallpaper service in the foreground")
    s = sub.add_parser("set", help="use a video file as wallpaper")
    s.add_argument("file")
    sub.add_parser("pause", help="pause the animation")
    sub.add_parser("resume", help="resume the animation")
    sub.add_parser("toggle", help="toggle play/pause")
    sub.add_parser("stop", help="remove the animated wallpaper")
    st = sub.add_parser("status", help="show what is happening")
    st.add_argument("--json", action="store_true", help="print raw JSON")
    sub.add_parser("quit-daemon", help="stop the background service")
    return p


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    argv = list(sys.argv[1:] if argv is None else argv)
    # `motionwall FILE.mp4` (e.g. "Open with Motionwall" from the file manager) == `motionwall gui FILE.mp4`
    if argv and argv[0] not in ("gui", "daemon", "set", "pause", "resume", "toggle", "stop", "status", "quit-daemon") \
            and not argv[0].startswith("-"):
        argv.insert(0, "gui")
    args = build_parser().parse_args(argv)
    if not args.command:
        args.command = "gui"
    return args


def format_status(status: Optional[dict]) -> str:
    if status is None:
        return "Motionwall daemon is not running."
    lines = [f"State:     {status['state']}"]
    if status.get("pause_reason_label"):
        lines[-1] += f" ({status['pause_reason_label']})"
    lines.append(f"Video:     {status['video'] or '(none)'}")
    if status.get("error"):
        lines.append(f"Error:     {status['error']}")
    for p in status.get("players", []):
        if p.get("unloaded"):
            lines.append(f"Monitor:   {p['monitor']}  released (paused, decoder and GPU memory freed)")
            continue
        hw = p.get("hwdec-current") or "software"
        fps = p.get("estimated-vf-fps")
        size = f"{p.get('video-params/w')}x{p.get('video-params/h')}" if p.get("video-params/w") else "?"
        lines.append(f"Monitor:   {p['monitor']}  decode={hw}  {size}  fps={fps if fps is None else round(fps, 1)}"
                     f"  dropped={p.get('frame-drop-count')}  restarts={p.get('restarts')}")
        if p.get("alive") and p.get("hwdec-current") == "no" and p.get("path"):
            lines.append("           software decoding: GPU driver missing or GPU memory full - see ~/.cache/motionwall/")
    if not status.get("players"):
        mons = ", ".join(f"{m['name']} {m['geometry']}" for m in status.get("monitors", []))
        lines.append(f"Monitors:  {mons}")
    lines.append(f"Extension: {'present' if status.get('extension_present') else 'not installed'}")
    lines.append(f"mpv:       {status.get('mpv') or 'NOT FOUND'}")
    return "\n".join(lines)


def open_files(files: List[str]) -> int:
    from gi.repository import GLib
    from . import library
    from .client import DaemonClient
    videos = [os.path.abspath(f) for f in files if os.path.isfile(f) and library.is_video(f)]
    if not videos:
        notify("Motionwall", "That is not a video file.")
        return 2
    for video in reversed(videos):
        library.add(video)
    try:
        DaemonClient().call("SetWallpaper", videos[0])
    except (GLib.Error, RuntimeError) as exc:
        notify("Motionwall", f"Could not start the wallpaper: {getattr(exc, 'message', exc)}")
        return 1
    notify("Motionwall", f"{os.path.basename(videos[0])} is now your wallpaper.")
    return 0


def notify(title: str, body: str) -> None:
    """Desktop notification via the session's notification server (best effort)."""
    try:
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                      "org.freedesktop.Notifications", "Notify",
                      GLib.Variant("(susssasa{sv}i)", ("Motionwall", 0, "org.motionwall.Motionwall", title, body, [], {}, 5000)),
                      None, Gio.DBusCallFlags.NONE, 2000, None)
    except Exception:  # noqa: BLE001 - notifications are optional
        print(f"{title}: {body}")


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if args.command == "daemon":
        from .daemon import main as daemon_main
        return daemon_main()
    if args.command == "gui" and getattr(args, "files", None):
        # "Open with Motionwall" from a file manager: set the wallpaper quietly, no window
        return open_files(args.files)
    if args.command == "gui":
        from .ui.app import main as gui_main
        return gui_main()

    from gi.repository import GLib
    from .client import DaemonClient
    client = DaemonClient()
    try:
        if args.command == "set":
            path = os.path.abspath(args.file)
            if not os.path.isfile(path):
                print(f"motionwall: no such file: {path}", file=sys.stderr)
                return 2
            client.call("SetWallpaper", path)
            print(format_status(client.status()))
        elif args.command == "status":
            status = client.status()
            if args.json:
                import json
                print(json.dumps(status, indent=2))
            else:
                print(format_status(status))
        elif args.command == "quit-daemon":
            if client.is_running():
                client.call("Quit", autostart=False)
                print("daemon stopped")
            else:
                print("daemon was not running")
        else:
            method = {"pause": "Pause", "resume": "Resume", "toggle": "Toggle", "stop": "Stop"}[args.command]
            if not client.is_running() and args.command in ("pause", "stop"):
                if args.command == "stop":
                    from . import config
                    cfg = config.load()
                    cfg.enabled = False
                    config.save(cfg)
                print("Motionwall daemon is not running.")
                return 0
            client.call(method)
            print(format_status(client.status()))
    except (GLib.Error, RuntimeError) as exc:
        print(f"motionwall: {getattr(exc, 'message', exc)}", file=sys.stderr)
        return 1
    return 0
