"""X11 desktop-layer windows (one per monitor) used as mpv render targets.

Works on native X11 sessions and on GNOME Wayland via Xwayland: mutter honours
_NET_WM_WINDOW_TYPE_DESKTOP for X clients and stacks such windows in the desktop
layer, below every normal window, on all workspaces.

Ubuntu's desktop-icons extension (DING) also lives in the desktop layer and keeps
re-lowering itself, which would hide the icons behind an opaque video. To stay
underneath it we re-lower our windows whenever the root window's stacking-related
properties change (active window, current desktop, client list). Mutter only
honours stacking requests from clients whose _NET_WM_USER_TIME is not older than
the focused window's, so the timestamp is refreshed right before each request.
"""

import logging
import os
import select
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from Xlib import X, Xatom, Xutil, display, error
from Xlib.ext import randr

log = logging.getLogger("motionwall.xdesktop")

WM_CLASS = ("motionwall", "Motionwall")
_ALL_DESKTOPS = 0xFFFFFFFF


@dataclass(frozen=True)
class Monitor:
    name: str
    x: int
    y: int
    width: int
    height: int
    primary: bool = False

    @property
    def geometry(self) -> str:
        return f"{self.width}x{self.height}+{self.x}+{self.y}"


@dataclass
class DesktopWindow:
    monitor: Monitor
    xid: int


class DesktopLayer:
    """Owns the X connection and the per-monitor desktop windows."""

    def __init__(self, on_screen_change: Optional[Callable[[], None]] = None):
        self.display = display.Display()
        self.screen = self.display.screen()
        self.root = self.screen.root
        self.on_screen_change = on_screen_change
        self.windows: List[DesktopWindow] = []
        self._pending_lower = False
        self._pending_screen_change = False
        self._atoms: Dict[str, int] = {}
        self._has_randr = self.display.has_extension("RANDR")
        self._select_root_events()

    # -- atoms -------------------------------------------------------------
    def atom(self, name: str) -> int:
        if name not in self._atoms:
            self._atoms[name] = self.display.intern_atom(name)
        return self._atoms[name]

    # -- monitors ----------------------------------------------------------
    def monitors(self) -> List[Monitor]:
        result: List[Monitor] = []
        if self._has_randr:
            try:
                res = randr.get_monitors(self.root, is_active=True)
                for m in res.monitors:
                    name = self.display.get_atom_name(m.name) if m.name else f"monitor{len(result)}"
                    result.append(Monitor(name, m.x, m.y, m.width_in_pixels, m.height_in_pixels, bool(m.primary)))
            except (error.XError, AttributeError) as exc:  # RandR < 1.5 or odd server
                log.debug("randr.get_monitors failed: %s", exc)
        if not result:
            geo = self.root.get_geometry()
            result.append(Monitor("screen", 0, 0, geo.width, geo.height, True))
        result.sort(key=lambda m: (not m.primary, m.x, m.y))
        return result

    def select_monitors(self, selection: str = "all") -> List[Monitor]:
        mons = self.monitors()
        if selection and selection != "all":
            chosen = [m for m in mons if m.name == selection]
            if chosen:
                return chosen
            log.warning("monitor %r not found, using all monitors", selection)
        return mons

    # -- windows -----------------------------------------------------------
    def create_windows(self, selection: str = "all") -> List[DesktopWindow]:
        self.destroy_windows()
        for mon in self.select_monitors(selection):
            self.windows.append(DesktopWindow(mon, self._create_window(mon)))
        self.display.sync()
        self.lower_all()
        return list(self.windows)

    def _create_window(self, mon: Monitor) -> int:
        win = self.root.create_window(
            mon.x, mon.y, mon.width, mon.height, 0,
            self.screen.root_depth, X.InputOutput, X.CopyFromParent,
            background_pixel=self.screen.black_pixel,
            override_redirect=0,
            event_mask=X.StructureNotifyMask | X.PropertyChangeMask,
        )
        win.set_wm_name("Motionwall")
        win.set_wm_class(*WM_CLASS)
        win.set_wm_hints(flags=Xutil.InputHint, input=0)
        win.set_wm_normal_hints(
            flags=Xutil.PPosition | Xutil.PSize | Xutil.PMinSize | Xutil.PMaxSize | Xutil.USPosition,
            min_width=mon.width, min_height=mon.height, max_width=mon.width, max_height=mon.height,
        )
        atom = self.atom
        win.change_property(atom("_NET_WM_WINDOW_TYPE"), Xatom.ATOM, 32, [atom("_NET_WM_WINDOW_TYPE_DESKTOP")])
        win.change_property(atom("_NET_WM_STATE"), Xatom.ATOM, 32, [
            atom("_NET_WM_STATE_BELOW"), atom("_NET_WM_STATE_STICKY"),
            atom("_NET_WM_STATE_SKIP_TASKBAR"), atom("_NET_WM_STATE_SKIP_PAGER"),
        ])
        win.change_property(atom("_NET_WM_DESKTOP"), Xatom.CARDINAL, 32, [_ALL_DESKTOPS])
        win.change_property(atom("_NET_WM_PID"), Xatom.CARDINAL, 32, [os.getpid()])
        # user time 0 == "do not focus me when mapped" (EWMH)
        win.change_property(atom("_NET_WM_USER_TIME"), Xatom.CARDINAL, 32, [0])
        # no decorations even if a WM ignores the window type
        win.change_property(atom("_MOTIF_WM_HINTS"), atom("_MOTIF_WM_HINTS"), 32, [2, 0, 0, 0, 0])
        win.change_property(atom("WM_PROTOCOLS"), Xatom.ATOM, 32, [atom("WM_DELETE_WINDOW")])
        win.map()
        return win.id

    def destroy_windows(self) -> None:
        for dw in self.windows:
            try:
                self.display.create_resource_object("window", dw.xid).destroy()
            except error.XError:
                pass
        self.windows = []
        self.display.sync()

    # -- stacking ----------------------------------------------------------
    def _server_time(self, win) -> int:
        """Round-trip to obtain the current X server timestamp."""
        marker = self.atom("_MOTIONWALL_TIME")
        win.change_property(marker, Xatom.STRING, 8, b"t")
        self.display.flush()
        deadline = time.monotonic() + 0.5
        while True:
            while self.display.pending_events():
                ev = self.display.next_event()
                if ev.type == X.PropertyNotify and ev.atom == marker:
                    return ev.time
                self._handle_event(ev)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return 0
            ready, _, _ = select.select([self.display.fileno()], [], [], remaining)
            if not ready:
                return 0

    def lower_all(self) -> None:
        """Push our windows to the very bottom of the desktop layer."""
        self._pending_lower = False
        for dw in self.windows:
            win = self.display.create_resource_object("window", dw.xid)
            try:
                now = self._server_time(win)
                if now:
                    win.change_property(self.atom("_NET_WM_USER_TIME"), Xatom.CARDINAL, 32, [now])
                win.configure(stack_mode=X.Below)
                self._clear_state(win, "_NET_WM_STATE_DEMANDS_ATTENTION")
            except error.XError as exc:
                log.debug("lower failed for %#x: %s", dw.xid, exc)
        self.display.flush()

    def _clear_state(self, win, state: str) -> None:
        from Xlib.protocol import event as xevent
        msg = xevent.ClientMessage(window=win, client_type=self.atom("_NET_WM_STATE"),
                                   data=(32, [0, self.atom(state), 0, 1, 0]))
        self.root.send_event(msg, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)

    # -- events ------------------------------------------------------------
    def _select_root_events(self) -> None:
        self.root.change_attributes(event_mask=X.PropertyChangeMask)
        if self._has_randr:
            randr.select_input(self.root, randr.RRScreenChangeNotifyMask | randr.RROutputChangeNotifyMask
                               | randr.RRCrtcChangeNotifyMask)
        self.display.flush()

    def fileno(self) -> int:
        return self.display.fileno()

    def process_events(self) -> None:
        """Drain the X event queue; call from an IO watch on fileno()."""
        while self.display.pending_events():
            self._handle_event(self.display.next_event())
        if self._pending_lower:
            self.lower_all()
        if self._pending_screen_change:
            self._pending_screen_change = False
            if self.on_screen_change:
                self.on_screen_change()

    _STACK_PROPS = ("_NET_ACTIVE_WINDOW", "_NET_CURRENT_DESKTOP", "_NET_CLIENT_LIST")

    def _handle_event(self, ev) -> None:
        name = ev.__class__.__name__
        if ev.type == X.PropertyNotify and ev.window.id == self.root.id:
            if ev.atom in (self.atom(p) for p in self._STACK_PROPS):
                self._pending_lower = True
        elif name in ("ScreenChangeNotify", "RRNotify", "RRCrtcChangeNotify", "RROutputChangeNotify"):
            self._pending_screen_change = True

    def close(self) -> None:
        self.destroy_windows()
        self.display.close()


def stacking_order(layer: DesktopLayer) -> List[int]:
    """Bottom-to-top list of managed X windows as reported by the WM (for tests)."""
    prop = layer.root.get_full_property(layer.atom("_NET_CLIENT_LIST_STACKING"), Xatom.WINDOW)
    return list(prop.value) if prop else []


if __name__ == "__main__":  # manual check: python3 -m motionwall.xdesktop [seconds]
    import select
    import sys

    logging.basicConfig(level=logging.DEBUG)
    layer = DesktopLayer(on_screen_change=lambda: print("screen changed", flush=True))
    for w in layer.create_windows():
        print(f"XID {w.xid} monitor {w.monitor.name} {w.monitor.geometry}", flush=True)
    end = time.monotonic() + float(sys.argv[1] if len(sys.argv) > 1 else 10)
    while time.monotonic() < end:
        r, _, _ = select.select([layer.fileno()], [], [], 0.5)
        if r:
            layer.process_events()
    layer.close()
