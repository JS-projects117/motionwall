"""Single control plane shared by the CLI and GUI.

The configuration file is the source of truth. State changes edit it and then
best-effort notify whichever renderer is active:

* the GNOME Shell extension (org.motionwall.Shell on the org.gnome.Shell bus),
  which is the renderer on Wayland; or
* the standalone daemon (org.motionwall.Daemon), used on X11/Xorg sessions.

This lets `motionwall set ...`, the "Open with" launcher and the GUI all work
whether the wallpaper is drawn by the extension or the daemon.
"""

import glob
import os
import tempfile
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


def notify(restart: bool = False) -> None:
    """Tell the active renderer the config changed.

    The extension re-reads the file and applies what it can live; restart=True
    forces it to respawn its players (the GUI's "Reload Wallpaper" action).
    """
    bus = _bus()
    if not bus:
        return
    if _name_has_owner(bus, SHELL_BUS_NAME):
        for method in (("Restart", "Reload") if restart else ("Reload",)):
            try:
                bus.call_sync(SHELL_BUS_NAME, SHELL_OBJECT_PATH, SHELL_INTERFACE, method, None, None,
                              Gio.DBusCallFlags.NONE, 2000, None)
                break
            except GLib.Error:
                continue  # older extension without Restart, or not loaded: the file monitor catches it
    if daemon_present(bus):
        try:
            bus.call_sync(DAEMON_BUS_NAME, DAEMON_OBJECT_PATH, DAEMON_INTERFACE, "ReloadConfig", None, None,
                          Gio.DBusCallFlags.NONE, 5000, None)
        except GLib.Error:
            pass


def extension_sockets() -> list:
    """IPC sockets of the mpv players the GNOME Shell extension is running."""
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return sorted(glob.glob(os.path.join(base, "motionwall-ext-mpv-*.sock")))


def apply_appearance(cfg: Optional[config_mod.Config] = None) -> int:
    """Push the appearance settings straight into the running players.

    The config file stays the source of truth (the renderer applies it on every
    start), but a slider should move the wallpaper instantly, so the settings
    are also set over mpv's IPC here. Returns the number of players updated.
    """
    from .engine import MpvIpc, appearance_properties
    cfg = cfg or config_mod.load()
    props = appearance_properties(cfg)
    updated = 0
    for path in extension_sockets():
        ipc = MpvIpc(path)
        if not ipc.connect(timeout=0.3):
            continue
        try:
            for name, value in props.items():
                ipc.command("set_property", name, value)
            updated += 1
        except (OSError, RuntimeError, TimeoutError):
            pass
        finally:
            ipc.close()
    bus = _bus()
    if bus and daemon_present(bus):
        try:
            bus.call_sync(DAEMON_BUS_NAME, DAEMON_OBJECT_PATH, DAEMON_INTERFACE, "ReloadConfig", None, None,
                          Gio.DBusCallFlags.NONE, 5000, None)
            updated += 1
        except GLib.Error:
            pass
    return updated


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
