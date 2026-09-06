// Motionwall - animated video wallpaper rendered inside GNOME Shell.
//
// Strategy (adapted from the DING and Hanabi extensions):
//   * one hidden mpv window per monitor renders the video on Xwayland;
//   * each monitor's Meta.BackgroundActor gets a Clutter.Clone of its mpv
//     window, so the live video appears in the real background layer - behind
//     every window, non-interactive, and visible through the overview;
//   * the source mpv windows are hidden from the window list, overview, tab
//     list and app tracker, and made click-through via an empty X11 input
//     shape (see player.js) - they stay mapped and visible so Mutter keeps
//     giving them full damage priority (a hidden/off-screen actor's content
//     gets deprioritized, which stuttered even though mpv itself never
//     dropped a frame); the clone is also force-redrawn every frame rather
//     than trusting the source's damage events to propagate reliably.

import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Gio from 'gi://Gio';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';

import {Extension, InjectionManager} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as Background from 'resource:///org/gnome/shell/ui/background.js';
import * as Workspace from 'resource:///org/gnome/shell/ui/workspace.js';
import * as WorkspaceThumbnail from 'resource:///org/gnome/shell/ui/workspaceThumbnail.js';

import {
    WINDOW_MARKER, CONFIG_PATH, SHELL_OBJECT_PATH, SHELL_INTERFACE,
    RENDERER_POLL_MS, DEBOUNCE_MS,
} from './constants.js';
import {readConfig, findMpv, MpvPlayer, APPEARANCE_KEYS} from './player.js';

const IFACE_XML = `
<node>
  <interface name="${SHELL_INTERFACE}">
    <property name="Occluded" type="b" access="read"/>
    <property name="Playing" type="b" access="read"/>
    <signal name="OccludedChanged"><arg type="b" name="occluded"/></signal>
    <method name="Reload"/>
    <method name="Restart"/>
  </interface>
</node>`;

// Set by the extension whenever the mpv renderers are paused; the clone's
// force-redraw tick stops while true.
let renderersPaused = false;

function isMarkerWindow(win) {
    return !!win?.title?.includes(WINDOW_MARKER);
}

function markerIndex(win) {
    const m = win?.title?.match(/@motionwall-wallpaper:(\d+)/);
    return m ? parseInt(m[1], 10) : -1;
}

// -----------------------------------------------------------------------------
// LiveWallpaper: a widget dropped inside a Meta.BackgroundActor that shows a
// Clone of the mpv window for that monitor.
// -----------------------------------------------------------------------------
const LiveWallpaper = GObject.registerClass(
class LiveWallpaper extends St.Widget {
    _init(backgroundActor, getAllWindowActors) {
        super._init({
            layout_manager: new Clutter.BinLayout(),
            width: backgroundActor.width,
            height: backgroundActor.height,
            opacity: 0,
        });
        this._backgroundActor = backgroundActor;
        this._monitorIndex = backgroundActor.monitor;
        this._getAllWindowActors = getAllWindowActors;
        this._clone = null;
        this._pollId = 0;
        this._sourceDestroyId = 0;
        this._tickId = 0;

        backgroundActor.layout_manager = new Clutter.BinLayout();
        backgroundActor.add_child(this);

        this.connect('destroy', () => this._onDestroy());
        this._apply();
    }

    _onDestroy() {
        if (this._pollId) {
            GLib.source_remove(this._pollId);
            this._pollId = 0;
        }
        this._dropClone();
    }

    _dropClone() {
        if (this._tickId) {
            GLib.source_remove(this._tickId);
            this._tickId = 0;
        }
        if (this._clone) {
            if (this._sourceDestroyId && this._clone.source) {
                this._clone.source.disconnect(this._sourceDestroyId);
                this._sourceDestroyId = 0;
            }
            this._clone.destroy();
            this._clone = null;
        }
    }

    _findRenderer() {
        const actors = this._getAllWindowActors().filter(a => isMarkerWindow(a.meta_window));
        // Prefer an exact index match; fall back to monitor geometry.
        let actor = actors.find(a => markerIndex(a.meta_window) === this._monitorIndex);
        if (!actor)
            actor = actors.find(a => a.meta_window?.get_monitor() === this._monitorIndex);
        return actor ?? null;
    }

    _apply() {
        const attach = () => {
            const renderer = this._findRenderer();
            if (!renderer)
                return true;      // keep polling
            this._dropClone();
            this._clone = new Clutter.Clone({
                source: renderer,
                x_expand: true,
                y_expand: true,
            });
            this.add_child(this._clone);
            this._sourceDestroyId = renderer.connect('destroy', () => {
                this._dropClone();
                if (!this._pollId)
                    this._apply();
            });
            this._tickId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 16, () => {
                // No point repainting a frozen frame at 60 Hz while paused.
                if (this._clone && !renderersPaused)
                    this._clone.queue_redraw();
                return GLib.SOURCE_CONTINUE;
            });
            this.ease({opacity: 255, duration: 500, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
            this._pollId = 0;
            return false;
        };
        if (attach())
            this._pollId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, RENDERER_POLL_MS, attach);
    }
});

// -----------------------------------------------------------------------------
// Extension
// -----------------------------------------------------------------------------
export default class MotionwallExtension extends Extension {
    enable() {
        this._injections = new InjectionManager();
        this._getAllWindowActors = () => global.get_window_actors();
        this._wallpapers = new Set();
        this._players = [];
        this._signals = [];
        this._occluded = false;
        this._playing = false;
        this._config = readConfig();
        this._mpv = findMpv();
        this._configMonitor = null;
        this._debounceId = 0;
        this._idleWatchId = 0;
        this._activeWatchId = 0;

        this._mpvMissing = !this._mpv;
        if (this._mpvMissing)
            this.getLogger?.().warn?.('mpv not found; wallpaper cannot render');

        this._exportDbus();
        this._installOverrides();
        this._installWindowHiding();
        this._connectSignals();
        this._watchConfig();

        // Wait for the shell to settle, then start.
        this._startId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 300, () => {
            this._startId = 0;
            this._reloadBackgrounds();
            this._restartPlayers();
            return GLib.SOURCE_REMOVE;
        });
    }

    disable() {
        if (this._startId) {
            GLib.source_remove(this._startId);
            this._startId = 0;
        }
        if (this._debounceId) {
            GLib.source_remove(this._debounceId);
            this._debounceId = 0;
        }
        this._removeIdleWatches();
        if (this._configMonitor) {
            this._configMonitor.cancel();
            this._configMonitor = null;
        }
        for (const [obj, id] of this._signals)
            obj.disconnect(id);
        this._signals = [];

        this._stopPlayers();

        this._injections.clear();
        this._destroyWallpapers();

        if (this._dbus) {
            this._dbus.unexport();
            this._dbus = null;
        }
        this._reloadBackgrounds();
    }

    log(msg) {
        try {
            this.getLogger().debug(msg);
        } catch {
            console.log(`[motionwall] ${msg}`);
        }
    }

    // -- D-Bus -------------------------------------------------------------
    _exportDbus() {
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE_XML, this);
        try {
            this._dbus.export(Gio.DBus.session, SHELL_OBJECT_PATH);
        } catch (e) {
            logError(e, 'motionwall: cannot export D-Bus');
        }
    }

    get Occluded() {
        return this._occluded;
    }

    get Playing() {
        return this._playing;
    }

    // Re-read the config and apply it: live where mpv allows it, respawning
    // the players only for settings that need it (or if none are running).
    Reload() {
        this._applyConfigChange(this._players.length === 0);
    }

    // Unconditionally respawn the players (GUI "Reload Wallpaper").
    Restart() {
        this._config = readConfig();
        this._restartPlayers();
    }

    // -- players -----------------------------------------------------------
    _restartPlayers() {
        this._stopPlayers();
        if (this._mpvMissing || !this._config.video || !this._config.enabled) {
            this._playing = false;
            this._reloadBackgrounds();
            return;
        }
        const monitors = Main.layoutManager.monitors;
        this._players = monitors.map((mon, i) => {
            const player = new MpvPlayer(i, {x: mon.x, y: mon.y, width: mon.width, height: mon.height},
                this._mpv, m => this.log(m), global.stage.width);
            player.start(this._config);
            return player;
        });
        this._playing = true;
        this._reloadBackgrounds();
        this._updatePolicy();
    }

    _stopPlayers() {
        for (const p of this._players)
            p.stop();
        this._players = [];
        this._playing = false;
    }

    _setPaused(paused) {
        renderersPaused = paused;
        for (const p of this._players)
            p.setPaused(paused);
    }

    // -- background injection ----------------------------------------------
    _installOverrides() {
        const self = this;
        this._injections.overrideMethod(
            Background.BackgroundManager.prototype, '_createBackgroundActor',
            original => function () {
                const actor = original.call(this);
                try {
                    const wallpaper = new LiveWallpaper(actor, self._getAllWindowActors);
                    self._wallpapers.add(wallpaper);
                    this.wallpaperActor = wallpaper;
                    wallpaper.connect('destroy', a => self._wallpapers.delete(a));
                } catch (e) {
                    logError(e, 'motionwall: failed to inject wallpaper');
                }
                return actor;
            });
    }

    _reloadBackgrounds() {
        this._destroyWallpapers();
        global.compositor.get_laters().add(Meta.LaterType.BEFORE_REDRAW, () => {
            try {
                Main.layoutManager._updateBackgrounds();
            } catch (e) {
                logError(e, 'motionwall: updateBackgrounds failed');
            }
            return GLib.SOURCE_REMOVE;
        });
    }

    _destroyWallpapers() {
        for (const w of [...this._wallpapers])
            w.destroy();
        this._wallpapers.clear();
    }

    // -- hide the source mpv windows everywhere ----------------------------
    _installWindowHiding() {
        const self = this;
        const filterActors = arr => arr.filter(a => !isMarkerWindow(a.meta_window));
        const filterWindows = arr => arr.filter(w => !isMarkerWindow(w));

        this._injections.overrideMethod(
            Shell.Global.prototype, 'get_window_actors',
            original => {
                self._getAllWindowActors = () => original.call(global);
                return function () {
                    return filterActors(original.call(this));
                };
            });
        this._injections.overrideMethod(
            Workspace.Workspace.prototype, '_isOverviewWindow',
            original => function (win) {
                return isMarkerWindow(win) ? false : original.call(this, win);
            });
        this._injections.overrideMethod(
            WorkspaceThumbnail.WorkspaceThumbnail.prototype, '_isOverviewWindow',
            original => function (win) {
                return isMarkerWindow(win) ? false : original.call(this, win);
            });
        this._injections.overrideMethod(
            Meta.Display.prototype, 'get_tab_list',
            original => function (type, workspace) {
                return filterWindows(original.call(this, type, workspace));
            });
        this._injections.overrideMethod(
            Shell.WindowTracker.prototype, 'get_window_app',
            original => function (win) {
                return isMarkerWindow(win) ? null : original.call(this, win);
            });
        this._injections.overrideMethod(
            Shell.App.prototype, 'get_windows',
            original => function () {
                return filterWindows(original.call(this));
            });
    }

    // -- signals & policy --------------------------------------------------
    _connectSignals() {
        const wm = global.window_manager;
        const display = global.display;
        // Hide + keep-at-bottom every mpv window as it maps.
        this._signals.push([wm, wm.connect_after('map', (_wm, actor) => this._onWindowMapped(actor))]);
        for (const sig of ['in-fullscreen-changed'])
            this._signals.push([display, display.connect(sig, () => this._debouncedPolicy())]);
        for (const sig of ['window-created'])
            this._signals.push([display, display.connect(sig, () => this._debouncedPolicy())]);
        this._signals.push([wm, wm.connect('minimize', () => this._debouncedPolicy())]);
        this._signals.push([wm, wm.connect('unminimize', () => this._debouncedPolicy())]);
        this._signals.push([wm, wm.connect('size-change', () => this._debouncedPolicy())]);
        this._signals.push([global.workspace_manager,
            global.workspace_manager.connect('active-workspace-changed', () => this._debouncedPolicy())]);
        this._signals.push([Main.layoutManager,
            Main.layoutManager.connect('monitors-changed', () => this._debounce(() => this._restartPlayers()))]);
        this._signals.push([Main.sessionMode, Main.sessionMode.connect('updated', () => this._updatePolicy())]);
        this._setupIdleWatch();
    }

    _onWindowMapped(actor) {
        const win = actor?.meta_window;
        if (!isMarkerWindow(win))
            return;
        // Deliberately do NOT hide the source actor (see the header comment):
        // player.js empties its X11 input shape instead, so it stays fully
        // click-through while remaining mapped and visible.
        try {
            win.stick?.();
        } catch (e) {
            logError(e, 'motionwall: stick failed');
        }
        this._reattachWallpapers();      // a fresh renderer appeared: attach clones
    }

    _reattachWallpapers() {
        for (const w of this._wallpapers)
            w._apply();
    }

    _debounce(fn) {
        if (this._debounceId)
            GLib.source_remove(this._debounceId);
        this._debounceId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, DEBOUNCE_MS, () => {
            this._debounceId = 0;
            fn();
            return GLib.SOURCE_REMOVE;
        });
    }

    _debouncedPolicy() {
        this._debounce(() => this._updatePolicy());
    }

    _computeOccluded() {
        if (Main.overview.visible)
            return false;
        const nMonitors = Main.layoutManager.monitors.length;
        if (nMonitors === 0)
            return false;
        const ws = global.workspace_manager.get_active_workspace();
        const covered = new Set();
        for (const win of ws.list_windows()) {
            if (isMarkerWindow(win) || win.minimized)
                continue;
            if (win.get_window_type() !== Meta.WindowType.NORMAL)
                continue;
            if (win.is_fullscreen() || (win.maximized_horizontally && win.maximized_vertically))
                covered.add(win.get_monitor());
        }
        return covered.size >= nMonitors;
    }

    _updatePolicy() {
        const cfg = this._config;
        const occluded = this._computeOccluded();
        if (occluded !== this._occluded) {
            this._occluded = occluded;
            this._dbus?.emit_signal('OccludedChanged', new GLib.Variant('(b)', [occluded]));
            this._dbus?.emit_property_changed('Occluded', GLib.Variant.new_boolean(occluded));
        }
        const locked = Main.sessionMode.currentMode === 'unlock-dialog';
        let paused = cfg.paused === true;
        if (cfg.pause_on_fullscreen && occluded)
            paused = true;
        if (cfg.pause_on_lock && locked)
            paused = true;
        if (cfg.pause_on_idle && this._idle)
            paused = true;
        this._setPaused(paused);
    }

    // -- idle watch (Meta.IdleMonitor) -------------------------------------
    _setupIdleWatch() {
        this._idle = false;
        try {
            const backend = global.backend ?? Meta.get_backend?.();
            this._idleMonitor = backend?.get_core_idle_monitor?.()
                ?? Meta.IdleMonitor?.get_core?.();
        } catch {
            this._idleMonitor = null;
        }
        this._armIdleWatch();
    }

    _armIdleWatch() {
        this._removeIdleWatches();
        const minutes = this._config.idle_minutes || 5;
        if (!this._idleMonitor || !this._config.pause_on_idle)
            return;
        try {
            this._idleWatchId = this._idleMonitor.add_idle_watch(minutes * 60 * 1000, () => {
                this._idle = true;
                this._updatePolicy();
                this._activeWatchId = this._idleMonitor.add_user_active_watch(() => {
                    this._activeWatchId = 0;
                    this._idle = false;
                    this._updatePolicy();
                });
            });
        } catch (e) {
            logError(e, 'motionwall: idle watch failed');
        }
    }

    _removeIdleWatches() {
        try {
            if (this._idleMonitor && this._idleWatchId)
                this._idleMonitor.remove_watch(this._idleWatchId);
            if (this._idleMonitor && this._activeWatchId)
                this._idleMonitor.remove_watch(this._activeWatchId);
        } catch {
            // ignore
        }
        this._idleWatchId = 0;
        this._activeWatchId = 0;
        this._idle = false;
    }

    // -- config watch ------------------------------------------------------
    _watchConfig() {
        try {
            const file = Gio.File.new_for_path(CONFIG_PATH);
            this._configMonitor = file.monitor_file(Gio.FileMonitorFlags.NONE, null);
            this._configMonitor.connect('changed', () => this._debounce(() => this._onConfigChanged()));
        } catch (e) {
            logError(e, 'motionwall: cannot watch config');
        }
    }

    _onConfigChanged() {
        this._applyConfigChange(false);
    }

    _applyConfigChange(forceRestart) {
        const old = this._config;
        const cfg = this._config = readConfig();
        const needRestart = forceRestart
            || old.video !== cfg.video
            || old.enabled !== cfg.enabled
            || old.hwdec !== cfg.hwdec
            || old.mute !== cfg.mute;
        if (old.idle_minutes !== cfg.idle_minutes || old.pause_on_idle !== cfg.pause_on_idle)
            this._armIdleWatch();
        if (needRestart) {
            this._restartPlayers();
            return;
        }
        const liveChanged = ['scaling', 'speed', 'loop', 'volume', ...APPEARANCE_KEYS]
            .some(key => old[key] !== cfg[key]);
        if (liveChanged) {
            for (const p of this._players)
                p.applyLive(cfg);
        }
        this._updatePolicy();
    }
}
