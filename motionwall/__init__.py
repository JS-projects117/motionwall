"""Motionwall - efficient video wallpaper for Ubuntu / GNOME."""

__version__ = "0.1.0"
APP_ID = "org.motionwall.Motionwall"
DAEMON_BUS_NAME = "org.motionwall.Daemon"
DAEMON_OBJECT_PATH = "/org/motionwall/Daemon"
DAEMON_INTERFACE = "org.motionwall.Daemon"
import os as _os
SHELL_BUS_NAME = _os.environ.get("MOTIONWALL_SHELL_BUS", "org.gnome.Shell")
SHELL_OBJECT_PATH = "/org/motionwall/Shell"
SHELL_INTERFACE = "org.motionwall.Shell"
