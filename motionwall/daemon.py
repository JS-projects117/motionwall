"""Background service: owns the desktop windows and mpv players, exposes D-Bus API."""

import json
import logging
import os
import signal
import sys
from typing import Optional

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import DAEMON_BUS_NAME, DAEMON_INTERFACE, DAEMON_OBJECT_PATH, __version__  # noqa: E402
from . import autostart, config, policy, runtime  # noqa: E402
from .dbus_xml import DAEMON_XML  # noqa: E402
from .engine import Engine  # noqa: E402
from .power import SessionMonitor  # noqa: E402
from .xdesktop import DesktopLayer  # noqa: E402

log = logging.getLogger("motionwall.daemon")

CHECK_INTERVAL_S = 3
HWDEC_RETRY_INTERVAL_S = 60
SCREEN_CHANGE_DEBOUNCE_MS = 500
RELEASE_REASONS = ("idle", "locked")   # long pauses: unload the file to free decoder/GPU memory


class Daemon:
    def __init__(self):
        self.cfg = config.load()
        self.loop = GLib.MainLoop()
        self.state = policy.PolicyState()
        self.layer: Optional[DesktopLayer] = None
        self.engine = Engine(self.cfg)
        self.windows = []
        self.monitor: Optional[SessionMonitor] = None
        self.bus: Optional[Gio.DBusConnection] = None
        self._reg_id = 0
        self._last_state = None
        self._error: Optional[str] = None
        self._ticks = 0
        self._screen_change_source = 0

    # -- startup -----------------------------------------------------------
    def run(self) -> int:
        if not os.environ.get("DISPLAY"):
            log.error("DISPLAY is not set; Motionwall needs X11 or Xwayland (GNOME provides it)")
            return 1
        try:
            self.layer = DesktopLayer(on_screen_change=self._on_screen_change)
        except Exception as exc:  # noqa: BLE001 - any X failure is fatal here
            log.error("cannot open X display: %s", exc)
            return 1
        if not self.engine.available:
            self._error = runtime.mpv_missing_message()
            log.error(self._error)
        GLib.io_add_watch(self.layer.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN, self._on_x_event)
        GLib.timeout_add_seconds(CHECK_INTERVAL_S, self._tick)
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            GLib.unix_signal_add(GLib.PRIORITY_HIGH, sig, self._on_signal, sig)
        Gio.bus_own_name(Gio.BusType.SESSION, DAEMON_BUS_NAME, Gio.BusNameOwnerFlags.NONE,
                         self._on_bus_acquired, self._on_name_acquired, self._on_name_lost)
        self.loop.run()
        self._shutdown()
        return 0

    def _on_bus_acquired(self, conn, name):
        self.bus = conn
        info = Gio.DBusNodeInfo.new_for_xml(DAEMON_XML)
        self._reg_id = conn.register_object(DAEMON_OBJECT_PATH, info.interfaces[0],
                                            self._on_method_call, self._on_get_property, None)
        self.monitor = SessionMonitor(conn, self._on_session_change)
        self.monitor.set_idle_timeout(self.cfg.idle_minutes, self.cfg.pause_on_idle)

    def _on_name_acquired(self, conn, name):
        log.info("motionwall daemon %s ready (pid %d)", __version__, os.getpid())
        if self.cfg.video and self.cfg.enabled:
            self._start_playback(self.cfg.video)
        self._apply_policy()

    def _on_name_lost(self, conn, name):
        log.error("could not own %s (another daemon running?)", name)
        self.loop.quit()

    # -- D-Bus ---------------------------------------------------------------
    def _on_method_call(self, conn, sender, path, iface, method, params, invocation):
        try:
            handler = getattr(self, f"dbus_{method}")
            result = handler(*params.unpack())
        except Exception as exc:  # noqa: BLE001
            log.exception("%s failed", method)
            invocation.return_dbus_error("org.motionwall.Error", str(exc))
            return
        if method == "GetStatus":
            invocation.return_value(GLib.Variant("(s)", (result,)))
        else:
            invocation.return_value(None)

    def _on_get_property(self, conn, sender, path, iface, prop):
        if prop == "Version":
            return GLib.Variant("s", __version__)
        return None

    def dbus_SetWallpaper(self, path: str):
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isfile(path):
            raise FileNotFoundError(f"no such file: {path}")
        self.cfg.video = path
        self.cfg.enabled = True
        config.save(self.cfg)
        self.state.user_play = True
        if self.engine.players and self.windows:
            self.engine.load(path)
        else:
            self._start_playback(path)
        self._apply_policy()

    def dbus_Pause(self):
        self.state.user_play = False
        self._apply_policy()

    def dbus_Resume(self):
        self.state.user_play = True
        self._ensure_enabled()
        self._apply_policy()

    def dbus_Toggle(self):
        if not self.windows and self.cfg.video:
            self.state.user_play = True
            self._ensure_enabled()
        else:
            self.state.user_play = not self.state.user_play
        self._apply_policy()

    def _ensure_enabled(self):
        """Resume after `stop`: the video is remembered, only the enabled flag was cleared."""
        if not self.cfg.enabled:
            self.cfg.enabled = True
            config.save(self.cfg)
        if self.cfg.video and not self.windows:
            self._start_playback(self.cfg.video)

    def dbus_Stop(self):
        self._stop_playback()
        self.cfg.enabled = False
        config.save(self.cfg)
        self._apply_policy()

    def dbus_Quit(self):
        GLib.idle_add(self.loop.quit)

    def dbus_ReloadConfig(self):
        old = self.cfg
        self.cfg = config.load()
        if self.monitor:
            self.monitor.set_idle_timeout(self.cfg.idle_minutes, self.cfg.pause_on_idle)
        if old.autostart != self.cfg.autostart:
            autostart.set_enabled(self.cfg.autostart)
        wanted = bool(self.cfg.video and self.cfg.enabled)
        if self.windows and wanted:
            if self.engine.needs_restart(old, self.cfg) or old.video != self.cfg.video:
                self._start_playback(self.cfg.video)
            else:
                self.engine.apply_config(self.cfg)
        elif wanted and not self.windows:
            self._start_playback(self.cfg.video)
        elif not wanted and self.windows:
            self._stop_playback()
        self._apply_policy()

    def dbus_GetStatus(self) -> str:
        return json.dumps(self.status())

    # -- playback ------------------------------------------------------------
    def _start_playback(self, video: str):
        self.engine.cfg = self.cfg
        if not self.engine.available:
            self._error = runtime.mpv_missing_message()
            return
        self._error = None
        self.engine.stop()                       # never leave mpv drawing into a destroyed window
        self.windows = self.layer.create_windows(self.cfg.monitors)
        self.engine.start(self.windows, video)
        self.engine.set_playback(self._playback_mode())

    def _stop_playback(self):
        self.engine.stop()
        if self.layer:
            self.layer.destroy_windows()
        self.windows = []

    def _on_screen_change(self):
        # RandR emits several events per hot-plug; coalesce them into one rebuild
        if self._screen_change_source:
            GLib.source_remove(self._screen_change_source)
        self._screen_change_source = GLib.timeout_add(SCREEN_CHANGE_DEBOUNCE_MS, self._rebuild_after_screen_change)

    def _rebuild_after_screen_change(self):
        self._screen_change_source = 0
        if self.windows and self.cfg.video:
            log.info("monitor layout changed; rebuilding wallpaper windows")
            self._start_playback(self.cfg.video)
        return False

    def _playback_mode(self) -> str:
        play, reason = policy.decide(self.state, self.cfg)
        if play:
            return "play"
        return "release" if reason in RELEASE_REASONS else "pause"

    def _on_session_change(self, field: str, value):
        if getattr(self.state, field) != value:
            setattr(self.state, field, value)
            log.info("session: %s=%s", field, value)
            self._apply_policy()

    def _apply_policy(self):
        if self.windows:
            self.engine.set_playback(self._playback_mode())
        state = self.state_name()
        if state != self._last_state:
            self._last_state = state
            if self.bus:
                self.bus.emit_signal(None, DAEMON_OBJECT_PATH, DAEMON_INTERFACE, "StatusChanged",
                                     GLib.Variant("(s)", (state,)))

    def state_name(self) -> str:
        """Cheap state computation (no mpv IPC)."""
        play, _ = policy.decide(self.state, self.cfg)
        if self._error:
            return "error"
        if not self.cfg.video or not self.windows:
            return "stopped"
        if self.engine.any_failed():
            return "error"
        return "playing" if play else "paused"

    def status(self) -> dict:
        play, reason = policy.decide(self.state, self.cfg)
        players = self.engine.stats() if self.windows else []
        state = self.state_name()
        show_reason = not play and state in ("paused", "playing")
        return {
            "state": state,
            "enabled": self.cfg.enabled,
            "video": self.cfg.video,
            "pause_reason": reason if show_reason else None,
            "pause_reason_label": policy.REASON_LABELS.get(reason, reason) if show_reason else None,
            "error": self._error,
            "policy": vars(self.state),
            "extension_present": bool(self.monitor and self.monitor.extension_present),
            "monitors": [{"name": w.monitor.name, "geometry": w.monitor.geometry, "xid": w.xid} for w in self.windows]
                        if self.windows else [{"name": m.name, "geometry": m.geometry} for m in self.layer.monitors()],
            "players": players,
            "mpv": self.engine.mpv,
            "pid": os.getpid(),
            "version": __version__,
        }

    # -- housekeeping ---------------------------------------------------------
    def _on_x_event(self, fd, cond):
        try:
            self.layer.process_events()
        except Exception:  # noqa: BLE001
            log.exception("X event handling failed")
        return True

    def _tick(self):
        self._ticks += 1
        if self._error and not self.engine.available:
            self.engine.mpv = runtime.find_mpv()      # mpv may have been installed meanwhile
            if self.engine.available:
                self._error = None
                if self.cfg.video and self.cfg.enabled:
                    self._start_playback(self.cfg.video)
                self._apply_policy()
        if self.windows:
            self.engine.check()
            if self.engine.any_failed():
                self._apply_policy()
            if self._ticks % (HWDEC_RETRY_INTERVAL_S // CHECK_INTERVAL_S) == 0 and not self.engine.paused:
                self.engine.retry_hwdec()
        return True

    def _on_signal(self, sig):
        log.info("signal %s, shutting down", sig)
        self.loop.quit()
        return False

    def _shutdown(self):
        if self._screen_change_source:
            GLib.source_remove(self._screen_change_source)
        if self.monitor:
            self.monitor.close()
        self.engine.stop()
        if self.layer:
            self.layer.close()
        if self.bus and self._reg_id:
            self.bus.unregister_object(self._reg_id)


def main() -> int:
    logging.basicConfig(level=os.environ.get("MOTIONWALL_LOG", "INFO"),
                        format="%(asctime)s %(name)s %(levelname)s: %(message)s", stream=sys.stderr)
    return Daemon().run()


if __name__ == "__main__":
    sys.exit(main())
