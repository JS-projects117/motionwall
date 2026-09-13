# Motionwall

Motionwall turns local videos into animated desktop wallpapers on Ubuntu and
GNOME. It combines mpv playback with a GTK4/libadwaita settings app, a video
library, appearance controls, and a command-line interface.

**Status: early preview (0.1.0).** Ready for experimentation and feedback;
broader desktop and GPU testing is still needed before a stable release.
No videos or wallpaper packs are included.

## Features

- Play MP4, MKV, WebM, MOV, GIF, and other supported local video formats.
- Use GPU decoding when supported by your hardware, drivers, and video codec.
- Manage a video library with generated thumbnails and file-manager “Open With” support.
- Adjust fill/fit/stretch, playback speed, sound, brightness, contrast, color,
  zoom, positioning, and rotation.
- Pause on lock, idle, or full-screen coverage; the Xorg daemon also supports
  battery pausing.
- On GNOME Wayland, render through a bundled Shell extension in the desktop
  background layer, including the Activities overview.
- On Xorg, render through a separate Python daemon with desktop-layer windows.

## Compatibility and current limits

The development setup previously documented for this project is Ubuntu 26.04,
GNOME 50 on Wayland, and an NVIDIA RTX 5060 Ti. The extension declares GNOME
45–50 compatibility, but that is not a tested compatibility matrix. Other
GNOME versions, GPUs, mixed-DPI displays, and multi-monitor layouts need testing.

| Area | Current behavior |
| --- | --- |
| GNOME Wayland | Requires the bundled extension and Xwayland. Log out and back in after first installation. |
| Xorg | Uses the Python daemon; start it explicitly or enable daemon autostart. |
| Other desktops | Not validated; the Wayland renderer depends on GNOME Shell. |
| Battery pausing | Implemented by the Xorg daemon; currently absent from the Wayland extension. |
| Monitor selection | The extension plays on all monitors; individual monitor selection is daemon-only. |
| Advanced renderer settings | Custom `mpv_args`, rendering quality, decoder retry, and long-pause unloading belong to the daemon and are not all implemented by the extension. |
| Renderer recovery | The daemon supervises mpv crashes; the extension may need “Reload Wallpaper” after a player exits. |

The extension uses GNOME Shell internals, so Shell updates and other extensions
can affect it. Performance depends on the backend, codec, resolution, GPU, and
display scaling; no fixed CPU or memory usage is guaranteed.

## Install from source

Install the system dependencies on a compatible Ubuntu/Debian GNOME desktop:

```bash
sudo apt install git mpv python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-xlib ffmpeg zip xwayland
git clone https://github.com/JS-projects117/motionwall.git
cd motionwall
```

For **GNOME Wayland**:

```bash
./install.sh --extension
```

Log out and back in, open **Motionwall** from the app grid, and select a local
video. The enabled extension starts with GNOME; daemon autostart is for Xorg.
Ensure `~/.local/bin` is on your `PATH` to use the CLI.

For **Xorg**:

```bash
./install.sh --autostart
motionwall daemon
```

The daemon command runs in the foreground for the current session. At the next
login, the autostart entry starts it automatically.

The application installs under `~/.local` without root. System dependencies
still need to be available. `./install.sh --local-mpv` can fetch a private mpv
runtime from apt packages; this fallback is experimental, especially on non-x86_64
systems, and does not install the GTK dependencies.

## Build and install a Debian package

From the checkout, with Python 3 and `dpkg-deb` installed:

```bash
scripts/build-deb.sh
sudo apt install ./dist/motionwall_0.1.0_all.deb
```

The package installs the application and extension system-wide, and apt installs
the declared dependencies. On GNOME Wayland, open Motionwall once to enable the
extension, then log out and back in. On Xorg, run `motionwall daemon` or enable
start-at-login in the app.

See [packaging notes](packaging/debian/README.md). Downloadable packages, when
published, will appear on [GitHub Releases](https://github.com/JS-projects117/motionwall/releases).

## Use

Open **Motionwall** to choose a video, or right-click a video in Files and select
**Open With → Motionwall**. You can also control it from a terminal:

```bash
motionwall set ~/Videos/loop.mp4
motionwall pause
motionwall resume
motionwall toggle
motionwall stop
motionwall status --json
```

`stop` restores the static desktop while remembering the chosen video; `resume`
enables playback again. `motionwall quit-daemon` stops the Xorg service.
The GUI's **Reload Wallpaper** action restarts the extension's players.

Settings and the library live in `~/.config/motionwall`; thumbnails and logs live
in `~/.cache/motionwall` (or their XDG equivalents). Original videos stay at their
selected paths, so moving or deleting a video requires selecting it again.

## Troubleshooting

- **No wallpaper on Wayland:** check `gnome-extensions info motionwall@motionwall`,
  confirm Xwayland and mpv are installed, and log out and back in after enabling
  the extension.
- **No wallpaper on Xorg:** run `motionwall daemon` in a terminal and inspect its
  output, then check `motionwall status` in another terminal.
- **Static wallpaper in the overview:** expected for the standalone Xorg daemon;
  with the extension, check that it loaded successfully.
- **High CPU usage:** decoding may have fallen back to software. Check that your
  GPU driver and mpv support the selected codec; try a smaller video or a
  different hardware decoding setting.
- **Wallpaper stopped after a renderer failure:** use **Reload Wallpaper**, or
  disable and re-enable the extension.

Report problems in [GitHub Issues](https://github.com/JS-projects117/motionwall/issues)
with your OS, GNOME version, session type (Wayland/Xorg), GPU, mpv version, and
steps to reproduce. Include relevant logs after reviewing them for local paths.

## Uninstall

For a source installation:

```bash
./uninstall.sh          # keeps settings and library
./uninstall.sh --purge  # also removes Motionwall settings and caches
```

For a package installation:

```bash
sudo apt remove motionwall
```

Original video files are not deleted by these commands.

## Development

```bash
python3 -m unittest discover -s tests -v
scripts/build-deb.sh
```

The Python tests run without a display. They do not validate GNOME Shell
integration. Before a stable release, test a fresh install/uninstall, first-login
enabling, lock/unlock, idle/full-screen pause and resume, monitor changes,
fractional scaling, and coexistence with desktop icons on each supported setup.

Source layout: `motionwall/` contains the Python application and daemon;
`extension/` contains the GNOME renderer; `scripts/`, `data/`, and `packaging/`
provide installation and desktop integration.

## Credits and licensing

The GNOME extension's background-rendering and window-management approach is
adapted from [Hanabi](https://github.com/jeffshee/gnome-ext-hanabi) by Jeff Shee
and contributors, and [Desktop Icons NG (DING)](https://gitlab.com/rastersoft/desktop-icons-ng)
by Sergio Costas and contributors, including Sundeep Mediratta's Shell override
work. Thank you to those projects for making their work available.

See [CREDITS.md](CREDITS.md) for source references and dependency acknowledgments.
The original application code is [MIT-licensed](LICENSE). The bundled extension
is distributed under [GPL version 3](extension/COPYING), with its attribution and
modification notice in [extension/NOTICE](extension/NOTICE). AppStream metadata
is marked CC0-1.0. External dependencies retain their own licenses.
