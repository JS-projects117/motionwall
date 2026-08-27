import unittest
from unittest import mock

from Xlib import X

from motionwall import xdesktop


def _bare_layer():
    layer = xdesktop.DesktopLayer.__new__(xdesktop.DesktopLayer)
    layer._atoms = {}
    layer._pending_lower = False
    layer._pending_screen_change = False
    layer.windows = []
    layer.on_screen_change = None
    layer.display = mock.Mock()
    layer.display.intern_atom.side_effect = lambda n: abs(hash(n)) % 100000
    layer.root = mock.Mock()
    layer.root.id = 999
    return layer


class FakeEvent:
    def __init__(self, name, type_=0, window=None, atom=None):
        self.__class__ = type(name, (FakeEvent,), {})
        self.type = type_
        self.window = window
        self.atom = atom


class XDesktopTests(unittest.TestCase):
    def test_select_monitors_all_and_by_name(self):
        layer = _bare_layer()
        mons = [xdesktop.Monitor("HDMI-1", 0, 0, 1920, 1080, True), xdesktop.Monitor("DP-2", 1920, 0, 1920, 1080)]
        layer.monitors = lambda: mons
        self.assertEqual(layer.select_monitors("all"), mons)
        self.assertEqual(layer.select_monitors("DP-2"), [mons[1]])
        self.assertEqual(layer.select_monitors("nope"), mons)

    def test_geometry_string(self):
        self.assertEqual(xdesktop.Monitor("x", 10, 20, 300, 400).geometry, "300x400+10+20")

    def test_root_property_change_schedules_lower(self):
        layer = _bare_layer()
        ev = FakeEvent("PropertyNotify", X.PropertyNotify, window=layer.root, atom=layer.atom("_NET_ACTIVE_WINDOW"))
        layer._handle_event(ev)
        self.assertTrue(layer._pending_lower)
        layer._pending_lower = False
        other = FakeEvent("PropertyNotify", X.PropertyNotify, window=layer.root, atom=layer.atom("_NET_WM_NAME"))
        layer._handle_event(other)
        self.assertFalse(layer._pending_lower)

    def test_randr_event_fires_callback(self):
        layer = _bare_layer()
        fired = []
        layer.on_screen_change = lambda: fired.append(1)
        layer.display.pending_events.return_value = 0
        layer._handle_event(FakeEvent("ScreenChangeNotify"))
        self.assertTrue(layer._pending_screen_change)
        layer.process_events()
        self.assertEqual(fired, [1])
        self.assertFalse(layer._pending_screen_change)

    def test_process_events_lowers_when_pending(self):
        layer = _bare_layer()
        layer.display.pending_events.return_value = 0
        layer._pending_lower = True
        with mock.patch.object(layer, "lower_all") as lower:
            layer.process_events()
            lower.assert_called_once()


if __name__ == "__main__":
    unittest.main()
