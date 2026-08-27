# Motionwall

Animated **video wallpaper for Ubuntu / GNOME** that is designed around one goal:
look great while costing almost nothing.

* Plays any video file (MP4, MKV, WebM, MOV, GIF …) as the desktop background.
* On **GNOME Wayland** (Ubuntu's default) a bundled GNOME Shell extension renders the
  video in the desktop's own background layer (behind every window, non-clickable,
  visible in the overview). On **X11/Xorg** a lightweight daemon does the same with
  desktop-layer windows.
* Hardware decoded on the GPU (NVDEC / Vulkan video / VA-API) — a 4K loop uses
  ~3 % of one CPU core on an RTX-class card, ~0 % when paused.
* Pauses automatically when nobody can see it: screen locked, user idle, on
  battery, or (with the optional shell extension) a full-screen app is in front.
* Clean GTK4 / libadwaita settings window, a CLI, D-Bus API, login autostart.
* Coexists with Ubuntu's desktop icons.

## Install

```bash
git clone <this repo> motionwall && cd motionwall
sudo apt install mpv python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-xlib ffmpeg
./install.sh --extension --autostart
```

No root? `./install.sh --local-mpv` downloads mpv's .deb files with
`apt-get download` and unpacks them into `~/.local/share/motionwall/runtime`
(only the handful of libraries that are actually missing are fetched).

Flags: `--extension` installs the GNOME Shell extension (pause behind
full-screen windows; needs a log-out/log-in on Wayland), `--autostart` restores
the wallpaper at login. Everything is installed under `~/.local`; remove it with
`./uninstall.sh` (`--purge` also deletes config and thumbnails).

## Use

* Right-click any video in Files → **Open With → Motionwall**: it becomes the
  wallpaper immediately (no window, just a notification).
* Or open **Motionwall** from the app grid to manage a library of videos,
  tweak scaling / power settings and watch the decoder stats.
* Or from a terminal:

```
motionwall set ~/Videos/loop.mp4   # use this file
motionwall pause | resume | toggle
motionwall stop                    # back to the normal wallpaper (video remembered)
motionwall resume                  # bring it back
motionwall status [--json]         # what is happening, decoder, fps, drops
motionwall quit-daemon
```

Settings (all live, no restart of the wallpaper needed unless noted):
scaling (fill / fit / stretch), monitors, playback speed, mute (restart), volume,
hardware decoding method (restart), rendering quality (restart), the four
power-saving rules, and start-at-login. Config lives in
`~/.config/motionwall/config.json`; unknown keys are preserved, and
`"mpv_args": [...]` passes extra options straight to mpv.

## How it works

```
 GTK4 UI  ──D-Bus──▶  motionwall daemon  ──X11 (Xwayland)──▶  desktop-layer windows
   CLI    ──D-Bus──▶   (GLib main loop)   ──JSON IPC──────▶   mpv per monitor (--wid)
                        ▲       ▲   ▲
        ScreenSaver ────┘       │   └──── org.motionwall.Shell (optional extension)
        IdleMonitor ────────────┘         UPower.OnBattery
```

* **Desktop layer.** The daemon creates one X11 window per monitor with
  `_NET_WM_WINDOW_TYPE_DESKTOP`, `_NET_WM_STATE_BELOW/STICKY`, on all
  workspaces, unfocusable. Mutter (GNOME's compositor) honours this for X
  clients even in a Wayland session, so the window sits under every other
  window on every workspace. Ubuntu's desktop icons live in the same layer and
  keep lowering themselves; Motionwall re-lowers its windows whenever the
  active window / workspace / client list changes so the icons stay visible.
* **Rendering.** [mpv](https://mpv.io) draws into that window (`--wid`) with
  `--hwdec=auto-safe`: decoding happens on the GPU (NVDEC, Vulkan video or
  VA-API) and frames never touch system memory. The audio track is not decoded
  at all when muted. `--profile=fast` uses cheap bilinear scaling and no
  dithering (switch to *High quality scaling* if you prefer). No OSD, no input
  handling, and mpv is told **not** to inhibit the screensaver.
* **Pause policy.** `policy.py` is a pure function of
  `(user play, locked, idle, on battery, occluded)` and the config. Lock state
  comes from `org.gnome.ScreenSaver`, idleness from
  `org.gnome.Mutter.IdleMonitor`, battery from UPower, occlusion from the
  extension. A paused mpv drops to ~0 % CPU and the GPU decoder goes idle.
  For the long pauses (idle, lock) the file is *released* instead: mpv stays
  attached to the window but unloads the decoder and its GPU frame pool, and
  reloads the video (about a second) when you come back.
* **Supervision.** If mpv dies it is restarted with exponential backoff
  (1, 2, 4, 8, 16 s, then give up and report *error*). Monitor hot-plug or
  resolution changes (RandR) rebuild the windows.
* **Processes.** One tiny daemon (0 % CPU when idle) plus one mpv per monitor.
  The GUI is a separate process you can close.

### Measured on the development machine

Ubuntu 26.04, GNOME 50 Wayland, RTX 5060 Ti, 4K monitor at 125 % scaling:

| state                    | decoder | mpv CPU (one core) | RSS    | VRAM    | dropped |
|--------------------------|---------|--------------------|--------|---------|---------|
| 1080p30 H.264 playing    | vulkan  | 3.2 %              | 357 MB |         | 0       |
| 2160p30 H.264 playing    | vulkan  | 2.8–3.2 %          | 368 MB | 287 MiB | 0       |
| paused (user)            | —       | 0.2–0.3 %          | 368 MB | 287 MiB |         |
| released (idle / locked) | —       | 0.3 %              | 358 MB | 219 MiB |         |
| daemon                   | —       | 0.0 %              | 16 MB  |         |         |

RSS is dominated by the Vulkan driver and libplacebo shader cache, not by
video buffers; releasing the file frees the decoder's GPU frame pool.

(The OpenGL backend with NVDEC measured 5.6 % / 456 MB; Vulkan is the default.)

If the GPU is out of memory when the video starts (e.g. a large local LLM is
loaded), mpv falls back to software decoding (~9 % CPU, 1 GB RSS at 4K). The
daemon notices `hwdec-current == no` and re-tries hardware decoding every
minute, up to five times; `motionwall status` flags the software path.

## Troubleshooting

* **`motionwall status` says mpv NOT FOUND** — `sudo apt install mpv` or
  `./install.sh --local-mpv`.
* **Software decoding (`decode=software`)** — install the driver for your GPU:
  Intel `intel-media-va-driver-non-free`, AMD `mesa-va-drivers`, NVIDIA
  `libnvidia-decode-*` (comes with the driver). Try *Hardware decoding →
  Vulkan* or *NVDEC* explicitly. mpv's log is in `~/.cache/motionwall/`.
* **Nothing shows on Wayland** — GNOME needs Xwayland (installed by default;
  `DISPLAY` must be set in the session). Other Wayland compositors without
  X11 desktop-window support are not supported; on wlroots compositors use
  `mpvpaper` instead.
* **Fractional scaling** — with GNOME's `xwayland-native-scaling` the X screen
  is larger than the physical one (e.g. 6144×3456 for a 4K display at 125 %),
  so mpv renders at that size. On discrete GPUs this is negligible; on an
  integrated GPU keep *Rendering: Efficient*.
* **Desktop icons hidden** — make sure only one Motionwall daemon runs
  (`motionwall quit-daemon`, then `motionwall resume`).
* **Extension not detected** — after `install.sh --extension`, log out and in
  (GNOME on Wayland only loads new extensions at login), then check
  `gnome-extensions info motionwall@motionwall`.
* **Overview / workspace switcher shows the static wallpaper** — expected;
  GNOME renders its own background in those views.
* **Stutter from a network or removable drive** — the demuxer read-ahead is
  kept tiny for local files; add `"mpv_args": ["--cache=yes"]` to the config.

## Development

```
python3 -m unittest discover -s tests -v   # unit tests (no display needed)
python3 -m motionwall.xdesktop 10          # show a black desktop window for 10 s
MOTIONWALL_LOG=DEBUG bin/motionwall daemon # run the daemon in the foreground
```

Layout: `motionwall/xdesktop.py` (X11 windows), `engine.py` (mpv + IPC),
`policy.py` / `power.py` (pausing), `daemon.py` (D-Bus service), `client.py`,
`cli.py`, `ui/app.py` (GTK4), `library.py` (videos + thumbnails),
`extension/` (GNOME Shell extension), `scripts/`, `install.sh`, `uninstall.sh`.

License: MIT.
