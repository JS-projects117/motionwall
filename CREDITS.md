# Credits

## Referenced and adapted extension work

Motionwall's extension source has acknowledged DING and Hanabi since commit
`49bc0ab`. Their work informed background actor cloning and hiding renderer
windows from GNOME's overview, window lists, and application tracking.

- **[Hanabi](https://github.com/jeffshee/gnome-ext-hanabi)** — Jeff Shee and
  contributors. Related implementations: `src/wallpaper.ts` and
  `src/gnomeShellOverride.ts`; upstream license: GPL-3.0-or-later.
- **[Desktop Icons NG (DING)](https://gitlab.com/rastersoft/desktop-icons-ng)** —
  Sergio Costas (rastersoft) and contributors. Its `gnomeShellOverride.js`
  credits Sundeep Mediratta (2021) and Sergio Costas (2020), under GPL version 3.
  DING's main extension also acknowledges original work by Carlos Soriano.

Full attribution, reviewed source links, modification details, and the license
for Motionwall's extension are in [extension/NOTICE](extension/NOTICE) and
[extension/COPYING](extension/COPYING). These notices are included in both the
extension installation archive and the Debian package.

The original adaptation did not record exact upstream commits. The references
in NOTICE document the implementations inspected during the release review,
not a complete provenance history. Please report any missing attribution with
a source link so it can be corrected.

## Runtime dependencies

Motionwall relies on the following projects, installed separately through the
system package manager or the optional local mpv runtime:

- [mpv](https://mpv.io/) — video playback, hardware decoding, and JSON IPC.
- [FFmpeg](https://ffmpeg.org/) — thumbnail generation and media tooling.
- [GNOME Shell and Mutter](https://gitlab.gnome.org/GNOME/gnome-shell) — desktop
  integration, window management, and compositing.
- [GTK](https://www.gtk.org/), [libadwaita](https://gitlab.gnome.org/GNOME/libadwaita),
  [GLib/GIO](https://gitlab.gnome.org/GNOME/glib), and
  [PyGObject](https://pygobject.gnome.org/) — application UI and desktop services.
- [python-xlib](https://github.com/python-xlib/python-xlib) and
  [Xwayland](https://wayland.freedesktop.org/xserver.html) — X11 window integration.
- [UPower](https://upower.freedesktop.org/) — daemon battery-state monitoring.

These are dependency acknowledgments; their code is not relicensed by
Motionwall's MIT license. Their installed packages carry their own notices.
Local videos are supplied by the user and are not bundled in this repository.
