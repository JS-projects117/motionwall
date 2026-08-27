"""Command line entry point."""

import argparse
import os
import sys
from typing import List, Optional

from . import __version__

COMMANDS = ("gui", "daemon", "set", "pause", "resume", "toggle", "stop", "status", "quit-daemon")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="motionwall", description="Animated video wallpaper for Ubuntu / GNOME")
    p.add_argument("--version", action="version", version=f"motionwall {__version__}")
    sub = p.add_subparsers(dest="command")
    sub.add_parser("gui", help="open the settings window (default)")
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


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if args.command == "daemon":
        from .daemon import main as daemon_main
        return daemon_main()
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
