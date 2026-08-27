// Config reading and mpv process management for the Motionwall extension.
//
// One mpv process is spawned per monitor. mpv runs on Xwayland (X11 backend) so
// it keeps producing frames even while its window is hidden - a native Wayland
// mpv throttles to frame callbacks and would freeze once we hide the source
// window. Each window carries WINDOW_MARKER:<index> in its title so the shell
// side can find, hide and clone it. Pause is driven over mpv's JSON IPC socket.

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
};

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

function mpvArgv(mpvPath, cfg, index, geometry, socketPath) {
    const argv = [
        mpvPath, '--no-config', '--profile=fast',
        `--title=${WINDOW_MARKER}:${index}`,
        '--x11-name=motionwall',
        `--geometry=${geometry.width}x${geometry.height}+${geometry.x}+${geometry.y}`,
        `--autofit=${geometry.width}x${geometry.height}`,
        '--gpu-context=x11',            // keep rendering while the window is hidden
        '--force-window=yes', '--idle=yes',
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
    ];
    if (cfg.video)
        argv.push(cfg.video);
    return argv;
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

function mpvEnv(mpvPath) {
    const env = GLib.get_environ();
    if (mpvPath.includes('/motionwall/runtime/')) {
        const lib = GLib.build_filenamev([
            GLib.get_user_data_dir(), 'motionwall', 'runtime', 'usr', 'lib', 'x86_64-linux-gnu',
        ]);
        const prev = GLib.environ_getenv(env, 'LD_LIBRARY_PATH');
        return GLib.environ_setenv(env, 'LD_LIBRARY_PATH', prev ? `${lib}:${prev}` : lib, true);
    }
    return env;
}

export class MpvPlayer {
    constructor(index, geometry, mpvPath, log) {
        this.index = index;
        this.geometry = geometry;
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

    _ipc(payload) {
        // Fire-and-forget one JSON command into mpv's IPC socket.
        try {
            const addr = Gio.UnixSocketAddress.new(this.socketPath);
            const client = new Gio.SocketClient();
            client.connect_async(addr, null, (obj, res) => {
                try {
                    const conn = obj.connect_finish(res);
                    const out = conn.get_output_stream();
                    out.write_all(`${JSON.stringify(payload)}\n`, null);
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
