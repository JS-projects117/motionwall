"""Thin D-Bus client for the daemon, shared by the CLI and the GUI."""

import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, Optional

from gi.repository import Gio, GLib

from . import DAEMON_BUS_NAME, DAEMON_INTERFACE, DAEMON_OBJECT_PATH
from .autostart import launcher_path


class DaemonClient:
    def __init__(self, bus: Optional[Gio.DBusConnection] = None):
        self.bus = bus or Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def is_running(self) -> bool:
        reply = self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                   "NameHasOwner", GLib.Variant("(s)", (DAEMON_BUS_NAME,)),
                                   GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, 1000, None)
        return bool(reply.unpack()[0])

    def ensure_running(self, timeout: float = 10.0) -> bool:
        if self.is_running():
            return True
        # try D-Bus activation first (service file installed by install.sh)
        try:
            self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                               "StartServiceByName", GLib.Variant("(su)", (DAEMON_BUS_NAME, 0)),
                               GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, int(timeout * 1000), None)
        except GLib.Error:
            self._spawn_daemon()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_running():
                return True
            time.sleep(0.1)
        return False

    @staticmethod
    def _spawn_daemon() -> None:
        launcher = launcher_path()
        cmd = launcher.split() + ["daemon"] if launcher.startswith(sys.executable) else [launcher, "daemon"]
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, env=dict(os.environ))

    def call(self, method: str, *args: Any, autostart: bool = True) -> Any:
        if autostart and not self.ensure_running():
            raise RuntimeError("the motionwall daemon could not be started")
        variant = None
        if args:
            variant = GLib.Variant("(s)", args)
        reply = self.bus.call_sync(DAEMON_BUS_NAME, DAEMON_OBJECT_PATH, DAEMON_INTERFACE, method, variant,
                                   None, Gio.DBusCallFlags.NONE, 15000, None)
        return reply.unpack()[0] if reply and reply.n_children() else None

    def status(self, autostart: bool = False) -> Optional[Dict[str, Any]]:
        if not autostart and not self.is_running():
            return None
        return json.loads(self.call("GetStatus", autostart=autostart))

    def subscribe(self, callback) -> int:
        return self.bus.signal_subscribe(DAEMON_BUS_NAME, DAEMON_INTERFACE, "StatusChanged", DAEMON_OBJECT_PATH,
                                         None, Gio.DBusSignalFlags.NONE,
                                         lambda *a: callback(a[5].unpack()[0]))
