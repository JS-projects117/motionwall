// Config reading and mpv process management for the Motionwall extension.
//
// One mpv process is spawned per monitor. mpv runs on Xwayland (X11 backend);
// its window is turned into a click-through, monitor-sized desktop-layer window
// (see prepareWindow) that stays mapped and visible, and the shell side clones
// it into each monitor's background. Each window carries WINDOW_MARKER:<index>
// in its title so the shell side can find and clone it. Pause is driven over
// mpv's JSON IPC socket.

import GLib from 'gi://GLib';
import Gio from 'gi://Gio';

import {WINDOW_MARKER, CONFIG_PATH, RENDERER_POLL_MS} from './constants.js';

const DEFAULTS = {
    video: '',
    enabled: true,
    paused: false,
    scaling: 'fill',       // fill | fit | stretch
    mute: true,
    volume: 50,
    hwdec: 'auto-safe',
    loop: true,
    speed: 1.0,
    pause_on_fullscreen: true,
    pause_on_lock: true,
    pause_on_idle: true,
    idle_minutes: 5,
    pause_on_battery: true,
    // appearance (see motionwall/config.py); -100..100 except rotate (degrees)
    brightness: 0,
    contrast: 0,
    saturation: 0,
    gamma: 0,
    hue: 0,
    zoom: 0,
    align_x: 0,
    align_y: 0,
    rotate: 0,
};

export const APPEARANCE_KEYS = ['brightness', 'contrast', 'saturation', 'gamma', 'hue',
    'zoom', 'align_x', 'align_y', 'rotate'];

export function readConfig() {
    const cfg = {...DEFAULTS};
    try {
        const [ok, bytes] = GLib.file_get_contents(CONFIG_PATH);
        if (ok) {
            const data = JSON.parse(new TextDecoder().decode(bytes));
            for (const key of Object.keys(DEFAULTS)) {
                if (data[key] !== undefined && data[key] !== null)
                    cfg[key] = data[key];
            }
        }
    } catch (e) {
        logError(e, 'motionwall: could not read config');
    }
    return cfg;
}

function scalingArgs(mode) {
    if (mode === 'fit')
        return ['--keepaspect=yes', '--panscan=0'];
    if (mode === 'stretch')
        return ['--keepaspect=no'];
    return ['--keepaspect=yes', '--panscan=1.0'];   // fill
}

// mpv property -> value for the appearance settings; every one of these can be
// set while playing. Keep in sync with appearance_properties() in motionwall/engine.py.
export function appearanceProps(cfg) {
    const clamp = v => Math.max(-100, Math.min(100, Math.round(Number(v) || 0)));
    const rotate = ((Math.round(Number(cfg.rotate) || 0) % 360) + 360) % 360;
    return {
        'brightness': clamp(cfg.brightness),
        'contrast': clamp(cfg.contrast),
        'saturation': clamp(cfg.saturation),
        'gamma': clamp(cfg.gamma),
        'hue': clamp(cfg.hue),
        'video-zoom': clamp(cfg.zoom) / 100,
        'video-align-x': clamp(cfg.align_x) / 100,
        'video-align-y': clamp(cfg.align_y) / 100,
        'video-rotate': [0, 90, 180, 270].includes(rotate) ? rotate : 0,
    };
}

// Settings mpv can take live over IPC (no respawn): scaling, speed, loop,
// volume and the appearance properties.
export function liveProps(cfg) {
    const props = appearanceProps(cfg);
    props.speed = Number(cfg.speed) || 1;
    props['loop-file'] = cfg.loop ? 'inf' : 'no';
    if (cfg.scaling === 'stretch') {
        props.keepaspect = false;
    } else {
        props.keepaspect = true;
        props.panscan = cfg.scaling === 'fill' ? 1.0 : 0.0;
    }
    if (!cfg.mute)
        props.volume = Math.round(Number(cfg.volume) || 0);
    return props;
}

function mpvArgv(mpvPath, cfg, index, geometry, socketPath) {
    const argv = [
        mpvPath, '--no-config', '--profile=fast',
        `--title=${WINDOW_MARKER}:${index}`,
        '--x11-name=motionwall',
        `--geometry=${geometry.width}x${geometry.height}+${geometry.x}+${geometry.y}`,
        `--autofit=${geometry.width}x${geometry.height}`,
        '--gpu-context=x11egl',         // keep rendering while the window is hidden; legacy GLX 'x11'
                                         // context is gone from mpv builds without --enable-x11-glx
        // Don't block in eglSwapBuffers waiting for Xwayland's present completion:
        // through Xwayland on NVIDIA that completion is erratic (measured ~13 ms
        // vsync jitter at 60 Hz), and it stops entirely while Mutter withholds
        // frame callbacks. Frames are paced by the audio/PTS clock instead and
        // pushed the moment they are due; the shell samples them at its own rate.
        '--opengl-swapinterval=0',
        '--force-window=yes', '--idle=yes',
        '--auto-window-resize=no',      // never resize the window on (re)load: prepareWindow()
                                         // sizes it to the monitor and mpv must not undo that
        '--no-border', '--ontop=no', '--fullscreen=no',
        `--input-ipc-server=${socketPath}`,
        cfg.loop ? '--loop-file=inf' : '--loop-file=no',
        `--hwdec=${cfg.hwdec}`,
        cfg.mute ? '--audio=no' : `--volume=${Math.round(cfg.volume)}`,
        `--speed=${Number(cfg.speed) || 1}`,
        '--osc=no', '--osd-bar=no', '--osd-level=0',
        '--input-default-bindings=no', '--input-vo-keyboard=no', '--input-cursor=no',
        '--cursor-autohide=no', '--stop-screensaver=no',
        '--msg-level=all=warn', '--terminal=no',
        '--background=color', '--background-color=#000000',
        '--cache=no', '--demuxer-readahead-secs=1', '--demuxer-max-bytes=32MiB',
        ...scalingArgs(cfg.scaling),
        ...Object.entries(appearanceProps(cfg)).map(([k, v]) => `--${k}=${v}`),
    ];
    if (cfg.video)
        argv.push(cfg.video);
    return argv;
}

// Prepares the marker window on the X11 side once mpv has mapped it:
//   * empties its input shape so the pointer passes straight through (same
//     technique as motionwall/xdesktop.py's X11-native path) - we keep the
//     window mapped and visible instead of Clutter-hiding it (see the comment
//     in extension.js's _onWindowMapped), so it needs its own click-through
//     mechanism rather than relying on being non-reactive;
//   * turns it into a _NET_WM_WINDOW_TYPE_DESKTOP window, which Mutter stacks
//     in the desktop layer under every normal window (including the desktop
//     icons window) and never focuses;
//   * moves/resizes it to cover exactly its monitor in X coordinates. With
//     xwayland-native-scaling the X screen is an integer multiple of the
//     logical size, so the scale is derived from root width / stage width.
// The visible source window then coincides pixel-for-pixel with the clone
// in the background layer instead of showing up as a smaller copy on top.
const PREPARE_WINDOW_PY = `
import sys, time
from Xlib import display, X, Xatom
from Xlib.ext import shape
from Xlib.protocol import event

needle = sys.argv[1]
mx, my, mw, mh, stage_w = (float(v) for v in sys.argv[2:7])

def find(win):
    try:
        name = win.get_wm_name()
        # Mutter's frame windows copy the client's title but carry no
        # WM_CLASS; only the client window has one.
        if name and needle in name and win.get_wm_class():
            return win
    except Exception:
        pass
    try:
        children = win.query_tree().children
    except Exception:
        return None
    for c in children:
        found = find(c)
        if found:
            return found
    return None

d = display.Display()
screen = d.screen()
root = screen.root
scale = screen.width_in_pixels / stage_w if stage_w > 0 else 1.0
geo = [int(round(v * scale)) for v in (mx, my, mw, mh)]
for _ in range(100):
    win = find(root)
    if win:
        try:
            win.shape_rectangles(shape.SO.Set, shape.SK.Input, 0, 0, 0, [])
        except Exception:
            pass
        try:
            atom = d.intern_atom
            win.change_property(atom('_NET_WM_WINDOW_TYPE'), Xatom.ATOM, 32,
                                [atom('_NET_WM_WINDOW_TYPE_DESKTOP')])
            msg = event.ClientMessage(window=win, client_type=atom('_NET_WM_STATE'),
                                      data=(32, [1, atom('_NET_WM_STATE_SKIP_TASKBAR'),
                                                 atom('_NET_WM_STATE_SKIP_PAGER'), 1, 0]))
            root.send_event(msg, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
            msg = event.ClientMessage(window=win, client_type=atom('_NET_WM_STATE'),
                                      data=(32, [1, atom('_NET_WM_STATE_BELOW'), 0, 1, 0]))
            root.send_event(msg, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
        except Exception:
            pass
        d.sync()
        # Both Mutter (re-placing the window after the type change) and mpv
        # (applying --geometry again once the file is loaded) can undo the
        # resize, so keep re-asserting the geometry for a few seconds.
        want = (geo[0], geo[1], max(geo[2], 1), max(geo[3], 1))
        for _ in range(80):
            try:
                g = win.get_geometry()
                if (g.x, g.y, g.width, g.height) != want:
                    win.configure(x=want[0], y=want[1], width=want[2], height=want[3])
                    d.sync()
            except Exception:
                break
            time.sleep(0.1)
        break
    time.sleep(0.1)
`;

function prepareWindow(title, geometry, stageWidth, env) {
    try {
        const launcher = new Gio.SubprocessLauncher({
            flags: Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE,
        });
        for (const kv of env) {
            const eq = kv.indexOf('=');
            if (eq > 0)
                launcher.setenv(kv.slice(0, eq), kv.slice(eq + 1), true);
        }
        launcher.spawnv(['python3', '-c', PREPARE_WINDOW_PY, title,
            String(geometry.x), String(geometry.y),
            String(geometry.width), String(geometry.height), String(stageWidth)]);
    } catch (e) {
        void e;      // best-effort; window just stays a normal, non-click-through one
    }
}

export function findMpv() {
    const local = GLib.build_filenamev([
        GLib.get_user_data_dir(), 'motionwall', 'runtime', 'usr', 'bin', 'mpv',
    ]);
    if (GLib.file_test(local, GLib.FileTest.IS_EXECUTABLE))
        return local;
    const system = GLib.find_program_in_path('mpv');
    return system || null;
}

// GNOME Shell's own process environment is captured at login, before Xwayland
// is up, so it has no DISPLAY/XAUTHORITY - a child spawned with GLib.get_environ()
// can't connect to X. systemd --user's activation environment is updated once
// Xwayland is ready and is the only reliable place to read those from in-process.
function systemdActivationEnv() {
    try {
        const reply = Gio.DBus.session.call_sync(
            'org.freedesktop.systemd1', '/org/freedesktop/systemd1',
            'org.freedesktop.DBus.Properties', 'Get',
            new GLib.Variant('(ss)', ['org.freedesktop.systemd1.Manager', 'Environment']),
            null, Gio.DBusCallFlags.NONE, -1, null);
        return reply.deep_unpack()[0].deep_unpack();
    } catch {
        return [];
    }
}

function mpvEnv(mpvPath) {
    let env = GLib.get_environ();
    if (!GLib.environ_getenv(env, 'DISPLAY')) {
        const activation = systemdActivationEnv();
        for (const key of ['DISPLAY', 'XAUTHORITY']) {
            const value = GLib.environ_getenv(activation, key);
            if (value)
                env = GLib.environ_setenv(env, key, value, true);
        }
    }
    if (mpvPath.includes('/motionwall/runtime/')) {
        const lib = GLib.build_filenamev([
            GLib.get_user_data_dir(), 'motionwall', 'runtime', 'usr', 'lib', 'x86_64-linux-gnu',
        ]);
        const prev = GLib.environ_getenv(env, 'LD_LIBRARY_PATH');
        env = GLib.environ_setenv(env, 'LD_LIBRARY_PATH', prev ? `${lib}:${prev}` : lib, true);
    }
    return env;
}

export class MpvPlayer {
    constructor(index, geometry, mpvPath, log, stageWidth = 0) {
        this.index = index;
        this.geometry = geometry;
        this.stageWidth = stageWidth;
        this.mpvPath = mpvPath;
        this.log = log;
        this.subprocess = null;
        this.socketPath = GLib.build_filenamev([
            GLib.get_user_runtime_dir() || GLib.get_tmp_dir(),
            `motionwall-ext-mpv-${index}.sock`,
        ]);
        this._paused = false;
    }

    start(cfg) {
        this.stop();
        try {
            GLib.unlink(this.socketPath);
        } catch {
            // socket may not exist
        }
        const argv = mpvArgv(this.mpvPath, cfg, this.index, this.geometry, this.socketPath);
        this.log(`monitor ${this.index}: ${argv.join(' ')}`);
        const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.STDERR_SILENCE});
        for (const kv of mpvEnv(this.mpvPath)) {
            const eq = kv.indexOf('=');
            if (eq > 0)
                launcher.setenv(kv.slice(0, eq), kv.slice(eq + 1), true);
        }
        this.subprocess = launcher.spawnv(argv);
        this._paused = false;
        prepareWindow(`${WINDOW_MARKER}:${this.index}`, this.geometry, this.stageWidth,
            mpvEnv(this.mpvPath));
    }

    stop() {
        if (this.subprocess) {
            try {
                this.subprocess.force_exit();
            } catch {
                // already gone
            }
            this.subprocess = null;
        }
        try {
            GLib.unlink(this.socketPath);
        } catch {
            // ignore
        }
    }

    setPaused(paused) {
        if (paused === this._paused || !this.subprocess)
            return;
        this._paused = paused;
        this._ipc({command: ['set_property', 'pause', paused]});
    }

    load(video) {
        if (this.subprocess)
            this._ipc({command: ['loadfile', video, 'replace']});
    }

    // Apply every live-settable setting from cfg without respawning mpv.
    applyLive(cfg) {
        if (!this.subprocess)
            return;
        this._ipc(...Object.entries(liveProps(cfg))
            .map(([name, value]) => ({command: ['set_property', name, value]})));
    }

    _ipc(...payloads) {
        // Fire-and-forget JSON command(s) into mpv's IPC socket.
        try {
            const addr = Gio.UnixSocketAddress.new(this.socketPath);
            const client = new Gio.SocketClient();
            client.connect_async(addr, null, (obj, res) => {
                try {
                    const conn = obj.connect_finish(res);
                    const out = conn.get_output_stream();
                    out.write_all(payloads.map(p => `${JSON.stringify(p)}\n`).join(''), null);
                    conn.close_async(GLib.PRIORITY_DEFAULT, null, null);
                } catch (e) {
                    void e;      // mpv may not have opened the socket yet
                }
            });
        } catch (e) {
            void e;
        }
    }
}

export {RENDERER_POLL_MS};
