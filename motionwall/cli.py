"""Command line entry point.

State changes go through the config file (`motionwall.control`), so they work
whether the wallpaper is drawn by the GNOME Shell extension (Wayland) or the
standalone daemon (Xorg). The `daemon` subcommand still runs the X11 daemon for
sessions without the extension.
"""

import argparse
import os
import sys
from typing import List, Optional

from . import __version__

STATE_LABELS = {
    "playing": "playing", "starting": "starting…", "paused": "paused",
    "stopped": "stopped", "waiting": "waiting for the renderer", "idle": "no wallpaper set",
    "unknown": "unknown",
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="motionwall", description="Animated video wallpaper for Ubuntu / GNOME")
    p.add_argument("--version", action="version", version=f"motionwall {__version__}")
    sub = p.add_subparsers(dest="command")
    g = sub.add_parser("gui", help="open the settings window (default)")
    g.add_argument("files", nargs="*", metavar="VIDEO", help="video file(s) to add and use")
    sub.add_parser("daemon", help="run the X11 wallpaper service (Xorg sessions only)")
    s = sub.add_parser("set", help="use a video file as wallpaper")
    s.add_argument("file")
    sub.add_parser("pause", help="pause the animation")
    sub.add_parser("resume", help="resume the animation")
    sub.add_parser("toggle", help="toggle play/pause")
    sub.add_parser("stop", help="remove the animated wallpaper")
    st = sub.add_parser("status", help="show what is happening")
    st.add_argument("--json", action="store_true", help="print raw JSON")
    sub.add_parser("quit-daemon", help="stop the X11 background service")
    return p


COMMANDS = ("gui", "daemon", "set", "pause", "resume", "toggle", "stop", "status", "quit-daemon")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    argv = list(sys.argv[1:] if argv is None else argv)
    # `motionwall FILE.mp4` (e.g. "Open with Motionwall" from the file manager) == `motionwall set FILE.mp4`
    if argv and argv[0] not in COMMANDS and not argv[0].startswith("-"):
        argv.insert(0, "set")
    args = build_parser().parse_args(argv)
    if not args.command:
        args.command = "gui"
    return args


def format_status(status: dict) -> str:
    lines = [f"State:     {STATE_LABELS.get(status['state'], status['state'])}"]
    if status["state"] == "paused":
        lines[-1] += " (by you)"
    lines.append(f"Video:     {status['video'] or '(none)'}")
    lines.append(f"Scaling:   {status.get('scaling', 'fill')}")
    if status.get("occluded"):
        lines.append("           paused: a full-screen window is covering the desktop")
    backend = status.get("backend")
    label = {"extension": "GNOME Shell extension (Wayland)", "daemon": "X11 daemon (Xorg)",
             "none": "not running — is the Motionwall extension enabled?"}.get(backend, backend)
    lines.append(f"Renderer:  {label}")
    return "\n".join(lines)


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


def open_files(files: List[str], quiet: bool = True) -> int:
    """Add video(s) to the library and make the first one the wallpaper."""
    from . import control, library
    videos = [os.path.abspath(f) for f in files if os.path.isfile(f) and library.is_video(f)]
    if not videos:
        msg = "That is not a video file." if files else "No video given."
        if quiet:
            notify("Motionwall", msg)
        else:
            print(f"motionwall: {msg}", file=sys.stderr)
        return 2
    for video in reversed(videos):
        library.add(video)
    control.apply_state(video=videos[0], enabled=True, paused=False)
    st = control.status()
    if st["backend"] == "none" and quiet:
        notify("Motionwall", "Saved. Enable the Motionwall extension (log out and back in) to see it.")
    elif quiet:
        notify("Motionwall", f"{os.path.basename(videos[0])} is now your wallpaper.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if args.command == "daemon":
        from .daemon import main as daemon_main
        return daemon_main()
    if args.command == "gui":
        if getattr(args, "files", None):
            return open_files(args.files, quiet=True)
        from .ui.app import main as gui_main
        return gui_main()

    from . import control

    if args.command == "set":
        return open_files([args.file], quiet=False)
    if args.command == "pause":
        control.apply_state(paused=True)
    elif args.command == "resume":
        control.apply_state(paused=False, enabled=True)
    elif args.command == "toggle":
        from . import config
        control.apply_state(paused=not config.load().paused, enabled=True)
    elif args.command == "stop":
        control.apply_state(enabled=False)
    elif args.command == "quit-daemon":
        from .client import DaemonClient
        client = DaemonClient()
        if client.is_running():
            client.call("Quit", autostart=False)
            print("daemon stopped")
        else:
            print("daemon was not running")
        return 0

    status = control.status()
    if args.command == "status" and args.json:
        import json
        print(json.dumps(status, indent=2))
    else:
        print(format_status(status))
    return 0
