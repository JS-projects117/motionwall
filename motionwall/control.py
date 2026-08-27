"""Single control plane shared by the CLI and GUI.

The configuration file is the source of truth. State changes edit it and then
best-effort notify whichever renderer is active:

* the GNOME Shell extension (org.motionwall.Shell on the org.gnome.Shell bus),
  which is the renderer on Wayland; or
* the standalone daemon (org.motionwall.Daemon), used on X11/Xorg sessions.

This lets `motionwall set ...`, the "Open with" launcher and the GUI all work
whether the wallpaper is drawn by the extension or the daemon.
"""

import os
from typing import Any, Dict, Optional

from gi.repository import Gio, GLib

from . import (DAEMON_BUS_NAME, DAEMON_INTERFACE, DAEMON_OBJECT_PATH,
               SHELL_BUS_NAME, SHELL_INTERFACE, SHELL_OBJECT_PATH)
from . import config as config_mod


def _bus() -> Optional[Gio.DBusConnection]:
    try:
        return Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error:
        return None


def _name_has_owner(bus: Gio.DBusConnection, name: str) -> bool:
    try:
        reply = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                              "NameHasOwner", GLib.Variant("(s)", (name,)), GLib.VariantType("(b)"),
                              Gio.DBusCallFlags.NONE, 1000, None)
        return bool(reply.unpack()[0])
    except GLib.Error:
        return False


def extension_present(bus: Optional[Gio.DBusConnection] = None) -> bool:
    bus = bus or _bus()
    if not bus:
        return False
    try:
        bus.call_sync(SHELL_BUS_NAME, SHELL_OBJECT_PATH, "org.freedesktop.DBus.Properties", "Get",
                      GLib.Variant("(ss)", (SHELL_INTERFACE, "Playing")), GLib.VariantType("(v)"),
                      Gio.DBusCallFlags.NONE, 1000, None)
        return True
    except GLib.Error:
        return False


def daemon_present(bus: Optional[Gio.DBusConnection] = None) -> bool:
    bus = bus or _bus()
    return bool(bus and _name_has_owner(bus, DAEMON_BUS_NAME))


def apply_state(**changes: Any) -> config_mod.Config:
    """Merge changes into the config on disk, then notify the active renderer."""
    cfg = config_mod.load()
    for key, value in changes.items():
        setattr(cfg, key, value)
    config_mod.save(cfg)
    notify()
    return cfg


def notify() -> None:
    bus = _bus()
    if not bus:
        return
    if _name_has_owner(bus, SHELL_BUS_NAME):
        try:
            bus.call_sync(SHELL_BUS_NAME, SHELL_OBJECT_PATH, SHELL_INTERFACE, "Reload", None, None,
                          Gio.DBusCallFlags.NONE, 2000, None)
        except GLib.Error:
            pass  # extension not loaded; the file monitor will catch the change
    if daemon_present(bus):
        try:
            bus.call_sync(DAEMON_BUS_NAME, DAEMON_OBJECT_PATH, DAEMON_INTERFACE, "ReloadConfig", None, None,
                          Gio.DBusCallFlags.NONE, 5000, None)
        except GLib.Error:
            pass


def status() -> Dict[str, Any]:
    """A backend-agnostic status dict for the CLI and GUI."""
    cfg = config_mod.load()
    bus = _bus()
    ext = extension_present(bus)
    daemon = daemon_present(bus)
    playing = None
    occluded = None
    if ext:
        playing = _get_shell_prop(bus, "Playing")
        occluded = _get_shell_prop(bus, "Occluded")
    if not cfg.video or not cfg.enabled:
        state = "stopped"
    elif cfg.paused:
        state = "paused"
    elif ext:
        state = "playing" if playing else "starting"
    elif daemon:
        state = _daemon_state(bus)
    else:
        state = "waiting"      # configured to play, but no renderer is active yet
    return {
        "state": state,
        "video": cfg.video,
        "enabled": cfg.enabled,
        "paused": cfg.paused,
        "scaling": cfg.scaling,
        "backend": "extension" if ext else ("daemon" if daemon else "none"),
        "occluded": occluded,
        "extension_present": ext,
        "daemon_present": daemon,
    }


def _get_shell_prop(bus: Gio.DBusConnection, prop: str) -> Optional[bool]:
    try:
        reply = bus.call_sync(SHELL_BUS_NAME, SHELL_OBJECT_PATH, "org.freedesktop.DBus.Properties", "Get",
                              GLib.Variant("(ss)", (SHELL_INTERFACE, prop)), GLib.VariantType("(v)"),
                              Gio.DBusCallFlags.NONE, 1500, None)
        return bool(reply.unpack()[0])
    except GLib.Error:
        return None


def _daemon_state(bus: Gio.DBusConnection) -> str:
    try:
        import json
        reply = bus.call_sync(DAEMON_BUS_NAME, DAEMON_OBJECT_PATH, DAEMON_INTERFACE, "GetStatus", None,
                              GLib.VariantType("(s)"), Gio.DBusCallFlags.NONE, 5000, None)
        return json.loads(reply.unpack()[0]).get("state", "unknown")
    except (GLib.Error, ValueError):
        return "unknown"
