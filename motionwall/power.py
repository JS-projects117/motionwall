"""Session monitors that feed the playback policy (all optional, all tolerant).

  * screen lock      org.gnome.ScreenSaver / org.freedesktop.ScreenSaver  ActiveChanged
  * user idle        org.gnome.Mutter.IdleMonitor  AddIdleWatch / AddUserActiveWatch
  * battery          org.freedesktop.UPower  OnBattery
  * occlusion        org.motionwall.Shell (our optional GNOME Shell extension)
"""

import logging
from typing import Callable, Optional

from gi.repository import Gio, GLib

from . import SHELL_BUS_NAME, SHELL_INTERFACE, SHELL_OBJECT_PATH

log = logging.getLogger("motionwall.power")

IDLE_MONITOR = ("org.gnome.Mutter.IdleMonitor", "/org/gnome/Mutter/IdleMonitor/Core", "org.gnome.Mutter.IdleMonitor")
SCREENSAVERS = (("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver"),
                ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver", "org.freedesktop.ScreenSaver"))
UPOWER = ("org.freedesktop.UPower", "/org/freedesktop/UPower", "org.freedesktop.UPower")


class SessionMonitor:
    """Calls on_change(field, value) for 'locked', 'idle', 'on_battery', 'occluded'."""

    def __init__(self, bus: Gio.DBusConnection, on_change: Callable[[str, object], None]):
        self.bus = bus
        self.on_change = on_change
        self.extension_present = False
        self._idle_watch_id: Optional[int] = None
        self._active_watch_id: Optional[int] = None
        self._idle_ms = 0
        self._subs = []
        self._subscribe_screensaver()
        self._subscribe_upower()
        self._subscribe_idle_signals()
        self._subscribe_extension()

    # -- helpers -----------------------------------------------------------
    def _call(self, dest, path, iface, method, params=None, reply_type=None, cb=None):
        def done(conn, res):
            try:
                result = conn.call_finish(res)
            except GLib.Error as exc:
                log.debug("%s.%s unavailable: %s", iface, method, exc.message)
                result = None
            if cb:
                cb(result)
        self.bus.call(dest, path, iface, method, params, reply_type, Gio.DBusCallFlags.NONE, 2000, None, done)

    def _sub(self, sender, iface, member, path, handler):
        self._subs.append(self.bus.signal_subscribe(sender, iface, member, path, None,
                                                    Gio.DBusSignalFlags.NONE, handler))

    # -- screensaver / lock --------------------------------------------------
    def _subscribe_screensaver(self):
        for dest, path, iface in SCREENSAVERS:
            self._sub(dest, iface, "ActiveChanged", path,
                      lambda *a: self.on_change("locked", bool(a[5].unpack()[0])))
        dest, path, iface = SCREENSAVERS[0]
        self._call(dest, path, iface, "GetActive", None, GLib.VariantType("(b)"),
                   lambda r: r is not None and self.on_change("locked", bool(r.unpack()[0])))

    # -- battery ------------------------------------------------------------
    def _subscribe_upower(self):
        dest, path, iface = UPOWER

        def props_changed(conn, sender, opath, piface, signal, params):
            changed = params.unpack()[1]
            if "OnBattery" in changed:
                self.on_change("on_battery", bool(changed["OnBattery"]))
        self._sub(dest, "org.freedesktop.DBus.Properties", "PropertiesChanged", path, props_changed)
        self._call(dest, path, "org.freedesktop.DBus.Properties", "Get",
                   GLib.Variant("(ss)", (iface, "OnBattery")), GLib.VariantType("(v)"),
                   lambda r: r is not None and self.on_change("on_battery", bool(r.unpack()[0])))

    # -- idle ---------------------------------------------------------------
    def _subscribe_idle_signals(self):
        dest, path, iface = IDLE_MONITOR

        def fired(conn, sender, opath, siface, signal, params):
            watch_id = params.unpack()[0]
            if watch_id == self._idle_watch_id:
                self.on_change("idle", True)
                self._call(dest, path, iface, "AddUserActiveWatch", None, GLib.VariantType("(u)"),
                           self._store_active_watch)
            elif watch_id == self._active_watch_id:
                self._active_watch_id = None
                self.on_change("idle", False)
        self._sub(dest, iface, "WatchFired", path, fired)

    def _store_active_watch(self, result):
        self._active_watch_id = result.unpack()[0] if result is not None else None

    def set_idle_timeout(self, minutes: int, enabled: bool = True) -> None:
        dest, path, iface = IDLE_MONITOR
        wanted = int(minutes * 60 * 1000) if enabled else 0
        if wanted == self._idle_ms:
            return
        self._idle_ms = wanted
        for wid in (self._idle_watch_id, self._active_watch_id):
            if wid is not None:
                self._call(dest, path, iface, "RemoveWatch", GLib.Variant("(u)", (wid,)))
        self._idle_watch_id = self._active_watch_id = None
        self.on_change("idle", False)
        if wanted:
            def store(result):
                self._idle_watch_id = result.unpack()[0] if result is not None else None
                if self._idle_watch_id is not None:
                    log.info("idle watch %s armed for %d min", self._idle_watch_id, minutes)
            self._call(dest, path, iface, "AddIdleWatch", GLib.Variant("(t)", (wanted,)), GLib.VariantType("(u)"), store)

    # -- shell extension (occlusion) ---------------------------------------
    def _subscribe_extension(self):
        self._sub(SHELL_BUS_NAME, SHELL_INTERFACE, "OccludedChanged", SHELL_OBJECT_PATH,
                  lambda *a: self._extension_value(bool(a[5].unpack()[0])))
        Gio.bus_watch_name_on_connection(self.bus, SHELL_BUS_NAME, Gio.BusNameWatcherFlags.NONE,
                                         lambda *_: self.probe_extension(), lambda *_: self._extension_lost())
        GLib.timeout_add_seconds(30, self._periodic_probe)

    def _periodic_probe(self):
        if not self.extension_present:
            self.probe_extension()
        return True

    def probe_extension(self):
        def got(result):
            if result is None:
                if self.extension_present:
                    self._extension_lost()
                return
            self._extension_value(bool(result.unpack()[0]))
        self._call(SHELL_BUS_NAME, SHELL_OBJECT_PATH, "org.freedesktop.DBus.Properties", "Get",
                   GLib.Variant("(ss)", (SHELL_INTERFACE, "Occluded")), GLib.VariantType("(v)"), got)

    def _extension_value(self, occluded: bool):
        if not self.extension_present:
            self.extension_present = True
            log.info("motionwall shell extension detected")
        self.on_change("occluded", occluded)

    def _extension_lost(self):
        if self.extension_present:
            log.info("motionwall shell extension gone")
        self.extension_present = False
        self.on_change("occluded", False)

    def close(self):
        for sid in self._subs:
            self.bus.signal_unsubscribe(sid)
        self._subs = []
