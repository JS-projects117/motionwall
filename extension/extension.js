// Motionwall GNOME Shell extension: exports org.motionwall.Shell on the session
// bus with an `Occluded` property that is true when every monitor is covered by
// a full-screen or maximized window (the wallpaper daemon pauses playback then).

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const OBJECT_PATH = '/org/motionwall/Shell';
const IFACE_XML = `
<node>
  <interface name="org.motionwall.Shell">
    <property name="Occluded" type="b" access="read"/>
    <signal name="OccludedChanged"><arg type="b" name="occluded"/></signal>
  </interface>
</node>`;

const OUR_WM_CLASS = 'motionwall';
const DEBOUNCE_MS = 150;

export default class MotionwallExtension extends Extension {
    enable() {
        this._occluded = false;
        this._connections = [];
        this._windowConnections = new Map();
        this._timeout = 0;

        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE_XML, this);
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);

        const display = global.display;
        const wm = global.window_manager;
        const wsm = global.workspace_manager;
        this._connect(display, 'window-created', (_d, win) => { this._track(win); this._schedule(); });
        this._connect(display, 'notify::focus-window', () => this._schedule());
        this._connect(display, 'in-fullscreen-changed', () => this._schedule());
        for (const sig of ['minimize', 'unminimize', 'size-change', 'destroy', 'map', 'switch-workspace'])
            this._connect(wm, sig, () => this._schedule());
        this._connect(wsm, 'active-workspace-changed', () => this._schedule());
        this._connect(Main.layoutManager, 'monitors-changed', () => this._schedule());
        this._connect(Main.overview, 'showing', () => this._schedule());
        this._connect(Main.overview, 'hidden', () => this._schedule());

        for (const actor of global.get_window_actors())
            this._track(actor.get_meta_window());
        this._schedule();
    }

    disable() {
        if (this._timeout) {
            GLib.source_remove(this._timeout);
            this._timeout = 0;
        }
        for (const [obj, id] of this._connections)
            obj.disconnect(id);
        this._connections = [];
        for (const [win, ids] of this._windowConnections)
            for (const id of ids)
                win.disconnect(id);
        this._windowConnections.clear();
        if (this._dbus) {
            this._dbus.unexport();
            this._dbus = null;
        }
    }

    // D-Bus property
    get Occluded() {
        return this._occluded;
    }

    _connect(obj, signal, handler) {
        this._connections.push([obj, obj.connect(signal, handler)]);
    }

    _track(win) {
        if (!win || this._windowConnections.has(win))
            return;
        const ids = [];
        for (const prop of ['notify::fullscreen', 'notify::maximized-horizontally',
            'notify::maximized-vertically', 'notify::minimized'])
            ids.push(win.connect(prop, () => this._schedule()));
        ids.push(win.connect('unmanaged', () => {
            for (const id of this._windowConnections.get(win) ?? [])
                win.disconnect(id);
            this._windowConnections.delete(win);
            this._schedule();
        }));
        this._windowConnections.set(win, ids);
    }

    _schedule() {
        if (this._timeout)
            return;
        this._timeout = GLib.timeout_add(GLib.PRIORITY_DEFAULT, DEBOUNCE_MS, () => {
            this._timeout = 0;
            this._update();
            return GLib.SOURCE_REMOVE;
        });
    }

    _isCovering(win) {
        if (win.minimized || win.get_window_type() !== Meta.WindowType.NORMAL)
            return false;
        const cls = (win.get_wm_class() ?? '').toLowerCase();
        const inst = (win.get_wm_class_instance() ?? '').toLowerCase();
        if (cls === OUR_WM_CLASS || inst === OUR_WM_CLASS)
            return false;
        return win.is_fullscreen() || (win.maximized_horizontally && win.maximized_vertically);
    }

    _computeOccluded() {
        if (Main.overview.visible)
            return false;               // the overview shows the desktop thumbnails
        const monitors = Main.layoutManager.monitors.length;
        if (monitors === 0)
            return false;
        const workspace = global.workspace_manager.get_active_workspace();
        const covered = new Set();
        for (const win of workspace.list_windows()) {
            if (this._isCovering(win))
                covered.add(win.get_monitor());
        }
        return covered.size >= monitors;
    }

    _update() {
        const occluded = this._computeOccluded();
        if (occluded === this._occluded)
            return;
        this._occluded = occluded;
        if (!this._dbus)
            return;
        this._dbus.emit_property_changed('Occluded', GLib.Variant.new_boolean(occluded));
        this._dbus.emit_signal('OccludedChanged', GLib.Variant.new('(b)', [occluded]));
    }
}
