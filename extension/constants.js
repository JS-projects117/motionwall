// Shared constants for the Motionwall shell extension and its window marker.
import GLib from 'gi://GLib';

// mpv windows carry this token in their title so the extension can find and hide
// them. The monitor index is appended: "<MARKER>:<index>".
export const WINDOW_MARKER = '@motionwall-wallpaper';

export const CONFIG_PATH = GLib.build_filenamev([
    GLib.get_user_config_dir(), 'motionwall', 'config.json',
]);

export const SHELL_BUS_NAME = 'org.gnome.Shell';
export const SHELL_OBJECT_PATH = '/org/motionwall/Shell';
export const SHELL_INTERFACE = 'org.motionwall.Shell';

// How long to wait for an mpv window to appear before re-polling (ms).
export const RENDERER_POLL_MS = 800;
// Debounce for monitors-changed / config-changed storms (ms).
export const DEBOUNCE_MS = 500;
